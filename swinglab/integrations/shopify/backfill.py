"""Dry-run-first, restartable Shopify customer backfill."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

from ...web.users import (
    SHOPIFY_SYNC_REQUIRES_REVIEW,
    User,
    UserStore,
)
from . import admin
from .customer_sync import CustomerSyncResult, sync_app_user_to_shopify


def _user_ref(user_id: str) -> str:
    """Stable operator correlation value that does not expose the local id."""

    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class BackfillItem:
    user_ref: str
    outcome: str
    status: str
    safe_error: str | None = None


@dataclass
class BackfillSummary:
    dry_run: bool
    scanned: int = 0
    linked: int = 0
    would_link: int = 0
    would_create: int = 0
    skipped: int = 0
    requires_review: int = 0
    failed: int = 0
    next_cursor: str | None = None
    items: list[BackfillItem] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        # ``asdict`` preserves the public field names and converts nested
        # dataclasses, which keeps CLI JSON stable without exposing emails.
        return payload


def _review_item(user: User, message: str) -> BackfillItem:
    return BackfillItem(
        user_ref=_user_ref(user.id),
        outcome="requires_review",
        status=SHOPIFY_SYNC_REQUIRES_REVIEW,
        safe_error=message,
    )


def _from_sync_result(user: User, result: CustomerSyncResult) -> BackfillItem:
    return BackfillItem(
        user_ref=_user_ref(user.id),
        outcome=result.action,
        status=result.status,
        safe_error=result.safe_error,
    )


def run_backfill_batch(
    users: UserStore,
    client: admin.ShopifyAdminClient,
    *,
    batch_size: int = 50,
    after: str | None = None,
    dry_run: bool = True,
    settings: dict[str, Any] | None = None,
) -> BackfillSummary:
    """Inspect or apply one deterministic batch.

    ``next_cursor`` can be supplied as ``after`` on the next invocation.
    Dry-run performs only ``customerByIdentifier`` Shopify reads and does not
    change user links or sync state. Constructing ``UserStore`` can still
    apply the application's normal additive schema migration before this
    function is called.
    """

    if not 1 <= int(batch_size) <= 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    rows, next_cursor = users.list_shopify_backfill(
        limit=int(batch_size), after=after
    )
    summary = BackfillSummary(
        dry_run=bool(dry_run),
        scanned=len(rows),
        next_cursor=next_cursor,
    )

    for user in rows:
        if user.shopify_customer_id:
            summary.skipped += 1
            summary.items.append(
                BackfillItem(
                    user_ref=_user_ref(user.id),
                    outcome="already_linked",
                    status=user.shopify_sync_status,
                )
            )
            continue
        if user.shopify_identity_locked:
            summary.requires_review += 1
            summary.items.append(
                _review_item(
                    user,
                    "A previous Shopify identity requires manual review.",
                )
            )
            continue
        if not user.email_verified:
            summary.requires_review += 1
            summary.items.append(
                _review_item(
                    user,
                    "Verified email is required before Shopify linking.",
                )
            )
            continue

        if not dry_run:
            users.mark_shopify_sync_pending(user.id)
            result = sync_app_user_to_shopify(
                users, user.id, client, settings or {}
            )
            summary.items.append(_from_sync_result(user, result))
            if result.status == "synced":
                summary.linked += 1
            elif result.status == SHOPIFY_SYNC_REQUIRES_REVIEW:
                summary.requires_review += 1
            else:
                summary.failed += 1
            continue

        try:
            existing = client.find_customer_by_email(user.email)
        except admin.ShopifyAdminUserError as exc:
            summary.requires_review += 1
            summary.items.append(
                _review_item(
                    user,
                    str(
                        getattr(
                            exc,
                            "safe_summary",
                            "Shopify rejected the customer lookup.",
                        )
                    )[:500],
                )
            )
        except admin.ShopifyAdminError as exc:
            summary.failed += 1
            summary.items.append(
                BackfillItem(
                    user_ref=_user_ref(user.id),
                    outcome="lookup_failed",
                    status="failed",
                    safe_error=str(
                        getattr(
                            exc,
                            "safe_summary",
                            "Shopify customer lookup failed.",
                        )
                    )[:500],
                )
            )
        else:
            if existing:
                summary.would_link += 1
                outcome = "would_link_existing"
            else:
                summary.would_create += 1
                outcome = "would_create"
            summary.items.append(
                BackfillItem(
                    user_ref=_user_ref(user.id),
                    outcome=outcome,
                    status="dry_run",
                )
            )
    return summary
