"""Selling CaddieInsight Pro through the Shopify store.

Entirely environment-driven and safely inert until configured:

    SHOPIFY_STORE_DOMAIN     yourstore.myshopify.com (shared with shop.py)
    SHOPIFY_WEBHOOK_SECRET   webhook signing secret from Shopify admin
                             (Settings -> Notifications -> Webhooks)

With those unset, ``enabled()`` is False and Pro upgrades fall back to
Stripe (billing.py) or "coming soon". When both Shopify and Stripe are
configured, the pricing and account pages send buyers to the Shopify store
— one checkout for gear and memberships alike.

Setup, on the Shopify side:

1. Create a product for Pro access. Each variant's SKU maps to a number of
   days of Pro in ``billing.shopify_skus`` (config.yaml) — e.g. variant
   ``SL-PRO-1MO`` grants 31 days. Prices live on the product in Shopify,
   never in code (the same rule as Stripe prices and gear prices).
2. In Settings -> Notifications -> Webhooks, add ``orders/paid``,
   ``orders/cancelled``, and ``refunds/create`` webhooks pointing at
   ``https://<your-app>/webhooks/shopify`` and copy the signing secret
   shown on that page into ``SHOPIFY_WEBHOOK_SECRET``.

Flow: checkout happens on the Shopify storefront; Shopify calls the
webhook, which is the ONLY place access changes (signed webhooks can't be
faked, storefront redirects can). A paid order first follows the stable
Shopify customer id to a linked account, then falls back to normalized
checkout email; if no account exists yet, the days are parked in
``pro_grants`` and claimed automatically the first time that email signs
up or logs in. Recording and granting happen in one SQLite transaction.
Replayed webhooks are no-ops (orders are recorded by id), and a cancelled
order takes back exactly the days it granted. A refund whose line items
identify a configured Pro SKU follows the same whole-order reversal
semantics; a gear-only or unattributable refund cannot revoke Pro. A
cancellation or attributable refund received before its paid event leaves a
tombstone, so out-of-order delivery cannot grant already-reversed access.

Shopify's Subscriptions app also creates a new paid order for each successful
billing cycle, so the same SKU grant path keeps extending Pro. The bridge
still grants fixed SKU terms (31 and 365 days), however; exact alignment to
Shopify's calendar-month/calendar-year contract dates requires authoritative
subscription billing-cycle data that is not present in an ``orders/paid``
payload and is deliberately not inferred here.

The same ``orders/paid`` webhook also feeds the gear ledger: every line
item that is NOT a Pro SKU is recorded in ``gear_orders`` (order id, sku,
title, quantity, normalized email) with the same per-order replay
idempotence as the Pro ledger, and ``orders/cancelled`` marks those rows
cancelled. That is what makes the gear-attach KPI (swinglab.kpis)
measurable — Pro processing itself is unchanged by it.

Account sync (customer webhooks, same endpoint and secret)
----------------------------------------------------------

Add ``customers/create``, ``customers/update``, and ``customers/delete``
webhooks pointing at the same ``/webhooks/shopify`` URL and a customer
created on the store automatically exists in the app:

- ``customers/create`` / ``customers/update`` upsert a "store account":
  no user for the (normalized) email -> a passwordless stub is created
  with ``shopify_customer_id`` and ``source='shopify'``; a user exists ->
  only the ``shopify_customer_id`` link is set/refreshed. The customer id
  is the stable identity: an unclaimed store-only stub can follow a changed
  Shopify email in place, while a claimed account keeps its verified app
  login email rather than splitting into a second user or auto-merging
  accounts. Replays are idempotent.
- ``customers/delete`` deletes the user ONLY when it is an unclaimed stub
  (never claimed by password or code sign-in, and no analyses); any Pro
  days it still carried are parked in ``pro_grants`` so a later signup
  keeps what was bought. A claimed account merely loses its
  ``shopify_customer_id`` link — store-side deletion never destroys app
  data. A plain-delete tombstone keeps the internal former-account mapping
  needed for the same customer's late paid event while preventing a
  delayed create/update webhook from recreating the removed store identity.
- ``customers/redact`` (GDPR) follows the delete semantics and further
  erases the shopify-sourced profile fields (link + source) on claimed
  accounts, drops any parked purchase for a deleted stub's email, and
  severs the former-account mapping.
- ``customers/data_request`` and ``shop/redact`` (GDPR) are acknowledged
  with a 200 and logged. A complete export/shop-wide erasure workflow is
  not implemented here and remains separate privacy-compliance work.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime

from ..config import Config
from ..integrations.shopify.identity import normalize_customer_id
from .users import UserStore

logger = logging.getLogger("swinglab.web.shopify")

PAID_TOPICS = ("orders/paid", "ORDERS_PAID")
CANCELLED_TOPICS = ("orders/cancelled", "ORDERS_CANCELLED")
REFUND_TOPICS = ("refunds/create", "REFUNDS_CREATE")
CUSTOMER_UPSERT_TOPICS = (
    "customers/create", "customers/update",
    "CUSTOMERS_CREATE", "CUSTOMERS_UPDATE",
)
CUSTOMER_DELETE_TOPICS = ("customers/delete", "CUSTOMERS_DELETE")
CUSTOMER_REDACT_TOPICS = ("customers/redact", "CUSTOMERS_REDACT")
ACK_TOPICS = (
    "customers/data_request", "CUSTOMERS_DATA_REQUEST",
    "shop/redact", "SHOP_REDACT",
)


def enabled() -> bool:
    return bool(
        os.environ.get("SHOPIFY_STORE_DOMAIN")
        and os.environ.get("SHOPIFY_WEBHOOK_SECRET")
    )


def buy_url(cfg: Config) -> str:
    """The Pro product's storefront page, where checkout starts."""
    domain = (
        os.environ.get("SHOPIFY_STORE_DOMAIN", "")
        .strip()
        .removeprefix("https://")
        .removeprefix("http://")
        .strip("/")
    )
    handle = str(cfg.billing.get("shopify_pro_handle") or "swinglab-pro")
    return f"https://{domain}/products/{handle}"


