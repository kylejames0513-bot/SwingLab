"""Device-bound Expo push registration persistence and sign-out cleanup."""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from typing import Mapping

from ..api.auth import MobileAuthContext
from ..api.contracts import (
    PushPreferencesRequest,
    PushRegistrationRequest,
    PushRegistrationResponse,
)
from .credential_mutations import (
    CredentialMutationGuard,
    CredentialMutationRejected,
)
from .mobile_schema import MOBILE_STATE_SCHEMA_GENERATION
from .review_auth import AppIdentityHeaders
from .users import UserStore


EXPO_PROJECT_ID_ENV = "CADDIEINSIGHT_EXPO_PROJECT_ID"
_EXPO_TOKEN = re.compile(r"^ExponentPushToken\[[A-Za-z0-9_-]{16,4096}\]$")
_CANONICAL_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class MobilePushUnauthorized(Exception):
    """The authenticated mobile credential is no longer admissible."""


class MobilePushInvalidRequest(ValueError):
    """The registration body failed closed validation."""


class MobilePushNotRegistered(LookupError):
    """Preferences were requested for a selector with no registration."""


@dataclass(frozen=True)
class MobilePushSettings:
    enabled: bool
    expo_project_id: str
    send_envelope_seconds: int = 30
    cutover_clock_skew_seconds: int = 60


