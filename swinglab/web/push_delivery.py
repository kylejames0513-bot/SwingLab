"""Expo push delivery provider and leased outbox worker (Task 7 outbox slice).

Registration lives in :mod:`push_store`. This module owns provider I/O and the
durable outbox that turns terminal job outcomes into generic Expo messages.
Missing ``EXPO_ACCESS_TOKEN`` disables enqueue and delivery without affecting
job success. Every Expo payload carries ``ttl=900``.
"""

from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from .jobs import DONE, FAILED, Job, JobManager
from .push_store import MobilePushSettings
from .push_cutover import PushFenceClosedError, require_open_fence
from .users import UserStore


logger = logging.getLogger("swinglab.web.push_delivery")

EXPO_ACCESS_TOKEN_ENV = "EXPO_ACCESS_TOKEN"
EXPO_SEND_URL = "https://exp.host/--/api/v2/push/send"
EXPO_RECEIPTS_URL = "https://exp.host/--/api/v2/push/getReceipts"
PUSH_TTL_SECONDS = 900

KIND_ANALYSIS_READY = "analysis_ready"
KIND_REFILM = "refilm_needed"

_MESSAGE_BODIES = {
    KIND_ANALYSIS_READY: "Your swing analysis is ready.",
    KIND_REFILM: "A quick re-film is needed.",
}


@dataclass(frozen=True)
class PushMessage:
    to: str
    title: str
    body: str
    data: dict[str, str]
    ttl: int = PUSH_TTL_SECONDS


@dataclass(frozen=True)
class PushTicket:
    status: str
    ticket_id: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class PushReceipt:
    ticket_id: str
    status: str
    error: str | None = None


class PushProvider(Protocol):
    def send(self, messages: Sequence[PushMessage]) -> Sequence[PushTicket]: ...

    def receipts(self, ticket_ids: Sequence[str]) -> Sequence[PushReceipt]: ...


class DeliveryDisabledProvider:
    """Provider used when EXPO_ACCESS_TOKEN is absent — never called."""

    def send(self, messages: Sequence[PushMessage]) -> Sequence[PushTicket]:
        raise RuntimeError("Expo push delivery is not configured.")

    def receipts(self, ticket_ids: Sequence[str]) -> Sequence[PushReceipt]:
        raise RuntimeError("Expo push delivery is not configured.")


class FakeExpoPushProvider:
    """In-memory provider for tests."""

    def __init__(self) -> None:
        self.sent: list[PushMessage] = []
        self._tickets: dict[str, PushTicket] = {}
        self.fail_send = False

    def send(self, messages: Sequence[PushMessage]) -> Sequence[PushTicket]:
        if self.fail_send:
            raise RuntimeError("synthetic Expo outage")
        tickets: list[PushTicket] = []
        for message in messages:
            assert message.ttl == PUSH_TTL_SECONDS
            self.sent.append(message)
            ticket_id = f"ticket-{len(self.sent)}"
            ticket = PushTicket(status="ok", ticket_id=ticket_id)
            self._tickets[ticket_id] = ticket
            tickets.append(ticket)
        return tickets

    def receipts(self, ticket_ids: Sequence[str]) -> Sequence[PushReceipt]:
        return [
            PushReceipt(ticket_id=ticket_id, status="ok")
            for ticket_id in ticket_ids
            if ticket_id in self._tickets
        ]


