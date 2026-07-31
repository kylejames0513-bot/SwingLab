"""Selling Pro through the Shopify store.

Shopify itself is never called: checkout happens on the storefront, and
purchases arrive as signed order webhooks — so the tests post payloads
shaped like Shopify's to /webhooks/shopify, signed with the test secret.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import sqlite3
import time

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from starlette.requests import Request

from swinglab.config import Config
from swinglab.web import jobs as jobs_module, shopify_billing
from swinglab.web.app import (
    SHOPIFY_WEBHOOK_MAX_BODY_BYTES,
    _read_bounded_request_body,
    create_app,
)
from swinglab.web.users import UserStore

from tests.test_web import fake_analyze_ok

SECRET = "shpss_test_secret"
PRIVACY_SECRET = "shpss_test_privacy_secret"
DAY = 86400


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "teststore.myshopify.com")
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", SECRET)
    cfg = Config()
    cfg.web["require_account"] = True
    return create_app(cfg, sessions_dir=tmp_path / "sessions")


def signup(
    client,
    email="kyle@example.com",
    password="longenough",
    *,
    verified=True,
):
    resp = client.post(
        "/signup", data={"email": email, "password": password},
        follow_redirects=False,
    )
    if resp.status_code == 503:
        # Billing tests do not exercise mail delivery. Complete the required
        # inbox-proof step through the same durable intent/code primitives.
        users: UserStore = client.app.state.users
        intent = users.issue_signup_intent(email, password)
        code = users.issue_email_code(email, "claim")
        assert code is not None
        users.complete_signup_intent_with_code(intent, code)
        resp = client.post(
            "/login",
            data={"email": email, "password": password},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    if verified:
        users = client.app.state.users
        current = users.get_by_email(email)
        if current is not None and not current.email_verified:
            users.verify_email_signin(email)


def order_webhook(client, order, topic="orders/paid", secret=SECRET):
    payload = json.dumps(order).encode()
    signature = base64.b64encode(
        hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    ).decode()
    return client.post(
        "/webhooks/shopify",
        content=payload,
        headers={
            "X-Shopify-Hmac-Sha256": signature,
            "X-Shopify-Topic": topic,
            "X-Shopify-Shop-Domain": "teststore.myshopify.com",
            "X-Shopify-Webhook-Id": "shopify-billing-test-delivery",
            "Content-Type": "application/json",
        },
    )


def pro_order(order_id=1001, email="kyle@example.com", sku="SL-PRO-1MO", qty=1):
    return {
        "id": order_id,
        "email": email,
        "line_items": [{"sku": sku, "quantity": qty}],
    }


def pro_refund(
    order_id=1001,
    refund_id=9001,
    sku="SL-PRO-1MO",
    qty=1,
):
    return {
        "id": refund_id,
        "order_id": order_id,
        "refund_line_items": [
            {
                "quantity": qty,
                "line_item": {"sku": sku},
            }
        ],
    }


def get_user(client, email="kyle@example.com"):
    users: UserStore = client.app.state.users
    return users.get_by_email(email)


def test_paid_order_unlocks_pro(app):
    client = TestClient(app)
    signup(client)
    assert not get_user(client).is_pro

    # Checkout email case differs from the account's — must still match.
    resp = order_webhook(client, pro_order(email="Kyle@Example.com"))
    assert resp.status_code == 200

    user = get_user(client)
    assert user.is_pro
    assert abs(user.pro_until - (time.time() + 31 * DAY)) < 60


def test_quantity_and_sku_days_multiply(app):
    client = TestClient(app)
    signup(client)
    order_webhook(client, pro_order(sku="SL-PRO-12MO", qty=2))
    assert abs(get_user(client).pro_until - (time.time() + 730 * DAY)) < 60


def test_buying_again_stacks_on_remaining_time(app):
    client = TestClient(app)
    signup(client)
    order_webhook(client, pro_order(order_id=1))
    order_webhook(client, pro_order(order_id=2))
    assert abs(get_user(client).pro_until - (time.time() + 62 * DAY)) < 60


def test_replayed_webhook_grants_once(app):
    client = TestClient(app)
    signup(client)
    order_webhook(client, pro_order())
    before = get_user(client).pro_until
    assert order_webhook(client, pro_order()).status_code == 200
    assert get_user(client).pro_until == before


def test_bad_signature_rejected(app):
    client = TestClient(app)
    signup(client)
    resp = order_webhook(client, pro_order(), secret="wrong-secret")
    assert resp.status_code == 400
    assert not get_user(client).is_pro


def test_gear_only_order_changes_nothing(app):
    client = TestClient(app)
    signup(client)
    order_webhook(client, pro_order(sku="SL-TEMPO-WAND"))
    assert not get_user(client).is_pro


def test_purchase_before_signup_is_claimed_at_signup(app):
    client = TestClient(app)
    order_webhook(client, pro_order(email="new@example.com"))
    signup(client, email="new@example.com")
    user = get_user(client, "new@example.com")
    assert user.is_pro
    assert abs(user.pro_until - (time.time() + 31 * DAY)) < 60


def test_parked_grant_is_claimed_at_login(app):
    client = TestClient(app)
    signup(client)
    client.post("/logout")
    # A grant parked under this email (e.g. the webhook raced signup) is
    # claimed on the next login, not lost.
    users: UserStore = client.app.state.users
    users.add_pending_grant("kyle@example.com", 31)
    client.post(
        "/login", data={"email": "kyle@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    assert get_user(client).is_pro


def test_customer_email_fallback(app):
    client = TestClient(app)
    signup(client)
    order = {
        "id": 2002,
        "customer": {"email": "kyle@example.com"},
        "line_items": [{"sku": "SL-PRO-1MO", "quantity": 1}],
    }
    order_webhook(client, order)
    assert get_user(client).is_pro


def test_cancelled_order_takes_back_its_days(app):
    client = TestClient(app)
    signup(client)
    order_webhook(client, pro_order())
    assert get_user(client).is_pro
    order_webhook(client, pro_order(), topic="orders/cancelled")
    assert not get_user(client).is_pro
    # Replayed cancellation must not subtract twice.
    order_webhook(client, pro_order(order_id=3))
    order_webhook(client, pro_order(), topic="orders/cancelled")
    assert get_user(client).is_pro


def test_cancelled_unclaimed_purchase_never_grants(app):
    client = TestClient(app)
    order_webhook(client, pro_order(email="new@example.com"))
    order_webhook(
        client, pro_order(email="new@example.com"), topic="orders/cancelled"
    )
    signup(client, email="new@example.com")
    assert not get_user(client, "new@example.com").is_pro


def test_refunded_pro_order_takes_back_its_days_idempotently(app):
    client = TestClient(app)
    signup(client)
    order_webhook(client, pro_order(order_id=1))
    order_webhook(client, pro_order(order_id=2))

    assert order_webhook(
        client, pro_refund(order_id=1), topic="refunds/create"
    ).status_code == 200
    assert abs(get_user(client).pro_until - (time.time() + 31 * DAY)) < 60

    # Replayed refunds share the order cancellation ledger, so the surviving
    # second purchase can never be subtracted twice.
    order_webhook(client, pro_refund(order_id=1), topic="refunds/create")
    assert abs(get_user(client).pro_until - (time.time() + 31 * DAY)) < 60


def test_refund_before_paid_is_a_tombstone(app):
    client = TestClient(app)
    signup(client)

    order_webhook(client, pro_refund(), topic="refunds/create")
    order_webhook(client, pro_order(), topic="orders/paid")

    assert not get_user(client).is_pro
    row = client.app.state.users._conn.execute(
        "SELECT days, cancelled_at FROM shopify_orders WHERE order_id = ?",
        ("1001",),
    ).fetchone()
    assert row["days"] == 0
    assert row["cancelled_at"] is not None


def test_gear_only_or_unattributable_refund_does_not_revoke_pro(app):
    client = TestClient(app)
    signup(client)
    order_webhook(client, pro_order())
    before = get_user(client).pro_until

    gear_refund = pro_refund(sku="SL-TEMPO-WAND")
    order_webhook(client, gear_refund, topic="refunds/create")
    order_webhook(
        client,
        {"id": 9002, "order_id": 1001, "refund_line_items": []},
        topic="refunds/create",
    )

    assert get_user(client).pro_until == before


def test_partial_pro_refund_uses_whole_order_reversal_semantics(app):
    client = TestClient(app)
    signup(client)
    order_webhook(client, pro_order(qty=2))
    assert get_user(client).is_pro

    # The ledger owns one interval per order rather than per line-item unit.
    # An attributable refund therefore follows orders/cancelled and reverses
    # the whole order instead of guessing which part of its interval survived.
    order_webhook(client, pro_refund(qty=1), topic="refunds/create")

    assert not get_user(client).is_pro


def test_cancellation_before_paid_is_a_tombstone(app):
    client = TestClient(app)
    signup(client)

    order_webhook(client, pro_order(), topic="orders/cancelled")
    order_webhook(client, pro_order(), topic="orders/paid")

    assert not get_user(client).is_pro
    row = client.app.state.users._conn.execute(
        "SELECT days, cancelled_at FROM shopify_orders WHERE order_id = ?",
        ("1001",),
    ).fetchone()
    assert row["days"] == 0
    assert row["cancelled_at"] is not None


def test_paid_order_ledger_and_entitlement_are_atomic(app):
    client = TestClient(app)
    signup(client)
    users: UserStore = client.app.state.users
    users._conn.execute(
        "CREATE TRIGGER fail_paid_grant"
        " BEFORE UPDATE OF pro_until ON users"
        " BEGIN SELECT RAISE(ABORT, 'simulated failure'); END"
    )
    users._conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        order_webhook(client, pro_order())

    assert not get_user(client).is_pro
    assert users._conn.execute(
        "SELECT 1 FROM shopify_orders WHERE order_id = ?", ("1001",)
    ).fetchone() is None

    users._conn.execute("DROP TRIGGER fail_paid_grant")
    users._conn.commit()
    assert order_webhook(client, pro_order()).status_code == 200
    assert get_user(client).is_pro


def test_cancellation_ledger_and_entitlement_are_atomic(app):
    client = TestClient(app)
    signup(client)
    order_webhook(client, pro_order())
    users: UserStore = client.app.state.users
    before = get_user(client).pro_until
    users._conn.execute(
        "CREATE TRIGGER fail_cancel_revoke"
        " BEFORE UPDATE OF pro_until ON users"
        " BEGIN SELECT RAISE(ABORT, 'simulated failure'); END"
    )
    users._conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        order_webhook(client, pro_order(), topic="orders/cancelled")

    row = users._conn.execute(
        "SELECT days, cancelled_at FROM shopify_orders WHERE order_id = ?",
        ("1001",),
    ).fetchone()
    assert row["days"] == 31
    assert row["cancelled_at"] is None
    assert get_user(client).pro_until == before

    users._conn.execute("DROP TRIGGER fail_cancel_revoke")
    users._conn.commit()
    assert (
        order_webhook(client, pro_order(), topic="orders/cancelled").status_code
        == 200
    )
    assert not get_user(client).is_pro


def test_late_cancellation_of_expired_order_keeps_newer_purchase(app):
    client = TestClient(app)
    signup(client)
    users: UserStore = client.app.state.users
    order_webhook(client, pro_order(order_id=1))
    now = time.time()
    users._conn.execute(
        "UPDATE users SET pro_until = ? WHERE email = ?",
        (now - DAY, "kyle@example.com"),
    )
    users._conn.execute(
        "UPDATE shopify_orders"
        " SET applied_at = ?, grant_start = ?, grant_end = ?"
        " WHERE order_id = '1'",
        (now - 62 * DAY, now - 62 * DAY, now - 31 * DAY),
    )
    users._conn.commit()

    order_webhook(client, pro_order(order_id=2))
    before = get_user(client).pro_until
    assert before > now

    order_webhook(
        client,
        pro_order(order_id=1),
        topic="orders/cancelled",
    )

    assert get_user(client).pro_until == before
    assert get_user(client).is_pro


def test_webhook_unavailable_until_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    monkeypatch.delenv("SHOPIFY_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("SHOPIFY_PRIVACY_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("SHOPIFY_STORE_DOMAIN", raising=False)
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s"))
    assert client.post("/webhooks/shopify", content=b"{}").status_code == 503


@pytest.mark.parametrize(
    ("domain", "primary_secret", "privacy_secret"),
    [
        ("teststore.myshopify.com", " \t\r\n", "  "),
        (" \t\r\n", SECRET, PRIVACY_SECRET),
        ("teststore.myshopify.com/path", SECRET, PRIVACY_SECRET),
    ],
)
def test_blank_or_invalid_webhook_values_do_not_enable_shopify(
    monkeypatch,
    domain,
    primary_secret,
    privacy_secret,
):
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", domain)
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", primary_secret)
    monkeypatch.setenv("SHOPIFY_PRIVACY_WEBHOOK_SECRET", privacy_secret)

    assert not shopify_billing.commerce_enabled()
    assert not shopify_billing.webhook_endpoint_enabled()
    assert not shopify_billing.enabled()


def test_privacy_only_webhook_does_not_enable_commerce(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "teststore.myshopify.com")
    monkeypatch.delenv("SHOPIFY_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("SHOPIFY_PRIVACY_WEBHOOK_SECRET", PRIVACY_SECRET)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("SWINGLAB_SMTP_URL", raising=False)
    monkeypatch.delenv("SWINGLAB_MAIL_FROM", raising=False)

    cfg = Config()
    cfg.web["require_account"] = True
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    pro_url = "https://teststore.myshopify.com/products/swinglab-pro"

    assert shopify_billing.webhook_endpoint_enabled()
    assert not shopify_billing.commerce_enabled()
    assert not shopify_billing.enabled()
    assert pro_url not in client.get("/pricing").text

    # Privacy-only configuration must not impose commerce-connected inbox
    # proof on an otherwise local password signup.
    response = client.post(
        "/signup",
        data={"email": "local@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert pro_url not in client.get("/account").text

    # The same configuration still accepts a correctly signed mandatory
    # compliance delivery at the shared webhook endpoint.
    response = order_webhook(
        client,
        {
            "shop_domain": "teststore.myshopify.com",
            "customer": {"id": 7001},
            "orders_requested": [],
        },
        topic="customers/data_request",
        secret=PRIVACY_SECRET,
    )
    assert response.status_code == 200


def test_webhook_rejects_oversized_body_before_hmac_or_json(app):
    client = TestClient(app)
    payload = b"x" * (SHOPIFY_WEBHOOK_MAX_BODY_BYTES + 1)

    response = client.post(
        "/webhooks/shopify",
        content=payload,
        headers={
            "X-Shopify-Hmac-Sha256": "not-even-checked",
            "X-Shopify-Topic": "orders/paid",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 413
    assert "too large" in response.json()["detail"]


def test_signed_non_object_webhook_payload_is_rejected(app):
    client = TestClient(app)

    response = order_webhook(
        client,
        [],
        topic="orders/paid",
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid Shopify webhook payload"


def test_bounded_webhook_reader_rejects_chunked_body_without_length():
    chunks = [
        {
            "type": "http.request",
            "body": b"x" * (SHOPIFY_WEBHOOK_MAX_BODY_BYTES // 2 + 1),
            "more_body": True,
        },
        {
            "type": "http.request",
            "body": b"y" * (SHOPIFY_WEBHOOK_MAX_BODY_BYTES // 2 + 1),
            "more_body": False,
        },
    ]

    async def receive():
        return chunks.pop(0)

    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/webhooks/shopify",
            "raw_path": b"/webhooks/shopify",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("test", 443),
        },
        receive,
    )

    with pytest.raises(fastapi.HTTPException) as exc:
        asyncio.run(
            _read_bounded_request_body(
                request, SHOPIFY_WEBHOOK_MAX_BODY_BYTES
            )
        )
    assert exc.value.status_code == 413


def test_pages_link_to_the_store(app):
    client = TestClient(app)
    signup(client)
    url = "https://teststore.myshopify.com/products/swinglab-pro"
    assert url in client.get("/pricing").text
    assert url in client.get("/account").text

    # Once Pro, the account page shows the expiry date instead of an upsell.
    order_webhook(client, pro_order())
    html = client.get("/account").text
    assert "Pro access until" in html
    assert "Upgrade to Pro" not in html


def test_pro_survives_db_reopen_and_old_dbs_migrate(tmp_path):
    # Fresh store, grant, reopen: the time-boxed plan is durable state.
    users = UserStore(tmp_path / "db.sqlite")
    user = users.create("pro@example.com", "longenough")
    users.grant_pro_days(user.id, 31)
    reopened = UserStore(tmp_path / "db.sqlite")
    assert reopened.get(user.id).is_pro

    # A pre-Shopify database (no pro_until column) migrates on open.
    import sqlite3

    old = tmp_path / "old.sqlite"
    conn = sqlite3.connect(old)
    conn.execute(
        "CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,"
        " password_hash TEXT NOT NULL, created_at REAL NOT NULL,"
        " stripe_customer_id TEXT, plan TEXT NOT NULL DEFAULT 'free',"
        " subscription_status TEXT NOT NULL DEFAULT 'none')"
    )
    conn.execute(
        "INSERT INTO users (id, email, password_hash, created_at)"
        " VALUES ('u1', 'old@example.com', 'x', 0)"
    )
    conn.commit()
    conn.close()
    migrated = UserStore(old)
    veteran = migrated.get("u1")
    assert veteran is not None and not veteran.is_pro
    migrated.grant_pro_days("u1", 31)
    assert migrated.get("u1").is_pro
