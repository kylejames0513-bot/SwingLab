"""Default-deny store-review authentication and immutable native identity.

The production admission database belongs to the entitlements subsystem.  This
module owns only the backend boundary: strict native identity parsing, a closed
default-deny protocol, rate-limited challenge orchestration, and reuse of the
generation-1 crash-safe token exchange journal.
"""

from __future__ import annotations

import hmac
import math
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Mapping, Protocol
from urllib.parse import urlsplit

from fastapi import Request

from .mobile_auth import (
    AUTH_WINDOW_SECONDS,
    MobileAuthService,
    MobileNativeAuthConflict,
    MobileNativeAuthExchange,
    MobileNativeAuthInvalidRequest,
    MobileNativeAuthRateLimited,
    MobileNativeAuthRejected,
    MobileNativeAuthStart,
    MobileNativeAuthUnavailable,
    RecoveryFencePublisher,
    _safe_client_ip,
)
from .credential_mutations import CredentialMutationGuard
from .mobile_schema import MobileStateDomain, VersionedHMAC
from .throttle import KeyedThrottle
from .users import (
    MobileAPITokenLimitError,
    MobileAuthChallengeLimit,
    MobileAuthChallengeRejected,
    MobileAuthExchangeConflict,
    UserStore,
)


MOBILE_DEPLOYMENT_ENVIRONMENT_VARIABLE = (
    "CADDIEINSIGHT_MOBILE_DEPLOYMENT_ENVIRONMENT"
)
MOBILE_DEPLOYMENT_ENVIRONMENTS = ("development", "staging", "production")
APPLICATION_ID_POLICY_REVISION = 1
_APPLICATION_ID_POLICY: dict[str, tuple[str, ...]] = {
    "development": ("com.caddieinsight.app.dev",),
    "staging": (
        "com.caddieinsight.app",
        "com.caddieinsight.app.staging",
    ),
    "production": ("com.caddieinsight.app",),
}
_IDENTITY_HEADER_NAMES = {
    "x-caddieinsight-environment": "environment",
    "x-caddieinsight-platform": "platform",
    "x-caddieinsight-app-version": "app_version",
    "x-caddieinsight-app-build": "app_build",
    "x-caddieinsight-application-id": "application_id",
}
_VERSION = re.compile(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){1,2}")
_BUILD = re.compile(r"[1-9][0-9]{0,9}")
_APPLICATION_ID = re.compile(r"[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*){2,7}")
_ACCOUNT = re.compile(r"[A-Za-z0-9@._+\-]{1,160}")
_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def resolve_mobile_deployment_environment(
    environment: Mapping[str, str] | None = None,
) -> str:
    """Return the one server-owned closed deployment environment."""

    if environment is None:
        import os

        environment = os.environ
    value = environment.get(MOBILE_DEPLOYMENT_ENVIRONMENT_VARIABLE, "development")
    if not isinstance(value, str) or value not in MOBILE_DEPLOYMENT_ENVIRONMENTS:
        raise ValueError(
            f"{MOBILE_DEPLOYMENT_ENVIRONMENT_VARIABLE} must be development, "
            "staging, or production."
        )
    return value


def allowed_application_ids(environment: str) -> tuple[str, ...]:
    try:
        return _APPLICATION_ID_POLICY[environment]
    except KeyError as exc:
        raise ValueError("A closed mobile deployment environment is required.") from exc


def canonical_mobile_public_origin(value: str | None, environment: str) -> str | None:
    """Normalize PUBLIC_BASE_URL without consulting any request-derived input."""

    if environment not in MOBILE_DEPLOYMENT_ENVIRONMENTS:
        raise ValueError("A closed mobile deployment environment is required.")
    if value is None or value == "":
        if environment == "development":
            return None
        raise ValueError("PUBLIC_BASE_URL is required for staging and production.")
    if not isinstance(value, str) or value != value.strip() or any(
        character.isspace() for character in value
    ):
        raise ValueError("PUBLIC_BASE_URL must be one canonical HTTPS origin.")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError("PUBLIC_BASE_URL must be one canonical HTTPS origin.") from None
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or parsed.netloc.endswith(":")
    ):
        raise ValueError("PUBLIC_BASE_URL must be one canonical HTTPS origin.")
    rendered_host = f"[{hostname.lower()}]" if ":" in hostname else hostname.lower()
    suffix = "" if port in (None, 443) else f":{port}"
    return f"https://{rendered_host}{suffix}"


