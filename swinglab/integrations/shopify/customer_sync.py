"""Outbound CaddieInsight-to-Shopify customer synchronization.

The existing :mod:`swinglab.web.shopify_billing` module remains the inbound
webhook and entitlement bridge.  This module owns the opposite direction:
after a verified CaddieInsight account is created, upsert the corresponding
Shopify customer through the server-only Admin GraphQL API and persist the
durable customer id.

Synchronization is intentionally independent from the registration
transaction.  The local account always wins that race; Shopify failures are
recorded for retry and never roll the account back.
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from ...web.users import (
    SHOPIFY_SYNC_FAILED,
    SHOPIFY_SYNC_PENDING,
    SHOPIFY_SYNC_REQUIRES_REVIEW,
    SHOPIFY_SYNC_SYNCED,
    User,
    UserStore,
)
from . import admin
from .identity import normalize_customer_id

logger = logging.getLogger("swinglab.integrations.shopify.customer_sync")


class ShopifySyncPolicyError(RuntimeError):
    """A local identity rule prevented an unsafe Shopify match."""

    def __init__(self, safe_summary: str):
        self.safe_summary = safe_summary
        super().__init__(safe_summary)


@dataclass(frozen=True)
class CustomerSyncResult:
    """Structured internal result; both IDs are protected customer data."""

    user_id: str
    status: str
    action: str
    customer_id: str | None = None
    attempt: str | None = None
    retryable: bool = False
    safe_error: str | None = None


def _error_summary(exc: BaseException, fallback: str) -> str:
    """Return only the exception's explicitly safe summary.

    Admin API exception messages can contain customer input.  The client
    exposes ``safe_summary`` after redaction; an unknown exception never gets
    stringified into the database or logs.
    """

    summary = getattr(exc, "safe_summary", None)
    return str(summary or fallback)[:500]


def _log_outcome(level: int, status: str, action: str) -> None:
    """Emit operational dimensions without customer identifiers or input."""

    logger.log(
        level,
        "Shopify customer sync outcome.",
        extra={
            "shopify_sync_status": status,
            "shopify_sync_action": action,
        },
    )


def validate_sync_settings(
    settings: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return bounded, typed settings or a PII-safe configuration error."""

    merged: dict[str, Any] = {
        "enabled": False,
        "auto_sync_new_users": True,
        "request_timeout_seconds": 10.0,
        "max_attempts": 5,
        "retry_base_seconds": 30.0,
        "retry_max_seconds": 3600.0,
        "retry_jitter_ratio": 0.2,
    }
    merged.update(settings or {})
    try:
        if not isinstance(merged["enabled"], bool):
            raise ValueError
        if not isinstance(merged["auto_sync_new_users"], bool):
            raise ValueError
        if (
            isinstance(merged["max_attempts"], bool)
            or not isinstance(merged["max_attempts"], int)
            or not 1 <= merged["max_attempts"] <= 100
        ):
            raise ValueError
        timeout = float(merged["request_timeout_seconds"])
        base = float(merged["retry_base_seconds"])
        maximum = float(merged["retry_max_seconds"])
        jitter = float(merged["retry_jitter_ratio"])
        if not all(
            math.isfinite(value)
            for value in (timeout, base, maximum, jitter)
        ):
            raise ValueError
        if not 0 < timeout <= 120:
            raise ValueError
        if not 0 < base <= maximum <= 604_800:
            raise ValueError
        if not 0 <= jitter <= 0.5:
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise admin.ShopifyAdminConfigurationError(
            "Shopify customer synchronization settings are invalid."
        ) from None
    merged.update(
        request_timeout_seconds=timeout,
        retry_base_seconds=base,
        retry_max_seconds=maximum,
        retry_jitter_ratio=jitter,
    )
    return merged