class ExpoPushProvider:
    """Real Expo HTTP provider (requires httpx + EXPO_ACCESS_TOKEN)."""

    def __init__(
        self,
        *,
        access_token: str,
        envelope_seconds: int = 30,
        client_factory: Callable | None = None,
    ) -> None:
        if not access_token:
            raise ValueError("EXPO_ACCESS_TOKEN is required.")
        self._token = access_token
        self._envelope_seconds = int(envelope_seconds)
        self._client_factory = client_factory

    def _client(self):
        if self._client_factory is not None:
            return self._client_factory()
        import httpx

        return httpx.Client(
            timeout=self._envelope_seconds,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    def send(self, messages: Sequence[PushMessage]) -> Sequence[PushTicket]:
        payload = [
            {
                "to": message.to,
                "title": message.title,
                "body": message.body,
                "data": message.data,
                "ttl": int(message.ttl),
            }
            for message in messages
        ]
        with self._client() as client:
            response = client.post(EXPO_SEND_URL, json=payload)
        if response.status_code == 401:
            raise RuntimeError("Expo push credential was rejected (UNAUTHORIZED).")
        response.raise_for_status()
        body = response.json()
        data = body.get("data")
        rows = data if isinstance(data, list) else [data]
        tickets: list[PushTicket] = []
        for row in rows:
            if not isinstance(row, dict):
                tickets.append(PushTicket(status="error", error="malformed"))
                continue
            tickets.append(
                PushTicket(
                    status=str(row.get("status") or "error"),
                    ticket_id=(
                        str(row["id"]) if row.get("id") is not None else None
                    ),
                    error=(
                        str(row.get("message"))
                        if row.get("message") is not None
                        else None
                    ),
                )
            )
        return tickets

    def receipts(self, ticket_ids: Sequence[str]) -> Sequence[PushReceipt]:
        with self._client() as client:
            response = client.post(
                EXPO_RECEIPTS_URL, json={"ids": list(ticket_ids)}
            )
        if response.status_code == 401:
            raise RuntimeError("Expo push credential was rejected (UNAUTHORIZED).")
        response.raise_for_status()
        body = response.json().get("data") or {}
        results: list[PushReceipt] = []
        for ticket_id in ticket_ids:
            row = body.get(ticket_id) if isinstance(body, dict) else None
            if not isinstance(row, dict):
                continue
            results.append(
                PushReceipt(
                    ticket_id=ticket_id,
                    status=str(row.get("status") or "error"),
                    error=(
                        str(row.get("message"))
                        if row.get("message") is not None
                        else None
                    ),
                )
            )
        return results


def expo_delivery_configured(
    *, environment: dict[str, str] | None = None
) -> bool:
    env = os.environ if environment is None else environment
    token = env.get(EXPO_ACCESS_TOKEN_ENV, "")
    return isinstance(token, str) and bool(token.strip())


def build_push_provider(
    *,
    envelope_seconds: int = 30,
    environment: dict[str, str] | None = None,
) -> PushProvider:
    env = os.environ if environment is None else environment
    token = env.get(EXPO_ACCESS_TOKEN_ENV, "")
    if not isinstance(token, str) or not token.strip():
        return DeliveryDisabledProvider()
    return ExpoPushProvider(
        access_token=token.strip(), envelope_seconds=envelope_seconds
    )


class PushOutboxStore:
    """SQLite outbox helpers bound to the shared users connection."""

    def __init__(self, users: UserStore) -> None:
        self._users = users

    def enqueue_job_notification(
        self,
        job: Job,
        *,
        kind: str,
        environment: str,
        expo_project_id: str,
        now: float | None = None,
    ) -> bool:
        """Insert one unique outbox row per selector when delivery is live."""

        if job.user_id is None or kind not in _MESSAGE_BODIES:
            return False
        if not expo_delivery_configured():
            return False
        observed = time.time() if now is None else float(now)
        terminal_at = observed
        with self._users._lock:
            try:
                require_open_fence(
                    self._users._conn,
                    environment=environment,
                    expo_project_id=expo_project_id,
                )
            except PushFenceClosedError:
                return False
            registrations = self._users._conn.execute(
                "SELECT selector, token, registered_at FROM mobile_push_registrations"
                " WHERE user_id = ? AND environment = ? AND expo_project_id = ?",
                (job.user_id, environment, expo_project_id),
            ).fetchall()
            if not registrations:
                return False
            watermark = self._users._conn.execute(
                "SELECT push_not_before FROM mobile_push_activation_watermarks"
                " WHERE environment = ? AND expo_project_id = ?",
                (environment, expo_project_id),
            ).fetchone()
            push_not_before = (
                float(watermark["push_not_before"]) if watermark is not None else 0.0
            )
            inserted = False
            for row in registrations:
                registered_at = float(row["registered_at"])
                if terminal_at < max(registered_at, push_not_before):
                    continue
                selector = str(row["selector"])
                try:
                    self._users._conn.execute(
                        "INSERT INTO mobile_push_outbox ("
                        " id, environment, expo_project_id, user_id, selector,"
                        " source_kind, source_id, kind, status, token,"
                        " attempts, created_at, updated_at, expires_at"
                        ") VALUES (?, ?, ?, ?, ?, 'job', ?, ?, 'pending', ?, 0, ?, ?, ?)",
                        (
                            uuid.uuid4().hex,
                            environment,
                            expo_project_id,
                            job.user_id,
                            selector,
                            job.id,
                            kind,
                            str(row["token"]),
                            observed,
                            observed,
                            observed + PUSH_TTL_SECONDS,
                        ),
                    )
                    inserted = True
                except sqlite3.IntegrityError:
                    continue
            self._users._conn.commit()
            return inserted

    def discard_for_selector(self, *, user_id: str, selector: str) -> None:
        with self._users._lock:
            self._users._conn.execute(
                "UPDATE mobile_push_outbox SET status = 'dead',"
                " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                " WHERE user_id = ? AND selector = ?"
                " AND status IN ('pending', 'leased')",
                (time.time(), user_id, selector),
            )
            self._users._conn.commit()

    def discard_for_user(self, user_id: str) -> None:
        with self._users._lock:
            self._users._conn.execute(
                "UPDATE mobile_push_outbox SET status = 'dead',"
                " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                " WHERE user_id = ?"
                " AND status IN ('pending', 'leased', 'awaiting_receipt')",
                (time.time(), user_id),
            )
            self._users._conn.commit()


class PushOutboxWorker:
    """Lease pending outbox rows and drive provider send/receipts."""

    def __init__(
        self,
        users: UserStore,
        provider: PushProvider,
        *,
        enabled: bool,
        lease_seconds: int = 30,
    ) -> None:
        self._users = users
        self._provider = provider
        self._enabled = enabled
        self._lease_seconds = int(lease_seconds)
        self._owner = secrets.token_hex(8)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self._enabled or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="push-outbox-worker", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(1.0):
            try:
                while self.drain_once():
                    if self._stop.is_set():
                        return
            except Exception:
                logger.exception("Push outbox worker iteration failed.")

    def drain_once(self, *, now: float | None = None) -> bool:
        if not self._enabled:
            return False
        if isinstance(self._provider, DeliveryDisabledProvider):
            return False
        observed = time.time() if now is None else float(now)
        with self._users._lock:
            self._users._conn.execute(
                "UPDATE mobile_push_outbox SET status = 'pending',"
                " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                " WHERE status = 'leased' AND lease_expires_at IS NOT NULL"
                " AND lease_expires_at < ?",
                (observed, observed),
            )
            # Expire stale pending rows so TTL is terminal, not just a skip filter.
            self._users._conn.execute(
                "UPDATE mobile_push_outbox SET status = 'dead',"
                " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                " WHERE status IN ('pending', 'leased') AND expires_at <= ?",
                (observed, observed),
            )
            row = self._users._conn.execute(
                "SELECT * FROM mobile_push_outbox"
                " WHERE status = 'pending' AND expires_at > ?"
                " ORDER BY created_at ASC LIMIT 1",
                (observed,),
            ).fetchone()
            if row is None:
                self._users._conn.commit()
                return False
            try:
                require_open_fence(
                    self._users._conn,
                    environment=str(row["environment"]),
                    expo_project_id=str(row["expo_project_id"]),
                )
            except PushFenceClosedError:
                self._users._conn.execute(
                    "UPDATE mobile_push_outbox SET status = 'dead',"
                    " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                    " WHERE id = ? AND status IN ('pending', 'leased')",
                    (observed, row["id"]),
                )
                self._users._conn.commit()
                return True
            # Recheck live registration and bind send to the live token.
            live = self._users._conn.execute(
                "SELECT token FROM mobile_push_registrations"
                " WHERE user_id = ? AND selector = ?"
                " AND environment = ? AND expo_project_id = ? LIMIT 1",
                (
                    row["user_id"],
                    row["selector"],
                    row["environment"],
                    row["expo_project_id"],
                ),
            ).fetchone()
            if live is None:
                self._users._conn.execute(
                    "UPDATE mobile_push_outbox SET status = 'dead',"
                    " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                    " WHERE id = ? AND status IN ('pending', 'leased')",
                    (observed, row["id"]),
                )
                self._users._conn.commit()
                return True
            live_token = str(live["token"])
            if live_token != str(row["token"]):
                # Registration rotated without matching outbox token: refuse
                # the frozen token and dead-letter rather than send stale.
                self._users._conn.execute(
                    "UPDATE mobile_push_outbox SET status = 'dead',"
                    " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                    " WHERE id = ? AND status = 'pending'",
                    (observed, row["id"]),
                )
                self._users._conn.commit()
                return True
            self._users._conn.execute(
                "UPDATE mobile_push_outbox SET status = 'leased',"
                " lease_owner = ?, lease_expires_at = ?, updated_at = ?,"
                " attempts = attempts + 1 WHERE id = ? AND status = 'pending'",
                (
                    self._owner,
                    observed + self._lease_seconds,
                    observed,
                    row["id"],
                ),
            )
            # Durably advance fence provider clocks before the first HTTP byte.
            may_until = observed + float(self._lease_seconds)
            environment = str(row["environment"])
            expo_project_id = str(row["expo_project_id"])
            self._users._conn.execute(
                "UPDATE mobile_push_environment_fences SET"
                " last_provider_started_at = CASE"
                "  WHEN last_provider_started_at IS NULL"
                "   OR last_provider_started_at < ? THEN ?"
                "  ELSE last_provider_started_at END,"
                " provider_may_accept_until = CASE"
                "  WHEN provider_may_accept_until IS NULL"
                "   OR provider_may_accept_until < ? THEN ?"
                "  ELSE provider_may_accept_until END,"
                " updated_at = ?"
                " WHERE environment = ? AND expo_project_id = ?"
                " AND state = 'open'",
                (
                    observed,
                    observed,
                    may_until,
                    may_until,
                    observed,
                    environment,
                    expo_project_id,
                ),
            )
            self._users._conn.commit()
            token = live_token
            kind = str(row["kind"])
            outbox_id = str(row["id"])
            source_id = str(row["source_id"])

        message = PushMessage(
            to=token,
            title="CaddieInsight",
            body=_MESSAGE_BODIES[kind],
            data={"kind": kind, "path": f"/sessions/{source_id}"},
            ttl=PUSH_TTL_SECONDS,
        )
        try:
            tickets = list(self._provider.send([message]))
        except Exception:
            logger.warning("Push send failed outbox_id=%s", outbox_id)
            with self._users._lock:
                # CAS: only revive if still leased by us (sign-out may have
                # already marked the row dead and cleared the lease).
                self._users._conn.execute(
                    "UPDATE mobile_push_outbox SET status = 'pending',"
                    " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                    " WHERE id = ? AND status = 'leased' AND lease_owner = ?",
                    (time.time(), outbox_id, self._owner),
                )
                self._users._conn.commit()
            return True

        ticket = tickets[0] if tickets else PushTicket(status="error", error="empty")
        accepted_at = time.time()
        with self._users._lock:
            if ticket.status == "ok" and ticket.ticket_id:
                self._users._conn.execute(
                    "UPDATE mobile_push_outbox SET status = 'delivered',"
                    " provider_ticket_id = ?, lease_owner = NULL,"
                    " lease_expires_at = NULL, updated_at = ?"
                    " WHERE id = ? AND status = 'leased' AND lease_owner = ?",
                    (ticket.ticket_id, accepted_at, outbox_id, self._owner),
                )
                self._users._conn.execute(
                    "UPDATE mobile_push_environment_fences SET"
                    " last_provider_accepted_at = CASE"
                    "  WHEN last_provider_accepted_at IS NULL"
                    "   OR last_provider_accepted_at < ? THEN ?"
                    "  ELSE last_provider_accepted_at END,"
                    " updated_at = ?"
                    " WHERE environment = ? AND expo_project_id = ?",
                    (
                        accepted_at,
                        accepted_at,
                        accepted_at,
                        environment,
                        expo_project_id,
                    ),
                )
            else:
                self._users._conn.execute(
                    "UPDATE mobile_push_outbox SET status = 'dead',"
                    " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                    " WHERE id = ? AND status = 'leased' AND lease_owner = ?",
                    (accepted_at, outbox_id, self._owner),
                )
            self._users._conn.commit()
        return True


def attach_job_push_observer(
    manager: JobManager,
    *,
    outbox: PushOutboxStore,
    settings: MobilePushSettings,
    deployment_environment: str,
) -> None:
    """Register an exception-isolated completion observer on JobManager."""

    def _observer(job: Job) -> None:
        if job.status != DONE:
            return
        # Generic analysis-ready notification. Refilm-specific copy is deferred
        # until report-outcome classification is wired into the observer.
        outbox.enqueue_job_notification(
            job,
            kind=KIND_ANALYSIS_READY,
            environment=deployment_environment,
            expo_project_id=settings.expo_project_id,
        )

    add = getattr(manager, "add_completion_observer", None)
    if callable(add):
        add(_observer)
    else:
        # Fallback for older JobManager shapes during partial upgrades.
        logger.warning("JobManager has no completion observer hook.")
