"""Push environment fences and approval-gated cutover operations.

Admission for registration/enqueue/provider send requires an ``open`` fence.
``ensure_open_fence`` creates revision 1 on first flag-on startup and never
reopens a previously closed or closing fence. Operator close/purge is driven
through :mod:`push_cutover_cli` (``swinglab mobile-push-cutover``).
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from typing import Any, Mapping, Protocol

from .mobile_schema import MobileStateDomain
from .users import UserStore

PUSH_TTL_SECONDS = 900


class PushFenceClosedError(RuntimeError):
    """Registration, enqueue, or reopen was refused because the fence is not open."""


class PushFenceMismatchError(ValueError):
    """Operator target environment/project does not match server configuration."""


class PushCutoverConflictError(ValueError):
    """An existing cutover operation conflicts with the supplied request hash."""


class PushCutoverNotReadyError(RuntimeError):
    """Purge was refused because provider_safe_after has not elapsed."""


class _LedgerPublisher(Protocol):
    def append_and_publish(self, event: Any) -> Any: ...


def _conn_of(users_or_conn: UserStore | sqlite3.Connection) -> sqlite3.Connection:
    if isinstance(users_or_conn, UserStore):
        return users_or_conn._conn
    return users_or_conn


def _lock_of(users_or_conn: UserStore | sqlite3.Connection):
    if isinstance(users_or_conn, UserStore):
        return users_or_conn._lock

    class _NullLock:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    return _NullLock()


def _fence_row(
    conn: sqlite3.Connection, *, environment: str, expo_project_id: str
):
    return conn.execute(
        "SELECT * FROM mobile_push_environment_fences"
        " WHERE environment = ? AND expo_project_id = ?",
        (environment, expo_project_id),
    ).fetchone()


def require_open_fence(
    conn: sqlite3.Connection,
    *,
    environment: str,
    expo_project_id: str,
) -> None:
    """Admit only when an ``open`` fence row exists for the environment/project.

    A missing row is not treated as open for admission callers. First-enable
    startup must call :func:`ensure_open_fence` before serving traffic.
    """

    row = _fence_row(conn, environment=environment, expo_project_id=expo_project_id)
    if row is None or str(row["state"]) != "open":
        raise PushFenceClosedError(
            "The push environment fence is closed or unavailable."
        )


def ensure_open_fence(
    users_or_conn: UserStore | sqlite3.Connection,
    *,
    environment: str,
    expo_project_id: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Create an open revision-1 fence or return the existing open fence.

    A previously closed or closing fence fails closed and is never reopened.
    """

    observed = time.time() if now is None else float(now)
    conn = _conn_of(users_or_conn)
    with _lock_of(users_or_conn):
        try:
            if isinstance(users_or_conn, UserStore):
                conn.execute("BEGIN IMMEDIATE")
            row = _fence_row(
                conn, environment=environment, expo_project_id=expo_project_id
            )
            if row is None:
                conn.execute(
                    "INSERT INTO mobile_push_environment_fences ("
                    " environment, expo_project_id, state,"
                    " activation_revision, cutoff_revision, updated_at"
                    ") VALUES (?, ?, 'open', 1, 1, ?)",
                    (environment, expo_project_id, observed),
                )
                if isinstance(users_or_conn, UserStore):
                    conn.commit()
                return {
                    "state": "open",
                    "activation_revision": 1,
                    "cutoff_revision": 1,
                }
            state = str(row["state"])
            if state != "open":
                raise PushFenceClosedError(
                    "The push environment fence is closed and cannot be reopened."
                )
            if isinstance(users_or_conn, UserStore) and conn.in_transaction:
                conn.commit()
            return {
                "state": "open",
                "activation_revision": int(row["activation_revision"]),
                "cutoff_revision": int(row["cutoff_revision"]),
            }
        except Exception:
            if isinstance(users_or_conn, UserStore) and conn.in_transaction:
                conn.rollback()
            raise


