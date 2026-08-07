"""Challenge-bound native email authentication and recovery-fenced rotation."""

from __future__ import annotations

import html
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator, Mapping, Protocol
from urllib.parse import urlencode, urlsplit

from . import mailer
from .credential_mutations import CredentialMutationGuard
from .mobile_schema import MobileStateDomain, VersionedHMAC
from .recovery_fence_ledger import RecoveryFenceError, TokenRevokeEvent
from .throttle import KeyedThrottle
from .users import (
    MobileAPITokenLimitError,
    MobileAuthChallenge,
    MobileAuthChallengeLimit,
    MobileAuthChallengeRejected,
    MobileAuthExchangeConflict,
    MobileAuthExchangeJournal,
    User,
    UserStore,
)


AUTH_WINDOW_SECONDS = 15 * 60
_SHA256 = re.compile(r"[0-9a-f]{64}")


class MobileNativeAuthInvalidRequest(ValueError):
    """A native auth request failed public shape validation."""


class MobileNativeAuthRejected(RuntimeError):
    """A challenge/proof failure with one non-enumerating response."""


class MobileNativeAuthConflict(RuntimeError):
    """A consumed challenge or idempotency tuple conflicts."""


class MobileNativeAuthUnavailable(RuntimeError):
    """Protected native-auth recovery dependencies are unavailable."""


class MobileNativeAuthRateLimited(RuntimeError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Too many authentication attempts.")
        self.retry_after_seconds = max(1, min(900, int(retry_after_seconds)))


class RecoveryFencePublisher(Protocol):
    def load_chain_snapshot(self): ...

    def append_and_publish(self, event: TokenRevokeEvent): ...


@dataclass(frozen=True)
class MobileNativeAuthSettings:
    enabled: bool
    starts_per_ip: int
    starts_per_email: int
    failed_exchanges_per_ip: int
    failed_exchanges_per_email: int
    live_challenges_per_ip: int
    live_challenges_per_email: int


_SETTING_BOUNDS: dict[str, tuple[int, int]] = {
    "mobile_auth_starts_per_15_minutes_per_ip": (1, 100),
    "mobile_auth_starts_per_15_minutes_per_email": (1, 50),
    "mobile_auth_failed_exchanges_per_15_minutes_per_ip": (1, 100),
    "mobile_auth_failed_exchanges_per_15_minutes_per_email": (1, 50),
    "mobile_auth_live_challenges_per_ip": (1, 100),
    "mobile_auth_live_challenges_per_email": (1, 20),
}


def validate_mobile_native_auth_settings(web: Mapping[str, object]) -> MobileNativeAuthSettings:
    """Validate the default-off flag and all six closed abuse bounds."""

    enabled = web.get("mobile_native_auth_enabled", False)
    if type(enabled) is not bool:
        raise ValueError("web.mobile_native_auth_enabled must be true or false.")
    values: dict[str, int] = {}
    for name, (minimum, maximum) in _SETTING_BOUNDS.items():
        value = web.get(name)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(
                f"web.{name} must be an integer from {minimum} to {maximum}."
            )
        values[name] = value
    return MobileNativeAuthSettings(
        enabled=enabled,
        starts_per_ip=values["mobile_auth_starts_per_15_minutes_per_ip"],
        starts_per_email=values["mobile_auth_starts_per_15_minutes_per_email"],
        failed_exchanges_per_ip=values[
            "mobile_auth_failed_exchanges_per_15_minutes_per_ip"
        ],
        failed_exchanges_per_email=values[
            "mobile_auth_failed_exchanges_per_15_minutes_per_email"
        ],
        live_challenges_per_ip=values["mobile_auth_live_challenges_per_ip"],
        live_challenges_per_email=values[
            "mobile_auth_live_challenges_per_email"
        ],
    )


@dataclass(frozen=True)
class MobileNativeAuthStart:
    challenge_id: str
    expires_at: float


@dataclass(frozen=True)
class MobileNativeAuthExchange:
    exchange_id: str
    pending: bool
    access_token: str | None = None
    expires_at: float | None = None
    retry_after_seconds: int = 1


@dataclass
class _OperationLock:
    lock: threading.Lock
    references: int = 0


class _OperationLocks:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._entries: dict[str, _OperationLock] = {}

    @contextmanager
    def hold(self, operation_id: str) -> Iterator[None]:
        with self._guard:
            entry = self._entries.get(operation_id)
            if entry is None:
                entry = _OperationLock(threading.Lock())
                self._entries[operation_id] = entry
            entry.references += 1
        entry.lock.acquire()
        try:
            yield
        finally:
            entry.lock.release()
            with self._guard:
                entry.references -= 1
                if entry.references == 0 and self._entries.get(operation_id) is entry:
                    self._entries.pop(operation_id, None)


def _safe_client_ip(value: str | None) -> str:
    # Requests without a socket identity share one bounded non-secret bucket.
    return value if isinstance(value, str) and value else "unavailable-client"


def _is_canonical_https_origin(value: str) -> bool:
    if (
        not value
        or value != value.strip()
        or any(character.isspace() for character in value)
        or "?" in value
        or "#" in value
    ):
        return False
    try:
        parsed = urlsplit(value)
        # Accessing these properties validates malformed hosts and ports.
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and hostname
        and parsed.username is None
        and parsed.password is None
        and not parsed.netloc.endswith(":")
        and parsed.path == ""
        and parsed.query == ""
        and parsed.fragment == ""
    )


