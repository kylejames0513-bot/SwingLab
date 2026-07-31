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
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ...web.users import (
    SHOPIFY_SYNC_FAILED,
    SHOPIFY_SYNC_PENDING,
    SHOPIFY_SYNC_REQUIRES_REVIEW,
    SHOPIFY_SYNC_SYNCED,
    ShopifySyncFencedError,
    User,
    UserStore,
)
from . import admin
from .identity import normalize_customer_id, normalize_shop_domain

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
    expected_generation: int | None = None,
) -> CustomerSyncResult:
    """Run one persisted synchronization attempt for ``user_id``."""

    try:
        started = users.start_shopify_sync(
            user_id,
            expected_generation=expected_generation,
        )
    except KeyError:
        return CustomerSyncResult(
            user_id=user_id,
            status=SHOPIFY_SYNC_REQUIRES_REVIEW,
            action="missing_user",
            safe_error="Local account no longer exists.",
        )
    except ShopifySyncFencedError:
        current = users.get(user_id)
        return CustomerSyncResult(
            user_id=user_id,
            status=(
                current.shopify_sync_status
                if current is not None
                else SHOPIFY_SYNC_REQUIRES_REVIEW
            ),
            action="superseded",
            safe_error="Shopify synchronization was privacy-fenced.",
        )
    user, attempt = started

    try:
        settings = validate_sync_settings(settings)
        with users.guard_shopify_sync_remote_write(
            user.id,
            attempt,
            generation=user.shopify_sync_generation,
        ) as allowed:
            customer_id = (
                find_or_create_shopify_customer(user, client)
                if allowed
                else None
            )
        if not allowed:
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
                    current.shopify_customer_id
                    if current is not None
                    else None
                ),
                attempt=attempt,
            )
        assert customer_id is not None
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


def operator_user_ref(user_id: str) -> str:
    """Return the PII-minimized reference used by protected operator tools."""

    return hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()[:12]


def find_user_by_operator_ref(
    users: UserStore,
    user_ref: str,
) -> User | None:
    """Resolve a protected short reference without accepting a raw user id."""

    expected = str(user_ref or "").strip().lower()
    if (
        len(expected) != 12
        or any(character not in "0123456789abcdef" for character in expected)
    ):
        return None
    matches: list[User] = []
    after: str | None = None
    while True:
        rows, after = users.list_shopify_sync_health(
            limit=1000,
            after=after,
        )
        matches.extend(
            user
            for user in rows
            if operator_user_ref(user.id) == expected
        )
        if after is None or len(matches) > 1:
            break
    return matches[0] if len(matches) == 1 else None


def _record_operator_status(
    users: UserStore,
    user: User,
    status: str,
    safe_error: str,
) -> str | None:
    """Persist one CAS-protected terminal operator result."""

    try:
        current, attempt = users.start_shopify_sync(user.id)
    except (KeyError, ShopifySyncFencedError):
        return None
    users.record_shopify_sync_failure(
        current.id,
        attempt,
        status,
        safe_error,
    )
    return attempt


