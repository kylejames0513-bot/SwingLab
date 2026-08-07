"""Selector admission leases and crash-safe current-device sign-out.

Lock order in this module is deliberately fixed: selector admission first,
then the UserStore lock/SQLite transaction.  Recovery publication and lease
draining happen only after both locks have been released.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Protocol

from ..api.auth import MobileAuthContext
from .mobile_schema import MobileStateDomain, VersionedHMAC
from .recovery_fence_ledger import (
    RecoveryFenceError,
    TokenRevokeEvent,
)
from .users import UserStore


_IDEMPOTENCY_KEY = re.compile(r"[0-9A-Fa-f]{32}")
_SIGN_OUT_REPLAY_SECONDS = 7 * 86400
logger = logging.getLogger("swinglab.mobile_sign_out")


class CredentialMutationRejected(RuntimeError):
    """The captured bearer may no longer authorize a final commit."""


class MobileSignOutUnauthorized(RuntimeError):
    """A sign-out credential or replay tuple is not valid."""


class MobileSignOutInvalidRequest(ValueError):
    """The caller did not supply one canonical 128-bit key."""


class MobileSignOutUnavailable(RuntimeError):
    """Protected sign-out state cannot be created safely."""


class RecoveryFencePublisher(Protocol):
    def append_and_publish(self, event: TokenRevokeEvent): ...


class SignOutExtension(Protocol):
    """One idempotent selector cleanup supplied by a later task owner."""

    def close_for_sign_out(
        self,
        *,
        users: UserStore,
        operation_id: str,
        user_id: str,
        selector: str,
    ) -> bool: ...


@dataclass
class _SelectorAdmission:
    closed: bool
    leases: set["CredentialMutationLease"]


class CredentialMutationLease:
    """One admitted unsafe mutation bound to a selector and auth epoch."""

    def __init__(
        self,
        guard: "CredentialMutationGuard",
        *,
        user_id: str,
        selector: str,
        auth_epoch: int,
    ) -> None:
        self._guard = guard
        self.user_id = user_id
        self.selector = selector
        self.auth_epoch = int(auth_epoch)
        self._cancel = threading.Event()
        self._released = False

    @property
    def cancellation_requested(self) -> bool:
        return self._cancel.is_set()

    @property
    def cancellation_event(self) -> threading.Event:
        return self._cancel

    @property
    def released(self) -> bool:
        return self._released

    def validate_locked(
        self,
        user_store: UserStore,
        *,
        now: float | None = None,
    ) -> None:
        """Recheck selector and epoch inside the caller's final transaction.

        The caller must already hold ``UserStore._lock`` and an SQLite
        transaction.  Keeping this method lock-free lets a larger mutation
        validate immediately before its own final writes without reversing the
        global admission -> UserStore ordering.
        """

        if self._released:
            raise CredentialMutationRejected("The credential lease is closed.")
        observed_at = time.time() if now is None else float(now)
        row = user_store._conn.execute(
            "SELECT token.user_id, token.auth_epoch, token.expires_at,"
            " token.revoked_at, token.state, token.fenced_at,"
            " COALESCE(owner.auth_epoch, 0) AS owner_auth_epoch"
            " FROM mobile_api_tokens AS token"
            " JOIN users AS owner ON owner.id = token.user_id"
            " WHERE token.selector = ?",
            (self.selector,),
        ).fetchone()
        if (
            row is None
            or str(row["user_id"]) != self.user_id
            or int(row["auth_epoch"]) != self.auth_epoch
            or int(row["owner_auth_epoch"]) != self.auth_epoch
            or float(row["expires_at"]) <= observed_at
            or row["revoked_at"] is not None
            or str(row["state"]) != "active"
            or row["fenced_at"] is not None
        ):
            raise CredentialMutationRejected(
                "The authenticated mobile credential changed."
            )

    def release(self) -> None:
        self._guard._release(self)

    close = release

    def __enter__(self) -> "CredentialMutationLease":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()


class CredentialMutationClose:
    """A closed selector whose remaining cooperative work can be drained."""

    def __init__(self, guard: "CredentialMutationGuard", selector: str) -> None:
        self._guard = guard
        self.selector = selector

    def drain(self, *, timeout_seconds: float) -> bool:
        timeout = max(0.0, float(timeout_seconds))
        with self._guard._condition:
            state = self._guard._states.get(self.selector)
            if state is None or not state.leases:
                return True
            return self._guard._condition.wait_for(
                lambda: not self._guard._states[self.selector].leases,
                timeout=timeout,
            )


class CredentialMutationGuard:
    """In-process selector admission with cooperative cancellation."""

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.Lock())
        self._states: dict[str, _SelectorAdmission] = {}

    def admit(self, auth_context: MobileAuthContext) -> CredentialMutationLease:
        if (
            not auth_context.via_bearer
            or not auth_context.selector
            or auth_context.auth_epoch != auth_context.user.auth_epoch
        ):
            raise CredentialMutationRejected(
                "An active bearer credential is required."
            )
        with self._condition:
            state = self._states.setdefault(
                auth_context.selector,
                _SelectorAdmission(closed=False, leases=set()),
            )
            if state.closed:
                raise CredentialMutationRejected(
                    "The mobile credential is closed."
                )
            lease = CredentialMutationLease(
                self,
                user_id=auth_context.user.id,
                selector=auth_context.selector,
                auth_epoch=auth_context.auth_epoch,
            )
            state.leases.add(lease)
            return lease

    def validate_and_close_caller(
        self,
        caller: CredentialMutationLease,
        user_store: UserStore,
        *,
        now: float | None = None,
        prepare_locked: Callable[[sqlite3.Connection], None] | None = None,
    ) -> CredentialMutationClose:
        """Atomically validate, persist a local fence, and convert the caller.

        The caller lease is removed before the returned handle can wait, so a
        self-sign-out can never drain itself.  ``prepare_locked`` allows the
        durable operation journal to share the same transaction as the token
        fence.
        """

        fenced_at = time.time() if now is None else float(now)
        with self._condition:
            state = self._states.get(caller.selector)
            if (
                caller._guard is not self
                or caller._released
                or state is None
                or caller not in state.leases
                or state.closed
            ):
                raise CredentialMutationRejected(
                    "The credential close transition is no longer valid."
                )
            with user_store._lock:
                try:
                    user_store._conn.execute("BEGIN IMMEDIATE")
                    caller.validate_locked(user_store, now=fenced_at)
                    if prepare_locked is not None:
                        prepare_locked(user_store._conn)
                    cursor = user_store._conn.execute(
                        "UPDATE mobile_api_tokens"
                        " SET state = 'fenced',"
                        " fenced_at = COALESCE(fenced_at, ?)"
                        " WHERE selector = ? AND user_id = ?"
                        " AND auth_epoch = ? AND revoked_at IS NULL"
                        " AND expires_at > ? AND state = 'active'"
                        " AND fenced_at IS NULL"
                        " AND EXISTS (SELECT 1 FROM users AS owner"
                        " WHERE owner.id = mobile_api_tokens.user_id"
                        " AND COALESCE(owner.auth_epoch, 0) = ?)",
                        (
                            fenced_at,
                            caller.selector,
                            caller.user_id,
                            caller.auth_epoch,
                            fenced_at,
                            caller.auth_epoch,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise CredentialMutationRejected(
                            "The mobile credential changed during close."
                        )
                    user_store._conn.commit()
                except Exception:
                    if user_store._conn.in_transaction:
                        user_store._conn.rollback()
                    raise

            state.closed = True
            state.leases.remove(caller)
            caller._released = True
            for lease in state.leases:
                lease._cancel.set()
            self._condition.notify_all()
        return CredentialMutationClose(self, caller.selector)

    def close_selector(self, selector: str) -> CredentialMutationClose:
        """Restore or repeat a previously persisted selector fence."""

        with self._condition:
            state = self._states.setdefault(
                selector,
                _SelectorAdmission(closed=True, leases=set()),
            )
            state.closed = True
            for lease in state.leases:
                lease._cancel.set()
            self._condition.notify_all()
        return CredentialMutationClose(self, selector)

    def _release(self, lease: CredentialMutationLease) -> None:
        with self._condition:
            if lease._released:
                return
            state = self._states.get(lease.selector)
            if state is not None:
                state.leases.discard(lease)
            lease._released = True
            self._condition.notify_all()


@dataclass(frozen=True)
class MobileSignOutResult:
    pending: bool


@dataclass(frozen=True)
class _SignOutMaterial:
    selector_hmac_key_id: str
    selector_hmac: str
    token_verifier_hmac_key_id: str
    token_verifier_hmac: str
    idempotency_hmac_key_id: str
    idempotency_hmac: str
    request_hash: str


class MobileSignOutService:
    """Drive one current-selector sign-out journal to a terminal receipt."""

    def __init__(
        self,
        users: UserStore,
        guard: CredentialMutationGuard,
        *,
        keyring: VersionedHMAC | None,
        recovery_fence_ledger: RecoveryFencePublisher | None,
        drain_timeout_seconds: float = 0.25,
        extensions: tuple[SignOutExtension, ...] = (),
    ) -> None:
        self._users = users
        self._guard = guard
        self._keyring = keyring
        self._ledger = recovery_fence_ledger
        self._drain_timeout_seconds = max(0.0, float(drain_timeout_seconds))
        self._extensions = tuple(extensions)

    @staticmethod
    def _idempotency_bytes(value: object) -> bytes:
        if not isinstance(value, str) or _IDEMPOTENCY_KEY.fullmatch(value) is None:
            raise MobileSignOutInvalidRequest("Invalid Idempotency-Key.")
        return bytes.fromhex(value)

    @staticmethod
    def _request_hash(
        *,
        selector_hmac_key_id: str,
        selector_hmac: str,
        token_verifier_hmac_key_id: str,
        token_verifier_hmac: str,
        idempotency_hmac_key_id: str,
        idempotency_hmac: str,
    ) -> str:
        body = json.dumps(
            {
                "operation": "mobile-sign-out-v1",
                "selector_hmac_key_id": selector_hmac_key_id,
                "selector_hmac": selector_hmac,
                "token_verifier_hmac_key_id": token_verifier_hmac_key_id,
                "token_verifier_hmac": token_verifier_hmac,
                "idempotency_hmac_key_id": idempotency_hmac_key_id,
                "idempotency_hmac": idempotency_hmac,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        return hashlib.sha256(body).hexdigest()

    def _new_material(
        self,
        selector: str,
        token_hash: str,
        idempotency: bytes,
    ) -> _SignOutMaterial:
        if self._keyring is None:
            raise MobileSignOutUnavailable(
                "Protected mobile sign-out state is not configured."
            )
        selector_key, selector_hmac = self._keyring.digest(
            MobileStateDomain.RECOVERY_SELECTOR, selector
        )
        verifier_key, verifier_hmac = self._keyring.digest(
            MobileStateDomain.RECOVERY_TOKEN_VERIFIER, token_hash
        )
        idempotency_key, idempotency_hmac = self._keyring.digest(
            MobileStateDomain.SIGN_OUT_IDEMPOTENCY, idempotency
        )
        request_hash = self._request_hash(
            selector_hmac_key_id=selector_key,
            selector_hmac=selector_hmac,
            token_verifier_hmac_key_id=verifier_key,
            token_verifier_hmac=verifier_hmac,
            idempotency_hmac_key_id=idempotency_key,
            idempotency_hmac=idempotency_hmac,
        )
        return _SignOutMaterial(
            selector_key,
            selector_hmac,
            verifier_key,
            verifier_hmac,
            idempotency_key,
            idempotency_hmac,
            request_hash,
        )

    def _row_matches_replay(
        self,
        row: sqlite3.Row,
        *,
        selector: str,
        token_hash: str,
        idempotency: bytes,
    ) -> tuple[bool, bool]:
        """Return ``(idempotency_match, exact_match)`` using retained keys."""

        if self._keyring is None:
            return False, False
        try:
            idempotency_digest = self._keyring.digest_with_key(
                str(row["idempotency_hmac_key_id"]),
                MobileStateDomain.SIGN_OUT_IDEMPOTENCY,
                idempotency,
            )
            selector_digest = self._keyring.digest_with_key(
                str(row["selector_hmac_key_id"]),
                MobileStateDomain.RECOVERY_SELECTOR,
                selector,
            )
            verifier_digest = self._keyring.digest_with_key(
                str(row["token_verifier_hmac_key_id"]),
                MobileStateDomain.RECOVERY_TOKEN_VERIFIER,
                token_hash,
            )
        except KeyError as exc:
            raise RuntimeError(str(exc)) from exc
        idempotency_match = hmac.compare_digest(
            idempotency_digest, str(row["idempotency_hmac"])
        )
        if not idempotency_match:
            return False, False
        expected_request_hash = self._request_hash(
            selector_hmac_key_id=str(row["selector_hmac_key_id"]),
            selector_hmac=str(row["selector_hmac"]),
            token_verifier_hmac_key_id=str(
                row["token_verifier_hmac_key_id"]
            ),
            token_verifier_hmac=str(row["token_verifier_hmac"]),
            idempotency_hmac_key_id=str(row["idempotency_hmac_key_id"]),
            idempotency_hmac=str(row["idempotency_hmac"]),
        )
        exact = (
            hmac.compare_digest(selector_digest, str(row["selector_hmac"]))
            and hmac.compare_digest(
                verifier_digest, str(row["token_verifier_hmac"])
            )
            and hmac.compare_digest(
                expected_request_hash, str(row["request_hash"])
            )
        )
        return True, exact

    def _find_replay(
        self,
        *,
        selector: str,
        token_hash: str,
        idempotency: bytes,
        now: float,
    ) -> tuple[str, sqlite3.Row] | tuple[str, None] | None:
        if self._keyring is None:
            return None
        candidates = self._keyring.candidates(
            MobileStateDomain.SIGN_OUT_IDEMPOTENCY,
            idempotency,
        )
        predicate = " OR ".join(
            "(idempotency_hmac_key_id = ? AND idempotency_hmac = ?)"
            for _candidate in candidates
        )
        candidate_parameters = tuple(
            value
            for candidate in candidates
            for value in (candidate.key_id, candidate.digest)
        )
        with self._users._lock:
            receipts = self._users._conn.execute(
                "SELECT * FROM mobile_signout_receipts"
                f" WHERE expires_at > ? AND ({predicate})"
                " ORDER BY completed_at DESC",
                (now, *candidate_parameters),
            ).fetchall()
            journals = self._users._conn.execute(
                "SELECT * FROM mobile_signout_journals"
                f" WHERE expires_at > ? AND ({predicate})"
                " ORDER BY created_at DESC",
                (now, *candidate_parameters),
            ).fetchall()
        for kind, rows in (("receipt", receipts), ("journal", journals)):
            for row in rows:
                idempotency_match, exact = self._row_matches_replay(
                    row,
                    selector=selector,
                    token_hash=token_hash,
                    idempotency=idempotency,
                )
                if exact:
                    return kind, row
                if idempotency_match:
                    return "conflict", None
        return None

    def sign_out(
        self,
        raw_token: object,
        idempotency_key: object,
        *,
        now: float | None = None,
    ) -> MobileSignOutResult:
        observed_at = time.time() if now is None else float(now)
        idempotency = self._idempotency_bytes(idempotency_key)
        parsed = self._users._parse_mobile_api_token(raw_token)
        if parsed is None:
            raise MobileSignOutUnauthorized(
                "Invalid mobile access token."
            )
        selector, secret = parsed
        token_hash = self._users._mobile_api_token_hash(selector, secret)

        replay = self._find_replay(
            selector=selector,
            token_hash=token_hash,
            idempotency=idempotency,
            now=observed_at,
        )
        if replay is not None:
            kind, row = replay
            if kind == "conflict":
                raise MobileSignOutUnauthorized(
                    "Invalid mobile access token."
                )
            if kind == "receipt":
                return MobileSignOutResult(pending=False)
            assert row is not None
            return self._advance(
                str(row["operation_id"]),
                selector=selector,
                close=None,
            )

        principal = self._users.authenticate_mobile_api_principal(
            raw_token, now=observed_at
        )
        if principal is None or principal.selector != selector:
            raise MobileSignOutUnauthorized(
                "Invalid mobile access token."
            )
        if self._ledger is None:
            # An absent publisher is configuration drift, not a transient
            # readback outage.  Do not create a fence that this process has no
            # path to finish; an injected/configured publisher reports real
            # outages as durable 202 after prepared is committed.
            raise MobileSignOutUnavailable(
                "Protected mobile sign-out is not configured."
            )
        material = self._new_material(selector, token_hash, idempotency)
        operation_id = uuid.uuid4().hex
        expires_at = observed_at + _SIGN_OUT_REPLAY_SECONDS
        context = MobileAuthContext(
            user=principal.user,
            via_bearer=True,
            selector=principal.selector,
            auth_epoch=principal.auth_epoch,
        )
        lease: CredentialMutationLease | None = None

        def prepare_locked(connection: sqlite3.Connection) -> None:
            for table in (
                "mobile_signout_journals",
                "mobile_signout_receipts",
            ):
                if connection.execute(
                    f"SELECT 1 FROM {table}"
                    " WHERE idempotency_hmac_key_id = ?"
                    " AND idempotency_hmac = ? LIMIT 1",
                    (
                        material.idempotency_hmac_key_id,
                        material.idempotency_hmac,
                    ),
                ).fetchone() is not None:
                    raise MobileSignOutUnauthorized(
                        "Invalid mobile access token."
                    )
            connection.execute(
                "INSERT INTO mobile_signout_journals"
                " (operation_id, user_id, phase, selector_hmac_key_id,"
                " selector_hmac, token_verifier_hmac_key_id,"
                " token_verifier_hmac, idempotency_hmac_key_id,"
                " idempotency_hmac, request_hash, created_at, updated_at,"
                " expires_at) VALUES (?, ?, 'prepared', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    operation_id,
                    principal.user.id,
                    material.selector_hmac_key_id,
                    material.selector_hmac,
                    material.token_verifier_hmac_key_id,
                    material.token_verifier_hmac,
                    material.idempotency_hmac_key_id,
                    material.idempotency_hmac,
                    material.request_hash,
                    observed_at,
                    observed_at,
                    expires_at,
                ),
            )

        try:
            lease = self._guard.admit(context)
            close = self._guard.validate_and_close_caller(
                lease,
                self._users,
                now=observed_at,
                prepare_locked=prepare_locked,
            )
        except CredentialMutationRejected:
            if lease is not None:
                lease.release()
            # Another close may have won after this request authenticated but
            # before it acquired the admission transition.  Re-read the
            # durable replay tuple instead of leaking a race as a 500.
            raced_replay = self._find_replay(
                selector=selector,
                token_hash=token_hash,
                idempotency=idempotency,
                now=time.time(),
            )
            if raced_replay is not None:
                kind, raced_row = raced_replay
                if kind == "receipt":
                    return MobileSignOutResult(pending=False)
                if kind == "journal" and raced_row is not None:
                    return self._advance(
                        str(raced_row["operation_id"]),
                        selector=selector,
                        close=None,
                    )
            raise MobileSignOutUnauthorized(
                "Invalid mobile access token."
            ) from None
        except Exception:
            if lease is not None:
                lease.release()
            raise
        return self._advance(
            operation_id,
            selector=selector,
            close=close,
        )

    def _operation(self, operation_id: str) -> sqlite3.Row:
        with self._users._lock:
            row = self._users._conn.execute(
                "SELECT * FROM mobile_signout_journals"
                " WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("A sign-out journal disappeared.")
        return row

    def _transition(
        self,
        operation_id: str,
        expected: str,
        next_phase: str,
        *,
        recovery_sequence: int | None = None,
        recovery_record_hash: str | None = None,
    ) -> None:
        updated_at = time.time()
        assignments = "phase = ?, updated_at = ?"
        parameters: list[object] = [next_phase, updated_at]
        if recovery_sequence is not None:
            assignments += ", recovery_sequence = ?, recovery_record_hash = ?"
            parameters.extend((recovery_sequence, recovery_record_hash))
        parameters.extend((operation_id, expected))
        with self._users._lock:
            try:
                self._users._conn.execute("BEGIN IMMEDIATE")
                self._users._conn.execute(
                    f"UPDATE mobile_signout_journals SET {assignments}"
                    " WHERE operation_id = ? AND phase = ?",
                    tuple(parameters),
                )
                self._users._conn.commit()
            except Exception:
                if self._users._conn.in_transaction:
                    self._users._conn.rollback()
                raise

    def _revoke_and_transition(
        self,
        operation_id: str,
        row: sqlite3.Row,
        selector: str,
    ) -> None:
        revoked_at = time.time()
        with self._users._lock:
            try:
                self._users._conn.execute("BEGIN IMMEDIATE")
                current = self._users._conn.execute(
                    "SELECT phase FROM mobile_signout_journals"
                    " WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if current is None:
                    raise RuntimeError("A sign-out journal disappeared.")
                if str(current["phase"]) != "extensions_closed":
                    self._users._conn.commit()
                    return
                token = self._users._conn.execute(
                    "SELECT token_hash FROM mobile_api_tokens"
                    " WHERE selector = ? AND user_id = ?",
                    (selector, row["user_id"]),
                ).fetchone()
                if token is None:
                    raise RuntimeError("A fenced sign-out token disappeared.")
                self._users._conn.execute(
                    "UPDATE mobile_api_tokens"
                    " SET revoked_at = COALESCE(revoked_at, ?),"
                    " state = 'fenced', fenced_at = COALESCE(fenced_at, ?)"
                    " WHERE selector = ? AND user_id = ?",
                    (revoked_at, revoked_at, selector, row["user_id"]),
                )
                self._users._conn.execute(
                    "UPDATE mobile_signout_journals"
                    " SET phase = 'token_revoked', updated_at = ?"
                    " WHERE operation_id = ? AND phase = 'extensions_closed'",
                    (revoked_at, operation_id),
                )
                self._users._conn.commit()
            except Exception:
                if self._users._conn.in_transaction:
                    self._users._conn.rollback()
                raise

    def _complete(self, operation_id: str, row: sqlite3.Row) -> None:
        completed_at = time.time()
        with self._users._lock:
            try:
                self._users._conn.execute("BEGIN IMMEDIATE")
                current = self._users._conn.execute(
                    "SELECT * FROM mobile_signout_journals"
                    " WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if current is None:
                    raise RuntimeError("A sign-out journal disappeared.")
                if str(current["phase"]) == "complete":
                    self._users._conn.commit()
                    return
                if str(current["phase"]) != "token_revoked":
                    self._users._conn.commit()
                    return
                self._users._conn.execute(
                    "INSERT OR IGNORE INTO mobile_signout_receipts"
                    " (operation_id, selector_hmac_key_id, selector_hmac,"
                    " token_verifier_hmac_key_id, token_verifier_hmac,"
                    " idempotency_hmac_key_id, idempotency_hmac, request_hash,"
                    " completed_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        operation_id,
                        current["selector_hmac_key_id"],
                        current["selector_hmac"],
                        current["token_verifier_hmac_key_id"],
                        current["token_verifier_hmac"],
                        current["idempotency_hmac_key_id"],
                        current["idempotency_hmac"],
                        current["request_hash"],
                        completed_at,
                        current["expires_at"],
                    ),
                )
                self._users._conn.execute(
                    "UPDATE mobile_signout_journals"
                    " SET phase = 'complete', updated_at = ?"
                    " WHERE operation_id = ? AND phase = 'token_revoked'",
                    (completed_at, operation_id),
                )
                self._users._conn.commit()
            except Exception:
                if self._users._conn.in_transaction:
                    self._users._conn.rollback()
                raise

    def _advance(
        self,
        operation_id: str,
        *,
        selector: str,
        close: CredentialMutationClose | None,
    ) -> MobileSignOutResult:
        while True:
            row = self._operation(operation_id)
            phase = str(row["phase"])
            if phase == "complete":
                return MobileSignOutResult(pending=False)
            if phase == "prepared":
                if self._ledger is None:
                    return MobileSignOutResult(pending=True)
                event = TokenRevokeEvent(
                    event_id=operation_id,
                    cutoff_at=float(row["created_at"]),
                    selector_hmac_key_id=str(row["selector_hmac_key_id"]),
                    selector_hmac=str(row["selector_hmac"]),
                    token_verifier_hmac_key_id=str(
                        row["token_verifier_hmac_key_id"]
                    ),
                    token_verifier_hmac=str(row["token_verifier_hmac"]),
                )
                try:
                    published = self._ledger.append_and_publish(event)
                except RecoveryFenceError:
                    return MobileSignOutResult(pending=True)
                sequence = int(published.sequence)
                record_hash = str(published.record_hash)
                if (
                    sequence < 1
                    or re.fullmatch(r"[0-9a-f]{64}", record_hash) is None
                ):
                    raise RuntimeError(
                        "Recovery-fence publication returned invalid readback."
                    )
                self._transition(
                    operation_id,
                    "prepared",
                    "recovery_fenced",
                    recovery_sequence=sequence,
                    recovery_record_hash=record_hash,
                )
                continue
            if phase == "recovery_fenced":
                if close is None:
                    close = self._guard.close_selector(selector)
                if not close.drain(
                    timeout_seconds=self._drain_timeout_seconds
                ):
                    return MobileSignOutResult(pending=True)
                for extension in self._extensions:
                    try:
                        closed = extension.close_for_sign_out(
                            users=self._users,
                            operation_id=operation_id,
                            user_id=str(row["user_id"]),
                            selector=selector,
                        )
                    except Exception:
                        # Hooks are idempotent and replayable.  Keep the
                        # journal durable/pending without exposing provider or
                        # local cleanup details to the bearer.
                        logger.warning(
                            "Mobile sign-out extension remains pending "
                            "operation_id=%s extension=%s",
                            operation_id,
                            type(extension).__name__,
                        )
                        return MobileSignOutResult(pending=True)
                    if closed is not True:
                        return MobileSignOutResult(pending=True)
                self._transition(
                    operation_id,
                    "recovery_fenced",
                    "extensions_closed",
                )
                continue
            if phase == "extensions_closed":
                self._revoke_and_transition(operation_id, row, selector)
                continue
            if phase == "token_revoked":
                self._complete(operation_id, row)
                continue
            raise RuntimeError("A sign-out journal phase is invalid.")

    def _selector_for_row(self, row: sqlite3.Row) -> str:
        if self._keyring is None:
            raise RuntimeError(
                "MOBILE_STATE_HMAC_KEYRING is required for pending sign-out."
            )
        selector_key = str(row["selector_hmac_key_id"])
        verifier_key = str(row["token_verifier_hmac_key_id"])
        with self._users._lock:
            tokens = self._users._conn.execute(
                "SELECT selector, token_hash FROM mobile_api_tokens"
                " WHERE user_id = ?",
                (row["user_id"],),
            ).fetchall()
        for token in tokens:
            selector = str(token["selector"])
            token_hash = str(token["token_hash"])
            try:
                selector_digest = self._keyring.digest_with_key(
                    selector_key,
                    MobileStateDomain.RECOVERY_SELECTOR,
                    selector,
                )
                verifier_digest = self._keyring.digest_with_key(
                    verifier_key,
                    MobileStateDomain.RECOVERY_TOKEN_VERIFIER,
                    token_hash,
                )
            except KeyError as exc:
                raise RuntimeError(str(exc)) from exc
            if hmac.compare_digest(
                selector_digest, str(row["selector_hmac"])
            ) and hmac.compare_digest(
                verifier_digest, str(row["token_verifier_hmac"])
            ):
                return selector
        raise RuntimeError("A pending sign-out credential cannot be resolved.")

    def resume_nonterminal(self) -> None:
        """Finish every crash journal before workers or requests can start."""

        with self._users._lock:
            rows = self._users._conn.execute(
                "SELECT * FROM mobile_signout_journals"
                " WHERE phase != 'complete' ORDER BY created_at, operation_id"
            ).fetchall()
        for row in rows:
            selector = self._selector_for_row(row)
            token_row = None
            with self._users._lock:
                token_row = self._users._conn.execute(
                    "SELECT state, fenced_at FROM mobile_api_tokens"
                    " WHERE selector = ?",
                    (selector,),
                ).fetchone()
            if (
                token_row is None
                or str(token_row["state"]) != "fenced"
                or token_row["fenced_at"] is None
            ):
                raise RuntimeError(
                    "A pending sign-out is missing its local credential fence."
                )
            close = self._guard.close_selector(selector)
            result = self._advance(
                str(row["operation_id"]),
                selector=selector,
                close=close,
            )
            if result.pending:
                raise RuntimeError(
                    "A pending sign-out could not be recovered before startup."
                )