@dataclass(frozen=True)
class AppIdentityHeaders:
    environment: str
    platform: str
    app_version: str
    app_build: str
    application_id: str


def parse_app_identity_headers(
    request: Request, *, deployment_environment: str
) -> AppIdentityHeaders:
    """Parse exactly five duplicate-aware native headers before side effects."""

    raw_members: dict[str, list[bytes]] = {
        name: [] for name in _IDENTITY_HEADER_NAMES
    }
    for raw_name, raw_value in request.scope.get("headers", ()):  # ASGI preserves dupes
        try:
            name = raw_name.decode("ascii").lower()
        except (UnicodeDecodeError, AttributeError):
            continue
        if name in raw_members:
            raw_members[name].append(raw_value)
        elif name.startswith("x-caddieinsight-"):
            raise MobileNativeAuthInvalidRequest(
                "Unsupported CaddieInsight identity header."
            )
    parsed: dict[str, str] = {}
    for header_name, field_name in _IDENTITY_HEADER_NAMES.items():
        values = raw_members[header_name]
        if len(values) != 1:
            raise MobileNativeAuthInvalidRequest(
                "Exactly one immutable application identity is required."
            )
        try:
            value = values[0].decode("ascii")
        except (UnicodeDecodeError, AttributeError):
            raise MobileNativeAuthInvalidRequest(
                "The application identity is malformed."
            ) from None
        if (
            not value
            or value != value.strip()
            or "," in value
            or any(character.isspace() for character in value)
        ):
            raise MobileNativeAuthInvalidRequest(
                "The application identity is malformed."
            )
        parsed[field_name] = value

    if parsed["environment"] != deployment_environment:
        raise MobileNativeAuthInvalidRequest(
            "The application environment does not match this service."
        )
    if parsed["platform"] not in ("ios", "android"):
        raise MobileNativeAuthInvalidRequest("The application platform is unsupported.")
    if _VERSION.fullmatch(parsed["app_version"]) is None:
        raise MobileNativeAuthInvalidRequest("The application version is malformed.")
    if _BUILD.fullmatch(parsed["app_build"]) is None:
        raise MobileNativeAuthInvalidRequest("The application build is malformed.")
    if _APPLICATION_ID.fullmatch(parsed["application_id"]) is None:
        raise MobileNativeAuthInvalidRequest("The application ID is malformed.")
    if parsed["application_id"] not in allowed_application_ids(
        deployment_environment
    ):
        raise MobileNativeAuthInvalidRequest(
            "The application ID is not allowed in this environment."
        )
    return AppIdentityHeaders(**parsed)


@dataclass(frozen=True)
class ReviewAuthStartMatch:
    user_id: str


@dataclass(frozen=True)
class ReviewAuthChallengeBinding:
    challenge_id: str
    provider: str
    identity: AppIdentityHeaders
    matched_user_id: str | None
    account_hmac_key_id: str
    account_hmac: str
    code_challenge: str
    expires_at: float


@dataclass(frozen=True)
class ReviewAuthGrant:
    user_id: str
    provider: str
    credential_hmac_key_id: str
    credential_hmac: str
    lane_revision: int
    bearer_expires_at: float


@dataclass(frozen=True)
class ReviewBearerScope:
    user_id: str
    provider: str
    build: str
    expires_at: float
    credential_hmac_key_id: str
    credential_hmac: str
    lane_revision: int


