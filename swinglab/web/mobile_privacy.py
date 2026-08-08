"""Purpose-bound native privacy step-up start/exchange (Task 6, first slice).

This module owns the email-code step-up challenge and its single deterministic
token exchange, modeled on :mod:`swinglab.web.mobile_auth`. Export, history
reset, and account deletion (which will consume the minted token) are added in
later slices; nothing here authenticates an ordinary API request.
"""

from __future__ import annotations

import html
import sqlite3
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlencode

from . import mailer
from .throttle import KeyedThrottle
from .users import (
    StepUpChallengeLimit,
    StepUpChallengeRejected,
    StepUpExchangeConflict,
    StepUpTokenGrant,
    UserStore,
)


STEP_UP_WINDOW_SECONDS = 15 * 60
STEP_UP_STARTS_PER_SELECTOR = 5
STEP_UP_STARTS_PER_USER = 5
STEP_UP_STARTS_PER_IP = 20
STEP_UP_FAILED_EXCHANGES_PER_USER = 5
STEP_UP_FAILED_EXCHANGES_PER_IP = 20

_PURPOSE_LABELS = {
    "data_export": "export your data",
    "history_reset": "reset your swing history",
    "account_delete": "delete your account",
}


class MobileStepUpInvalidRequest(ValueError):
    """A step-up request failed public shape validation."""


class MobileStepUpRejected(RuntimeError):
    """A challenge/proof failure with one non-enumerating response."""


class MobileStepUpConflict(RuntimeError):
    """A consumed challenge or idempotency tuple conflicts."""


class MobileStepUpUnavailable(RuntimeError):
    """Step-up dependencies are unavailable."""