def fence_status(
    users_or_conn: UserStore | sqlite3.Connection,
    *,
    environment: str,
    expo_project_id: str,
) -> dict[str, Any]:
    """Return aggregate-only fence health without tokens or identifiers."""

    conn = _conn_of(users_or_conn)
    with _lock_of(users_or_conn):
        row = _fence_row(
            conn, environment=environment, expo_project_id=expo_project_id
        )
        if row is None:
            return {
                "state": "absent",
                "activation_revision": None,
                "cutoff_revision": None,
                "registration_count": 0,
                "outbox_status_counts": {},
                "lease_count": 0,
                "last_provider_started": False,
                "last_provider_accepted": False,
                "provider_safe_after": None,
            }
        registration_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM mobile_push_registrations"
                " WHERE environment = ? AND expo_project_id = ?",
                (environment, expo_project_id),
            ).fetchone()[0]
        )
        status_rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM mobile_push_outbox"
            " WHERE environment = ? AND expo_project_id = ?"
            " GROUP BY status",
            (environment, expo_project_id),
        ).fetchall()
        outbox_status_counts = {
            str(item["status"]): int(item["n"]) for item in status_rows
        }
        lease_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM mobile_push_outbox"
                " WHERE environment = ? AND expo_project_id = ?"
                " AND status = 'leased'",
                (environment, expo_project_id),
            ).fetchone()[0]
        )
        safe_after = row["provider_safe_after"]
        return {
            "state": str(row["state"]),
            "activation_revision": int(row["activation_revision"]),
            "cutoff_revision": int(row["cutoff_revision"]),
            "registration_count": registration_count,
            "outbox_status_counts": outbox_status_counts,
            "lease_count": lease_count,
            "last_provider_started": row["last_provider_started_at"] is not None,
            "last_provider_accepted": row["last_provider_accepted_at"] is not None,
            "provider_safe_after": (
                float(safe_after) if safe_after is not None else None
            ),
        }


def _compute_provider_safe_after(
    *,
    closed_at: float,
    frozen_skew: float,
    last_provider_started_at: float | None,
    last_provider_accepted_at: float | None,
    provider_may_accept_until: float | None,
) -> float:
    # No provider call ever started: close time is already safe.
    if last_provider_started_at is None:
        return float(closed_at)
    accepted = (
        float(last_provider_accepted_at)
        if last_provider_accepted_at is not None
        else float(last_provider_started_at)
    )
    may_accept = (
        float(provider_may_accept_until)
        if provider_may_accept_until is not None
        else accepted
    )
    return max(accepted, may_accept) + PUSH_TTL_SECONDS + float(frozen_skew)


def _load_operation(
    conn: sqlite3.Connection, *, operation_id: str
):
    return conn.execute(
        "SELECT * FROM mobile_push_cutover_operations WHERE operation_id = ?",
        (operation_id,),
    ).fetchone()


def _status_payload_from_row(row) -> dict[str, Any]:
    return {
        "state": str(row["state"]),
        "activation_revision": int(row["activation_revision"]),
        "cutoff_revision": int(row["cutoff_revision"]),
        "closed_at": (
            float(row["closed_at"]) if row["closed_at"] is not None else None
        ),
        "provider_safe_after": (
            float(row["provider_safe_after"])
            if row["provider_safe_after"] is not None
            else None
        ),
        "frozen_cutoff_skew_seconds": (
            float(row["frozen_cutoff_skew_seconds"])
            if row["frozen_cutoff_skew_seconds"] is not None
            else None
        ),
    }