class ReviewAuthAdmission(Protocol):
    """Entitlements-owned lane and dedicated credential proof boundary."""

    def any_lane_active(self) -> bool: ...

    def match_start(
        self,
        *,
        provider: str,
        account: str,
        identity: AppIdentityHeaders,
        now: float,
    ) -> ReviewAuthStartMatch | None: ...

    def verify_exchange(
        self,
        *,
        challenge: ReviewAuthChallengeBinding,
        password: str,
        identity: AppIdentityHeaders,
        now: float,
    ) -> ReviewAuthGrant | None: ...

    def recheck(self, scope: ReviewBearerScope, *, now: float) -> bool: ...


class DenyReviewAuthAdmission:
    """Shipped composition: no production review lane until Task 5 injects one."""

    def any_lane_active(self) -> bool:
        return False

    def match_start(self, **_kwargs) -> ReviewAuthStartMatch | None:
        return None

    def verify_exchange(self, **_kwargs) -> ReviewAuthGrant | None:
        return None

    def recheck(self, _scope: ReviewBearerScope, *, now: float) -> bool:
        return False


@dataclass(frozen=True)
class ReviewAuthSettings:
    starts_per_ip: int
    starts_per_account: int
    failed_exchanges_per_ip: int
    failed_exchanges_per_account: int
    live_challenges_per_ip: int
    live_challenges_per_account: int


_SETTING_DEFAULTS = {
    "review_auth_starts_per_15_minutes_per_ip": 20,
    "review_auth_starts_per_15_minutes_per_account": 5,
    "review_auth_failed_exchanges_per_15_minutes_per_ip": 20,
    "review_auth_failed_exchanges_per_15_minutes_per_account": 10,
    "review_auth_live_challenges_per_ip": 20,
    "review_auth_live_challenges_per_account": 3,
}


def validate_review_auth_settings(web: Mapping[str, object]) -> ReviewAuthSettings:
    values: dict[str, int] = {}
    for name, default in _SETTING_DEFAULTS.items():
        value = web.get(name, default)
        if type(value) is not int or not 1 <= value <= 100:
            raise ValueError(f"web.{name} must be an integer from 1 to 100.")
        values[name] = value
    return ReviewAuthSettings(
        starts_per_ip=values["review_auth_starts_per_15_minutes_per_ip"],
        starts_per_account=values[
            "review_auth_starts_per_15_minutes_per_account"
        ],
        failed_exchanges_per_ip=values[
            "review_auth_failed_exchanges_per_15_minutes_per_ip"
        ],
        failed_exchanges_per_account=values[
            "review_auth_failed_exchanges_per_15_minutes_per_account"
        ],
        live_challenges_per_ip=values["review_auth_live_challenges_per_ip"],
        live_challenges_per_account=values[
            "review_auth_live_challenges_per_account"
        ],
    )


def _account(value: object) -> str:
    if not isinstance(value, str) or _ACCOUNT.fullmatch(value) is None:
        raise MobileNativeAuthInvalidRequest("Invalid review account.")
    return value