def _email_bodies(
    *, brand_name: str, public_base_url: str, challenge: MobileAuthChallenge
) -> tuple[str, str, str]:
    grouped = challenge.email_code[:4] + "-" + challenge.email_code[4:]
    query = urlencode(
        {"challenge_id": challenge.challenge_id, "code": challenge.email_code}
    )
    link = f"{public_base_url}/app/auth/callback?{query}"
    subject = f"Open {brand_name} to sign in"
    text = (
        f"Open {brand_name} on the device where sign-in started:\n{link}\n\n"
        f"Or enter this code on that device: {grouped}\n\n"
        "This code expires in 10 minutes. If you did not request it, ignore this email."
    )
    safe_brand = html.escape(brand_name)
    safe_link = html.escape(link, quote=True)
    safe_code = html.escape(grouped)
    html_body = (
        "<!doctype html><html><body>"
        '<div style="display:none;max-height:0;overflow:hidden">'
        "Open the app on the device where sign-in started.</div>"
        f"<p>Open {safe_brand} on the device where sign-in started.</p>"
        f'<p><a href="{safe_link}">Open {safe_brand}</a></p>'
        f"<p>Or enter this code on that device: <strong>{safe_code}</strong></p>"
        "<p>This code expires in 10 minutes. If you did not request it, ignore this email.</p>"
        "</body></html>"
    )
    return subject, text, html_body