def close_fence(
    users_or_conn: UserStore | sqlite3.Connection,
    *,
    environment: str,
    expo_project_id: str,
    operation_id: str,
    request_hash: str,
    apply: bool,
    skew_seconds: float,
    now: float | None = None,
    ledger: _LedgerPublisher | None = None,
    keyring=None,
) -> dict[str, Any]:
    """Close the fence, terminalize unsent outbox work, and journal the operation.

    Dry-run (``apply=False``) reports the planned close without writes.
    Exact ``operation_id`` + ``request_hash`` replay is idempotent; a conflicting
    hash fails closed.
    """

    observed = time.time() if now is None else float(now)
    conn = _conn_of(users_or_conn)
    with _lock_of(users_or_conn):
        try:
            if isinstance(users_or_conn, UserStore):
                conn.execute("BEGIN IMMEDIATE")
            existing = _load_operation(conn, operation_id=operation_id)
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise PushCutoverConflictError(
                        "A conflicting cutover request hash already exists."
                    )
                if str(existing["command"]) != "close":
                    raise PushCutoverConflictError(
                        "A conflicting cutover command already exists."
                    )
                row = _fence_row(
                    conn, environment=environment, expo_project_id=expo_project_id
                )
                if row is None:
                    raise PushFenceClosedError(
                        "The push environment fence is missing after close."
                    )
                if isinstance(users_or_conn, UserStore) and conn.in_transaction:
                    conn.commit()
                payload = _status_payload_from_row(row)
                payload["operation_id"] = operation_id
                payload["apply"] = bool(existing["apply"])
                payload["phase"] = str(existing["phase"])
                payload.update(
                    fence_status(
                        users_or_conn,
                        environment=environment,
                        expo_project_id=expo_project_id,
                    )
                )
                return payload

            row = _fence_row(
                conn, environment=environment, expo_project_id=expo_project_id
            )
            if row is None:
                raise PushFenceClosedError(
                    "The push environment fence is absent; close has nothing to close."
                )
            pending_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM mobile_push_outbox"
                    " WHERE environment = ? AND expo_project_id = ?"
                    " AND status IN ('pending', 'leased')",
                    (environment, expo_project_id),
                ).fetchone()[0]
            )
            registration_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM mobile_push_registrations"
                    " WHERE environment = ? AND expo_project_id = ?",
                    (environment, expo_project_id),
                ).fetchone()[0]
            )
            if not apply:
                if isinstance(users_or_conn, UserStore) and conn.in_transaction:
                    conn.commit()
                return {
                    "state": str(row["state"]),
                    "activation_revision": int(row["activation_revision"]),
                    "cutoff_revision": int(row["cutoff_revision"]),
                    "apply": False,
                    "would_terminalize_outbox": pending_count,
                    "registration_count": registration_count,
                    "operation_id": operation_id,
                    "phase": "dry_run",
                }

            if str(row["state"]) == "closed":
                raise PushFenceClosedError(
                    "The push environment fence is already closed."
                )

            frozen_skew = float(skew_seconds)
            closed_at = observed
            safe_after = _compute_provider_safe_after(
                closed_at=closed_at,
                frozen_skew=frozen_skew,
                last_provider_started_at=(
                    float(row["last_provider_started_at"])
                    if row["last_provider_started_at"] is not None
                    else None
                ),
                last_provider_accepted_at=(
                    float(row["last_provider_accepted_at"])
                    if row["last_provider_accepted_at"] is not None
                    else None
                ),
                provider_may_accept_until=(
                    float(row["provider_may_accept_until"])
                    if row["provider_may_accept_until"] is not None
                    else None
                ),
            )
            cutoff_revision = max(
                int(row["cutoff_revision"]), int(row["activation_revision"])
            ) + 1

            conn.execute(
                "UPDATE mobile_push_environment_fences SET state = 'closing',"
                " updated_at = ? WHERE environment = ? AND expo_project_id = ?",
                (observed, environment, expo_project_id),
            )
            conn.execute(
                "UPDATE mobile_push_outbox SET status = 'dead',"
                " lease_owner = NULL, lease_expires_at = NULL, updated_at = ?"
                " WHERE environment = ? AND expo_project_id = ?"
                " AND status IN ('pending', 'leased')",
                (observed, environment, expo_project_id),
            )
            recovery_sequence = None
            recovery_record_hash = None
            # Optional recovery-ledger publish when a ledger (+ keyring) is supplied.
            if ledger is not None and keyring is not None:
                from .recovery_fence_ledger import PushEnvironmentCutoffEvent

                key_id, digest = keyring.digest(
                    MobileStateDomain.PUSH_EXPO_PROJECT, expo_project_id
                )
                published = ledger.append_and_publish(
                    PushEnvironmentCutoffEvent(
                        event_id=str(uuid.uuid4()),
                        cutoff_at=closed_at,
                        deployment_environment=environment,
                        expo_project_hmac_key_id=key_id,
                        expo_project_hmac=digest,
                        activation_revision=int(row["activation_revision"]),
                        cutoff_revision=cutoff_revision,
                        last_provider_started_at=(
                            float(row["last_provider_started_at"])
                            if row["last_provider_started_at"] is not None
                            else None
                        ),
                        last_provider_accepted_at=(
                            float(row["last_provider_accepted_at"])
                            if row["last_provider_accepted_at"] is not None
                            else None
                        ),
                        provider_may_accept_until=(
                            float(row["provider_may_accept_until"])
                            if row["provider_may_accept_until"] is not None
                            else None
                        ),
                        closed_at=closed_at,
                        cutoff_skew_seconds=frozen_skew,
                        provider_safe_after=safe_after,
                        state="closed",
                    )
                )
                recovery_sequence = int(published.sequence)
                recovery_record_hash = str(published.record_hash)
            else:
                # Local fence close completes without off-volume publish when no
                # ledger is wired; operators may attach recovery publish later.
                pass

            conn.execute(
                "UPDATE mobile_push_environment_fences SET state = 'closed',"
                " cutoff_revision = ?, closed_at = ?,"
                " frozen_cutoff_skew_seconds = ?, provider_safe_after = ?,"
                " recovery_record_hash = ?, recovery_sequence = ?,"
                " updated_at = ?"
                " WHERE environment = ? AND expo_project_id = ?",
                (
                    cutoff_revision,
                    closed_at,
                    frozen_skew,
                    safe_after,
                    recovery_record_hash,
                    recovery_sequence,
                    observed,
                    environment,
                    expo_project_id,
                ),
            )
            conn.execute(
                "INSERT INTO mobile_push_cutover_operations ("
                " operation_id, environment, expo_project_id, command,"
                " request_hash, phase, apply, created_at, updated_at"
                ") VALUES (?, ?, ?, 'close', ?, 'closed', 1, ?, ?)",
                (
                    operation_id,
                    environment,
                    expo_project_id,
                    request_hash,
                    observed,
                    observed,
                ),
            )
            if isinstance(users_or_conn, UserStore):
                conn.commit()
            status = fence_status(
                users_or_conn,
                environment=environment,
                expo_project_id=expo_project_id,
            )
            status["operation_id"] = operation_id
            status["apply"] = True
            status["phase"] = "closed"
            status["closed_at"] = closed_at
            status["provider_safe_after"] = safe_after
            status["frozen_cutoff_skew_seconds"] = frozen_skew
            return status
        except Exception:
            if isinstance(users_or_conn, UserStore) and conn.in_transaction:
                conn.rollback()
            raise