def handle_webhook(
    payload: bytes, signature: str, topic: str, users: UserStore, cfg: Config
) -> None:
    """Verify the payload came from Shopify, then apply it. Raises
    ValueError on a bad signature (the route turns that into a 400)."""
    # .strip() the secret the way shop.py strips the Storefront token: a
    # trailing newline or space pasted into a PaaS variable UI would
    # otherwise silently change the key and reject every real delivery.
    secret = os.environ["SHOPIFY_WEBHOOK_SECRET"].strip().encode()
    expected = base64.b64encode(hmac.new(secret, payload, hashlib.sha256).digest())
    # Compare on bytes: the header is decoded latin-1 upstream, so a
    # malformed (non-ASCII) signature would make the str form of
    # compare_digest raise TypeError — a 500 instead of a clean reject.
    supplied = (signature or "").encode("latin-1", "replace")
    if not hmac.compare_digest(expected, supplied):
        # Log the reject: without this a wrong/whitespaced secret is
        # invisible from the app side (just uvicorn 400s), and the only
        # other place to see it is Shopify's own delivery log.
        logger.warning("Rejected Shopify webhook (bad signature): topic=%s", topic or "?")
        raise ValueError("Invalid Shopify webhook signature")
    try:
        data = json.loads(payload)
    except ValueError:
        logger.warning("Rejected Shopify webhook (bad JSON body): topic=%s", topic or "?")
        raise ValueError("Invalid Shopify webhook payload")
    apply_webhook(topic, data, users, cfg)


