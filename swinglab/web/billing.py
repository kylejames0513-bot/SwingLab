"""Stripe subscriptions for the Pro plan.

Entirely environment-driven and safely inert until configured:

    STRIPE_SECRET_KEY      sk_live_... / sk_test_...
    STRIPE_PRICE_ID        price_... of the recurring Pro price
    STRIPE_WEBHOOK_SECRET  whsec_... for POST /webhooks/stripe

With those unset, ``enabled()`` is False and the app runs free-tier only
(the pricing page shows Pro as coming soon). The price itself lives in
Stripe — change it in the dashboard, never in code.

Flow: checkout happens on Stripe's hosted page; Stripe calls the webhook,
which is the ONLY place plan state changes. Never mark a user Pro from a
redirect URL — redirects can be faked, signed webhooks cannot.
"""

from __future__ import annotations

import os

from .users import FREE, PRO, User, UserStore

_PRO_OK_STATUSES = ("active", "trialing", "past_due")


def enabled() -> bool:
    return bool(
        os.environ.get("STRIPE_SECRET_KEY") and os.environ.get("STRIPE_PRICE_ID")
    )


def _stripe():
    import stripe

    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    return stripe


def create_checkout_url(user: User, base_url: str) -> str:
    """Hosted Stripe Checkout for the Pro subscription."""
    stripe = _stripe()
    kwargs = dict(
        mode="subscription",
        line_items=[{"price": os.environ["STRIPE_PRICE_ID"], "quantity": 1}],
        client_reference_id=user.id,
        success_url=f"{base_url}/account?upgraded=1",
        cancel_url=f"{base_url}/pricing",
    )
    if user.stripe_customer_id:
        kwargs["customer"] = user.stripe_customer_id
    else:
        kwargs["customer_email"] = user.email
    return stripe.checkout.Session.create(**kwargs).url


def create_portal_url(user: User, base_url: str) -> str:
    """Stripe's hosted portal: change card, cancel, download invoices."""
    stripe = _stripe()
    session = stripe.billing_portal.Session.create(
        customer=user.stripe_customer_id,
        return_url=f"{base_url}/account",
    )
    return session.url


def handle_webhook(payload: bytes, signature: str, users: UserStore) -> None:
    """Verify the event came from Stripe, then apply it. Raises ValueError on
    a bad signature (the route turns that into a 400)."""
    stripe = _stripe()
    try:
        event = stripe.Webhook.construct_event(
            payload, signature, os.environ["STRIPE_WEBHOOK_SECRET"]
        )
    except Exception as exc:  # stripe raises its own SignatureVerificationError
        raise ValueError(f"Invalid Stripe webhook: {exc}")
    apply_event(event, users)


def apply_event(event, users: UserStore) -> None:
    """Update plan state from a (verified) Stripe event.

    Separated from signature verification so tests can drive it directly.
    """
    kind = event["type"]
    obj = event["data"]["object"]

    if kind == "checkout.session.completed":
        user = users.get(obj.get("client_reference_id") or "")
        if user is not None:
            customer_id = obj.get("customer")
            if customer_id:
                users.set_customer(user.id, customer_id)
            users.set_plan(user.id, PRO, "active")

    elif kind in ("customer.subscription.created", "customer.subscription.updated"):
        user = users.get_by_customer(obj.get("customer"))
        if user is not None:
            status = obj.get("status") or "none"
            plan = PRO if status in _PRO_OK_STATUSES else FREE
            users.set_plan(user.id, plan, status)

    elif kind == "customer.subscription.deleted":
        user = users.get_by_customer(obj.get("customer"))
        if user is not None:
            users.set_plan(user.id, FREE, "canceled")