def _next_retry_at(
    attempt_count: int,
    settings: dict[str, Any],
    now: float,
    *,
    jitter_seed: str,
) -> float | None:
    try:
        max_attempts = int(settings["max_attempts"])
        if attempt_count >= max_attempts:
            return None
        base = float(settings["retry_base_seconds"])
        maximum = float(settings["retry_max_seconds"])
        ratio = float(settings.get("retry_jitter_ratio") or 0.0)
        delay = min(maximum, base * (2 ** max(0, attempt_count - 1)))
        if ratio:
            digest = hashlib.sha256(jitter_seed.encode("utf-8")).digest()
            unit = int.from_bytes(digest[:8], "big") / ((1 << 64) - 1)
            delay *= (1.0 - ratio) + (2.0 * ratio * unit)
            delay = min(maximum, delay)
        return now + delay
    except (KeyError, TypeError, ValueError, OverflowError):
        # Invalid runtime settings must never strand a row as a hot-looping
        # pending attempt. Startup validation normally prevents this path.
        return None


def find_or_create_shopify_customer(
    user: User,
    client: admin.ShopifyAdminClient,
) -> str:
    """Return the durable canonical customer id for a safe app identity.

    A previously linked id is authoritative.  Email is used only for an
    initially unlinked, verified account that has never had a different
    Shopify identity.
    """

    existing = normalize_customer_id(user.shopify_customer_id)
    if existing:
        return existing
    if user.shopify_identity_locked:
        raise ShopifySyncPolicyError(
            "A previous Shopify identity requires manual review."
        )
    if not user.email_verified:
        raise ShopifySyncPolicyError(
            "Verified email is required before Shopify linking."
        )
    customer_id = normalize_customer_id(
        client.set_customer(email=user.email)
    )
    if customer_id is None:
        raise admin.ShopifyAdminTransportError(
            "Shopify Admin API returned an invalid customer identifier.",
            retryable=False,
        )
    return customer_id