def apply_webhook(topic: str, data: dict, users: UserStore, cfg: Config) -> None:
    """Route a (verified) Shopify webhook by topic: orders change Pro
    access, customer events sync store accounts, GDPR events are
    acknowledged. Unknown topics are no-ops (still a 200 — Shopify retries
    anything else)."""
    if topic in PAID_TOPICS or topic in CANCELLED_TOPICS or topic in REFUND_TOPICS:
        apply_order(topic, data, users, cfg)
    else:
        apply_customer(topic, data, users)


def apply_order(topic: str, order: dict, users: UserStore, cfg: Config) -> None:
    """Update Pro access from a (verified) Shopify order webhook.

    Separated from signature verification so tests can drive it directly.
    """
    if topic in PAID_TOPICS:
        _apply_paid(order, users, cfg)
    elif topic in CANCELLED_TOPICS:
        _apply_cancelled(order, users)
    elif topic in REFUND_TOPICS:
        _apply_refund(order, users, cfg)


def apply_customer(topic: str, data: dict, users: UserStore) -> None:
    """Sync a (verified) Shopify customer webhook into the users table.
    See the module docstring for the exact semantics per topic."""
    if topic in CUSTOMER_UPSERT_TOPICS:
        email = (data.get("email") or "").strip().lower()
        if not email:
            logger.info("Shopify %s webhook skipped: customer has no email.", topic)
            return  # a customer with no email has nothing to sync to
        customer_id = str(data.get("id") or "") or None
        try:
            comparable_customer_id = normalize_customer_id(customer_id)
        except ValueError:
            comparable_customer_id = customer_id
        user = users.upsert_store_customer(
            email,
            customer_id,
            updated_at=_customer_updated_at(data),
        )
        if user is None:
            logger.info(
                "Shopify %s ignored for a deleted/redacted customer.",
                topic,
            )
        elif (
            comparable_customer_id
            and user.shopify_customer_id != comparable_customer_id
        ):
            logger.warning(
                "Shopify %s did not auto-merge an identity conflict; "
                "administrative review is required.",
                topic,
            )
        elif user.email != email:
            logger.warning(
                "Shopify %s preserved the verified app login email instead "
                "of applying a store-side identity change.",
                topic,
            )
        else:
            logger.info("Shopify %s synchronized a store account.", topic)
    elif topic in CUSTOMER_DELETE_TOPICS:
        _detach_customer(data, users, redact=False)
    elif topic in CUSTOMER_REDACT_TOPICS:
        _detach_customer(data.get("customer") or {}, users, redact=True)
    elif topic in ACK_TOPICS:
        logger.info(
            "Shopify %s webhook acknowledged; privacy workflow pending.", topic
        )
    else:
        # Reached only for a topic that is none of orders/*, customers/* or
        # the GDPR acks — i.e. the operator subscribed the wrong topic in
        # Shopify. Without this the route returns a green 200 and does
        # nothing, so a mis-picked topic is invisible in Shopify's log.
        logger.warning(
            "Ignoring unrecognized Shopify webhook topic: %s "
            "(check the webhook is 'orders/paid', not e.g. 'orders/create').",
            topic or "?",
        )


def _customer_updated_at(data: dict) -> float | None:
    """Parse Shopify's resource timestamp for stale-event rejection."""
    raw = data.get("updated_at") or data.get("created_at")
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        logger.warning("Ignoring invalid Shopify customer timestamp.")
        return None


def _detach_customer(customer: dict, users: UserStore, redact: bool) -> None:
    """customers/delete and customers/redact. An unclaimed stub (never
    claimed by password or code sign-in, no analyses) is deleted outright —
    on plain deletion any Pro days it still carried are parked so a later
    signup keeps what was bought; on redaction the parked purchase is
    erased too. A claimed account — including a passwordless one whose
    owner signed in with an emailed code — only loses its store link
    (never its app data), and redaction additionally clears the
    shopify-sourced ``source`` field."""
    customer_id = str(customer.get("id") or "") or None
    email = (customer.get("email") or "").strip().lower()
    users.remove_shopify_customer(customer_id, email, redact=redact)


