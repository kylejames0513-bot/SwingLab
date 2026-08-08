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
import math
import re
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator, Protocol

from ..api.auth import MobileAuthContext
from .mobile_schema import MobileStateDomain, VersionedHMAC
from .recovery_fence_ledger import (
    RecoveryFenceError,
    TokenRevokeEvent,
)
from .users import UserStore


_IDEMPOTENCY_KEY = re.compile(r"[0-9A-Fa-f]{32}")
_SIGN_OUT_EXTENSION_ID = re.compile(r"[a-z][a-z0-9.-]{0,63}")
_SIGN_OUT_EXTENSION_CONTRACT_VERSION = 1
_SIGN_OUT_REPLAY_SECONDS = 7 * 86400
_DEVICE_REVOKE_REPLAY_SECONDS = 7 * 86400
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

    extension_id: str

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
        review_provider: str | None,
        review_build: str | None,
        review_expires_at: float | None,
        review_credential_hmac_key_id: str | None,
        review_credential_hmac: str | None,
        review_lane_revision: int | None,
    ) -> None:
        self._guard = guard
        self.user_id = user_id
        self.selector = selector
        self.auth_epoch = int(auth_epoch)
        self.review_provider = review_provider
        self.review_build = review_build
        self.review_expires_at = review_expires_at
        self.review_credential_hmac_key_id = review_credential_hmac_key_id
        self.review_credential_hmac = review_credential_hmac
        self.review_lane_revision = review_lane_revision
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

        if self._released or self._cancel.is_set():
            raise CredentialMutationRejected("The credential lease is closed.")
        observed_at = time.time() if now is None else float(now)
        row = user_store._conn.execute(
            "SELECT token.user_id, token.auth_epoch, token.expires_at,"
            " token.revoked_at, token.state, token.fenced_at,"
            " token.review_provider, token.review_build,"
            " token.review_expires_at, token.review_credential_hmac_key_id,"
            " token.review_credential_hmac, token.review_lane_revision,"
            " COALESCE(owner.auth_epoch, 0) AS owner_auth_epoch"
            " FROM mobile_api_tokens AS token"
            " JOIN users AS owner ON owner.id = token.user_id"
            " WHERE token.selector = ?",
            (self.selector,),
        ).fetchone()
        scope_valid = False
        if row is not None:
            if self.review_provider is None:
                scope_valid = all(
                    row[name] is None
                    for name in (
                        "review_provider",
                        "review_build",
                        "review_expires_at",
                        "review_credential_hmac_key_id",
                        "review_credential_hmac",
                        "review_lane_revision",
                    )
                )
            else:
                try:
                    scope_valid = (
                        str(row["review_provider"]) == self.review_provider
                        and str(row["review_build"]) == self.review_build
                        and float(row["review_expires_at"])
                        == float(self.review_expires_at)
                        and math.isfinite(float(row["review_expires_at"]))
                        and float(row["review_expires_at"]) > observed_at
                        and str(row["review_credential_hmac_key_id"])
                        == self.review_credential_hmac_key_id
                        and str(row["review_credential_hmac"])
                        == self.review_credential_hmac
                        and int(row["review_lane_revision"])
                        == self.review_lane_revision
                    )
                except (TypeError, ValueError):
                    scope_valid = False
        if (
            self._cancel.is_set()
            or row is None
            or str(row["user_id"]) != self.user_id
            or int(row["auth_epoch"]) != self.auth_epoch
            or int(row["owner_auth_epoch"]) != self.auth_epoch
            or float(row["expires_at"]) <= observed_at
            or row["revoked_at"] is not None
            or str(row["state"]) != "active"
            or row["fenced_at"] is not None
            or not scope_valid
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
        review_scope = (
            auth_context.review_provider,
            auth_context.review_build,
            auth_context.review_expires_at,
            auth_context.review_credential_hmac_key_id,
            auth_context.review_credential_hmac,
            auth_context.review_lane_revision,
        )
        if auth_context.review_provider is None:
            valid_scope = all(value is None for value in review_scope)
        else:
            valid_scope = (
                isinstance(auth_context.review_provider, str)
                and bool(auth_context.review_provider)
                and isinstance(auth_context.review_build, str)
                and bool(auth_context.review_build)
                and not isinstance(auth_context.review_expires_at, bool)
                and isinstance(auth_context.review_expires_at, (int, float))
                and math.isfinite(float(auth_context.review_expires_at))
                and isinstance(auth_context.review_credential_hmac_key_id, str)
                and bool(auth_context.review_credential_hmac_key_id)
                and isinstance(auth_context.review_credential_hmac, str)
                and bool(auth_context.review_credential_hmac)
                and not isinstance(auth_context.review_lane_revision, bool)
                and isinstance(auth_context.review_lane_revision, int)
                and auth_context.review_lane_revision >= 1
            )
        if not valid_scope:
            raise CredentialMutationRejected(
                "The mobile credential review scope is incomplete."
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
                review_provider=auth_context.review_provider,
                review_build=auth_context.review_build,
                review_expires_at=auth_context.review_expires_at,
                review_credential_hmac_key_id=(
                    auth_context.review_credential_hmac_key_id
                ),
                review_credential_hmac=auth_context.review_credential_hmac,
                review_lane_revision=auth_context.review_lane_revision,
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


@dataclass
class _OperationLockEntry:
    lock: threading.Lock
    references: int = 0


class _OperationLockRegistry:
    """Bounded per-operation serialization without retaining terminal IDs."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._entries: dict[str, _OperationLockEntry] = {}

    @contextmanager
    def hold(self, operation_id: str) -> Iterator[None]:
        with self._guard:
            entry = self._entries.get(operation_id)
            if entry is None:
                entry = _OperationLockEntry(threading.Lock())
                self._entries[operation_id] = entry
            entry.references += 1
        acquired = False
        try:
            entry.lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                entry.lock.release()
            with self._guard:
                entry.references -= 1
                if (
                    entry.references == 0
                    and self._entries.get(operation_id) is entry
                ):
                    self._entries.pop(operation_id, None)

    @property
    def count(self) -> int:
        with self._guard:
            return len(self._entries)


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
        extension_ids: list[str] = []
        seen_extension_ids: set[str] = set()
        for extension in self._extensions:
            extension_id = getattr(extension, "extension_id", None)
            if (
                not isinstance(extension_id, str)
                or _SIGN_OUT_EXTENSION_ID.fullmatch(extension_id) is None
            ):
                raise ValueError(
                    "Every sign-out extension requires a valid stable extension ID."
                )
            if extension_id in seen_extension_ids:
                raise ValueError("Sign-out extension IDs must be unique.")
            extension_ids.append(extension_id)
            seen_extension_ids.add(extension_id)
        self._extension_contract_version = _SIGN_OUT_EXTENSION_CONTRACT_VERSION
        self._extension_contract_sha256 = self._contract_digest(extension_ids)
        self._operation_locks = _OperationLockRegistry()

    @staticmethod
    def _contract_digest(extension_ids: list[str]) -> str:
        canonical = json.dumps(
            {
                "extensions": extension_ids,
                "version": _SIGN_OUT_EXTENSION_CONTRACT_VERSION,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        return hashlib.sha256(canonical).hexdigest()

    @property
    def operation_lock_count(self) -> int:
        return self._operation_locks.count

    def _validate_extension_contract(self, row: sqlite3.Row) -> None:
        try:
            version = row["extension_contract_version"]
            digest = row["extension_contract_sha256"]
        except (IndexError, KeyError) as exc:
            raise MobileSignOutUnavailable(
                "The pending sign-out extension contract is invalid."
            ) from exc
        if (
            type(version) is not int
            or not isinstance(digest, str)
            or version != self._extension_contract_version
            or not hmac.compare_digest(digest, self._extension_contract_sha256)
        ):
            raise MobileSignOutUnavailable(
                "The pending sign-out extension contract changed."
            )

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
        operation_id = str(uuid.uuid4())
        expires_at = observed_at + _SIGN_OUT_REPLAY_SECONDS
        context = MobileAuthContext(
            user=principal.user,
            via_bearer=True,
            selector=principal.selector,
            auth_epoch=principal.auth_epoch,
            review_provider=principal.review_provider,
            review_build=principal.review_build,
            review_expires_at=principal.review_expires_at,
            review_credential_hmac_key_id=principal.review_credential_hmac_key_id,
            review_credential_hmac=principal.review_credential_hmac,
            review_lane_revision=principal.review_lane_revision,
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
                " idempotency_hmac, request_hash, extension_contract_version,"
                " extension_contract_sha256, created_at, updated_at, expires_at)"
                " VALUES (?, ?, 'prepared', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    self._extension_contract_version,
                    self._extension_contract_sha256,
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
        with self._operation_locks.hold(operation_id):
            return self._advance_serialized(
                operation_id,
                selector=selector,
                close=close,
            )

    def _advance_serialized(
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
            self._validate_extension_contract(row)
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
            self._validate_extension_contract(row)
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


class MobileDeviceRevokeNotFound(LookupError):
    """The targeted device selector is unknown or belongs to another owner."""


@dataclass(frozen=True)
class MobileDeviceRevokeResult:
    pending: bool


@dataclass(frozen=True)
class _DeviceRevokeMaterial:
    target_selector_hmac_key_id: str
    target_selector_hmac: str
    target_token_verifier_hmac_key_id: str
    target_token_verifier_hmac: str
    idempotency_hmac_key_id: str
    idempotency_hmac: str
    request_hash: str


class MobileDeviceRevokeService:
    """Drive one recovery-fenced device revocation to a terminal receipt.

    This is the Task 3 sign-out state machine adapted for revoking a *target*
    selector that may differ from the initiator.  Self-revocation fences the
    caller's own credential through ``validate_and_close_caller`` so a revoked
    bearer can still replay to a terminal 204.  Other-device revocation keeps
    the initiator lease valid and fences only the target.  The legacy browser
    ``/api/v1/mobile-tokens`` revoke reuses the same fenced writes without a
    bearer credential.
    """

    def __init__(
        self,
        users: UserStore,
        guard: "CredentialMutationGuard",
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
        self._operation_locks = _OperationLockRegistry()

    @property
    def operation_lock_count(self) -> int:
        return self._operation_locks.count

    def verify_enabled_recovery_readiness(self, enabled: bool) -> None:
        """Fail closed when device management is on without a fence path.

        The strict remote chain is validated by ``compose_web_recovery_fence``
        at startup; this mirror keeps an enabled flag from silently running
        without a configured keyring and recovery publisher.
        """

        if not enabled:
            return
        if self._keyring is None or self._ledger is None:
            raise MobileSignOutUnavailable(
                "Device management requires a configured recovery fence."
            )

    @staticmethod
    def _idempotency_bytes(value: object) -> bytes:
        if not isinstance(value, str) or _IDEMPOTENCY_KEY.fullmatch(value) is None:
            raise MobileSignOutInvalidRequest("Invalid Idempotency-Key.")
        return bytes.fromhex(value)

    @staticmethod
    def _request_hash(
        *,
        target_selector_hmac_key_id: str,
        target_selector_hmac: str,
        target_token_verifier_hmac_key_id: str,
        target_token_verifier_hmac: str,
        idempotency_hmac_key_id: str,
        idempotency_hmac: str,
    ) -> str:
        body = json.dumps(
            {
                "operation": "mobile-device-revoke-v1",
                "target_selector_hmac_key_id": target_selector_hmac_key_id,
                "target_selector_hmac": target_selector_hmac,
                "target_token_verifier_hmac_key_id": (
                    target_token_verifier_hmac_key_id
                ),
                "target_token_verifier_hmac": target_token_verifier_hmac,
                "idempotency_hmac_key_id": idempotency_hmac_key_id,
                "idempotency_hmac": idempotency_hmac,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        return hashlib.sha256(body).hexdigest()

    def _new_material(
        self,
        target_selector: str,
        target_token_hash: str,
        idempotency: bytes,
    ) -> _DeviceRevokeMaterial:
        if self._keyring is None:
            raise MobileSignOutUnavailable(
                "Protected device revocation state is not configured."
            )
        selector_key, selector_hmac = self._keyring.digest(
            MobileStateDomain.RECOVERY_SELECTOR, target_selector
        )
        verifier_key, verifier_hmac = self._keyring.digest(
            MobileStateDomain.RECOVERY_TOKEN_VERIFIER, target_token_hash
        )
        idempotency_key, idempotency_hmac = self._keyring.digest(
            MobileStateDomain.DEVICE_REVOKE_IDEMPOTENCY, idempotency
        )
        request_hash = self._request_hash(
            target_selector_hmac_key_id=selector_key,
            target_selector_hmac=selector_hmac,
            target_token_verifier_hmac_key_id=verifier_key,
            target_token_verifier_hmac=verifier_hmac,
            idempotency_hmac_key_id=idempotency_key,
            idempotency_hmac=idempotency_hmac,
        )
        return _DeviceRevokeMaterial(
            selector_key,
            selector_hmac,
            verifier_key,
            verifier_hmac,
            idempotency_key,
            idempotency_hmac,
            request_hash,
        )

    def _find_replay(
        self,
        *,
        target_selector: str,
        idempotency: bytes,
        now: float,
    ) -> tuple[str, sqlite3.Row] | tuple[str, None] | None:
        """Return ``(kind, row)`` for a durable replay of this exact request.

        ``kind`` is ``"receipt"`` (terminal), ``"journal"`` (resumable), or
        ``"conflict"`` when the same idempotency key was bound to a different
        target selector.
        """

        if self._keyring is None:
            return None
        candidates = self._keyring.candidates(
            MobileStateDomain.DEVICE_REVOKE_IDEMPOTENCY, idempotency
        )
        predicate = " OR ".join(
            "(idempotency_hmac_key_id = ? AND idempotency_hmac = ?)"
            for _candidate in candidates
        )
        if not predicate:
            return None
        parameters = tuple(
            value
            for candidate in candidates
            for value in (candidate.key_id, candidate.digest)
        )
        with self._users._lock:
            receipts = self._users._conn.execute(
                "SELECT * FROM mobile_device_revoke_receipts"
                f" WHERE expires_at > ? AND ({predicate})"
                " ORDER BY completed_at DESC",
                (now, *parameters),
            ).fetchall()
            journals = self._users._conn.execute(
                "SELECT * FROM mobile_device_revoke_journals"
                f" WHERE expires_at > ? AND ({predicate})"
                " ORDER BY created_at DESC",
                (now, *parameters),
            ).fetchall()
        conflict = False
        for row in receipts:
            try:
                selector_digest = self._keyring.digest_with_key(
                    str(row["target_selector_hmac_key_id"]),
                    MobileStateDomain.RECOVERY_SELECTOR,
                    target_selector,
                )
            except KeyError as exc:
                raise RuntimeError(str(exc)) from exc
            if hmac.compare_digest(
                selector_digest, str(row["target_selector_hmac"])
            ):
                return "receipt", row
            conflict = True
        for row in journals:
            if str(row["target_selector"]) == target_selector:
                return "journal", row
            conflict = True
        if conflict:
            return "conflict", None
        return None

    def _target_token_hash(self, target_selector: str, owner_user_id: str) -> str | None:
        with self._users._lock:
            row = self._users._conn.execute(
                "SELECT token_hash FROM mobile_api_tokens"
                " WHERE selector = ? AND user_id = ?",
                (target_selector, owner_user_id),
            ).fetchone()
        return None if row is None else str(row["token_hash"])

    def _insert_journal_locked(
        self,
        connection: sqlite3.Connection,
        *,
        operation_id: str,
        owner_user_id: str,
        initiator_selector: str | None,
        target_selector: str,
        material: _DeviceRevokeMaterial,
        now: float,
        expires_at: float,
    ) -> None:
        for table in (
            "mobile_device_revoke_journals",
            "mobile_device_revoke_receipts",
        ):
            if connection.execute(
                f"SELECT 1 FROM {table}"
                " WHERE idempotency_hmac_key_id = ?"
                " AND idempotency_hmac = ? LIMIT 1",
                (material.idempotency_hmac_key_id, material.idempotency_hmac),
            ).fetchone() is not None:
                raise CredentialMutationRejected(
                    "A device revocation idempotency key is already recorded."
                )
        connection.execute(
            "INSERT INTO mobile_device_revoke_journals"
            " (operation_id, owner_user_id, initiator_selector, target_selector,"
            " phase, target_selector_hmac_key_id, target_selector_hmac,"
            " target_token_verifier_hmac_key_id, target_token_verifier_hmac,"
            " idempotency_hmac_key_id, idempotency_hmac, request_hash,"
            " recovery_sequence, recovery_record_hash, created_at, updated_at,"
            " expires_at)"
            " VALUES (?, ?, ?, ?, 'prepared', ?, ?, ?, ?, ?, ?, ?, NULL, NULL,"
            " ?, ?, ?)",
            (
                operation_id,
                owner_user_id,
                initiator_selector,
                target_selector,
                material.target_selector_hmac_key_id,
                material.target_selector_hmac,
                material.target_token_verifier_hmac_key_id,
                material.target_token_verifier_hmac,
                material.idempotency_hmac_key_id,
                material.idempotency_hmac,
                material.request_hash,
                now,
                now,
                expires_at,
            ),
        )

    def _prepare_target_revoke(
        self,
        *,
        operation_id: str,
        owner_user_id: str,
        initiator_selector: str | None,
        target_selector: str,
        material: _DeviceRevokeMaterial,
        now: float,
        lease: "CredentialMutationLease | None",
    ) -> CredentialMutationClose:
        """Fence the target token and journal the operation in one transaction.

        Unlike a self sign-out this never fences the initiator; the initiator
        lease (when present) is only revalidated so a stale browser/bearer
        credential cannot drive a revoke.
        """

        expires_at = now + _DEVICE_REVOKE_REPLAY_SECONDS
        with self._users._lock:
            try:
                self._users._conn.execute("BEGIN IMMEDIATE")
                if lease is not None:
                    lease.validate_locked(self._users, now=now)
                cursor = self._users._conn.execute(
                    "UPDATE mobile_api_tokens"
                    " SET state = 'fenced', fenced_at = COALESCE(fenced_at, ?)"
                    " WHERE selector = ? AND user_id = ? AND expires_at > ?"
                    " AND revoked_at IS NULL AND state = 'active'"
                    " AND fenced_at IS NULL",
                    (now, target_selector, owner_user_id, now),
                )
                if cursor.rowcount != 1:
                    raise MobileDeviceRevokeNotFound("Mobile device not found.")
                self._insert_journal_locked(
                    self._users._conn,
                    operation_id=operation_id,
                    owner_user_id=owner_user_id,
                    initiator_selector=initiator_selector,
                    target_selector=target_selector,
                    material=material,
                    now=now,
                    expires_at=expires_at,
                )
                self._users._conn.commit()
            except Exception:
                if self._users._conn.in_transaction:
                    self._users._conn.rollback()
                raise
        return self._guard.close_selector(target_selector)

    def revoke(
        self,
        raw_token: object,
        target_selector: object,
        idempotency_key: object,
        *,
        now: float | None = None,
    ) -> MobileDeviceRevokeResult:
        observed_at = time.time() if now is None else float(now)
        idempotency = self._idempotency_bytes(idempotency_key)
        parsed = self._users._parse_mobile_api_token(raw_token)
        if parsed is None:
            raise MobileSignOutUnauthorized("Invalid mobile access token.")
        initiator_selector, initiator_secret = parsed
        if not isinstance(target_selector, str) or not target_selector:
            raise MobileSignOutUnauthorized("Invalid mobile access token.")
        is_self = target_selector == initiator_selector

        if is_self:
            replay = self._find_replay(
                target_selector=target_selector,
                idempotency=idempotency,
                now=observed_at,
            )
            resolved = self._resolve_replay(replay, target_selector)
            if resolved is not None:
                return resolved

        principal = self._users.authenticate_mobile_api_principal(
            raw_token, now=observed_at
        )
        if principal is None or principal.selector != initiator_selector:
            raise MobileSignOutUnauthorized("Invalid mobile access token.")
        if self._ledger is None:
            raise MobileSignOutUnavailable(
                "Protected device revocation is not configured."
            )
        context = self._context(principal)

        if is_self:
            initiator_token_hash = self._users._mobile_api_token_hash(
                initiator_selector, initiator_secret
            )
            material = self._new_material(
                target_selector, initiator_token_hash, idempotency
            )
            operation_id = str(uuid.uuid4())
            expires_at = observed_at + _DEVICE_REVOKE_REPLAY_SECONDS

            def prepare_locked(connection: sqlite3.Connection) -> None:
                self._insert_journal_locked(
                    connection,
                    operation_id=operation_id,
                    owner_user_id=principal.user.id,
                    initiator_selector=initiator_selector,
                    target_selector=target_selector,
                    material=material,
                    now=observed_at,
                    expires_at=expires_at,
                )

            lease: CredentialMutationLease | None = None
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
                raced = self._resolve_replay(
                    self._find_replay(
                        target_selector=target_selector,
                        idempotency=idempotency,
                        now=time.time(),
                    ),
                    target_selector,
                )
                if raced is not None:
                    return raced
                raise MobileSignOutUnauthorized(
                    "Invalid mobile access token."
                ) from None
            except Exception:
                if lease is not None:
                    lease.release()
                raise
            return self._advance(operation_id, target_selector, close)

        # Other-device revocation keeps the initiator lease valid.
        target_token_hash = self._target_token_hash(
            target_selector, principal.user.id
        )
        if target_token_hash is None:
            raise MobileDeviceRevokeNotFound("Mobile device not found.")
        replay = self._find_replay(
            target_selector=target_selector,
            idempotency=idempotency,
            now=observed_at,
        )
        resolved = self._resolve_replay(replay, target_selector)
        if resolved is not None:
            return resolved
        material = self._new_material(
            target_selector, target_token_hash, idempotency
        )
        operation_id = str(uuid.uuid4())
        lease = None
        try:
            lease = self._guard.admit(context)
            close = self._prepare_target_revoke(
                operation_id=operation_id,
                owner_user_id=principal.user.id,
                initiator_selector=initiator_selector,
                target_selector=target_selector,
                material=material,
                now=observed_at,
                lease=lease,
            )
        except CredentialMutationRejected:
            raced = self._resolve_replay(
                self._find_replay(
                    target_selector=target_selector,
                    idempotency=idempotency,
                    now=time.time(),
                ),
                target_selector,
            )
            if raced is not None:
                return raced
            raise MobileSignOutUnauthorized(
                "Invalid mobile access token."
            ) from None
        finally:
            if lease is not None:
                lease.release()
        return self._advance(operation_id, target_selector, close)

    def revoke_owned_selector(
        self,
        owner_user_id: str,
        target_selector: object,
        *,
        now: float | None = None,
    ) -> MobileDeviceRevokeResult:
        """Fence-revoke one browser-owned selector without a bearer credential.

        The legacy ``/api/v1/mobile-tokens`` DELETE routes through this so a
        cookie-owner revocation is durably published rather than a local-only
        delete.  A retry resumes the owned-selector journal.
        """

        observed_at = time.time() if now is None else float(now)
        if self._ledger is None:
            raise MobileSignOutUnavailable(
                "Protected device revocation is not configured."
            )
        if not isinstance(target_selector, str) or not target_selector:
            raise MobileDeviceRevokeNotFound("Mobile device not found.")
        with self._users._lock:
            existing = self._users._conn.execute(
                "SELECT operation_id FROM mobile_device_revoke_journals"
                " WHERE owner_user_id = ? AND target_selector = ?"
                " AND phase != 'complete'"
                " ORDER BY created_at DESC LIMIT 1",
                (owner_user_id, target_selector),
            ).fetchone()
        if existing is not None:
            return self._advance(
                str(existing["operation_id"]),
                target_selector,
                self._guard.close_selector(target_selector),
            )
        token_hash = self._target_token_hash(target_selector, owner_user_id)
        if token_hash is None:
            raise MobileDeviceRevokeNotFound("Mobile device not found.")
        idempotency = secrets.token_bytes(16)
        material = self._new_material(target_selector, token_hash, idempotency)
        operation_id = str(uuid.uuid4())
        close = self._prepare_target_revoke(
            operation_id=operation_id,
            owner_user_id=owner_user_id,
            initiator_selector=None,
            target_selector=target_selector,
            material=material,
            now=observed_at,
            lease=None,
        )
        return self._advance(operation_id, target_selector, close)

    def _context(self, principal) -> MobileAuthContext:
        return MobileAuthContext(
            user=principal.user,
            via_bearer=True,
            selector=principal.selector,
            auth_epoch=principal.auth_epoch,
            review_provider=principal.review_provider,
            review_build=principal.review_build,
            review_expires_at=principal.review_expires_at,
            review_credential_hmac_key_id=principal.review_credential_hmac_key_id,
            review_credential_hmac=principal.review_credential_hmac,
            review_lane_revision=principal.review_lane_revision,
        )

    def _resolve_replay(
        self,
        replay: tuple[str, sqlite3.Row] | tuple[str, None] | None,
        target_selector: str,
    ) -> MobileDeviceRevokeResult | None:
        if replay is None:
            return None
        kind, row = replay
        if kind == "conflict":
            raise MobileSignOutUnauthorized("Invalid mobile access token.")
        if kind == "receipt":
            return MobileDeviceRevokeResult(pending=False)
        assert row is not None
        return self._advance(
            str(row["operation_id"]),
            target_selector,
            None,
        )

    def _operation(self, operation_id: str) -> sqlite3.Row:
        with self._users._lock:
            row = self._users._conn.execute(
                "SELECT * FROM mobile_device_revoke_journals"
                " WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("A device revocation journal disappeared.")
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
                    f"UPDATE mobile_device_revoke_journals SET {assignments}"
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
        target_selector: str,
    ) -> None:
        revoked_at = time.time()
        with self._users._lock:
            try:
                self._users._conn.execute("BEGIN IMMEDIATE")
                current = self._users._conn.execute(
                    "SELECT phase FROM mobile_device_revoke_journals"
                    " WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if current is None:
                    raise RuntimeError("A device revocation journal disappeared.")
                if str(current["phase"]) != "extensions_closed":
                    self._users._conn.commit()
                    return
                self._users._conn.execute(
                    "UPDATE mobile_api_tokens"
                    " SET revoked_at = COALESCE(revoked_at, ?),"
                    " state = 'fenced', fenced_at = COALESCE(fenced_at, ?)"
                    " WHERE selector = ? AND user_id = ?",
                    (
                        revoked_at,
                        revoked_at,
                        target_selector,
                        row["owner_user_id"],
                    ),
                )
                self._users._conn.execute(
                    "UPDATE mobile_device_revoke_journals"
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
                    "SELECT * FROM mobile_device_revoke_journals"
                    " WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if current is None:
                    raise RuntimeError("A device revocation journal disappeared.")
                if str(current["phase"]) == "complete":
                    self._users._conn.commit()
                    return
                if str(current["phase"]) != "token_revoked":
                    self._users._conn.commit()
                    return
                self._users._conn.execute(
                    "INSERT OR IGNORE INTO mobile_device_revoke_receipts"
                    " (operation_id, owner_user_id, target_selector_hmac_key_id,"
                    " target_selector_hmac, idempotency_hmac_key_id,"
                    " idempotency_hmac, request_hash, completed_at, expires_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        operation_id,
                        current["owner_user_id"],
                        current["target_selector_hmac_key_id"],
                        current["target_selector_hmac"],
                        current["idempotency_hmac_key_id"],
                        current["idempotency_hmac"],
                        current["request_hash"],
                        completed_at,
                        current["expires_at"],
                    ),
                )
                self._users._conn.execute(
                    "UPDATE mobile_device_revoke_journals"
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
        target_selector: str,
        close: CredentialMutationClose | None,
    ) -> MobileDeviceRevokeResult:
        with self._operation_locks.hold(operation_id):
            return self._advance_serialized(operation_id, target_selector, close)

    def _advance_serialized(
        self,
        operation_id: str,
        target_selector: str,
        close: CredentialMutationClose | None,
    ) -> MobileDeviceRevokeResult:
        while True:
            row = self._operation(operation_id)
            phase = str(row["phase"])
            if phase == "complete":
                return MobileDeviceRevokeResult(pending=False)
            if phase == "prepared":
                if self._ledger is None:
                    return MobileDeviceRevokeResult(pending=True)
                event = TokenRevokeEvent(
                    event_id=operation_id,
                    cutoff_at=float(row["created_at"]),
                    selector_hmac_key_id=str(row["target_selector_hmac_key_id"]),
                    selector_hmac=str(row["target_selector_hmac"]),
                    token_verifier_hmac_key_id=str(
                        row["target_token_verifier_hmac_key_id"]
                    ),
                    token_verifier_hmac=str(row["target_token_verifier_hmac"]),
                )
                try:
                    published = self._ledger.append_and_publish(event)
                except RecoveryFenceError:
                    return MobileDeviceRevokeResult(pending=True)
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
                    close = self._guard.close_selector(target_selector)
                if not close.drain(timeout_seconds=self._drain_timeout_seconds):
                    return MobileDeviceRevokeResult(pending=True)
                for extension in self._extensions:
                    try:
                        closed = extension.close_for_sign_out(
                            users=self._users,
                            operation_id=operation_id,
                            user_id=str(row["owner_user_id"]),
                            selector=target_selector,
                        )
                    except Exception:
                        logger.warning(
                            "Mobile device revoke extension remains pending "
                            "operation_id=%s extension=%s",
                            operation_id,
                            type(extension).__name__,
                        )
                        return MobileDeviceRevokeResult(pending=True)
                    if closed is not True:
                        return MobileDeviceRevokeResult(pending=True)
                self._transition(
                    operation_id, "recovery_fenced", "extensions_closed"
                )
                continue
            if phase == "extensions_closed":
                self._revoke_and_transition(operation_id, row, target_selector)
                continue
            if phase == "token_revoked":
                self._complete(operation_id, row)
                continue
            raise RuntimeError("A device revocation journal phase is invalid.")

    def resume_nonterminal(self) -> None:
        """Finish every crash-interrupted revoke before routes admit traffic."""

        with self._users._lock:
            rows = self._users._conn.execute(
                "SELECT operation_id, target_selector FROM"
                " mobile_device_revoke_journals WHERE phase != 'complete'"
                " ORDER BY created_at, operation_id"
            ).fetchall()
        for row in rows:
            target_selector = str(row["target_selector"])
            close = self._guard.close_selector(target_selector)
            result = self._advance(
                str(row["operation_id"]), target_selector, close
            )
            if result.pending:
                raise RuntimeError(
                    "A pending device revocation could not be recovered "
                    "before startup."
                )