class MobileAuthService:
    """Own native challenge delivery and crash-safe exchange phase changes."""

    def __init__(
        self,
        users: UserStore,
        keyed_throttle: KeyedThrottle | None,
        credential_guard: CredentialMutationGuard,
        *,
        keyring: VersionedHMAC | None,
        recovery_fence_ledger: RecoveryFencePublisher | None,
        settings: MobileNativeAuthSettings,
        public_base_url: str | None,
        brand_name: str,
        shopify_sync_eligible: Callable[[str], bool] | None = None,
        on_success: Callable[[User], None] | None = None,
        drain_timeout_seconds: float = 0.25,
    ) -> None:
        self._users = users
        self._throttle = keyed_throttle
        self._guard = credential_guard
        self._keyring = keyring
        self._ledger = recovery_fence_ledger
        self.settings = settings
        self._public_base_url = (public_base_url or "").rstrip("/")
        self._brand_name = brand_name
        self._shopify_sync_eligible = shopify_sync_eligible or (lambda _email: False)
        self._on_success = on_success or (lambda _user: None)
        self._drain_timeout_seconds = max(0.0, float(drain_timeout_seconds))
        self._now: Callable[[], float] = time.time
        self._operation_locks = _OperationLocks()

    def verify_enabled_recovery_readiness(self) -> None:
        if not self.settings.enabled:
            return
        if self._keyring is None or self._throttle is None or self._ledger is None:
            raise MobileNativeAuthUnavailable(
                "Native authentication recovery is not configured."
            )
        if not _is_canonical_https_origin(self._public_base_url):
            raise MobileNativeAuthUnavailable(
                "Native authentication requires a canonical HTTPS PUBLIC_BASE_URL."
            )
        try:
            snapshot = self._ledger.load_chain_snapshot()
        except Exception as exc:
            raise MobileNativeAuthUnavailable(
                "Native authentication recovery is not ready."
            ) from exc
        records = getattr(snapshot, "records", ())
        if not isinstance(records, tuple) or not records:
            raise MobileNativeAuthUnavailable(
                "Native authentication recovery is not ready."
            )
        first_kind = getattr(records[0], "kind", None)
        first_value = getattr(first_kind, "value", first_kind)
        if first_value != "cutover_baseline":
            raise MobileNativeAuthUnavailable(
                "Native authentication recovery baseline is invalid."
            )

    def start(
        self,
        *,
        email: object,
        code_challenge: object,
        installation_id: object,
        device_label: object,
        client_ip: str | None,
    ) -> MobileNativeAuthStart:
        if self._throttle is None:
            raise MobileNativeAuthUnavailable(
                "Native authentication is not configured."
            )
        try:
            normalized_email = self._users.validate_email(email)
        except ValueError as exc:
            raise MobileNativeAuthInvalidRequest(str(exc)) from exc
        ip = _safe_client_ip(client_ip)
        now = float(self._now())
        self._purge_expired(now)
        decision = self._throttle.consume_many(
            [
                ("auth-start-ip", ip, self.settings.starts_per_ip, AUTH_WINDOW_SECONDS),
                (
                    "auth-start-email",
                    normalized_email,
                    self.settings.starts_per_email,
                    AUTH_WINDOW_SECONDS,
                ),
            ],
            now=now,
        )
        if not decision.allowed:
            raise MobileNativeAuthRateLimited(decision.retry_after_seconds)
        try:
            challenge = self._users.begin_mobile_email_signin(
                normalized_email,
                code_challenge=code_challenge,
                installation_id=installation_id,
                device_label=device_label,
                client_ip=ip,
                live_challenges_per_ip=self.settings.live_challenges_per_ip,
                live_challenges_per_email=self.settings.live_challenges_per_email,
                now=now,
            )
        except MobileAuthChallengeLimit as exc:
            raise MobileNativeAuthRateLimited(exc.retry_after_seconds) from exc
        except ValueError as exc:
            raise MobileNativeAuthInvalidRequest(str(exc)) from exc
        if challenge.send_required and mailer.enabled():
            subject, text, html_body = _email_bodies(
                brand_name=self._brand_name,
                public_base_url=self._public_base_url,
                challenge=challenge,
            )
            try:
                mailer.send(
                    normalized_email,
                    subject,
                    text,
                    html_body=html_body,
                )
            except Exception:
                # Delivery state is intentionally invisible to the API.  An
                # uncertain provider outcome leaves the one-time challenge
                # valid; a later request is resend-suppressed for 60 seconds.
                pass
        return MobileNativeAuthStart(
            challenge_id=challenge.challenge_id,
            expires_at=challenge.expires_at,
        )

    def _debit_failed_exchange(
        self, *, normalized_email: str | None, client_ip: str, now: float
    ) -> None:
        if self._throttle is None:
            raise MobileNativeAuthUnavailable(
                "Native authentication is not configured."
            )
        entries = [
            (
                "auth-exchange-ip",
                client_ip,
                self.settings.failed_exchanges_per_ip,
                AUTH_WINDOW_SECONDS,
            )
        ]
        if normalized_email is not None:
            entries.append(
                (
                    "auth-exchange-email",
                    normalized_email,
                    self.settings.failed_exchanges_per_email,
                    AUTH_WINDOW_SECONDS,
                )
            )
        decision = self._throttle.consume_many(entries, now=now)
        if not decision.allowed:
            raise MobileNativeAuthRateLimited(decision.retry_after_seconds)

    def exchange(
        self,
        *,
        challenge_id: object,
        email_code: object,
        code_verifier: object,
        idempotency_key: object,
        client_ip: str | None,
    ) -> MobileNativeAuthExchange:
        now = float(self._now())
        self._purge_expired(now)
        ip = _safe_client_ip(client_ip)
        if not isinstance(challenge_id, str):
            self._debit_failed_exchange(
                normalized_email=None, client_ip=ip, now=now
            )
            raise MobileNativeAuthRejected("Invalid authentication challenge.")
        normalized_email = self._users.mobile_auth_challenge_email(challenge_id)
        if normalized_email is None:
            self._debit_failed_exchange(
                normalized_email=None,
                client_ip=ip,
                now=now,
            )
            raise MobileNativeAuthRejected("Invalid authentication challenge.")
        try:
            journal = self._users.prepare_mobile_email_signin_exchange(
                challenge_id,
                email_code,
                code_verifier,
                idempotency_key,
                shopify_sync_pending=bool(
                    self._shopify_sync_eligible(normalized_email)
                ),
                now=now,
            )
        except MobileAuthChallengeRejected as exc:
            self._debit_failed_exchange(
                normalized_email=normalized_email,
                client_ip=ip,
                now=now,
            )
            raise MobileNativeAuthRejected(
                "Invalid authentication challenge."
            ) from exc
        except MobileAuthExchangeConflict as exc:
            # A mismatched replay against a consumed, known challenge must
            # not become an unbounded proof oracle.  Global idempotency
            # collisions take the same bounded path without disclosing which
            # persisted field conflicted.
            self._debit_failed_exchange(
                normalized_email=normalized_email,
                client_ip=ip,
                now=now,
            )
            raise MobileNativeAuthConflict(
                "The authentication exchange conflicts."
            ) from exc
        except MobileAPITokenLimitError:
            raise
        except ValueError as exc:
            raise MobileNativeAuthInvalidRequest(str(exc)) from exc
        advanced = self._advance(journal.exchange_id)
        if advanced is None:
            return MobileNativeAuthExchange(
                exchange_id=journal.exchange_id,
                pending=True,
            )
        try:
            access_token, expires_at = self._users.recover_mobile_email_exchange_credential(
                advanced, code_verifier
            )
        except MobileAuthChallengeRejected as exc:
            raise MobileNativeAuthConflict(
                "The authentication exchange conflicts."
            ) from exc
        user = self._users.get(advanced.user_id)
        if user is None:
            raise MobileNativeAuthUnavailable(
                "The authenticated account is unavailable."
            )
        self._on_success(user)
        return MobileNativeAuthExchange(
            exchange_id=journal.exchange_id,
            pending=False,
            access_token=access_token,
            expires_at=expires_at,
        )

    def _purge_expired(self, now: float) -> None:
        if self._throttle is None:
            raise MobileNativeAuthUnavailable(
                "Native authentication is not configured."
            )
        try:
            self._users.purge_expired_mobile_auth_state(now=now)
            self._throttle.purge_expired(now=now)
        except (sqlite3.Error, RuntimeError) as exc:
            raise MobileNativeAuthUnavailable(
                "Native authentication maintenance is unavailable."
            ) from exc

    def _row(self, exchange_id: str) -> sqlite3.Row:
        with self._users._lock:
            row = self._users._conn.execute(
                "SELECT * FROM mobile_auth_exchange_journals"
                " WHERE exchange_id = ? AND purpose = 'email'",
                (exchange_id,),
            ).fetchone()
        if row is None:
            raise MobileNativeAuthUnavailable(
                "A native authentication journal disappeared."
            )
        return row

    def _transition_without_recovery(
        self, exchange_id: str, expected: str, next_phase: str
    ) -> None:
        now = float(self._now())
        with self._users._lock:
            try:
                self._users._conn.execute("BEGIN IMMEDIATE")
                cursor = self._users._conn.execute(
                    "UPDATE mobile_auth_exchange_journals"
                    " SET phase = ?, updated_at = ?"
                    " WHERE exchange_id = ? AND phase = ?",
                    (next_phase, now, exchange_id, expected),
                )
                if cursor.rowcount != 1:
                    current = self._users._conn.execute(
                        "SELECT phase FROM mobile_auth_exchange_journals"
                        " WHERE exchange_id = ?",
                        (exchange_id,),
                    ).fetchone()
                    if current is None or str(current["phase"]) != next_phase:
                        raise MobileNativeAuthUnavailable(
                            "A native authentication phase changed unexpectedly."
                        )
                self._users._conn.commit()
            except Exception:
                if self._users._conn.in_transaction:
                    self._users._conn.rollback()
                raise

    def _publish_prior_revoke(self, row: sqlite3.Row) -> bool:
        prior_selector = str(row["prior_selector"])
        close = self._guard.close_selector(prior_selector)
        if not close.drain(timeout_seconds=self._drain_timeout_seconds):
            return False
        if self._ledger is None or self._keyring is None:
            return False
        selector_key_id, selector_hmac = self._keyring.digest(
            MobileStateDomain.RECOVERY_SELECTOR, prior_selector
        )
        event = TokenRevokeEvent(
            event_id=str(row["exchange_id"]),
            cutoff_at=float(row["created_at"]),
            selector_hmac_key_id=selector_key_id,
            selector_hmac=selector_hmac,
            token_verifier_hmac_key_id=str(row["token_verifier_hmac_key_id"]),
            token_verifier_hmac=str(row["token_verifier_hmac"]),
        )
        try:
            published = self._ledger.append_and_publish(event)
        except RecoveryFenceError:
            return False
        sequence = getattr(published, "sequence", None)
        record_hash = getattr(published, "record_hash", None)
        if type(sequence) is not int or sequence < 1 or not isinstance(
            record_hash, str
        ) or _SHA256.fullmatch(record_hash) is None:
            raise MobileNativeAuthUnavailable(
                "Recovery-fence publication returned invalid readback."
            )
        revoked_at = float(self._now())
        with self._users._lock:
            try:
                self._users._conn.execute("BEGIN IMMEDIATE")
                current = self._users._conn.execute(
                    "SELECT phase FROM mobile_auth_exchange_journals"
                    " WHERE exchange_id = ?",
                    (row["exchange_id"],),
                ).fetchone()
                if current is None:
                    raise MobileNativeAuthUnavailable(
                        "A native authentication journal disappeared."
                    )
                if str(current["phase"]) != "prepared":
                    self._users._conn.commit()
                    return True
                cursor = self._users._conn.execute(
                    "UPDATE mobile_api_tokens"
                    " SET revoked_at = COALESCE(revoked_at, ?), state = 'fenced',"
                    " fenced_at = COALESCE(fenced_at, ?)"
                    " WHERE selector = ? AND user_id = ? AND state = 'fenced'"
                    " AND fenced_at IS NOT NULL",
                    (
                        revoked_at,
                        revoked_at,
                        prior_selector,
                        row["user_id"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise MobileNativeAuthUnavailable(
                        "A rotation lost its prior local credential fence."
                    )
                self._users._conn.execute(
                    "UPDATE mobile_auth_exchange_journals"
                    " SET phase = 'prior_recovery_fenced', recovery_sequence = ?,"
                    " recovery_record_hash = ?, updated_at = ?"
                    " WHERE exchange_id = ? AND phase = 'prepared'",
                    (
                        sequence,
                        record_hash,
                        revoked_at,
                        row["exchange_id"],
                    ),
                )
                self._users._conn.commit()
            except Exception:
                if self._users._conn.in_transaction:
                    self._users._conn.rollback()
                raise
        return True

    def _activate_replacement(self, row: sqlite3.Row) -> None:
        activated_at = float(self._now())
        with self._users._lock:
            try:
                self._users._conn.execute("BEGIN IMMEDIATE")
                current = self._users._conn.execute(
                    "SELECT phase FROM mobile_auth_exchange_journals"
                    " WHERE exchange_id = ?",
                    (row["exchange_id"],),
                ).fetchone()
                if current is None:
                    raise MobileNativeAuthUnavailable(
                        "A native authentication journal disappeared."
                    )
                if str(current["phase"]) != "prior_recovery_fenced":
                    self._users._conn.commit()
                    return
                prior_selector = row["prior_selector"]
                if prior_selector is not None:
                    prior = self._users._conn.execute(
                        "SELECT revoked_at, state, fenced_at"
                        " FROM mobile_api_tokens WHERE selector = ?",
                        (prior_selector,),
                    ).fetchone()
                    if (
                        prior is None
                        or prior["revoked_at"] is None
                        or str(prior["state"]) != "fenced"
                        or prior["fenced_at"] is None
                    ):
                        raise MobileNativeAuthUnavailable(
                            "A replacement cannot activate before prior revocation."
                        )
                cursor = self._users._conn.execute(
                    "UPDATE mobile_api_tokens SET state = 'active'"
                    " WHERE selector = ? AND user_id = ? AND auth_epoch = ?"
                    " AND state = 'inactive' AND revoked_at IS NULL"
                    " AND fenced_at IS NULL AND expires_at > ?",
                    (
                        row["replacement_selector"],
                        row["user_id"],
                        row["auth_epoch"],
                        activated_at,
                    ),
                )
                if cursor.rowcount != 1:
                    raise MobileNativeAuthUnavailable(
                        "A replacement credential could not be activated."
                    )
                self._users._conn.execute(
                    "UPDATE mobile_auth_exchange_journals"
                    " SET phase = 'replacement_active', updated_at = ?"
                    " WHERE exchange_id = ? AND phase = 'prior_recovery_fenced'",
                    (activated_at, row["exchange_id"]),
                )
                self._users._conn.commit()
            except Exception:
                if self._users._conn.in_transaction:
                    self._users._conn.rollback()
                raise

    def _complete(self, row: sqlite3.Row) -> None:
        completed_at = float(self._now())
        with self._users._lock:
            try:
                self._users._conn.execute("BEGIN IMMEDIATE")
                current = self._users._conn.execute(
                    "SELECT * FROM mobile_auth_exchange_journals"
                    " WHERE exchange_id = ?",
                    (row["exchange_id"],),
                ).fetchone()
                if current is None:
                    raise MobileNativeAuthUnavailable(
                        "A native authentication journal disappeared."
                    )
                if str(current["phase"]) != "replacement_active":
                    self._users._conn.commit()
                    return
                self._users._conn.execute(
                    "INSERT OR IGNORE INTO mobile_auth_exchange_receipts"
                    " (exchange_id, purpose, challenge_id, replacement_selector,"
                    " idempotency_hmac_key_id, idempotency_hmac, request_hash,"
                    " completed_at, expires_at) VALUES (?, 'email', ?, ?, ?, ?, ?, ?, ?)",
                    (
                        current["exchange_id"],
                        current["challenge_id"],
                        current["replacement_selector"],
                        current["idempotency_hmac_key_id"],
                        current["idempotency_hmac"],
                        current["request_hash"],
                        completed_at,
                        current["expires_at"],
                    ),
                )
                self._users._conn.execute(
                    "UPDATE mobile_auth_exchange_journals SET phase = 'complete',"
                    " updated_at = ? WHERE exchange_id = ?"
                    " AND phase = 'replacement_active'",
                    (completed_at, current["exchange_id"]),
                )
                self._users._conn.commit()
            except Exception:
                if self._users._conn.in_transaction:
                    self._users._conn.rollback()
                raise

    def _advance(self, exchange_id: str) -> MobileAuthExchangeJournal | None:
        with self._operation_locks.hold(exchange_id):
            while True:
                row = self._row(exchange_id)
                phase = str(row["phase"])
                if phase == "complete":
                    return self._users._mobile_auth_journal_from_row(row)
                if phase == "prepared":
                    if row["prior_selector"] is None:
                        self._transition_without_recovery(
                            exchange_id, "prepared", "prior_recovery_fenced"
                        )
                        continue
                    if not self._publish_prior_revoke(row):
                        return None
                    continue
                if phase == "prior_recovery_fenced":
                    self._activate_replacement(row)
                    continue
                if phase == "replacement_active":
                    self._complete(row)
                    continue
                raise MobileNativeAuthUnavailable(
                    "A native authentication journal phase is invalid."
                )

    def resume_nonterminal(self) -> None:
        """Complete every durable exchange before workers or requests start."""

        for exchange_id in self._users.nonterminal_mobile_auth_exchange_ids():
            if self._keyring is None or self._ledger is None:
                raise MobileNativeAuthUnavailable(
                    "Pending native authentication recovery is not configured."
                )
            if self._advance(exchange_id) is None:
                raise MobileNativeAuthUnavailable(
                    "Pending native authentication recovery could not complete."
                )
