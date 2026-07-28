"""Selling Pro through the Shopify store.

Shopify itself is never called: checkout happens on the storefront, and
purchases arrive as signed order webhooks — so the tests post payloads
shaped like Shopify's to /webhooks/shopify, signed with the test secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sqlite3
import time

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from swinglab.web.users import UserStore

from tests.test_web import fake_analyze_ok

SECRET = "shpss_test_secret"
DAY = 86400


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "teststore.myshopify.com")
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", SECRET)
    cfg = Config()
    cfg.web["require_account"] = True
    return create_app(cfg, sessions_dir=tmp_path / "sessions")


def signup(client, email="kyle@example.com", password="longenough"):
    resp = client.post(
        "/signup", data={"email": email, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 303


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
            "Content-Type": "application/json",
        },
    )


def pro_order(order_id=1001, email="kyle@example.com", sku="SL-PRO-1MO", qty=1):
    return {
        "id": order_id,
        "email": email,
        "line_items": [{"sku": sku, "quantity": qty}],
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
    monkeypatch.delenv("SHOPIFY_STORE_DOMAIN", raising=False)
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s"))
    assert client.post("/webhooks/shopify", content=b"{}").status_code == 503


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