def sync_app_user_to_shopify(
    users: UserStore,
    user_id: str,
    client: admin.ShopifyAdminClient,
    settings: dict[str, Any] | None = None,
    *,
    clock: Callable[[], float] = time.time,
) -> CustomerSyncResult:
    """Run one persisted synchronization attempt for ``user_id``."""

    try:
        started = users.start_shopify_sync(user_id)
    except KeyError:
        return CustomerSyncResult(
            user_id=user_id,
            status=SHOPIFY_SYNC_REQUIRES_REVIEW,
            action="missing_user",
            safe_error="Local account no longer exists.",
        )
    user, attempt = started

    try:
        settings = validate_sync_settings(settings)
        customer_id = find_or_create_shopify_customer(user, client)
        recorded = users.record_shopify_sync_success(
            user.id, attempt, customer_id
        )
        if not recorded:
            current = users.get(user.id)
            return CustomerSyncResult(
                user_id=user.id,
                status=(
                    current.shopify_sync_status
                    if current is not None
                    else SHOPIFY_SYNC_REQUIRES_REVIEW
                ),
                action="superseded",
                customer_id=(
                    current.shopify_customer_id if current is not None else None
                ),
                attempt=attempt,
            )
        action = "already_linked" if user.shopify_customer_id else "upserted"
        _log_outcome(logging.INFO, SHOPIFY_SYNC_SYNCED, action)
        return CustomerSyncResult(
            user_id=user.id,
            status=SHOPIFY_SYNC_SYNCED,
            action=action,
            customer_id=customer_id,
            attempt=attempt,
        )
    except (ShopifySyncPolicyError, admin.ShopifyAdminUserError) as exc:
        safe_error = _error_summary(
            exc, "Shopify rejected the customer data."
        )
        users.record_shopify_sync_failure(
            user.id,
            attempt,
            SHOPIFY_SYNC_REQUIRES_REVIEW,
            safe_error,
        )
        _log_outcome(
            logging.WARNING,
            SHOPIFY_SYNC_REQUIRES_REVIEW,
            "requires_review",
        )
        return CustomerSyncResult(
            user_id=user.id,
            status=SHOPIFY_SYNC_REQUIRES_REVIEW,
            action="requires_review",
            attempt=attempt,
            safe_error=safe_error,
        )
    except admin.ShopifyAdminTransportError as exc:
        safe_error = _error_summary(
            exc, "Shopify is temporarily unavailable."
        )
        now = clock()
        next_attempt_at = (
            _next_retry_at(
                user.shopify_sync_attempts,
                settings,
                now,
                jitter_seed=f"{user.id}:{user.shopify_sync_attempts}",
            )
            if exc.retryable
            else None
        )
        retry_after = getattr(exc, "retry_after_seconds", None)
        if next_attempt_at is not None and retry_after is not None:
            try:
                retry_after_value = float(retry_after)
                provider_retry_at = (
                    now + max(0.0, retry_after_value)
                    if math.isfinite(retry_after_value)
                    else next_attempt_at
                )
            except (TypeError, ValueError, OverflowError):
                provider_retry_at = next_attempt_at
            next_attempt_at = max(next_attempt_at, provider_retry_at)
        users.record_shopify_sync_failure(
            user.id,
            attempt,
            SHOPIFY_SYNC_FAILED,
            safe_error,
            next_attempt_at=next_attempt_at,
        )
        action = "retry_scheduled" if next_attempt_at else "failed"
        _log_outcome(logging.WARNING, SHOPIFY_SYNC_FAILED, action)
        return CustomerSyncResult(
            user_id=user.id,
            status=SHOPIFY_SYNC_FAILED,
            action=action,
            attempt=attempt,
            retryable=next_attempt_at is not None,
            safe_error=safe_error,
        )
    except admin.ShopifyAdminConfigurationError as exc:
        safe_error = _error_summary(
            exc, "Shopify customer synchronization is not configured."
        )
        users.record_shopify_sync_failure(
            user.id,
            attempt,
            SHOPIFY_SYNC_FAILED,
            safe_error,
        )
        _log_outcome(
            logging.ERROR,
            SHOPIFY_SYNC_FAILED,
            "configuration_error",
        )
        return CustomerSyncResult(
            user_id=user.id,
            status=SHOPIFY_SYNC_FAILED,
            action="configuration_error",
            attempt=attempt,
            safe_error=safe_error,
        )
    except Exception:
        # The class name is enough for operator correlation; never persist or
        # log an arbitrary exception message that could contain a GraphQL
        # request, token, or customer input.
        safe_error = "Unexpected Shopify synchronization error."
        users.record_shopify_sync_failure(
            user.id,
            attempt,
            SHOPIFY_SYNC_FAILED,
            safe_error,
        )
        logger.error("Unexpected Shopify customer sync failure.")
        return CustomerSyncResult(
            user_id=user.id,
            status=SHOPIFY_SYNC_FAILED,
            action="internal_error",
            attempt=attempt,
            safe_error=safe_error,
        )


def retry_shopify_customer_sync(
    users: UserStore,
    user_id: str,
    client: admin.ShopifyAdminClient,
    settings: dict[str, Any] | None = None,
) -> CustomerSyncResult:
    """Make a failed/review-required account eligible and retry it now."""

    if users.mark_shopify_sync_pending(user_id) is None:
        return CustomerSyncResult(
            user_id=user_id,
            status=SHOPIFY_SYNC_REQUIRES_REVIEW,
            action="missing_user",
            safe_error="Local account no longer exists.",
        )
    return sync_app_user_to_shopify(users, user_id, client, settings)


