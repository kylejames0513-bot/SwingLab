"""Selling SwingLab Pro through the Shopify store.

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
2. In Settings -> Notifications -> Webhooks, add ``orders/paid`` and
   ``orders/cancelled`` webhooks pointing at
   ``https://<your-app>/webhooks/shopify`` and copy the signing secret
   shown on that page into ``SHOPIFY_WEBHOOK_SECRET``.

Flow: checkout happens on the Shopify storefront; Shopify calls the
webhook, which is the ONLY place access changes (signed webhooks can't be
faked, storefront redirects can). A paid order extends ``pro_until`` on
the account whose email matches the checkout email; if no account exists
yet, the days are parked in ``pro_grants`` and claimed automatically the
first time that email signs up or logs in. Replayed webhooks are no-ops
(orders are recorded by id), and a cancelled order takes back exactly the
days it granted.

This also works unchanged with Shopify's Subscriptions app: each billing
cycle creates a new paid order with the same SKU, so Pro keeps extending
itself for as long as the subscription runs.

Account sync (customer webhooks, same endpoint and secret)
----------------------------------------------------------

Add ``customers/create``, ``customers/update``, and ``customers/delete``
webhooks pointing at the same ``/webhooks/shopify`` URL and a customer
created on the store automatically exists in the app:

- ``customers/create`` / ``customers/update`` upsert a "store account":
  no user for the (normalized) email -> a passwordless stub is created
  with ``shopify_customer_id`` and ``source='shopify'``; a user exists ->
  only the ``shopify_customer_id`` link is set/refreshed. An existing
  password or email is NEVER overwritten (Shopify does not expose customer
  credentials, so passwords cannot sync — the user sets their app password
  once, by signing up with the store email, which claims the same row).
  Replays are idempotent: the upsert lands on the same row every time.
- ``customers/delete`` deletes the user ONLY when it is an unclaimed stub
  (no password, no analyses); any Pro days it still carried are parked in
  ``pro_grants`` so a later signup keeps what was bought. A claimed
  account merely loses its ``shopify_customer_id`` link — store-side
  deletion never destroys app data.
- ``customers/redact`` (GDPR) follows the delete semantics and further
  erases the shopify-sourced profile fields (link + source) on claimed
  accounts, and drops any parked purchase for a deleted stub's email.
- ``customers/data_request`` and ``shop/redact`` (GDPR) are acknowledged
  with a 200 and logged; there is nothing to change app-side.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from ..config import Config
from .users import UserStore

PAID_TOPICS = ("orders/paid", "ORDERS_PAID")
CANCELLED_TOPICS = ("orders/cancelled", "ORDERS_CANCELLED")
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
    secret = os.environ["SHOPIFY_WEBHOOK_SECRET"].encode()
    expected = base64.b64encode(hmac.new(secret, payload, hashlib.sha256).digest())
    # Compare on bytes: the header is decoded latin-1 upstream, so a
    # malformed (non-ASCII) signature would make the str form of
    # compare_digest raise TypeError — a 500 instead of a clean reject.
    supplied = (signature or "").encode("latin-1", "replace")
    if not hmac.compare_digest(expected, supplied):
        raise ValueError("Invalid Shopify webhook signature")
    try:
        data = json.loads(payload)
    except ValueError:
        raise ValueError("Invalid Shopify webhook payload")
    apply_webhook(topic, data, users, cfg)


def apply_webhook(topic: str, data: dict, users: UserStore, cfg: Config) -> None:
    """Route a (verified) Shopify webhook by topic: orders change Pro
    access, customer events sync store accounts, GDPR events are
    acknowledged. Unknown topics are no-ops (still a 200 — Shopify retries
    anything else)."""
    if topic in PAID_TOPICS or topic in CANCELLED_TOPICS:
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


def apply_customer(topic: str, data: dict, users: UserStore) -> None:
    """Sync a (verified) Shopify customer webhook into the users table.
    See the module docstring for the exact semantics per topic."""
    if topic in CUSTOMER_UPSERT_TOPICS:
        email = (data.get("email") or "").strip().lower()
        if not email:
            return  # a customer with no email has nothing to sync to
        users.upsert_store_customer(email, str(data.get("id") or "") or None)
    elif topic in CUSTOMER_DELETE_TOPICS:
        _detach_customer(data, users, redact=False)
    elif topic in CUSTOMER_REDACT_TOPICS:
        _detach_customer(data.get("customer") or {}, users, redact=True)
    elif topic in ACK_TOPICS:
        print(
            f"Shopify {topic} webhook acknowledged — no app-side data changes."
        )


def _detach_customer(customer: dict, users: UserStore, redact: bool) -> None:
    """customers/delete and customers/redact. An unclaimed stub (no
    password, no analyses) is deleted outright — on plain deletion any Pro
    days it still carried are parked so a later signup keeps what was
    bought; on redaction the parked purchase is erased too. A claimed
    account only loses its store link (never its app data), and redaction
    additionally clears the shopify-sourced ``source`` field."""
    user = users.get_by_shopify(str(customer.get("id") or ""))
    if user is None:
        email = (customer.get("email") or "").strip().lower()
        user = users.get_by_email(email) if email else None
    if user is None:
        return  # unknown customer, or the webhook replayed after removal
    if not user.has_password and not users.has_activity(user.id):
        users.delete_user(user.id)
        if redact:
            users.pop_pending_grant(user.email)
        else:
            remaining = (user.pro_until - time.time()) / 86400
            if remaining > 0:
                users.add_pending_grant(user.email, remaining)
    else:
        users.unlink_shopify(user.id, clear_source=redact)


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


def _order_email(order: dict) -> str:
    customer = order.get("customer") or {}
    email = order.get("email") or order.get("contact_email") or customer.get("email")
    return (email or "").strip().lower()


def _apply_paid(order: dict, users: UserStore, cfg: Config) -> None:
    order_id = str(order.get("id") or "")
    email = _order_email(order)
    days = _order_days(order, cfg)
    if not order_id or not email or days <= 0:
        return  # gear-only order, or nothing to attach the grant to
    if not users.record_order(order_id, email, days):
        return  # replayed webhook — already granted
    user = users.get_by_email(email)
    if user is not None:
        users.grant_pro_days(user.id, days)
    else:
        users.add_pending_grant(email, days)


def _apply_cancelled(order: dict, users: UserStore) -> None:
    email, days = users.void_order(str(order.get("id") or ""))
    if days <= 0:
        return  # unknown order, or already voided
    user = users.get_by_email(email)
    if user is not None:
        users.revoke_pro_days(user.id, days)
    else:
        users.reduce_pending_grant(email, days)
