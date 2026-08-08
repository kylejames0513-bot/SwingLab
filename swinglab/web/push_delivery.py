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
KIND_PRACTICE_REMINDER = "practice_reminder"
KIND_SECURITY_NOTICE = "security_notice"

_MESSAGE_BODIES = {
    KIND_ANALYSIS_READY: "Your swing analysis is ready.",
    KIND_REFILM: "A quick re-film is needed.",
    KIND_PRACTICE_REMINDER: "Ready for your next practice check-in.",
    KIND_SECURITY_NOTICE: (
        "A new device signed in to your CaddieInsight account."
    ),
}

# Typed failure codes already persisted on Job that mean "re-film needed".
# DONE jobs that need re-film via report/metrics classification do not carry a
# reliable Job field today — see attach_job_push_observer TODO.
_REFILM_FAILURE_CODES = frozenset({"capture_no_strike", "capture_pose_unusable"})

PENDING_MAX_AGE_SECONDS = 24 * 60 * 60
TERMINAL_RETENTION_SECONDS = 30 * 24 * 60 * 60
TICKET_DETAIL_RETENTION_SECONDS = 7 * 24 * 60 * 60
BACKFILL_LOOKBACK_SECONDS = 24 * 60 * 60
WORKER_SCAN_EVERY_N = 60


def message_path_for_kind(kind: str, source_id: str) -> str:
    if kind in (KIND_ANALYSIS_READY, KIND_REFILM):
        return f"/sessions/{source_id}"
    if kind == KIND_PRACTICE_REMINDER:
        return "/practice"
    if kind == KIND_SECURITY_NOTICE:
        return "/devices"
    return "/"


def job_push_kind(job: Job) -> str | None:
    """Return the outbox kind for a terminal job, or None to skip."""

    failure = getattr(job, "failure_code", None)
    if isinstance(failure, str) and failure in _REFILM_FAILURE_CODES:
        return KIND_REFILM
    if job.status == DONE:
        # TODO: DONE + report-level refilm (coaching-ineligible / capture
        # outcome) is not a field on Job; until that classification is wired
        # into the observer, enqueue analysis_ready for DONE only.
        return KIND_ANALYSIS_READY
    return None


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
        self.fail_receipt = False
        self.device_not_registered: set[str] = set()
        self.pending_receipts: set[str] = set()

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
        if self.fail_receipt:
            raise RuntimeError("synthetic Expo receipt outage")
        results: list[PushReceipt] = []
        for ticket_id in ticket_ids:
            if ticket_id not in self._tickets:
                continue
            if ticket_id in self.pending_receipts:
                continue
            if ticket_id in self.device_not_registered:
                results.append(
                    PushReceipt(
                        ticket_id=ticket_id,
                        status="error",
                        error="DeviceNotRegistered",
                    )
                )
                continue
            results.append(PushReceipt(ticket_id=ticket_id, status="ok"))
        return results


class PushDeliveryClosed(RuntimeError):
    """Selector admission is closed; new provider work must not start."""


@dataclass(frozen=True)
class _DeliveryGuardToken:
    selector: str
    token_id: int