def link_existing_shopify_customer(
    users: UserStore,
    user_id: str,
    customer_id: str,
) -> CustomerSyncResult:
    """Trusted operator path for a manually verified identity conflict."""

    users.mark_shopify_sync_pending(user_id)
    try:
        started = users.start_shopify_sync(user_id)
    except KeyError:
        return CustomerSyncResult(
            user_id=user_id,
            status=SHOPIFY_SYNC_REQUIRES_REVIEW,
            action="missing_user",
            safe_error="Local account no longer exists.",
        )
    user, attempt = started
    try:
        normalized = normalize_customer_id(customer_id)
    except ValueError:
        normalized = None
    if not normalized:
        users.record_shopify_sync_failure(
            user.id,
            attempt,
            SHOPIFY_SYNC_REQUIRES_REVIEW,
            "Invalid Shopify customer identifier.",
        )
        return CustomerSyncResult(
            user_id=user.id,
            status=SHOPIFY_SYNC_REQUIRES_REVIEW,
            action="invalid_customer_id",
            attempt=attempt,
            safe_error="Invalid Shopify customer identifier.",
        )
    if not users.record_shopify_sync_success(user.id, attempt, normalized):
        current = users.get(user.id)
        return CustomerSyncResult(
            user_id=user.id,
            status=(
                current.shopify_sync_status
                if current is not None
                else SHOPIFY_SYNC_REQUIRES_REVIEW
            ),
            action="conflict",
            attempt=attempt,
            safe_error=(
                current.shopify_sync_error if current is not None else None
            ),
        )
    return CustomerSyncResult(
        user_id=user.id,
        status=SHOPIFY_SYNC_SYNCED,
        action="linked_existing",
        customer_id=normalized,
        attempt=attempt,
    )


def update_linked_shopify_customer(
    user: User,
    client: admin.ShopifyAdminClient,
    *,
    email: str | None = None,
) -> str:
    """Push only explicitly approved shared fields to an existing customer."""

    customer_id = normalize_customer_id(user.shopify_customer_id)
    if not customer_id:
        raise ShopifySyncPolicyError(
            "The app account is not linked to Shopify."
        )
    if email is None:
        return customer_id
    if not user.email_verified:
        raise ShopifySyncPolicyError(
            "Verified email is required before updating Shopify."
        )
    if email.strip().lower() != user.email.strip().lower():
        raise ShopifySyncPolicyError(
            "A new email must be verified in CaddieInsight before updating Shopify."
        )
    updated_id = normalize_customer_id(
        client.set_customer(email=email, customer_id=customer_id)
    )
    if updated_id != customer_id:
        raise admin.ShopifyAdminTransportError(
            "Shopify Admin API returned a conflicting customer identifier.",
            retryable=False,
        )
    return customer_id


def process_shopify_webhook(*args: Any, **kwargs: Any) -> None:
    """Compatibility entry point for the existing signed webhook bridge."""

    from ...web.shopify_billing import handle_webhook

    handle_webhook(*args, **kwargs)


class ShopifyCustomerSyncCoordinator:
    """Single-replica, persisted retry coordinator.

    User rows are the durable outbox.  The worker is intentionally separate
    from the CPU-heavy video executor, wakes on registration/manual retry,
    and scans pending/due rows on startup so a process crash does not lose
    work.
    """

    def __init__(
        self,
        users: UserStore,
        client: admin.ShopifyAdminClient,
        settings: dict[str, Any],
        *,
        poll_seconds: float = 1.0,
        start: bool = True,
    ):
        self.users = users
        self.client = client
        self.settings = validate_sync_settings(settings)
        self.poll_seconds = max(0.05, float(poll_seconds))
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        if start:
            self.start()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="shopify-customer-sync",
            daemon=True,
        )
        self._thread.start()

    def enqueue(self, user_id: str) -> bool:
        if self.users.mark_shopify_sync_pending(user_id) is None:
            return False
        self._wake.set()
        return True

    def run_once(self, *, now: float | None = None) -> int:
        due = self.users.list_due_shopify_syncs(now=now, limit=20)
        for user in due:
            sync_app_user_to_shopify(
                self.users,
                user.id,
                self.client,
                self.settings,
            )
        return len(due)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                processed = self.run_once()
            except Exception:
                # Keep the daemon alive across an unexpected database/runtime
                # failure, but never stringify an exception that could carry
                # customer data or credentials.
                logger.error("Shopify customer sync coordinator iteration failed.")
                processed = 0
            if processed:
                continue
            self._wake.wait(self.poll_seconds)
            self._wake.clear()

    def shutdown(self, wait: bool = True) -> None:
        self._stop.set()
        self._wake.set()
        if (
            wait
            and self._thread is not None
            and self._thread is not threading.current_thread()
        ):
            self._thread.join(timeout=5)