class ReviewAuthService:
    """Orchestrate review challenges without owning provider lane state."""

    def __init__(
        self,
        users: UserStore,
        throttle: KeyedThrottle | None,
        email_exchange_service: MobileAuthService,
        credential_guard: CredentialMutationGuard,
        *,
        admission: ReviewAuthAdmission,
        keyring: VersionedHMAC | None,
        recovery_fence_ledger: RecoveryFencePublisher | None,
        settings: ReviewAuthSettings,
        activated_at_startup: bool,
    ) -> None:
        if type(activated_at_startup) is not bool:
            raise TypeError("Review activation must be a startup-owned boolean.")
        self._users = users
        self._throttle = throttle
        self._email_exchange_service = email_exchange_service
        self._guard = credential_guard
        self.admission = admission
        self._keyring = keyring
        self._ledger = recovery_fence_ledger
        self.settings = settings
        self._activated_at_startup = activated_at_startup
        self._now = time.time

    def available(self) -> bool:
        # Activation is deliberately one-way for a process lifetime. A lane
        # that was closed during startup cannot open after the recovery gate
        # was skipped; activation requires a fresh, recovery-validated start.
        if not self._activated_at_startup:
            return False
        try:
            return self.admission.any_lane_active() is True
        except Exception as exc:
            raise MobileNativeAuthUnavailable(
                "Review authentication admission is unavailable."
            ) from exc

    def verify_enabled_recovery_readiness(self) -> None:
        if not self.available():
            return
        if self._keyring is None or self._throttle is None or self._ledger is None:
            raise MobileNativeAuthUnavailable(
                "Review authentication recovery is not configured."
            )
        try:
            snapshot = self._ledger.load_chain_snapshot()
        except Exception as exc:
            raise MobileNativeAuthUnavailable(
                "Review authentication recovery is not ready."
            ) from exc
        records = getattr(snapshot, "records", ())
        first = records[0] if isinstance(records, tuple) and records else None
        kind = getattr(first, "kind", None)
        if getattr(kind, "value", kind) != "cutover_baseline":
            raise MobileNativeAuthUnavailable(
                "Review authentication recovery baseline is invalid."
            )

    def _require_dependencies(self) -> tuple[KeyedThrottle, VersionedHMAC]:
        if self._throttle is None or self._keyring is None:
            raise MobileNativeAuthUnavailable(
                "Review authentication is not configured."
            )
        return self._throttle, self._keyring

    @staticmethod
    def _rate_account_key(key_id: str, digest: str) -> str:
        return f"{key_id}:{digest}"

    def start(
        self,
        *,
        provider: object,
        account: object,
        identity: AppIdentityHeaders,
        code_challenge: object,
        installation_id: object,
        device_label: object,
        client_ip: str | None,
    ) -> MobileNativeAuthStart:
        throttle, keyring = self._require_dependencies()
        if provider not in ("apple", "google"):
            raise MobileNativeAuthInvalidRequest("Invalid review provider.")
        normalized_account = _account(account)
        try:
            normalized_code_challenge = (
                self._users._validate_mobile_auth_code_challenge(code_challenge)
            )
            normalized_installation = (
                self._users._normalize_mobile_auth_installation(installation_id)
            )
            normalized_label = self._users._normalize_mobile_api_token_label(
                device_label
            )
        except ValueError as exc:
            raise MobileNativeAuthInvalidRequest(str(exc)) from exc
        now = float(self._now())
        self.purge_expired(now=now)
        ip = _safe_client_ip(client_ip)
        account_key_id, account_hmac = keyring.digest(
            MobileStateDomain.REVIEW_AUTH_ACCOUNT, normalized_account
        )
        account_rate_key = self._rate_account_key(account_key_id, account_hmac)
        decision = throttle.consume_many(
            [
                ("review-auth-start-ip", ip, self.settings.starts_per_ip, AUTH_WINDOW_SECONDS),
                (
                    "review-auth-start-account",
                    account_rate_key,
                    self.settings.starts_per_account,
                    AUTH_WINDOW_SECONDS,
                ),
            ],
            now=now,
        )
        if not decision.allowed:
            raise MobileNativeAuthRateLimited(decision.retry_after_seconds)
        try:
            match = self.admission.match_start(
                provider=provider,
                account=normalized_account,
                identity=identity,
                now=now,
            )
        except Exception as exc:
            raise MobileNativeAuthUnavailable(
                "Review authentication admission is unavailable."
            ) from exc
        matched_user_id = (
            match.user_id
            if isinstance(match, ReviewAuthStartMatch)
            and isinstance(match.user_id, str)
            and bool(match.user_id)
            else None
        )
        try:
            challenge = self._users.begin_mobile_review_signin(
                provider=provider,
                identity=identity,
                account=normalized_account,
                matched_user_id=matched_user_id,
                code_challenge=normalized_code_challenge,
                installation_id=normalized_installation,
                device_label=normalized_label,
                client_ip=ip,
                live_challenges_per_ip=self.settings.live_challenges_per_ip,
                live_challenges_per_account=self.settings.live_challenges_per_account,
                now=now,
            )
        except MobileAuthChallengeLimit as exc:
            raise MobileNativeAuthRateLimited(exc.retry_after_seconds) from exc
        except ValueError as exc:
            raise MobileNativeAuthInvalidRequest(str(exc)) from exc
        return MobileNativeAuthStart(challenge.challenge_id, challenge.expires_at)

    def _debit_failure(
        self,
        *,
        client_ip: str,
        challenge: ReviewAuthChallengeBinding | None,
        now: float,
    ) -> None:
        throttle, _ = self._require_dependencies()
        entries = [
            (
                "review-auth-exchange-ip",
                client_ip,
                self.settings.failed_exchanges_per_ip,
                AUTH_WINDOW_SECONDS,
            )
        ]
        if challenge is not None:
            entries.append(
                (
                    "review-auth-exchange-account",
                    self._rate_account_key(
                        challenge.account_hmac_key_id, challenge.account_hmac
                    ),
                    self.settings.failed_exchanges_per_account,
                    AUTH_WINDOW_SECONDS,
                )
            )
        decision = throttle.consume_many(entries, now=now)
        if not decision.allowed:
            raise MobileNativeAuthRateLimited(decision.retry_after_seconds)

    @staticmethod
    def _validated_grant(
        grant: object,
        *,
        challenge: ReviewAuthChallengeBinding,
        identity: AppIdentityHeaders,
        now: float,
    ) -> ReviewAuthGrant | None:
        if not isinstance(grant, ReviewAuthGrant):
            return None
        if (
            grant.user_id != challenge.matched_user_id
            or grant.provider != challenge.provider
            or not isinstance(grant.user_id, str)
            or not grant.user_id
            or not isinstance(grant.credential_hmac_key_id, str)
            or _KEY_ID.fullmatch(grant.credential_hmac_key_id) is None
            or not isinstance(grant.credential_hmac, str)
            or _SHA256.fullmatch(grant.credential_hmac) is None
            or isinstance(grant.lane_revision, bool)
            or not isinstance(grant.lane_revision, int)
            or grant.lane_revision < 1
            or isinstance(grant.bearer_expires_at, bool)
            or not isinstance(grant.bearer_expires_at, (int, float))
            or not math.isfinite(grant.bearer_expires_at)
            or not now < grant.bearer_expires_at <= now + 86400
            or identity != challenge.identity
        ):
            return None
        return grant

    def exchange(
        self,
        *,
        challenge_id: object,
        password: object,
        code_verifier: object,
        idempotency_key: object,
        identity: AppIdentityHeaders,
        client_ip: str | None,
    ) -> MobileNativeAuthExchange:
        now = float(self._now())
        self.purge_expired(now=now)
        ip = _safe_client_ip(client_ip)
        challenge = self._users.mobile_review_auth_challenge(challenge_id)
        try:
            self._users._mobile_auth_idempotency_bytes(idempotency_key)
        except ValueError as exc:
            raise MobileNativeAuthInvalidRequest(str(exc)) from exc
        verifier = self._users._validate_mobile_auth_verifier(code_verifier)
        if (
            challenge is None
            or not isinstance(password, str)
            or not password
            or len(password) > 1024
        ):
            self._debit_failure(client_ip=ip, challenge=challenge, now=now)
            raise MobileNativeAuthRejected("Invalid review authentication.")
        if verifier is None or not hmac.compare_digest(
            self._users._mobile_auth_pkce_challenge(verifier),
            challenge.code_challenge,
        ):
            self._users.record_mobile_review_signin_failure(
                challenge.challenge_id, now=now
            )
            self._debit_failure(client_ip=ip, challenge=challenge, now=now)
            raise MobileNativeAuthRejected("Invalid review authentication.")
        try:
            candidate = self.admission.verify_exchange(
                challenge=challenge,
                password=password,
                identity=identity,
                now=now,
            )
        except Exception as exc:
            raise MobileNativeAuthUnavailable(
                "Review authentication admission is unavailable."
            ) from exc
        grant = self._validated_grant(
            candidate, challenge=challenge, identity=identity, now=now
        )
        if grant is None:
            self._users.record_mobile_review_signin_failure(
                challenge.challenge_id, now=now
            )
            self._debit_failure(client_ip=ip, challenge=challenge, now=now)
            raise MobileNativeAuthRejected("Invalid review authentication.")
        try:
            journal = self._users.prepare_mobile_review_signin_exchange(
                challenge.challenge_id,
                password,
                code_verifier,
                idempotency_key,
                grant=grant,
                now=now,
            )
        except MobileAuthChallengeRejected as exc:
            self._debit_failure(client_ip=ip, challenge=challenge, now=now)
            raise MobileNativeAuthRejected("Invalid review authentication.") from exc
        except MobileAuthExchangeConflict as exc:
            self._debit_failure(client_ip=ip, challenge=challenge, now=now)
            raise MobileNativeAuthConflict(
                "The review authentication exchange conflicts."
            ) from exc
        except MobileAPITokenLimitError:
            raise
        except ValueError as exc:
            raise MobileNativeAuthInvalidRequest(str(exc)) from exc
        advanced = self._email_exchange_service.advance_exchange(journal.exchange_id)
        if advanced is None:
            return MobileNativeAuthExchange(exchange_id=journal.exchange_id, pending=True)
        try:
            access_token, expires_at = self._users.recover_mobile_exchange_credential(
                advanced, code_verifier
            )
        except MobileAuthChallengeRejected as exc:
            raise MobileNativeAuthConflict(
                "The review authentication exchange conflicts."
            ) from exc
        return MobileNativeAuthExchange(
            exchange_id=journal.exchange_id,
            pending=False,
            access_token=access_token,
            expires_at=expires_at,
        )

    def purge_expired(self, *, now: float | None = None) -> None:
        observed_at = float(self._now() if now is None else now)
        try:
            revocable: list[str] = []
            for selector in self._users.expired_mobile_review_token_selectors(
                now=observed_at
            ):
                close = self._guard.close_selector(selector)
                if close.drain(timeout_seconds=0.25):
                    revocable.append(selector)
            self._users.purge_expired_mobile_review_auth_state(
                now=observed_at,
                revocable_selectors=tuple(revocable),
            )
            if self._throttle is not None:
                self._throttle.purge_expired(now=observed_at)
        except (sqlite3.Error, RuntimeError) as exc:
            raise MobileNativeAuthUnavailable(
                "Review authentication maintenance is unavailable."
            ) from exc


def review_scope_is_active(
    admission: ReviewAuthAdmission,
    principal,
    *,
    now: float | None = None,
) -> bool:
    """Recheck a durable review token scope without rebuilding its identity."""

    if principal.review_provider is None:
        return True
    observed_at = time.time() if now is None else float(now)
    if (
        principal.review_build is None
        or principal.review_expires_at is None
        or principal.review_expires_at <= observed_at
        or principal.review_credential_hmac_key_id is None
        or principal.review_credential_hmac is None
        or principal.review_lane_revision is None
    ):
        return False
    scope = ReviewBearerScope(
        user_id=principal.user.id,
        provider=principal.review_provider,
        build=principal.review_build,
        expires_at=principal.review_expires_at,
        credential_hmac_key_id=principal.review_credential_hmac_key_id,
        credential_hmac=principal.review_credential_hmac,
        lane_revision=principal.review_lane_revision,
    )
    try:
        return admission.recheck(scope, now=observed_at) is True
    except Exception:
        return False