def _canonical_uuid(value: str, *, label: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a canonical UUID.") from exc
    rendered = str(parsed)
    if _CANONICAL_UUID.fullmatch(rendered) is None or value != rendered:
        raise ValueError(f"{label} must be a canonical UUID.")
    return rendered


def resolve_mobile_push_expo_project_id(
    configured: object,
    *,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Return the effective public Expo project UUID, or blank when unset."""

    env = os.environ if environment is None else environment
    override = env.get(EXPO_PROJECT_ID_ENV, "")
    if isinstance(override, str) and override.strip():
        return _canonical_uuid(override.strip(), label=EXPO_PROJECT_ID_ENV)
    if configured in (None, ""):
        return ""
    if not isinstance(configured, str):
        raise ValueError("web.mobile_push_expo_project_id must be a UUID string.")
    return _canonical_uuid(
        configured.strip(), label="web.mobile_push_expo_project_id"
    )


def load_mobile_push_settings(
    web: Mapping[str, object],
    *,
    environment: Mapping[str, str] | None = None,
) -> MobilePushSettings:
    enabled = bool(web.get("mobile_push_enabled"))
    project_id = resolve_mobile_push_expo_project_id(
        web.get("mobile_push_expo_project_id", ""),
        environment=environment,
    )
    envelope = web.get("mobile_push_send_envelope_seconds", 30)
    skew = web.get("mobile_push_cutover_clock_skew_seconds", 60)
    if type(envelope) is not int or isinstance(envelope, bool):
        raise ValueError("web.mobile_push_send_envelope_seconds must be an int.")
    if type(skew) is not int or isinstance(skew, bool):
        raise ValueError(
            "web.mobile_push_cutover_clock_skew_seconds must be an int."
        )
    if enabled:
        if not project_id:
            raise ValueError(
                "web.mobile_push_expo_project_id must be a canonical UUID when "
                "mobile_push_enabled is true."
            )
        if not 5 <= envelope <= 60:
            raise ValueError(
                "web.mobile_push_send_envelope_seconds must be between 5 and 60."
            )
        if not 30 <= skew <= 300:
            raise ValueError(
                "web.mobile_push_cutover_clock_skew_seconds must be between "
                "30 and 300."
            )
    return MobilePushSettings(
        enabled=enabled,
        expo_project_id=project_id,
        send_envelope_seconds=int(envelope),
        cutover_clock_skew_seconds=int(skew),
    )


def validate_expo_push_token(token: str) -> str:
    if not isinstance(token, str) or _EXPO_TOKEN.fullmatch(token) is None:
        raise MobilePushInvalidRequest("The Expo push token is malformed.")
    return token


@dataclass(frozen=True)
class _RegistrationRow:
    platform: str
    app_version: str
    practice_reminders_enabled: bool
    registered_at: float

    def response(self) -> PushRegistrationResponse:
        return PushRegistrationResponse(
            platform=self.platform,  # type: ignore[arg-type]
            app_version=self.app_version,
            practice_reminders_enabled=self.practice_reminders_enabled,
            registered_at=float(self.registered_at),
        )


class PushRegistrationService:
    """Upsert, prefer, and remove the current selector's Expo registration."""

    extension_id = "push-registration"

    def __init__(
        self,
        users: UserStore,
        settings: MobilePushSettings,
        *,
        deployment_environment: str,
        guard: CredentialMutationGuard,
        clock=None,
    ) -> None:
        self._users = users
        self._settings = settings
        self._environment = deployment_environment
        self._guard = guard
        self._clock = clock or (lambda: __import__("time").time())

    def close_for_sign_out(
        self,
        *,
        users: UserStore,
        operation_id: str,
        user_id: str,
        selector: str,
    ) -> bool:
        del operation_id
        with users._lock:
            try:
                users._conn.execute("BEGIN IMMEDIATE")
                users._conn.execute(
                    "DELETE FROM mobile_push_registrations"
                    " WHERE user_id = ? AND selector = ?",
                    (user_id, selector),
                )
                users._conn.execute(
                    "UPDATE mobile_push_outbox SET status = 'dead',"
                    " lease_owner = NULL, lease_expires_at = NULL,"
                    " updated_at = ? WHERE user_id = ? AND selector = ?"
                    " AND status IN ('pending', 'leased')",
                    (__import__("time").time(), user_id, selector),
                )
                users._conn.commit()
            except Exception:
                if users._conn.in_transaction:
                    users._conn.rollback()
                raise
        return True

    def _require_enabled(self) -> None:
        if not self._settings.enabled:
            raise RuntimeError("Mobile push is not enabled.")

    def _row_from_sqlite(self, row) -> _RegistrationRow:
        return _RegistrationRow(
            platform=str(row["platform"]),
            app_version=str(row["app_version"]),
            practice_reminders_enabled=bool(row["practice_reminders_enabled"]),
            registered_at=float(row["registered_at"]),
        )

    def _validate_request_identity(
        self,
        request: PushRegistrationRequest,
        identity: AppIdentityHeaders,
    ) -> None:
        if request.platform != identity.platform:
            raise MobilePushInvalidRequest(
                "The application platform does not match the request body."
            )
        if request.app_version != identity.app_version:
            raise MobilePushInvalidRequest(
                "The application version does not match the request body."
            )
        try:
            project_id = _canonical_uuid(
                request.expo_project_id, label="expo_project_id"
            )
        except ValueError as exc:
            raise MobilePushInvalidRequest(str(exc)) from exc
        if project_id != self._settings.expo_project_id:
            raise MobilePushInvalidRequest(
                "The Expo project ID does not match this service."
            )
        validate_expo_push_token(request.token)

    def register(
        self,
        context: MobileAuthContext,
        request: PushRegistrationRequest,
        identity: AppIdentityHeaders,
    ) -> PushRegistrationResponse:
        self._require_enabled()
        self._validate_request_identity(request, identity)
        try:
            lease = self._guard.admit(context)
        except CredentialMutationRejected as exc:
            raise MobilePushUnauthorized(
                "Invalid mobile access token."
            ) from exc

        timestamp = float(self._clock())
        project_id = self._settings.expo_project_id
        try:
            with self._users._lock:
                try:
                    self._users._conn.execute("BEGIN IMMEDIATE")
                    lease.validate_locked(self._users, now=timestamp)
                    current = self._users._conn.execute(
                        "SELECT token, platform, app_version, app_build,"
                        " application_id, practice_reminders_enabled,"
                        " registered_at, activation_generation"
                        " FROM mobile_push_registrations"
                        " WHERE environment = ? AND expo_project_id = ?"
                        " AND selector = ?",
                        (self._environment, project_id, context.selector),
                    ).fetchone()
                    if (
                        current is not None
                        and str(current["token"]) == request.token
                        and str(current["platform"]) == identity.platform
                        and str(current["app_version"]) == identity.app_version
                        and str(current["app_build"]) == identity.app_build
                        and str(current["application_id"])
                        == identity.application_id
                        and bool(current["practice_reminders_enabled"])
                        == bool(request.practice_reminders_enabled)
                    ):
                        row = self._row_from_sqlite(current)
                        self._users._conn.commit()
                        return row.response()

                    # Token takeover: another selector owning this token loses it.
                    self._users._conn.execute(
                        "DELETE FROM mobile_push_registrations"
                        " WHERE environment = ? AND expo_project_id = ?"
                        " AND provider = ? AND token = ?"
                        " AND selector != ?",
                        (
                            self._environment,
                            project_id,
                            request.provider,
                            request.token,
                            context.selector,
                        ),
                    )

                    watermark = self._users._conn.execute(
                        "SELECT push_not_before"
                        " FROM mobile_push_activation_watermarks"
                        " WHERE environment = ? AND expo_project_id = ?",
                        (self._environment, project_id),
                    ).fetchone()
                    if watermark is None:
                        self._users._conn.execute(
                            "INSERT INTO mobile_push_activation_watermarks"
                            " (environment, expo_project_id, push_not_before)"
                            " VALUES (?, ?, ?)",
                            (self._environment, project_id, timestamp),
                        )

                    if current is not None and str(current["token"]) == request.token:
                        # Same token: preserve immutable registered_at.
                        self._users._conn.execute(
                            "UPDATE mobile_push_registrations SET"
                            " user_id = ?, application_id = ?, platform = ?,"
                            " app_version = ?, app_build = ?,"
                            " practice_reminders_enabled = ?, updated_at = ?"
                            " WHERE environment = ? AND expo_project_id = ?"
                            " AND selector = ?",
                            (
                                context.user.id,
                                identity.application_id,
                                identity.platform,
                                identity.app_version,
                                identity.app_build,
                                1 if request.practice_reminders_enabled else 0,
                                timestamp,
                                self._environment,
                                project_id,
                                context.selector,
                            ),
                        )
                        registered_at = float(current["registered_at"])
                    else:
                        # Selector replacement or first registration.
                        self._users._conn.execute(
                            "DELETE FROM mobile_push_registrations"
                            " WHERE environment = ? AND expo_project_id = ?"
                            " AND selector = ?",
                            (self._environment, project_id, context.selector),
                        )
                        self._users._conn.execute(
                            "INSERT INTO mobile_push_registrations ("
                            " environment, expo_project_id, provider, token,"
                            " user_id, selector, application_id, platform,"
                            " app_version, app_build, activation_generation,"
                            " practice_reminders_enabled, registered_at,"
                            " updated_at"
                            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                self._environment,
                                project_id,
                                request.provider,
                                request.token,
                                context.user.id,
                                context.selector,
                                identity.application_id,
                                identity.platform,
                                identity.app_version,
                                identity.app_build,
                                MOBILE_STATE_SCHEMA_GENERATION,
                                1 if request.practice_reminders_enabled else 0,
                                timestamp,
                                timestamp,
                            ),
                        )
                        registered_at = timestamp
                    row = _RegistrationRow(
                        platform=identity.platform,
                        app_version=identity.app_version,
                        practice_reminders_enabled=bool(
                            request.practice_reminders_enabled
                        ),
                        registered_at=registered_at,
                    )
                    self._users._conn.commit()
                    return row.response()
                except Exception:
                    if self._users._conn.in_transaction:
                        self._users._conn.rollback()
                    raise
        except CredentialMutationRejected as exc:
            raise MobilePushUnauthorized(
                "Invalid mobile access token."
            ) from exc
        finally:
            lease.release()

    def update_preferences(
        self,
        context: MobileAuthContext,
        request: PushPreferencesRequest,
    ) -> PushRegistrationResponse:
        self._require_enabled()
        try:
            lease = self._guard.admit(context)
        except CredentialMutationRejected as exc:
            raise MobilePushUnauthorized(
                "Invalid mobile access token."
            ) from exc

        timestamp = float(self._clock())
        project_id = self._settings.expo_project_id
        desired = 1 if request.practice_reminders_enabled else 0
        try:
            with self._users._lock:
                try:
                    self._users._conn.execute("BEGIN IMMEDIATE")
                    lease.validate_locked(self._users, now=timestamp)
                    current = self._users._conn.execute(
                        "SELECT platform, app_version,"
                        " practice_reminders_enabled, registered_at"
                        " FROM mobile_push_registrations"
                        " WHERE environment = ? AND expo_project_id = ?"
                        " AND selector = ?",
                        (self._environment, project_id, context.selector),
                    ).fetchone()
                    if current is None:
                        raise MobilePushNotRegistered(
                            "No push registration exists for this device."
                        )
                    if int(current["practice_reminders_enabled"]) == desired:
                        row = self._row_from_sqlite(current)
                        self._users._conn.commit()
                        return row.response()
                    self._users._conn.execute(
                        "UPDATE mobile_push_registrations"
                        " SET practice_reminders_enabled = ?, updated_at = ?"
                        " WHERE environment = ? AND expo_project_id = ?"
                        " AND selector = ?",
                        (
                            desired,
                            timestamp,
                            self._environment,
                            project_id,
                            context.selector,
                        ),
                    )
                    row = _RegistrationRow(
                        platform=str(current["platform"]),
                        app_version=str(current["app_version"]),
                        practice_reminders_enabled=bool(desired),
                        registered_at=float(current["registered_at"]),
                    )
                    self._users._conn.commit()
                    return row.response()
                except Exception:
                    if self._users._conn.in_transaction:
                        self._users._conn.rollback()
                    raise
        except CredentialMutationRejected as exc:
            raise MobilePushUnauthorized(
                "Invalid mobile access token."
            ) from exc
        finally:
            lease.release()

    def unregister(self, context: MobileAuthContext) -> None:
        self._require_enabled()
        try:
            lease = self._guard.admit(context)
        except CredentialMutationRejected as exc:
            raise MobilePushUnauthorized(
                "Invalid mobile access token."
            ) from exc

        timestamp = float(self._clock())
        project_id = self._settings.expo_project_id
        try:
            with self._users._lock:
                try:
                    self._users._conn.execute("BEGIN IMMEDIATE")
                    lease.validate_locked(self._users, now=timestamp)
                    self._users._conn.execute(
                        "DELETE FROM mobile_push_registrations"
                        " WHERE environment = ? AND expo_project_id = ?"
                        " AND selector = ?",
                        (self._environment, project_id, context.selector),
                    )
                    self._users._conn.commit()
                except Exception:
                    if self._users._conn.in_transaction:
                        self._users._conn.rollback()
                    raise
        except CredentialMutationRejected as exc:
            raise MobilePushUnauthorized(
                "Invalid mobile access token."
            ) from exc
        finally:
            lease.release()
