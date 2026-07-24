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
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os

from ..config import Config
from .users import UserStore

PAID_TOPICS = ("orders/paid", "ORDERS_PAID")
CANCELLED_TOPICS = ("orders/cancelled", "ORDERS_CANCELLED")


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
    expected = base64.b64encode(
        hmac.new(secret, payload, hashlib.sha256).digest()
    ).decode()
    if not hmac.compare_digest(expected, signature or ""):
        raise ValueError("Invalid Shopify webhook signature")
    try:
        order = json.loads(payload)
    except ValueError:
        raise ValueError("Invalid Shopify webhook payload")
    apply_order(topic, order, users, cfg)


def apply_order(topic: str, order: dict, users: UserStore, cfg: Config) -> None:
    """Update Pro access from a (verified) Shopify order webhook.

    Separated from signature verification so tests can drive it directly.
    """
    if topic in PAID_TOPICS:
        _apply_paid(order, users, cfg)
    elif topic in CANCELLED_TOPICS:
        _apply_cancelled(order, users)


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