class PushDeliveryGuard:
    """Tracks in-flight Expo send/receipt work by selector."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._inflight: dict[str, set[int]] = {}
        self._token_threads: dict[int, int] = {}
        self._closed: set[str] = set()
        self._next_id = 1
        self._condition = threading.Condition(self._lock)

    def begin(self, *, selector: str) -> _DeliveryGuardToken:
        with self._condition:
            if selector in self._closed:
                raise PushDeliveryClosed(
                    "Push delivery admission is closed for this selector."
                )
            token_id = self._next_id
            self._next_id += 1
            self._inflight.setdefault(selector, set()).add(token_id)
            self._token_threads[token_id] = threading.get_ident()
            return _DeliveryGuardToken(selector=selector, token_id=token_id)

    def end(self, token: _DeliveryGuardToken) -> None:
        with self._condition:
            active = self._inflight.get(token.selector)
            if active is not None:
                active.discard(token.token_id)
                if not active:
                    self._inflight.pop(token.selector, None)
            self._token_threads.pop(token.token_id, None)
            self._condition.notify_all()

    def close_selector(self, selector: str) -> None:
        with self._condition:
            self._closed.add(selector)
            self._condition.notify_all()

    def is_closed(self, selector: str) -> bool:
        with self._condition:
            return selector in self._closed

    def reopen_selector(self, selector: str) -> None:
        """Test helper: allow registration again after a closed drain."""

        with self._condition:
            self._closed.discard(selector)
            self._condition.notify_all()

    def drain_selector(
        self, selector: str, *, timeout_seconds: float
    ) -> bool:
        """Wait for other threads' in-flight work; ignore this thread's tokens."""

        deadline = time.time() + float(timeout_seconds)
        caller = threading.get_ident()
        with self._condition:
            while True:
                active = self._inflight.get(selector, set())
                foreign = {
                    token_id
                    for token_id in active
                    if self._token_threads.get(token_id) != caller
                }
                if not foreign:
                    return True
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=remaining)

RECEIPT_POLL_DELAY_SECONDS = 2.0
RECEIPT_POLL_BACKOFF_SECONDS = 5.0

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
                        str(
                            (row.get("details") or {}).get("error")
                            if isinstance(row.get("details"), dict)
                            else row.get("message")
                        )
                        if (
                            (
                                isinstance(row.get("details"), dict)
                                and row.get("details", {}).get("error") is not None
                            )
                            or row.get("message") is not None
                        )
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


def mark_stale_pending_dead(conn, *, now: float) -> int:
    """Mark pending/leased rows older than 24h from created_at as dead."""

    cutoff = float(now) - PENDING_MAX_AGE_SECONDS
    cursor = conn.execute(
        "UPDATE mobile_push_outbox SET status = 'dead',"
        " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
        " WHERE status IN ('pending', 'leased')"
        " AND created_at < ?",
        (now, cutoff),
    )
    return int(cursor.rowcount or 0)


def purge_terminal_outbox(
    conn, *, now: float, limit: int = 1000
) -> int:
    """Delete delivered|dead rows older than 30 days, at most ``limit`` rows.

    Also clears provider ticket details on awaiting_receipt rows older than
    seven days by nulling ticket fields when past detail retention (rows stay).
    """

    max_rows = max(0, int(limit))
    if max_rows == 0:
        return 0
    terminal_cutoff = float(now) - TERMINAL_RETENTION_SECONDS
    detail_cutoff = float(now) - TICKET_DETAIL_RETENTION_SECONDS
    conn.execute(
        "UPDATE mobile_push_outbox SET provider_ticket_id = NULL,"
        " receipt_due_at = NULL, updated_at = ?"
        " WHERE status = 'awaiting_receipt'"
        " AND provider_ticket_id IS NOT NULL"
        " AND created_at < ?",
        (now, detail_cutoff),
    )
    ids = [
        str(row["id"])
        for row in conn.execute(
            "SELECT id FROM mobile_push_outbox"
            " WHERE status IN ('delivered', 'dead')"
            " AND updated_at < ?"
            " ORDER BY updated_at ASC LIMIT ?",
            (terminal_cutoff, max_rows),
        ).fetchall()
    ]
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    cursor = conn.execute(
        f"DELETE FROM mobile_push_outbox WHERE id IN ({placeholders})",
        ids,
    )
    return int(cursor.rowcount or 0)


class PushOutboxStore:
    """SQLite outbox helpers bound to the shared users connection."""

    def __init__(
        self,
        users: UserStore,
        *,
        global_cap: int = 10000,
        per_selector_cap: int = 50,
    ) -> None:
        self._users = users
        self._global_cap = int(global_cap)
        self._per_selector_cap = int(per_selector_cap)

    def _global_capacity_ok(
        self,
        *,
        environment: str,
        expo_project_id: str,
        observed: float,
    ) -> bool:
        nonterminal = int(
            self._users._conn.execute(
                "SELECT COUNT(*) FROM mobile_push_outbox"
                " WHERE environment = ? AND expo_project_id = ?"
                " AND status IN ('pending', 'leased', 'awaiting_receipt')",
                (environment, expo_project_id),
            ).fetchone()[0]
        )
        if nonterminal >= self._global_cap:
            self._users._conn.execute(
                "UPDATE mobile_push_environment_fences"
                " SET aggregate_drop_count = aggregate_drop_count + 1,"
                " updated_at = ?"
                " WHERE environment = ? AND expo_project_id = ?",
                (observed, environment, expo_project_id),
            )
            return False
        return True

    def _admit_capacity(
        self,
        *,
        environment: str,
        expo_project_id: str,
        selector: str,
        observed: float,
    ) -> bool:
        selector_nonterminal = int(
            self._users._conn.execute(
                "SELECT COUNT(*) FROM mobile_push_outbox"
                " WHERE selector = ?"
                " AND status IN ('pending', 'leased', 'awaiting_receipt')",
                (selector,),
            ).fetchone()[0]
        )
        if selector_nonterminal >= self._per_selector_cap:
            self._users._conn.execute(
                "UPDATE mobile_push_environment_fences"
                " SET aggregate_drop_count = aggregate_drop_count + 1,"
                " updated_at = ?"
                " WHERE environment = ? AND expo_project_id = ?",
                (observed, environment, expo_project_id),
            )
            return False
        return True

    def _insert_outbox_row(
        self,
        *,
        environment: str,
        expo_project_id: str,
        user_id: str,
        selector: str,
        source_kind: str,
        source_id: str,
        kind: str,
        token: str,
        observed: float,
    ) -> bool:
        try:
            self._users._conn.execute(
                "INSERT INTO mobile_push_outbox ("
                " id, environment, expo_project_id, user_id, selector,"
                " source_kind, source_id, kind, status, token,"
                " attempts, created_at, updated_at, expires_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, 0, ?, ?, ?)",
                (
                    uuid.uuid4().hex,
                    environment,
                    expo_project_id,
                    user_id,
                    selector,
                    source_kind,
                    source_id,
                    kind,
                    token,
                    observed,
                    observed,
                    observed + PUSH_TTL_SECONDS,
                ),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def enqueue_job_notification(
        self,
        job: Job,
        *,
        kind: str,
        environment: str,
        expo_project_id: str,
        now: float | None = None,
        terminal_at: float | None = None,
    ) -> bool:
        """Insert one unique outbox row per selector when delivery is live."""

        if job.user_id is None or kind not in _MESSAGE_BODIES:
            return False
        if not expo_delivery_configured():
            return False
        observed = time.time() if now is None else float(now)
        finished_at = observed if terminal_at is None else float(terminal_at)
        if finished_at < observed - PENDING_MAX_AGE_SECONDS:
            return False
        with self._users._lock:
            mark_stale_pending_dead(self._users._conn, now=observed)
            try:
                require_open_fence(
                    self._users._conn,
                    environment=environment,
                    expo_project_id=expo_project_id,
                )
            except PushFenceClosedError:
                self._users._conn.commit()
                return False
            nonterminal = int(
                self._users._conn.execute(
                    "SELECT COUNT(*) FROM mobile_push_outbox"
                    " WHERE environment = ? AND expo_project_id = ?"
                    " AND status IN ('pending', 'leased', 'awaiting_receipt')",
                    (environment, expo_project_id),
                ).fetchone()[0]
            )
            if nonterminal >= self._global_cap:
                self._users._conn.execute(
                    "UPDATE mobile_push_environment_fences"
                    " SET aggregate_drop_count = aggregate_drop_count + 1,"
                    " updated_at = ?"
                    " WHERE environment = ? AND expo_project_id = ?",
                    (observed, environment, expo_project_id),
                )
                self._users._conn.commit()
                return False
            registrations = self._users._conn.execute(
                "SELECT selector, token, registered_at FROM mobile_push_registrations"
                " WHERE user_id = ? AND environment = ? AND expo_project_id = ?",
                (job.user_id, environment, expo_project_id),
            ).fetchall()
            if not registrations:
                self._users._conn.commit()
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
                if finished_at < max(registered_at, push_not_before):
                    continue
                selector = str(row["selector"])
                if not self._admit_capacity(
                    environment=environment,
                    expo_project_id=expo_project_id,
                    selector=selector,
                    observed=observed,
                ):
                    continue
                if self._insert_outbox_row(
                    environment=environment,
                    expo_project_id=expo_project_id,
                    user_id=job.user_id,
                    selector=selector,
                    source_kind="job",
                    source_id=job.id,
                    kind=kind,
                    token=str(row["token"]),
                    observed=observed,
                ):
                    inserted = True
                    nonterminal += 1
                    if nonterminal >= self._global_cap:
                        break
            self._users._conn.commit()
            return inserted

    def enqueue_refilm_needed(
        self,
        job: Job,
        *,
        environment: str,
        expo_project_id: str,
        now: float | None = None,
        terminal_at: float | None = None,
    ) -> bool:
        """Helper for KIND_REFILM enqueue (typed failure / future DONE refilm)."""

        return self.enqueue_job_notification(
            job,
            kind=KIND_REFILM,
            environment=environment,
            expo_project_id=expo_project_id,
            now=now,
            terminal_at=terminal_at,
        )

    def enqueue_security_notices_for_other_devices(
        self,
        *,
        user_id: str,
        new_selector: str,
        environment: str,
        expo_project_id: str,
        source_id: str,
        now: float | None = None,
    ) -> int:
        """Notify other active selectors that a new device registered."""

        if not expo_delivery_configured():
            return 0
        observed = time.time() if now is None else float(now)
        inserted = 0
        with self._users._lock:
            mark_stale_pending_dead(self._users._conn, now=observed)
            try:
                require_open_fence(
                    self._users._conn,
                    environment=environment,
                    expo_project_id=expo_project_id,
                )
            except PushFenceClosedError:
                self._users._conn.commit()
                return 0
            nonterminal = int(
                self._users._conn.execute(
                    "SELECT COUNT(*) FROM mobile_push_outbox"
                    " WHERE environment = ? AND expo_project_id = ?"
                    " AND status IN ('pending', 'leased', 'awaiting_receipt')",
                    (environment, expo_project_id),
                ).fetchone()[0]
            )
            if nonterminal >= self._global_cap:
                self._users._conn.execute(
                    "UPDATE mobile_push_environment_fences"
                    " SET aggregate_drop_count = aggregate_drop_count + 1,"
                    " updated_at = ?"
                    " WHERE environment = ? AND expo_project_id = ?",
                    (observed, environment, expo_project_id),
                )
                self._users._conn.commit()
                return 0
            others = self._users._conn.execute(
                "SELECT selector, token FROM mobile_push_registrations"
                " WHERE user_id = ? AND environment = ? AND expo_project_id = ?"
                " AND selector != ?",
                (user_id, environment, expo_project_id, new_selector),
            ).fetchall()
            for row in others:
                selector = str(row["selector"])
                if not self._admit_capacity(
                    environment=environment,
                    expo_project_id=expo_project_id,
                    selector=selector,
                    observed=observed,
                ):
                    continue
                if self._insert_outbox_row(
                    environment=environment,
                    expo_project_id=expo_project_id,
                    user_id=user_id,
                    selector=selector,
                    source_kind="security_notice",
                    source_id=source_id,
                    kind=KIND_SECURITY_NOTICE,
                    token=str(row["token"]),
                    observed=observed,
                ):
                    inserted += 1
                    nonterminal += 1
                    if nonterminal >= self._global_cap:
                        break
            self._users._conn.commit()
        return inserted

    def enqueue_practice_reminder(
        self,
        *,
        user_id: str,
        selector: str,
        environment: str,
        expo_project_id: str,
        source_id: str | None = None,
        now: float | None = None,
    ) -> bool:
        """Enqueue a practice reminder when the selector preference allows it.

        Full due-time cron is deferred: registrations have no next-due column
        yet. Callers (or a future scanner) supply a unique ``source_id``.
        """

        if not expo_delivery_configured():
            return False
        observed = time.time() if now is None else float(now)
        reminder_id = source_id or f"{selector}:{int(observed)}"
        with self._users._lock:
            mark_stale_pending_dead(self._users._conn, now=observed)
            try:
                require_open_fence(
                    self._users._conn,
                    environment=environment,
                    expo_project_id=expo_project_id,
                )
            except PushFenceClosedError:
                self._users._conn.commit()
                return False
            registration = self._users._conn.execute(
                "SELECT token, practice_reminders_enabled"
                " FROM mobile_push_registrations"
                " WHERE user_id = ? AND selector = ?"
                " AND environment = ? AND expo_project_id = ?",
                (user_id, selector, environment, expo_project_id),
            ).fetchone()
            if registration is None:
                self._users._conn.commit()
                return False
            if not bool(registration["practice_reminders_enabled"]):
                self._users._conn.commit()
                return False
            if not self._global_capacity_ok(
                environment=environment,
                expo_project_id=expo_project_id,
                observed=observed,
            ):
                self._users._conn.commit()
                return False
            if not self._admit_capacity(
                environment=environment,
                expo_project_id=expo_project_id,
                selector=selector,
                observed=observed,
            ):
                self._users._conn.commit()
                return False
            inserted = self._insert_outbox_row(
                environment=environment,
                expo_project_id=expo_project_id,
                user_id=user_id,
                selector=selector,
                source_kind="practice_reminder",
                source_id=reminder_id,
                kind=KIND_PRACTICE_REMINDER,
                token=str(registration["token"]),
                observed=observed,
            )
            self._users._conn.commit()
            return inserted

    def backfill_missing_for_terminal_jobs(
        self,
        *,
        environment: str,
        expo_project_id: str,
        now: float | None = None,
        lookback_seconds: float = BACKFILL_LOOKBACK_SECONDS,
        limit: int = 100,
    ) -> int:
        """Insert missing analysis_ready rows for recent DONE jobs.

        Uses jobs.updated_at as terminal_at. Jobs that finished before
        registration / activation watermark are skipped by enqueue rules.
        Existing unique keys are no-ops.
        """

        if not expo_delivery_configured():
            return 0
        observed = time.time() if now is None else float(now)
        since = observed - float(lookback_seconds)
        with self._users._lock:
            mark_stale_pending_dead(self._users._conn, now=observed)
            try:
                require_open_fence(
                    self._users._conn,
                    environment=environment,
                    expo_project_id=expo_project_id,
                )
            except PushFenceClosedError:
                self._users._conn.commit()
                return 0
            rows = self._users._conn.execute(
                "SELECT id, user_id, updated_at, failure_code, status"
                " FROM jobs"
                " WHERE status = ? AND user_id IS NOT NULL"
                " AND updated_at >= ?"
                " ORDER BY updated_at DESC LIMIT ?",
                (DONE, since, max(1, int(limit))),
            ).fetchall()
            self._users._conn.commit()

        from pathlib import Path

        inserted = 0
        for row in rows:
            job = Job(
                id=str(row["id"]),
                session_dir=Path("."),
                status=DONE,
                user_id=str(row["user_id"]),
                failure_code=(
                    str(row["failure_code"])
                    if row["failure_code"] is not None
                    else None
                ),
            )
            # session_dir unused by enqueue; only id/user_id/kind matter.
            kind = job_push_kind(job) or KIND_ANALYSIS_READY
            if self.enqueue_job_notification(
                job,
                kind=kind,
                environment=environment,
                expo_project_id=expo_project_id,
                now=observed,
                terminal_at=float(row["updated_at"]),
            ):
                inserted += 1
        return inserted

    def discard_for_selector(self, *, user_id: str, selector: str) -> None:
        with self._users._lock:
            self._users._conn.execute(
                "UPDATE mobile_push_outbox SET status = 'dead',"
                " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                " WHERE user_id = ? AND selector = ?"
                " AND status IN ('pending', 'leased', 'awaiting_receipt')",
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
        delivery_guard: PushDeliveryGuard | None = None,
        receipt_delay_seconds: float = RECEIPT_POLL_DELAY_SECONDS,
        outbox: PushOutboxStore | None = None,
        environment: str | None = None,
        expo_project_id: str | None = None,
        scan_every_n: int = WORKER_SCAN_EVERY_N,
    ) -> None:
        self._users = users
        self._provider = provider
        self._enabled = enabled
        self._lease_seconds = int(lease_seconds)
        self._delivery_guard = delivery_guard or PushDeliveryGuard()
        self._receipt_delay_seconds = float(receipt_delay_seconds)
        self._outbox = outbox
        self._environment = environment
        self._expo_project_id = expo_project_id
        self._scan_every_n = max(1, int(scan_every_n))
        self._owner = secrets.token_hex(8)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._iterations = 0

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
        # Startup retention pass; subsequent scans run every N drain loops.
        try:
            self.scan_once()
        except Exception:
            logger.exception("Push outbox startup scan failed.")
        while not self._stop.wait(1.0):
            try:
                while self.drain_once():
                    if self._stop.is_set():
                        return
                self._iterations += 1
                if self._iterations % self._scan_every_n == 0:
                    self.scan_once()
            except Exception:
                logger.exception("Push outbox worker iteration failed.")

    def scan_once(self, *, now: float | None = None) -> None:
        """Infrequent maintenance: stale pending, retention purge, backfill."""

        if not self._enabled:
            return
        observed = time.time() if now is None else float(now)
        with self._users._lock:
            mark_stale_pending_dead(self._users._conn, now=observed)
            purge_terminal_outbox(self._users._conn, now=observed, limit=1000)
            self._users._conn.commit()
        if (
            self._outbox is not None
            and self._environment
            and self._expo_project_id
            and expo_delivery_configured()
        ):
            try:
                self._outbox.backfill_missing_for_terminal_jobs(
                    environment=self._environment,
                    expo_project_id=self._expo_project_id,
                    now=observed,
                )
            except Exception:
                logger.exception("Push outbox terminal-job backfill failed.")

    def drain_once(self, *, now: float | None = None) -> bool:
        if not self._enabled:
            return False
        if isinstance(self._provider, DeliveryDisabledProvider):
            return False
        observed = time.time() if now is None else float(now)
        with self._users._lock:
            mark_stale_pending_dead(self._users._conn, now=observed)
            self._users._conn.commit()
        if self._drain_receipt(observed):
            return True
        return self._drain_send(observed)

    def _drain_receipt(self, observed: float) -> bool:
        with self._users._lock:
            self._users._conn.execute(
                "UPDATE mobile_push_outbox SET status = 'dead',"
                " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                " WHERE status = 'awaiting_receipt' AND expires_at <= ?",
                (observed, observed),
            )
            row = self._users._conn.execute(
                "SELECT * FROM mobile_push_outbox"
                " WHERE status = 'awaiting_receipt'"
                " AND provider_ticket_id IS NOT NULL"
                " AND (receipt_due_at IS NULL OR receipt_due_at <= ?)"
                " AND expires_at > ?"
                " ORDER BY COALESCE(receipt_due_at, created_at) ASC LIMIT 1",
                (observed, observed),
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
                    " WHERE id = ? AND status = 'awaiting_receipt'",
                    (observed, row["id"]),
                )
                self._users._conn.commit()
                return True
            self._users._conn.execute(
                "UPDATE mobile_push_outbox SET lease_owner = ?,"
                " lease_expires_at = ?, updated_at = ?, attempts = attempts + 1"
                " WHERE id = ? AND status = 'awaiting_receipt'",
                (
                    self._owner,
                    observed + self._lease_seconds,
                    observed,
                    row["id"],
                ),
            )
            self._users._conn.commit()
            outbox_id = str(row["id"])
            ticket_id = str(row["provider_ticket_id"])
            selector = str(row["selector"])
            user_id = str(row["user_id"])
            environment = str(row["environment"])
            expo_project_id = str(row["expo_project_id"])
            token = str(row["token"])

        try:
            guard_token = self._delivery_guard.begin(selector=selector)
        except PushDeliveryClosed:
            with self._users._lock:
                self._users._conn.execute(
                    "UPDATE mobile_push_outbox SET status = 'dead',"
                    " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                    " WHERE id = ? AND status = 'awaiting_receipt'"
                    " AND lease_owner = ?",
                    (observed, outbox_id, self._owner),
                )
                self._users._conn.commit()
            return True

        try:
            try:
                receipts = list(self._provider.receipts([ticket_id]))
            except Exception:
                logger.warning("Push receipt poll failed outbox_id=%s", outbox_id)
                with self._users._lock:
                    self._users._conn.execute(
                        "UPDATE mobile_push_outbox SET receipt_due_at = ?,"
                        " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                        " WHERE id = ? AND status = 'awaiting_receipt'"
                        " AND lease_owner = ?",
                        (
                            observed + RECEIPT_POLL_BACKOFF_SECONDS,
                            observed,
                            outbox_id,
                            self._owner,
                        ),
                    )
                    self._users._conn.commit()
                return True

            receipt = receipts[0] if receipts else None
            now = observed
            with self._users._lock:
                if self._delivery_guard.is_closed(selector):
                    self._users._conn.execute(
                        "UPDATE mobile_push_outbox SET status = 'dead',"
                        " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                        " WHERE id = ? AND status = 'awaiting_receipt'"
                        " AND lease_owner = ?",
                        (now, outbox_id, self._owner),
                    )
                elif receipt is None:
                    self._users._conn.execute(
                        "UPDATE mobile_push_outbox SET receipt_due_at = ?,"
                        " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                        " WHERE id = ? AND status = 'awaiting_receipt'"
                        " AND lease_owner = ?",
                        (
                            now + RECEIPT_POLL_BACKOFF_SECONDS,
                            now,
                            outbox_id,
                            self._owner,
                        ),
                    )
                elif receipt.status == "ok":
                    self._users._conn.execute(
                        "UPDATE mobile_push_outbox SET status = 'delivered',"
                        " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                        " WHERE id = ? AND status = 'awaiting_receipt'"
                        " AND lease_owner = ?",
                        (now, outbox_id, self._owner),
                    )
                elif (
                    receipt.error
                    and "devicenotregistered" in receipt.error.lower()
                ):
                    self._users._conn.execute(
                        "UPDATE mobile_push_outbox SET status = 'dead',"
                        " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                        " WHERE id = ? AND status = 'awaiting_receipt'"
                        " AND lease_owner = ?",
                        (now, outbox_id, self._owner),
                    )
                    self._users._conn.execute(
                        "DELETE FROM mobile_push_registrations"
                        " WHERE user_id = ? AND selector = ?"
                        " AND environment = ? AND expo_project_id = ?"
                        " AND token = ?",
                        (
                            user_id,
                            selector,
                            environment,
                            expo_project_id,
                            token,
                        ),
                    )
                else:
                    self._users._conn.execute(
                        "UPDATE mobile_push_outbox SET status = 'dead',"
                        " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                        " WHERE id = ? AND status = 'awaiting_receipt'"
                        " AND lease_owner = ?",
                        (now, outbox_id, self._owner),
                    )
                self._users._conn.commit()
            return True
        finally:
            self._delivery_guard.end(guard_token)
    def _drain_send(self, observed: float) -> bool:
        with self._users._lock:
            self._users._conn.execute(
                "UPDATE mobile_push_outbox SET status = 'pending',"
                " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                " WHERE status = 'leased' AND lease_expires_at IS NOT NULL"
                " AND lease_expires_at < ?",
                (observed, observed),
            )
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
            selector = str(row["selector"])

        message = PushMessage(
            to=token,
            title="CaddieInsight",
            body=_MESSAGE_BODIES.get(kind, _MESSAGE_BODIES[KIND_ANALYSIS_READY]),
            data={
                "kind": kind,
                "path": message_path_for_kind(kind, source_id),
            },
            ttl=PUSH_TTL_SECONDS,
        )
        try:
            guard_token = self._delivery_guard.begin(selector=selector)
        except PushDeliveryClosed:
            with self._users._lock:
                self._users._conn.execute(
                    "UPDATE mobile_push_outbox SET status = 'dead',"
                    " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                    " WHERE id = ? AND status = 'leased' AND lease_owner = ?",
                    (observed, outbox_id, self._owner),
                )
                self._users._conn.commit()
            return True

        try:
            try:
                tickets = list(self._provider.send([message]))
            except Exception:
                logger.warning("Push send failed outbox_id=%s", outbox_id)
                with self._users._lock:
                    self._users._conn.execute(
                        "UPDATE mobile_push_outbox SET status = 'pending',"
                        " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                        " WHERE id = ? AND status = 'leased' AND lease_owner = ?",
                        (observed, outbox_id, self._owner),
                    )
                    self._users._conn.commit()
                return True

            ticket = tickets[0] if tickets else PushTicket(status="error", error="empty")
            accepted_at = observed
            with self._users._lock:
                if self._delivery_guard.is_closed(selector):
                    self._users._conn.execute(
                        "UPDATE mobile_push_outbox SET status = 'dead',"
                        " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                        " WHERE id = ? AND status = 'leased' AND lease_owner = ?",
                        (accepted_at, outbox_id, self._owner),
                    )
                elif ticket.status == "ok" and ticket.ticket_id:
                    self._users._conn.execute(
                        "UPDATE mobile_push_outbox SET status = 'awaiting_receipt',"
                        " provider_ticket_id = ?, receipt_due_at = ?,"
                        " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                        " WHERE id = ? AND status = 'leased' AND lease_owner = ?",
                        (
                            ticket.ticket_id,
                            accepted_at + self._receipt_delay_seconds,
                            accepted_at,
                            outbox_id,
                            self._owner,
                        ),
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
        finally:
            self._delivery_guard.end(guard_token)

def attach_job_push_observer(
    manager: JobManager,
    *,
    outbox: PushOutboxStore,
    settings: MobilePushSettings,
    deployment_environment: str,
) -> None:
    """Register an exception-isolated completion observer on JobManager."""

    def _observer(job: Job) -> None:
        kind = job_push_kind(job)
        if kind is None:
            return
        outbox.enqueue_job_notification(
            job,
            kind=kind,
            environment=deployment_environment,
            expo_project_id=settings.expo_project_id,
        )

    add = getattr(manager, "add_completion_observer", None)
    if callable(add):
        add(_observer)
    else:
        # Fallback for older JobManager shapes during partial upgrades.
        logger.warning("JobManager has no completion observer hook.")