class MobileStepUpRateLimited(RuntimeError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Too many step-up attempts.")
        self.retry_after_seconds = max(1, min(900, int(retry_after_seconds)))


@dataclass(frozen=True)
class MobileStepUpStart:
    challenge_id: str
    expires_at: float


@dataclass(frozen=True)
class MobileStepUpExchange:
    step_up_token: str
    purpose: str
    expires_at: float


def _safe_client_ip(value: str | None) -> str:
    return value if isinstance(value, str) and value else "unavailable-client"


def _email_bodies(
    *, brand_name: str, public_base_url: str, challenge_id: str, purpose: str, code: str
) -> tuple[str, str, str]:
    grouped = code[:4] + "-" + code[4:]
    action = _PURPOSE_LABELS[purpose]
    query = urlencode({"challenge_id": challenge_id, "purpose": purpose})
    link = f"{public_base_url}/app/auth/callback?{query}" if public_base_url else ""
    subject = f"Confirm your request to {action} in {brand_name}"
    link_line = f"{link}\n\n" if link else ""
    text = (
        f"You asked to {action} in {brand_name}.\n\n"
        f"{link_line}"
        f"Enter this code on the device where you started, to confirm: {grouped}\n\n"
        "This code expires in 10 minutes. If you did not request this, ignore"
        " this email and no change is made."
    )
    safe_brand = html.escape(brand_name)
    safe_action = html.escape(action)
    safe_code = html.escape(grouped)
    link_html = (
        f'<p><a href="{html.escape(link, quote=True)}">Open {safe_brand}</a></p>'
        if link
        else ""
    )
    html_body = (
        "<!doctype html><html><body>"
        f"<p>You asked to {safe_action} in {safe_brand}.</p>"
        f"{link_html}"
        "<p>Enter this code on the device where you started, to confirm:"
        f" <strong>{safe_code}</strong></p>"
        "<p>This code expires in 10 minutes. If you did not request this,"
        " ignore this email and no change is made.</p>"
        "</body></html>"
    )
    return subject, text, html_body


class MobileStepUpService:
    """Own purpose-bound step-up challenge delivery and token exchange."""

    def __init__(
        self,
        users: UserStore,
        keyed_throttle: KeyedThrottle | None,
        *,
        enabled: bool,
        public_base_url: str | None,
        brand_name: str,
    ) -> None:
        self._users = users
        self._throttle = keyed_throttle
        self.enabled = bool(enabled)
        self._public_base_url = (public_base_url or "").rstrip("/")
        self._brand_name = brand_name
        self._now: Callable[[], float] = time.time

    def verify_enabled_readiness(self) -> None:
        if not self.enabled:
            return
        if self._throttle is None or self._users._mobile_state_hmac is None:
            raise MobileStepUpUnavailable(
                "Native privacy step-up is not configured."
            )

    def _purge_expired(self, now: float) -> None:
        if self._throttle is None:
            raise MobileStepUpUnavailable(
                "Native privacy step-up is not configured."
            )
        try:
            self._users.purge_expired_mobile_step_up_state(now=now)
            self._throttle.purge_expired(now=now)
        except (sqlite3.Error, RuntimeError) as exc:
            raise MobileStepUpUnavailable(
                "Native privacy step-up maintenance is unavailable."
            ) from exc

    def start(
        self,
        *,
        user_id: str,
        selector: str,
        auth_epoch: int,
        purpose: object,
        code_challenge: object,
        email: str | None,
        client_ip: str | None,
    ) -> MobileStepUpStart:
        if self._throttle is None:
            raise MobileStepUpUnavailable(
                "Native privacy step-up is not configured."
            )
        purpose_value = self._users._validate_step_up_purpose(purpose)
        ip = _safe_client_ip(client_ip)
        now = float(self._now())
        self._purge_expired(now)
        decision = self._throttle.consume_many(
            [
                (
                    "stepup-start-selector",
                    selector,
                    STEP_UP_STARTS_PER_SELECTOR,
                    STEP_UP_WINDOW_SECONDS,
                ),
                (
                    "stepup-start-user",
                    user_id,
                    STEP_UP_STARTS_PER_USER,
                    STEP_UP_WINDOW_SECONDS,
                ),
                (
                    "stepup-start-client-ip",
                    ip,
                    STEP_UP_STARTS_PER_IP,
                    STEP_UP_WINDOW_SECONDS,
                ),
            ],
            now=now,
        )
        if not decision.allowed:
            raise MobileStepUpRateLimited(decision.retry_after_seconds)
        try:
            challenge = self._users.begin_mobile_step_up(
                user_id=user_id,
                selector=selector,
                auth_epoch=auth_epoch,
                purpose=purpose_value,
                code_challenge=code_challenge,
                now=now,
            )
        except StepUpChallengeLimit as exc:
            raise MobileStepUpRateLimited(exc.retry_after_seconds) from exc
        except StepUpChallengeRejected as exc:
            raise MobileStepUpRejected("The step-up request is not eligible.") from exc
        except ValueError as exc:
            raise MobileStepUpInvalidRequest(str(exc)) from exc
        if challenge.send_required and email and mailer.enabled():
            subject, text, html_body = _email_bodies(
                brand_name=self._brand_name,
                public_base_url=self._public_base_url,
                challenge_id=challenge.challenge_id,
                purpose=challenge.purpose,
                code=challenge.email_code,
            )
            try:
                mailer.send(email, subject, text, html_body=html_body)
            except Exception:
                # Delivery state stays invisible to the API; an uncertain
                # provider outcome leaves the one-time challenge valid.
                pass
        return MobileStepUpStart(
            challenge_id=challenge.challenge_id,
            expires_at=challenge.expires_at,
        )

    def _debit_failed_exchange(
        self, *, user_id: str | None, client_ip: str, now: float
    ) -> None:
        if self._throttle is None:
            raise MobileStepUpUnavailable(
                "Native privacy step-up is not configured."
            )
        entries = [
            (
                "stepup-exchange-client-ip",
                client_ip,
                STEP_UP_FAILED_EXCHANGES_PER_IP,
                STEP_UP_WINDOW_SECONDS,
            )
        ]
        if user_id is not None:
            entries.append(
                (
                    "stepup-exchange-user",
                    user_id,
                    STEP_UP_FAILED_EXCHANGES_PER_USER,
                    STEP_UP_WINDOW_SECONDS,
                )
            )
        decision = self._throttle.consume_many(entries, now=now)
        if not decision.allowed:
            raise MobileStepUpRateLimited(decision.retry_after_seconds)

    def exchange(
        self,
        *,
        challenge_id: object,
        email_code: object,
        code_verifier: object,
        idempotency_key: object,
        client_ip: str | None,
    ) -> MobileStepUpExchange:
        if self._throttle is None:
            raise MobileStepUpUnavailable(
                "Native privacy step-up is not configured."
            )
        now = float(self._now())
        self._purge_expired(now)
        ip = _safe_client_ip(client_ip)
        user_id = (
            self._users.step_up_challenge_user_id(challenge_id)
            if isinstance(challenge_id, str)
            else None
        )
        try:
            grant: StepUpTokenGrant = self._users.prepare_mobile_step_up_exchange(
                challenge_id,
                email_code,
                code_verifier,
                idempotency_key,
                now=now,
            )
        except StepUpChallengeRejected as exc:
            self._debit_failed_exchange(user_id=user_id, client_ip=ip, now=now)
            raise MobileStepUpRejected("Invalid step-up challenge.") from exc
        except StepUpExchangeConflict as exc:
            self._debit_failed_exchange(user_id=user_id, client_ip=ip, now=now)
            raise MobileStepUpConflict("The step-up exchange conflicts.") from exc
        except ValueError as exc:
            raise MobileStepUpInvalidRequest(str(exc)) from exc
        return MobileStepUpExchange(
            step_up_token=grant.step_up_token,
            purpose=grant.purpose,
            expires_at=grant.expires_at,
        )
