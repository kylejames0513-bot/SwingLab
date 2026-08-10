"""Selling CaddieInsight Pro through the Shopify store.

Entirely environment-driven and safely inert until configured:

    SHOPIFY_STORE_DOMAIN     yourstore.myshopify.com (shared with shop.py)
    SHOPIFY_WEBHOOK_SECRET   webhook signing secret from Shopify admin
                             (Settings -> Notifications -> Webhooks)
    SHOPIFY_PRIVACY_WEBHOOK_SECRET
                             optional client secret for mandatory compliance
                             topics delivered by a dedicated custom app

``SHOPIFY_STORE_DOMAIN`` plus the primary ``SHOPIFY_WEBHOOK_SECRET`` enables
the commerce bridge: ``commerce_enabled()`` (and its compatibility alias
``enabled()``) then exposes Pro purchase links and requires inbox proof for
Shopify-connected signup semantics. A store plus either signing secret keeps
``webhook_endpoint_enabled()`` available, so a privacy-only app can deliver
mandatory compliance topics without advertising checkout. Without the
store configured, Pro upgrades honestly read "coming soon" — this is the
only purchase path (owner decision, 2026-08-10; the dormant Stripe
fallback was removed with swinglab/web/billing.py).

Setup, on the Shopify side:

1. Create a product for Pro access. Each variant's SKU maps to a number of
   days of Pro in ``billing.shopify_skus`` (config.yaml) — e.g. variant
   ``SL-PRO-1MO`` grants 31 days. Prices live on the product in Shopify,
   never in code (the same rule as gear prices).
2. In Settings -> Notifications -> Webhooks, add ``orders/paid``,
   ``orders/cancelled``, and ``refunds/create`` webhooks pointing at
   ``https://<your-app>/webhooks/shopify`` and copy the signing secret
   shown on that page into ``SHOPIFY_WEBHOOK_SECRET``. After the supplier
   proof gate is cleared, ``fulfillments/create`` and ``fulfillments/update``
   can send the same endpoint a delivery-status telemetry signal; neither
   topic changes order or fulfillment state.

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

When email delivery is configured (mailer.py), a reconciled paid Pro order
also sends exactly one transactional email: a confirmation (days added, new
end date, /today link) when the grant landed on an account, or a "your Pro
is waiting — activate your account" nudge to the checkout email when it
parked in ``pro_grants``. The send is claimed first in the lifecycle
ledger (users.claim_lifecycle_email), so webhook retries and crash-window
repairs never double-email, and a delivery failure only logs — the webhook
stays a 200 because the grant is already committed.

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
  with ``shopify_customer_id`` and ``source='shopify'``; an inbox-verified
  user may receive the stable link, while a pre-existing unverified local
  account is quarantined in a pending identity row until successful email
  proof. The customer id is the stable identity: an unclaimed store-only
  stub can follow a changed Shopify email in place, while a claimed account
  keeps its verified app login email rather than splitting into a second
  user or auto-merging accounts. Replays are idempotent.
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
- ``customers/data_request`` atomically stores an integrity-checked export
  snapshot for operator delivery. Exact delivery replays reuse the same
  request. Snapshots expire after the explicit retention window.
- ``shop/redact`` accepts only the exact configured store and transactionally
  removes the single-store Shopify ledgers, pending grants, identities,
  sync/backfill state, store-only stubs, signup intents, and privacy snapshots.
  Independently claimed accounts and swing analyses remain.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from urllib.parse import quote

from ..config import Config
from ..integrations.shopify.identity import (
    normalize_customer_id,
    normalize_shop_domain,
)
from . import mailer
from .users import FREE, PRO_TIER, UserStore, stronger_tier

logger = logging.getLogger("swinglab.web.shopify")

# Grants at least this long display (and read in email) as "Lifetime" —
# the same 50-year boundary as the account page (app.LIFETIME_DISPLAY_MIN_S;
# SL-PRO-LIFE is 36500 days).
LIFETIME_MIN_DAYS = 50 * 365

PAID_TOPICS = ("orders/paid", "ORDERS_PAID")
CANCELLED_TOPICS = ("orders/cancelled", "ORDERS_CANCELLED")
REFUND_TOPICS = ("refunds/create", "REFUNDS_CREATE")
FULFILLMENT_TOPICS = (
    "fulfillments/create",
    "fulfillments/update",
    "FULFILLMENTS_CREATE",
    "FULFILLMENTS_UPDATE",
)
CUSTOMER_UPSERT_TOPICS = (
    "customers/create", "customers/update",
    "CUSTOMERS_CREATE", "CUSTOMERS_UPDATE",
)
CUSTOMER_DELETE_TOPICS = ("customers/delete", "CUSTOMERS_DELETE")
CUSTOMER_REDACT_TOPICS = ("customers/redact", "CUSTOMERS_REDACT")
DATA_REQUEST_TOPICS = (
    "customers/data_request",
    "CUSTOMERS_DATA_REQUEST",
)
SHOP_REDACT_TOPICS = ("shop/redact", "SHOP_REDACT")
PRIVACY_TOPICS = (
    *CUSTOMER_REDACT_TOPICS,
    *DATA_REQUEST_TOPICS,
    *SHOP_REDACT_TOPICS,
)
MUTATING_TOPICS = (
    *PAID_TOPICS,
    *CANCELLED_TOPICS,
    *REFUND_TOPICS,
    *FULFILLMENT_TOPICS,
    *CUSTOMER_UPSERT_TOPICS,
    *CUSTOMER_DELETE_TOPICS,
    *PRIVACY_TOPICS,
)


def commerce_enabled() -> bool:
    """Whether buyer-facing Shopify commerce semantics are configured."""
    return bool(
        _configured_shop_domain()
        and _signing_secret_present("SHOPIFY_WEBHOOK_SECRET")
    )


def webhook_endpoint_enabled() -> bool:
    """Whether at least one signed Shopify webhook source is configured."""
    return bool(
        _configured_shop_domain()
        and (
            _signing_secret_present("SHOPIFY_WEBHOOK_SECRET")
            or _signing_secret_present("SHOPIFY_PRIVACY_WEBHOOK_SECRET")
        )
    )


def enabled() -> bool:
    """Compatibility alias for the historical commerce-readiness predicate."""
    return commerce_enabled()


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
    payload: bytes,
    signature: str,
    topic: str,
    users: UserStore,
    cfg: Config,
    event_id: str | None = None,
    shop_domain: str | None = None,
) -> None:
    """Verify the payload came from Shopify, then apply it. Raises
    ValueError on a bad signature (the route turns that into a 400)."""
    # .strip() the secret the way shop.py strips the Storefront token: a
    # trailing newline or space pasted into a PaaS variable UI would
    # otherwise silently change the key and reject every real delivery.
    # Compare on bytes: the header is decoded latin-1 upstream, so a
    # malformed (non-ASCII) signature would make the str form of
    # compare_digest raise TypeError — a 500 instead of a clean reject.
    supplied = (signature or "").encode("latin-1", "replace")
    secrets = []
    primary_secret = os.environ.get("SHOPIFY_WEBHOOK_SECRET", "").strip()
    if primary_secret:
        secrets.append(primary_secret.encode())
    if topic in PRIVACY_TOPICS:
        privacy_secret = os.environ.get(
            "SHOPIFY_PRIVACY_WEBHOOK_SECRET", ""
        ).strip()
        if privacy_secret:
            secrets.append(privacy_secret.encode())
    # Every eligible secret is always evaluated. Do not return on the first
    # match: the relative timing must not reveal which signing key accepted a
    # mandatory compliance delivery.
    matched = 0
    for secret in secrets:
        expected = base64.b64encode(
            hmac.new(secret, payload, hashlib.sha256).digest()
        )
        matched |= int(hmac.compare_digest(expected, supplied))
    if not matched:
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
    if not isinstance(data, dict):
        logger.warning(
            "Rejected Shopify webhook (non-object JSON body): topic=%s",
            topic or "?",
        )
        raise ValueError("Invalid Shopify webhook payload")
    if topic in MUTATING_TOPICS:
        supplied_shop = normalize_shop_domain(shop_domain)
        configured_shop = _configured_shop_domain()
        if not (
            supplied_shop
            and configured_shop
            and hmac.compare_digest(supplied_shop, configured_shop)
        ):
            logger.warning(
                "Rejected Shopify webhook for an unexpected store: topic=%s",
                topic or "?",
            )
            raise ValueError("Invalid Shopify webhook store")
    if topic in PRIVACY_TOPICS:
        delivery_id = str(event_id or "").strip()
        if not delivery_id or len(delivery_id) > 255:
            logger.warning(
                "Rejected Shopify privacy webhook without a valid "
                "delivery id: topic=%s",
                topic or "?",
            )
            raise ValueError("Invalid Shopify privacy webhook delivery id")
    apply_webhook(topic, data, users, cfg, event_id=event_id)


def apply_webhook(
    topic: str,
    data: dict,
    users: UserStore,
    cfg: Config,
    event_id: str | None = None,
) -> None:
    """Route a (verified) Shopify webhook by topic: orders change Pro
    access, customer events sync store accounts, GDPR events are
    acknowledged. Unknown topics are no-ops (still a 200 — Shopify retries
    anything else)."""
    if (
        topic in PAID_TOPICS
        or topic in CANCELLED_TOPICS
        or topic in REFUND_TOPICS
    ):
        apply_order(topic, data, users, cfg)
    elif topic in FULFILLMENT_TOPICS:
        _apply_fulfillment(data, users)
    else:
        apply_customer(topic, data, users, event_id=event_id)


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


def apply_customer(
    topic: str,
    data: dict,
    users: UserStore,
    event_id: str | None = None,
) -> None:
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
            # This is the moment a parked order was waiting for. A
            # customer-bearing orders/paid is deliberately NOT granted on a
            # matching email alone (users.py) — otherwise registering an
            # address first would inherit a stranger's purchase — so it parks
            # until the Shopify customer id is bound to an account. Binding it
            # is what just happened, and Shopify does not guarantee that
            # customers/create arrives first. Without claiming here the days
            # sit in pro_grants until the buyer's next sign-in, so anyone who
            # paid and closed the tab stays on Free with no way to know why.
            #
            # claim_pending_grant re-checks the link itself and refuses on an
            # identity conflict, so this widens *when* a legitimate grant
            # lands, never *what* qualifies for one.
            #
            # Only for a CLAIMED account. An unclaimed stub is a row this
            # webhook just provisioned, which nobody has yet proven they own,
            # and an unclaimed stub is still allowed to follow a changed
            # Shopify email in place — so granting onto one would let a later
            # store-side email change carry Pro to an address the buyer never
            # controlled. Signup and code sign-in already claim on the way in,
            # which is the point at which ownership is proven.
            if user.claimed:
                claimed_days = users.claim_pending_grant(user.id, user.email)
                if claimed_days:
                    logger.info(
                        "Shopify %s released %s parked Pro day(s) to the "
                        "now-linked account.",
                        topic,
                        claimed_days,
                    )
    elif topic in CUSTOMER_DELETE_TOPICS:
        _detach_customer(data, users, redact=False)
    elif topic in CUSTOMER_REDACT_TOPICS:
        if not _privacy_shop_matches(data):
            logger.warning(
                "Ignored Shopify customer redaction for a different store."
            )
            return
        _detach_customer(
            data.get("customer") or {},
            users,
            redact=True,
            event_id=event_id,
            shop_domain=_configured_shop_domain(),
            order_ids=data.get("orders_to_redact") or [],
        )
    elif topic in DATA_REQUEST_TOPICS:
        customer = data.get("customer")
        if not isinstance(customer, dict):
            customer = {}
        request, replayed = users.capture_shopify_data_request(
            shop_domain=str(data.get("shop_domain") or ""),
            configured_shop_domain=_configured_shop_domain(),
            customer_id=customer.get("id"),
            order_ids=data.get("orders_requested") or [],
            event_id=event_id,
            include_replay_status=True,
        )
        if replayed:
            logger.info(
                "Shopify data request replay was already applied."
            )
        elif request is None:
            logger.warning(
                "Ignored Shopify data request for a different store."
            )
        else:
            logger.info(
                "Shopify customer data snapshot is ready: "
                "request_ref=%s status=%s.",
                request.request_id,
                request.status,
            )
    elif topic in SHOP_REDACT_TOPICS:
        result = users.redact_shopify_store(
            str(data.get("shop_domain") or ""),
            _configured_shop_domain(),
            event_id=event_id,
        )
        if result.replayed:
            logger.info(
                "Shopify shop redaction replay was already applied."
            )
        elif not result.applied:
            logger.warning(
                "Ignored Shopify shop redaction for a different store."
            )
        else:
            logger.info(
                "Shopify shop redaction completed transactionally."
            )
    else:
        # Reached only for a topic that is none of orders/*, customers/* or
        # the mandatory privacy topics — i.e. the operator subscribed the
        # wrong topic in Shopify. Without this the route returns a green 200
        # and does nothing, so a mis-picked topic is invisible in Shopify's
        # log.
        logger.warning(
            "Ignoring unrecognized Shopify webhook topic: %s "
            "(check the webhook is 'orders/paid', not e.g. 'orders/create').",
            topic or "?",
        )


def _configured_shop_domain() -> str:
    return normalize_shop_domain(os.environ.get("SHOPIFY_STORE_DOMAIN")) or ""


def _signing_secret_present(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


def _privacy_shop_matches(data: dict) -> bool:
    supplied = normalize_shop_domain(data.get("shop_domain"))
    configured = _configured_shop_domain()
    return bool(
        supplied
        and configured
        and hmac.compare_digest(supplied, configured)
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


def _detach_customer(
    customer: dict,
    users: UserStore,
    redact: bool,
    *,
    event_id: str | None = None,
    shop_domain: str | None = None,
    order_ids: list[object] | None = None,
) -> None:
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
    users.remove_shopify_customer(
        customer_id,
        email,
        redact=redact,
        privacy_event_id=(event_id if redact else None),
        privacy_shop_domain=(shop_domain if redact else None),
        order_ids=(order_ids if redact else None),
    )


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


def _order_tier(order: dict, cfg: Config) -> str:
    """The membership tier an order buys: the STRONGEST tier it contains.

    A mixed cart (a Coach year plus a Pro month) grants the days of both and
    the level of the better one, which is the only reading that cannot take
    money for something it then withholds. Anything the tier map does not
    name is Pro — the same default as config, so an unconfigured install
    behaves exactly as it did before two tiers existed.

    An order with NO membership SKU at all is FREE, not Pro. It used to
    start at Pro and only ever climb, so a gear-only order stored tier
    "pro" on its ledger row — a tier the order did not buy. Harmless only
    because a guard three call-frames away also checks days > 0, and
    claim_pending_grant reads the stored tier back for attribution; the
    row should tell the truth on its own.
    """
    day_skus = {
        str(sku)
        for sku, days in (cfg.billing.get("shopify_skus") or {}).items()
        if float(days) > 0
    }
    tiers = {
        str(sku): str(tier or "").strip().lower()
        for sku, tier in (cfg.billing.get("shopify_sku_tiers") or {}).items()
    }
    best = FREE
    for item in order.get("line_items") or []:
        sku = str(item.get("sku") or "")
        if sku in day_skus:
            best = stronger_tier(best, tiers.get(sku, PRO_TIER))
    return best


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
    applied, effective_email, user_id = users.apply_shopify_order(
        order_id, email, days, customer_id, gear=gear,
        tier=_order_tier(order, cfg),
    )
    if not applied:
        logger.info("Shopify order webhook replay skipped.")
        return
    _record_order_funnel_event(users, "paid_order", order_id, user_id)
    if days > 0:
        _send_pro_purchase_email(
            users, cfg, order_id, effective_email, user_id, days
        )
    logger.info("Shopify order webhook reconciled.")


def _app_base_url() -> str:
    """The app's public origin for links inside email (PUBLIC_BASE_URL —
    https://app.caddieinsight.com in production), same source as digest.py."""
    return (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")


def _format_day(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%B %d, %Y")


def _send_pro_purchase_email(
    users: UserStore,
    cfg: Config,
    order_id: str,
    email: str,
    user_id: str | None,
    days: float,
) -> None:
    """One transactional email per paid Pro order: a confirmation when the
    order landed on an account, or an "activate your account" nudge when the
    grant parked in ``pro_grants``.

    Claim-before-send through the lifecycle ledger keeps webhook retries and
    crash-window repairs from ever double-emailing one order, and a delivery
    failure is only logged — the grant is already committed, so the webhook
    must stay a 200 (Shopify would otherwise replay the whole mutation)."""
    if not mailer.enabled():
        return
    brand = str(cfg.brand["name"])
    base = _app_base_url()
    lifetime = days >= LIFETIME_MIN_DAYS
    added = "Lifetime Pro" if lifetime else f"{days:g} days of Pro"
    try:
        if user_id is not None:
            user = users.get(user_id)
            if user is None or not user.email:
                return
            if not users.claim_lifecycle_email(
                "shopify_pro_activated", order_id, user_id=user_id
            ):
                return
            until_line = (
                "It never expires."
                if lifetime
                else f"Your Pro access now runs until {_format_day(user.pro_until)}."
            )
            mailer.send(
                user.email,
                f"{brand} Pro is active on your account",
                f"Thanks — your {brand} order added {added} to your"
                " account.\n"
                f"{until_line}\n\n"
                "Your next coaching check-in is ready when you are:\n"
                f"{base}/today\n\n"
                "This confirmation is about a purchase on your account,"
                " not a mailing list.",
            )
        else:
            if not email:
                return
            if not users.claim_lifecycle_email(
                "shopify_pro_waiting", order_id
            ):
                return
            mailer.send(
                email,
                f"Your {brand} Pro is waiting — activate your account",
                f"Thanks for your {brand} order — {added} is paid for and"
                " parked under this email address.\n\n"
                "Create your account with this same email and it activates"
                " automatically:\n"
                f"{base}/signup?email={quote(email)}\n\n"
                "Nothing expires while you wait — the purchase is applied"
                " the moment you sign in.",
            )
    except mailer.EmailDeliveryError as exc:
        logger.error("Shopify order email delivery failed: %s", exc)
    except Exception:
        logger.exception("Shopify order email could not be sent.")


def _apply_fulfillment(fulfillment: dict, users: UserStore) -> None:
    """Record a proof-of-shipment funnel event without mutating commerce.

    The fulfillment APIs and supplier remain Shopify-owned.  This handler
    intentionally only counts a signed, same-store delivery when the prior
    paid-order ledger already proves exactly one app account for its order.
    """

    order_id = str(fulfillment.get("order_id") or "").strip()
    user_id = users.user_id_for_shopify_order(order_id)
    _record_order_funnel_event(users, "fulfillment_updated", order_id, user_id)


def _record_order_funnel_event(
    users: UserStore,
    event_name: str,
    order_id: str,
    user_id: str | None,
) -> None:
    """Best-effort, replay-safe telemetry for a transactionally known user."""

    if not order_id or user_id is None:
        return
    # Keep the external order reference out of the product-event ledger.
    # The SHA-256 digest is only an idempotency key and is removed with the
    # linked account's product events during redaction.
    reference = hashlib.sha256(order_id.encode("utf-8")).hexdigest()
    try:
        users.record_product_event(
            event_name,
            user_id=user_id,
            dedupe_key=f"shopify.{event_name}.{reference}",
        )
    except Exception:
        # Measurement must never make Shopify retry a successful paid or
        # fulfillment webhook, and the log intentionally has no order/user ID.
        logger.warning("Shopify funnel event write unavailable: event=%s", event_name)


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