def link_existing_shopify_customer(
    users: UserStore,
    user_id: str,
    customer_id: str,
) -> CustomerSyncResult:
    """Low-level link commit after a caller has verified remote identity.

    Use :func:`verify_and_link_existing_shopify_customer` from operator
    surfaces. This helper intentionally performs no network request.
    """

    try:
        started = users.start_shopify_sync(user_id)
    except KeyError:
        return CustomerSyncResult(
            user_id=user_id,
            status=SHOPIFY_SYNC_REQUIRES_REVIEW,
            action="missing_user",
            safe_error="Local account no longer exists.",
        )
    except ShopifySyncFencedError:
        current = users.get(user_id)
        return CustomerSyncResult(
            user_id=user_id,
            status=(
                current.shopify_sync_status
                if current is not None
                else SHOPIFY_SYNC_REQUIRES_REVIEW
            ),
            action="superseded",
            safe_error="Shopify synchronization was privacy-fenced.",
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


def verify_and_link_existing_shopify_customer(
    users: UserStore,
    user_id: str,
    customer_id: str,
    client: admin.ShopifyAdminClient,
) -> CustomerSyncResult:
    """Verify a remote email/id match, then commit the link transactionally.

    The supplied customer id is never trusted on its own. Shopify must return
    that same id for the local account's already-verified email before the
    database link is written. ``record_shopify_sync_success`` remains the
    final transaction-level duplicate/current-link guard.
    """

    user = users.get(user_id)
    if user is None:
        return CustomerSyncResult(
            user_id=user_id,
            status=SHOPIFY_SYNC_REQUIRES_REVIEW,
            action="missing_user",
            safe_error="Local account no longer exists.",
        )
    if not user.email_verified:
        safe_error = (
            "Verified email is required before resolving a Shopify identity."
        )
        attempt = _record_operator_status(
            users,
            user,
            SHOPIFY_SYNC_REQUIRES_REVIEW,
            safe_error,
        )
        return CustomerSyncResult(
            user_id=user.id,
            status=SHOPIFY_SYNC_REQUIRES_REVIEW,
            action="unverified_user",
            attempt=attempt,
            safe_error=safe_error,
        )
    try:
        expected_customer_id = normalize_customer_id(customer_id)
    except ValueError:
        expected_customer_id = None
    if expected_customer_id is None:
        safe_error = "Invalid Shopify customer identifier."
        attempt = _record_operator_status(
            users,
            user,
            SHOPIFY_SYNC_REQUIRES_REVIEW,
            safe_error,
        )
        return CustomerSyncResult(
            user_id=user.id,
            status=SHOPIFY_SYNC_REQUIRES_REVIEW,
            action="invalid_customer_id",
            attempt=attempt,
            safe_error=safe_error,
        )
    try:
        current_customer_id = normalize_customer_id(
            user.shopify_customer_id
        )
    except ValueError:
        safe_error = (
            "The stored Shopify customer identity is invalid."
        )
        attempt = _record_operator_status(
            users,
            user,
            SHOPIFY_SYNC_REQUIRES_REVIEW,
            safe_error,
        )
        return CustomerSyncResult(
            user_id=user.id,
            status=SHOPIFY_SYNC_REQUIRES_REVIEW,
            action="invalid_current_customer_id",
            attempt=attempt,
            safe_error=safe_error,
        )
    if (
        current_customer_id is not None
        and current_customer_id != expected_customer_id
    ):
        safe_error = (
            "The local account already has a different Shopify identity."
        )
        attempt = _record_operator_status(
            users,
            user,
            SHOPIFY_SYNC_REQUIRES_REVIEW,
            safe_error,
        )
        return CustomerSyncResult(
            user_id=user.id,
            status=SHOPIFY_SYNC_REQUIRES_REVIEW,
            action="current_link_conflict",
            attempt=attempt,
            safe_error=safe_error,
        )

    try:
        current, attempt = users.start_shopify_sync(
            user.id,
            expected_generation=user.shopify_sync_generation,
        )
    except (KeyError, ShopifySyncFencedError):
        latest = users.get(user.id)
        return CustomerSyncResult(
            user_id=user.id,
            status=(
                latest.shopify_sync_status
                if latest is not None
                else SHOPIFY_SYNC_REQUIRES_REVIEW
            ),
            action="superseded",
            safe_error="Shopify synchronization was privacy-fenced.",
        )

    try:
        with users.guard_shopify_sync_remote_write(
            current.id,
            attempt,
            generation=current.shopify_sync_generation,
        ) as allowed:
            remote_value = (
                client.find_customer_by_email(current.email)
                if allowed
                else None
            )
        if not allowed:
            return CustomerSyncResult(
                user_id=current.id,
                status=SHOPIFY_SYNC_REQUIRES_REVIEW,
                action="superseded",
                attempt=attempt,
                safe_error="Shopify synchronization was privacy-fenced.",
            )
        remote_customer_id = normalize_customer_id(remote_value)
    except ValueError:
        safe_error = (
            "Shopify returned an invalid customer identity."
        )
        users.record_shopify_sync_failure(
            current.id,
            attempt,
            SHOPIFY_SYNC_FAILED,
            safe_error,
        )
        return CustomerSyncResult(
            user_id=current.id,
            status=SHOPIFY_SYNC_FAILED,
            action="invalid_remote_customer_id",
            attempt=attempt,
            safe_error=safe_error,
        )
    except admin.ShopifyAdminUserError as exc:
        safe_error = _error_summary(
            exc,
            "Shopify rejected the customer verification request.",
        )
        users.record_shopify_sync_failure(
            current.id,
            attempt,
            SHOPIFY_SYNC_REQUIRES_REVIEW,
            safe_error,
        )
        return CustomerSyncResult(
            user_id=current.id,
            status=SHOPIFY_SYNC_REQUIRES_REVIEW,
            action="remote_verification_rejected",
            attempt=attempt,
            safe_error=safe_error,
        )
    except admin.ShopifyAdminError as exc:
        safe_error = _error_summary(
            exc,
            "Shopify customer verification failed.",
        )
        users.record_shopify_sync_failure(
            current.id,
            attempt,
            SHOPIFY_SYNC_FAILED,
            safe_error,
        )
        return CustomerSyncResult(
            user_id=current.id,
            status=SHOPIFY_SYNC_FAILED,
            action="remote_verification_failed",
            attempt=attempt,
            safe_error=safe_error,
        )

    if remote_customer_id is None:
        safe_error = (
            "No Shopify customer matched the verified local email."
        )
        users.record_shopify_sync_failure(
            current.id,
            attempt,
            SHOPIFY_SYNC_REQUIRES_REVIEW,
            safe_error,
        )
        return CustomerSyncResult(
            user_id=current.id,
            status=SHOPIFY_SYNC_REQUIRES_REVIEW,
            action="remote_customer_not_found",
            attempt=attempt,
            safe_error=safe_error,
        )
    if remote_customer_id != expected_customer_id:
        safe_error = (
            "The supplied Shopify customer did not match the verified "
            "local email."
        )
        users.record_shopify_sync_failure(
            current.id,
            attempt,
            SHOPIFY_SYNC_REQUIRES_REVIEW,
            safe_error,
        )
        return CustomerSyncResult(
            user_id=current.id,
            status=SHOPIFY_SYNC_REQUIRES_REVIEW,
            action="remote_identity_mismatch",
            attempt=attempt,
            safe_error=safe_error,
        )
    if not users.record_shopify_sync_success(
        current.id,
        attempt,
        expected_customer_id,
    ):
        latest = users.get(current.id)
        return CustomerSyncResult(
            user_id=current.id,
            status=(
                latest.shopify_sync_status
                if latest is not None
                else SHOPIFY_SYNC_REQUIRES_REVIEW
            ),
            action="superseded",
            attempt=attempt,
            safe_error="Shopify synchronization was privacy-fenced.",
        )
    return CustomerSyncResult(
        user_id=current.id,
        status=SHOPIFY_SYNC_SYNCED,
        action="linked_existing",
        customer_id=expected_customer_id,
        attempt=attempt,
    )


def update_linked_shopify_customer(
    users: UserStore,
    user_id: str,
    client: admin.ShopifyAdminClient,
    *,
    email: str | None = None,
    expected_generation: int | None = None,
) -> str:
    """Push an approved field under the durable privacy/attempt fence."""

    user = users.get(user_id)
    if user is None:
        raise ShopifySyncPolicyError("The local account no longer exists.")
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
    try:
        current, attempt = users.start_shopify_sync(
            user.id,
            expected_generation=(
                user.shopify_sync_generation
                if expected_generation is None
                else expected_generation
            ),
        )
    except ShopifySyncFencedError:
        raise ShopifySyncPolicyError(
            "Shopify synchronization was privacy-fenced."
        ) from None
    except KeyError:
        raise ShopifySyncPolicyError(
            "The local account no longer exists."
        ) from None
    try:
        with users.guard_shopify_sync_remote_write(
            current.id,
            attempt,
            generation=current.shopify_sync_generation,
        ) as allowed:
            if allowed:
                updated_id = normalize_customer_id(
                    client.set_customer(
                        email=email,
                        customer_id=customer_id,
                    )
                )
            else:
                updated_id = None
        if not allowed:
            raise ShopifySyncPolicyError(
                "Shopify synchronization was privacy-fenced."
            )
    except ShopifySyncPolicyError:
        # Redaction already invalidated the attempt token; this is a no-op in
        # that case and closes it for review in every other policy failure.
        users.record_shopify_sync_failure(
            current.id,
            attempt,
            SHOPIFY_SYNC_REQUIRES_REVIEW,
            "Shopify synchronization was privacy-fenced.",
        )
        raise
    except admin.ShopifyAdminError:
        users.record_shopify_sync_failure(
            current.id,
            attempt,
            SHOPIFY_SYNC_FAILED,
            "Shopify customer update failed.",
        )
        raise
    if updated_id != customer_id:
        users.record_shopify_sync_failure(
            current.id,
            attempt,
            SHOPIFY_SYNC_REQUIRES_REVIEW,
            "Shopify returned a conflicting customer identity.",
        )
        raise admin.ShopifyAdminTransportError(
            "Shopify Admin API returned a conflicting customer identifier.",
            retryable=False,
        )
    if not users.record_shopify_sync_success(
        current.id,
        attempt,
        customer_id,
    ):
        raise ShopifySyncPolicyError(
            "Shopify synchronization was privacy-fenced."
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
        client: admin.ShopifyAdminClient | None,
        settings: dict[str, Any],
        *,
        binding_db_path: str | Path | None = None,
        initial_binding_status: str | None = None,
        initial_binding_error: str | None = None,
        poll_seconds: float = 1.0,
        binding_retry_base_seconds: float = 5.0,
        binding_retry_max_seconds: float = 300.0,
        start: bool = True,
    ):
        self.users = users
        self.client = client
        self.settings = validate_sync_settings(settings)
        self.poll_seconds = max(0.05, float(poll_seconds))
        self.binding_retry_base_seconds = max(
            0.05, float(binding_retry_base_seconds)
        )
        self.binding_retry_max_seconds = max(
            self.binding_retry_base_seconds,
            float(binding_retry_max_seconds),
        )
        self.binding_db_path = (
            Path(binding_db_path) if binding_db_path is not None else None
        )
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._health_lock = threading.Lock()
        self._last_loop_at: float | None = None
        self._last_attempt_at: float | None = None
        self._binding_status = (
            str(initial_binding_status)
            if initial_binding_status
            else "unchecked"
        )
        self._binding_safe_error = (
            str(initial_binding_error)[:500]
            if initial_binding_error
            else "Shopify store binding has not been verified."
        )
        self._binding_last_checked_at: float | None = None
        self._binding_last_verified_at: float | None = None
        self._binding_store_ref: str | None = None
        self._binding_shop_ref: str | None = None
        self._local_binding_matched = False
        self._inspect_local_binding()
        if start:
            self.start()

    def _store_domain_invariant_holds(self, *, checked_at: float) -> bool:
        """Fail closed unless inbound, Admin, and binding stores can agree.

        ``SHOPIFY_STORE_DOMAIN`` is read for every gate rather than copied at
        startup.  Railway configuration drift therefore blocks a previously
        verified worker before it can enroll or process another customer.
        The persisted binding is checked separately below, including its exact
        authenticated Shop GID.
        """

        client = self.client
        if client is None:
            self._set_binding_state(
                "unverifiable",
                "Shopify Admin API authentication is unavailable.",
                checked_at=checked_at,
            )
            return False
        inbound_domain = normalize_shop_domain(
            os.environ.get("SHOPIFY_STORE_DOMAIN")
        )
        if (
            inbound_domain is None
            or inbound_domain != client.store_domain
        ):
            self._set_binding_state(
                "mismatch",
                "Inbound and outbound Shopify store configuration does not "
                "match.",
                checked_at=checked_at,
            )
            return False
        return True

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if not self._store_domain_invariant_holds(
            checked_at=time.time()
        ):
            return
        if (
            not self.binding_verified
            and not self.verify_store_binding()
            and self.binding_hard_blocked
        ):
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="shopify-customer-sync",
            daemon=True,
        )
        self._thread.start()

    def enqueue(self, user_id: str) -> bool:
        if not self._store_domain_invariant_holds(
            checked_at=time.time()
        ):
            return False
        if not self.enrollment_allowed:
            return False
        if self.users.mark_shopify_sync_pending(user_id) is None:
            return False
        self._wake.set()
        return True

    def run_once(self, *, now: float | None = None) -> int:
        observed_at = time.time() if now is None else float(now)
        with self._health_lock:
            self._last_loop_at = observed_at
        if not self._store_domain_invariant_holds(
            checked_at=observed_at
        ):
            return 0
        due = self.users.list_due_shopify_syncs(now=now, limit=20)
        if due and not self.verify_store_binding(now=observed_at):
            return 0
        client = self.client
        if due and client is None:
            self._set_binding_state(
                "unverifiable",
                "Shopify Admin API authentication is unavailable.",
                checked_at=observed_at,
            )
            return 0
        for user in due:
            with self._health_lock:
                self._last_attempt_at = observed_at
            sync_app_user_to_shopify(
                self.users,
                user.id,
                client,
                self.settings,
                expected_generation=user.shopify_sync_generation,
            )
        return len(due)

    def _set_binding_state(
        self,
        status: str,
        safe_error: str | None,
        *,
        checked_at: float,
        store_ref: str | None = None,
        shop_ref: str | None = None,
    ) -> None:
        with self._health_lock:
            self._binding_status = status
            self._binding_safe_error = (
                str(safe_error)[:500] if safe_error else None
            )
            self._binding_last_checked_at = checked_at
            if store_ref is not None:
                self._binding_store_ref = store_ref
            if shop_ref is not None:
                self._binding_shop_ref = shop_ref
            if status == "verified":
                self._binding_last_verified_at = checked_at
            if status in {"unbound", "mismatch"}:
                self._local_binding_matched = False

    def _inspect_local_binding(self) -> None:
        """Set the local gate without making an Admin API request."""

        checked_at = time.time()
        if not self._store_domain_invariant_holds(
            checked_at=checked_at
        ):
            return
        client = self.client
        if self.binding_db_path is None:
            self._set_binding_state(
                "unbound",
                "The database is not explicitly bound to a Shopify store.",
                checked_at=checked_at,
            )
            return
        if client is None:
            self._set_binding_state(
                "unverifiable",
                "Shopify Admin API authentication is unavailable.",
                checked_at=checked_at,
            )
            return
        from .backfill import BackfillSafetyError, preflight_backfill_database

        try:
            preflight = preflight_backfill_database(
                self.binding_db_path,
                client.store_domain,
            )
        except BackfillSafetyError as exc:
            self._set_binding_state(
                "unverifiable",
                exc.safe_summary,
                checked_at=checked_at,
            )
            return
        if preflight.binding_status in {"unbound", "incomplete"}:
            self._set_binding_state(
                "unbound",
                "The database is not explicitly bound to a Shopify store.",
                checked_at=checked_at,
                store_ref=preflight.store_ref,
                shop_ref=preflight.shop_ref,
            )
            return
        if preflight.binding_status == "mismatch":
            self._set_binding_state(
                "mismatch",
                "The configured Shopify store does not match the database "
                "binding.",
                checked_at=checked_at,
                store_ref=preflight.store_ref,
                shop_ref=preflight.shop_ref,
            )
            return
        with self._health_lock:
            self._local_binding_matched = True
            self._binding_store_ref = preflight.store_ref
            self._binding_shop_ref = preflight.shop_ref
            if self._binding_status not in {"unverifiable", "verified"}:
                self._binding_status = "unchecked"
                self._binding_safe_error = (
                    "Remote Shopify store identity has not been verified."
                )

    def verify_store_binding(self, *, now: float | None = None) -> bool:
        """Authenticate and match the exact persisted domain + Shop GID.

        Local unbound/domain-mismatch states are rejected before any network
        request. The binding is read-only here: only an explicit operator CLI
        command may create or upgrade it.
        """

        checked_at = time.time() if now is None else float(now)
        if not self._store_domain_invariant_holds(
            checked_at=checked_at
        ):
            return False
        if self.binding_db_path is None:
            self._set_binding_state(
                "unbound",
                "The database is not explicitly bound to a Shopify store.",
                checked_at=checked_at,
            )
            return False
        client = self.client
        if client is None:
            self._set_binding_state(
                "unverifiable",
                "Shopify Admin API authentication is unavailable.",
                checked_at=checked_at,
            )
            return False

        # Delayed import avoids the backfill module's dependency on this
        # synchronization module during normal package initialization.
        from .backfill import (
            BackfillSafetyError,
            require_matching_shopify_store_binding,
            preflight_backfill_database,
        )

        try:
            preflight = preflight_backfill_database(
                self.binding_db_path,
                client.store_domain,
            )
        except BackfillSafetyError as exc:
            self._set_binding_state(
                "unverifiable",
                exc.safe_summary,
                checked_at=checked_at,
            )
            return False
        if preflight.binding_status in {"unbound", "incomplete"}:
            self._set_binding_state(
                "unbound",
                "The database is not explicitly bound to a Shopify store.",
                checked_at=checked_at,
                store_ref=preflight.store_ref,
                shop_ref=preflight.shop_ref,
            )
            return False
        if preflight.binding_status == "mismatch":
            self._set_binding_state(
                "mismatch",
                "The configured Shopify store does not match the database "
                "binding.",
                checked_at=checked_at,
                store_ref=preflight.store_ref,
                shop_ref=preflight.shop_ref,
            )
            return False
        with self._health_lock:
            self._local_binding_matched = True

        try:
            shop_gid = client.verify_store_access()
            verified = require_matching_shopify_store_binding(
                self.binding_db_path,
                client.store_domain,
                shop_gid,
            )
        except admin.ShopifyAdminError:
            self._set_binding_state(
                "unverifiable",
                "Shopify store access could not be verified.",
                checked_at=checked_at,
                store_ref=preflight.store_ref,
                shop_ref=preflight.shop_ref,
            )
            return False
        except BackfillSafetyError as exc:
            status = getattr(exc, "status", "mismatch")
            self._set_binding_state(
                status if status in {"unbound", "mismatch"} else "mismatch",
                exc.safe_summary,
                checked_at=checked_at,
                store_ref=preflight.store_ref,
                shop_ref=preflight.shop_ref,
            )
            return False

        self._set_binding_state(
            "verified",
            None,
            checked_at=checked_at,
            store_ref=verified.store_ref,
            shop_ref=verified.shop_ref,
        )
        return True

    @property
    def worker_alive(self) -> bool:
        return bool(self._thread is not None and self._thread.is_alive())

    @property
    def last_loop_at(self) -> float | None:
        with self._health_lock:
            return self._last_loop_at

    @property
    def last_attempt_at(self) -> float | None:
        with self._health_lock:
            return self._last_attempt_at

    @property
    def binding_status(self) -> str:
        with self._health_lock:
            return self._binding_status

    @property
    def binding_verified(self) -> bool:
        return self.binding_status == "verified"

    @property
    def binding_blocked(self) -> bool:
        return not self.binding_verified

    @property
    def binding_hard_blocked(self) -> bool:
        return self.binding_status in {"unbound", "mismatch"}

    @property
    def enrollment_allowed(self) -> bool:
        if not self._store_domain_invariant_holds(
            checked_at=time.time()
        ):
            return False
        with self._health_lock:
            return bool(
                self.client is not None and self._local_binding_matched
            )

    def health_snapshot(
        self,
        *,
        now: float | None = None,
    ) -> dict[str, bool | int | float | str | None]:
        """Return PII-free worker liveness, backlog, and failure dimensions."""

        observed_at = time.time() if now is None else float(now)
        self._store_domain_invariant_holds(checked_at=observed_at)
        totals = self.users.shopify_sync_health_counts(now=observed_at)
        with self._health_lock:
            binding = {
                "binding_status": self._binding_status,
                "binding_blocked": self._binding_status != "verified",
                "binding_safe_error": self._binding_safe_error,
                "binding_last_checked_at": self._binding_last_checked_at,
                "binding_last_verified_at": self._binding_last_verified_at,
                "binding_store_ref": self._binding_store_ref,
                "binding_shop_ref": self._binding_shop_ref,
            }
        return {
            "worker_alive": self.worker_alive,
            "last_loop_at": self.last_loop_at,
            "last_attempt_at": self.last_attempt_at,
            **totals,
            **binding,
        }

    def _run(self) -> None:
        binding_retry = self.binding_retry_base_seconds
        while not self._stop.is_set():
            if not self._store_domain_invariant_holds(
                checked_at=time.time()
            ):
                return
            if not self.binding_verified:
                if self.binding_hard_blocked:
                    return
                self._wake.wait(binding_retry)
                self._wake.clear()
                if self._stop.is_set():
                    return
                if self.verify_store_binding():
                    binding_retry = self.binding_retry_base_seconds
                else:
                    binding_retry = min(
                        self.binding_retry_max_seconds,
                        binding_retry * 2,
                    )
                    continue
            try:
                processed = self.run_once()
            except Exception:
                # Keep the daemon alive across an unexpected database/runtime
                # failure, but never stringify an exception that could carry
                # customer data or credentials.
                logger.error("Shopify customer sync coordinator iteration failed.")
                processed = 0
            if self.binding_hard_blocked:
                return
            if self.binding_blocked:
                continue
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