def _order_days(order: dict, cfg: Config) -> float:
    """Days of Pro an order buys: the SKU->days map applied to line items."""
    skus = {
        str(sku): float(days)
        for sku, days in (cfg.billing.get("shopify_skus") or {}).items()
    }
    total = 0.0
    for item in order.get("line_items") or []:
        days = skus.get(str(item.get("sku") or ""))
        if days:
            total += days * int(item.get("quantity") or 1)
    return total


def _gear_items(order: dict, cfg: Config) -> list[tuple[str, str, int]]:
    """The order's non-Pro line items as (sku, title, quantity) — everything
    the billing.shopify_skus map doesn't claim (the exact complement of
    _order_days's Pro test, so no item is ever counted as both)."""
    skus = {
        str(sku): float(days)
        for sku, days in (cfg.billing.get("shopify_skus") or {}).items()
    }
    items = []
    for item in order.get("line_items") or []:
        sku = str(item.get("sku") or "")
        if skus.get(sku):
            continue  # a Pro line item — the grant path handles it
        items.append(
            (sku, str(item.get("title") or ""), int(item.get("quantity") or 1))
        )
    return items


def _order_email(order: dict) -> str:
    customer = order.get("customer") or {}
    email = order.get("email") or order.get("contact_email") or customer.get("email")
    return (email or "").strip().lower()


def _order_customer_id(order: dict) -> str | None:
    customer_id = str((order.get("customer") or {}).get("id") or "").strip()
    return customer_id or None


def _apply_paid(order: dict, users: UserStore, cfg: Config) -> None:
    order_id = str(order.get("id") or "")
    email = _order_email(order)
    customer_id = _order_customer_id(order)
    gear = _gear_items(order, cfg)
    days = _order_days(order, cfg)
    if not order_id or (days <= 0 and not gear):
        return
    applied, _, _ = users.apply_shopify_order(
        order_id, email, days, customer_id, gear=gear
    )
    if not applied:
        logger.info("Shopify order webhook replay skipped.")
        return
    logger.info("Shopify order webhook reconciled.")


def _apply_cancelled(order: dict, users: UserStore) -> None:
    order_id = str(order.get("id") or "")
    users.cancel_shopify_order(
        order_id,
        email=_order_email(order),
        shopify_customer_id=_order_customer_id(order),
    )


def _apply_refund(refund: dict, users: UserStore, cfg: Config) -> None:
    """Reverse a refunded Pro order using the cancellation ledger.

    Shopify's ``refunds/create`` payload embeds each refunded order line in
    ``refund_line_items[].line_item``. Only a positive-quantity line whose
    SKU is configured as Pro is strong enough evidence to revoke access.
    This avoids taking Pro away when a mixed order refunds gear only, while
    reusing ``cancel_shopify_order`` gives refunds the existing atomic,
    replay-idempotent, and refund-before-paid tombstone behavior.

    The existing order ledger models one entitlement interval per order, not
    per line-item quantity. Consequently any attributable Pro refund reverses
    that order's entire Pro grant, matching the current whole-order
    cancellation semantics. The storefront policy permits refunds only for
    unused Pro purchases, so partial-use/partial-quantity refunds require
    explicit operator reconciliation rather than an unsafe guess here.
    """
    pro_skus = {
        str(sku)
        for sku, days in (cfg.billing.get("shopify_skus") or {}).items()
        if float(days) > 0
    }
    has_pro_refund = False
    for refunded in refund.get("refund_line_items") or []:
        try:
            quantity = int(refunded.get("quantity") or 0)
        except (TypeError, ValueError):
            quantity = 0
        line_item = refunded.get("line_item") or {}
        if quantity > 0 and str(line_item.get("sku") or "") in pro_skus:
            has_pro_refund = True
            break
    if not has_pro_refund:
        logger.info(
            "Shopify refund did not identify a Pro SKU; entitlement unchanged."
        )
        return
    users.cancel_shopify_order(str(refund.get("order_id") or ""))