def purge_fence(
    users_or_conn: UserStore | sqlite3.Connection,
    *,
    environment: str,
    expo_project_id: str,
    operation_id: str,
    request_hash: str,
    apply: bool,
    now: float | None = None,
    ledger: _LedgerPublisher | None = None,
    keyring=None,
) -> dict[str, Any]:
    """Delete registrations/outbox for a closed fence after provider_safe_after.

    Never reopens the fence. Dry-run reports counts without deleting.
    """

    del ledger, keyring  # purge publish of a later cutoff revision is deferred
    observed = time.time() if now is None else float(now)
    conn = _conn_of(users_or_conn)
    with _lock_of(users_or_conn):
        try:
            if isinstance(users_or_conn, UserStore):
                conn.execute("BEGIN IMMEDIATE")
            existing = _load_operation(conn, operation_id=operation_id)
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise PushCutoverConflictError(
                        "A conflicting cutover request hash already exists."
                    )
                if str(existing["command"]) != "purge":
                    raise PushCutoverConflictError(
                        "A conflicting cutover command already exists."
                    )
                row = _fence_row(
                    conn, environment=environment, expo_project_id=expo_project_id
                )
                if isinstance(users_or_conn, UserStore) and conn.in_transaction:
                    conn.commit()
                status = fence_status(
                    users_or_conn,
                    environment=environment,
                    expo_project_id=expo_project_id,
                )
                status["operation_id"] = operation_id
                status["apply"] = bool(existing["apply"])
                status["phase"] = str(existing["phase"])
                if row is not None:
                    status.update(_status_payload_from_row(row))
                return status

            row = _fence_row(
                conn, environment=environment, expo_project_id=expo_project_id
            )
            if row is None or str(row["state"]) != "closed":
                raise PushFenceClosedError(
                    "Purge requires a closed push environment fence."
                )
            safe_after = row["provider_safe_after"]
            frozen = row["frozen_cutoff_skew_seconds"]
            closed_at = row["closed_at"]
            if safe_after is None:
                if closed_at is None:
                    raise PushCutoverNotReadyError(
                        "Purge refuses until provider_safe_after is known."
                    )
                safe_after = _compute_provider_safe_after(
                    closed_at=float(closed_at),
                    frozen_skew=float(frozen or 0.0),
                    last_provider_started_at=(
                        float(row["last_provider_started_at"])
                        if row["last_provider_started_at"] is not None
                        else None
                    ),
                    last_provider_accepted_at=(
                        float(row["last_provider_accepted_at"])
                        if row["last_provider_accepted_at"] is not None
                        else None
                    ),
                    provider_may_accept_until=(
                        float(row["provider_may_accept_until"])
                        if row["provider_may_accept_until"] is not None
                        else None
                    ),
                )
                conn.execute(
                    "UPDATE mobile_push_environment_fences"
                    " SET provider_safe_after = ?, updated_at = ?"
                    " WHERE environment = ? AND expo_project_id = ?",
                    (safe_after, observed, environment, expo_project_id),
                )
            else:
                safe_after = float(safe_after)

            registration_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM mobile_push_registrations"
                    " WHERE environment = ? AND expo_project_id = ?",
                    (environment, expo_project_id),
                ).fetchone()[0]
            )
            outbox_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM mobile_push_outbox"
                    " WHERE environment = ? AND expo_project_id = ?",
                    (environment, expo_project_id),
                ).fetchone()[0]
            )

            if observed < safe_after:
                raise PushCutoverNotReadyError(
                    "Purge refuses until provider_safe_after has elapsed."
                )

            if not apply:
                if isinstance(users_or_conn, UserStore) and conn.in_transaction:
                    conn.commit()
                return {
                    "state": "closed",
                    "apply": False,
                    "phase": "dry_run",
                    "operation_id": operation_id,
                    "provider_safe_after": safe_after,
                    "would_delete_registrations": registration_count,
                    "would_delete_outbox": outbox_count,
                }

            conn.execute(
                "DELETE FROM mobile_push_registrations"
                " WHERE environment = ? AND expo_project_id = ?",
                (environment, expo_project_id),
            )
            conn.execute(
                "DELETE FROM mobile_push_outbox"
                " WHERE environment = ? AND expo_project_id = ?",
                (environment, expo_project_id),
            )
            conn.execute(
                "UPDATE mobile_push_environment_fences SET state = 'closed',"
                " updated_at = ? WHERE environment = ? AND expo_project_id = ?",
                (observed, environment, expo_project_id),
            )
            conn.execute(
                "INSERT INTO mobile_push_cutover_operations ("
                " operation_id, environment, expo_project_id, command,"
                " request_hash, phase, apply, created_at, updated_at"
                ") VALUES (?, ?, ?, 'purge', ?, 'purged', 1, ?, ?)",
                (
                    operation_id,
                    environment,
                    expo_project_id,
                    request_hash,
                    observed,
                    observed,
                ),
            )
            if isinstance(users_or_conn, UserStore):
                conn.commit()
            status = fence_status(
                users_or_conn,
                environment=environment,
                expo_project_id=expo_project_id,
            )
            status["operation_id"] = operation_id
            status["apply"] = True
            status["phase"] = "purged"
            status["provider_safe_after"] = safe_after
            return status
        except Exception:
            if isinstance(users_or_conn, UserStore) and conn.in_transaction:
                conn.rollback()
            raise


def cutover_request_hash(
    *,
    environment: str,
    expo_project_id: str,
    command: str,
    operation_id: str,
) -> str:
    """Stable SHA-256 request hash for close/purge idempotency."""

    import hashlib
    import json

    material = json.dumps(
        {
            "environment": environment,
            "expo_project_id": expo_project_id,
            "command": command,
            "operation_id": operation_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
