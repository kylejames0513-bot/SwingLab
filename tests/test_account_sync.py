"""Shopify-to-app account sync: customer webhooks provision "store
accounts" (passwordless stubs), signup claims them in place — keeping the
Shopify link and anything bought via order webhooks — and store-side
deletion never destroys app data.

Shopify itself is never called: customers arrive as signed webhooks, so
the tests post payloads shaped like Shopify's customer events to the same
/webhooks/shopify endpoint (and signing secret) the order tests use.
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
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("SWINGLAB_SMTP_URL", raising=False)
    monkeypatch.delenv("SWINGLAB_MAIL_FROM", raising=False)
    cfg = Config()
    cfg.web["require_account"] = True
    return create_app(cfg, sessions_dir=tmp_path / "sessions")


def webhook(client, payload, topic, secret=SECRET):
    body = json.dumps(payload).encode()
    signature = base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()
    return client.post(
        "/webhooks/shopify",
        content=body,
        headers={
            "X-Shopify-Hmac-Sha256": signature,
            "X-Shopify-Topic": topic,
            "Content-Type": "application/json",
        },
    )


def customer(customer_id=7001, email="buyer@example.com"):
    return {"id": customer_id, "email": email, "first_name": "Buyer"}


def pro_order(order_id=1001, email="buyer@example.com", sku="SL-PRO-1MO"):
    return {
        "id": order_id,
        "email": email,
        "line_items": [{"sku": sku, "quantity": 1}],
    }


def signup(client, email="buyer@example.com", password="longenough"):
    resp = client.post(
        "/signup", data={"email": email, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return resp


def get_user(client, email="buyer@example.com"):
    users: UserStore = client.app.state.users
    return users.get_by_email(email)


def count_users(client):
    db = client.app.state.jobs.sessions_dir / "swinglab.db"
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


# -- provisioning ----------------------------------------------------------

def test_customers_create_provisions_a_stub(app):
    client = TestClient(app)
    assert webhook(client, customer(), "customers/create").status_code == 200

    user = get_user(client)
    assert user is not None
    assert user.shopify_customer_id == "7001"
    assert user.source == "shopify"
    assert not user.has_password  # a stub can't log in until claimed
    users: UserStore = client.app.state.users
    assert users.authenticate("buyer@example.com", "") is None
    assert users.get_by_shopify("7001").id == user.id


def test_replayed_customer_webhook_is_idempotent(app):
    client = TestClient(app)
    webhook(client, customer(), "customers/create")
    first = get_user(client)
    webhook(client, customer(), "customers/create")
    webhook(client, customer(), "customers/update")
    assert count_users(client) == 1
    assert get_user(client).id == first.id


def test_customer_webhook_email_is_normalized(app):
    client = TestClient(app)
    webhook(client, customer(email="  KYLE@Example.COM "), "customers/create")
    assert get_user(client, "kyle@example.com") is not None
    # ...and matches an app signup spelled differently.
    signup(client, email="Kyle@example.com  ")
    assert count_users(client) == 1


def test_customer_webhook_bad_signature_rejected(app):
    client = TestClient(app)
    resp = webhook(client, customer(), "customers/create", secret="wrong")
    assert resp.status_code == 400
    assert get_user(client) is None


def test_malformed_signature_header_is_rejected_not_500(app, monkeypatch):
    # An empty or garbage-but-ASCII signature rejects cleanly (400).
    client = TestClient(app)
    body = json.dumps(customer()).encode()
    for bad_sig in ("", "!!not-base64!!", "AAAA"):
        resp = client.post(
            "/webhooks/shopify",
            content=body,
            headers={
                "X-Shopify-Hmac-Sha256": bad_sig,
                "X-Shopify-Topic": "customers/create",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 400, bad_sig
    assert get_user(client) is None

    # A non-ASCII signature (what an ASGI server yields when a client sends
    # raw high bytes, decoded latin-1) must raise ValueError — NOT the
    # TypeError that str compare_digest throws on non-ASCII, which would
    # surface as a 500. TestClient/httpx can't transmit such a header, so
    # drive the verifier directly.
    from swinglab.web import shopify_billing
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", SECRET)
    for bad_sig in ("\xff\xfe\xfa", "café-signature"):
        with pytest.raises(ValueError):
            shopify_billing.handle_webhook(
                body, bad_sig, "customers/create",
                client.app.state.users, client.app.state.cfg,
            )
    assert get_user(client) is None


def test_customers_update_links_existing_account_untouched(app):
    client = TestClient(app)
    signup(client)  # app-born account first
    before = get_user(client)
    assert before.shopify_customer_id is None

    webhook(client, customer(email="Buyer@Example.com"), "customers/update")
    user = get_user(client)
    assert user.id == before.id
    assert user.shopify_customer_id == "7001"
    assert user.email == "buyer@example.com"  # email never overwritten
    assert user.source is None  # origin stays app-born

    client.post("/logout")
    resp = client.post(  # password never overwritten either
        "/login", data={"email": "buyer@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    assert resp.status_code == 303


# -- claiming --------------------------------------------------------------

def test_signup_claims_stub_and_keeps_everything_bought(app):
    client = TestClient(app)
    webhook(client, customer(), "customers/create")
    webhook(client, pro_order(), "orders/paid")  # lands on the stub directly
    stub = get_user(client)
    assert stub.pro_until > time.time() and not stub.has_password

    signup(client, email="  BUYER@example.COM ")  # normalization must match
    user = get_user(client)
    assert user.id == stub.id  # same row claimed — no duplicate users
    assert user.has_password
    assert user.is_pro
    assert abs(user.pro_until - (time.time() + 31 * DAY)) < 60
    assert user.shopify_customer_id == "7001"
    assert count_users(client) == 1

    assert "Connected to the CaddieInsight store" in client.get("/account").text


def test_claim_composes_with_parked_presignup_purchase(app):
    client = TestClient(app)
    # Order arrives before the customer webhook: days park in pro_grants,
    # then the stub appears, then signup claims stub + parked days at once.
    webhook(client, pro_order(), "orders/paid")
    webhook(client, customer(), "customers/create")
    signup(client)
    user = get_user(client)
    assert user.is_pro and user.shopify_customer_id == "7001"
    assert count_users(client) == 1


def test_stub_login_shows_finish_setup_notice(app):
    client = TestClient(app)
    webhook(client, customer(), "customers/create")
    resp = client.post(
        "/login", data={"email": "Buyer@Example.com", "password": "whatever1"}
    )
    assert resp.status_code == 200
    assert "store account" in resp.text
    assert "create your password" in resp.text
    assert "Wrong email or password" not in resp.text
    assert 'value="buyer@example.com"' in resp.text  # signup prefilled


def test_wrong_password_on_claimed_account_stays_generic(app):
    client = TestClient(app)
    signup(client)
    client.post("/logout")
    resp = client.post(
        "/login", data={"email": "buyer@example.com", "password": "wrongwrong"}
    )
    assert "Wrong email or password" in resp.text
    assert "store account" not in resp.text


# -- deletion / redaction --------------------------------------------------

def test_customers_delete_removes_unclaimed_stub(app):
    client = TestClient(app)
    webhook(client, customer(), "customers/create")
    webhook(client, {"id": 7001}, "customers/delete")  # delete payload: id only
    assert get_user(client) is None
    # Replayed deletion of a now-unknown customer is still a 200 no-op.
    assert webhook(client, {"id": 7001}, "customers/delete").status_code == 200


def test_customers_delete_parks_bought_days_for_later_signup(app):
    client = TestClient(app)
    webhook(client, customer(), "customers/create")
    webhook(client, pro_order(), "orders/paid")
    webhook(client, {"id": 7001}, "customers/delete")
    assert get_user(client) is None

    signup(client)  # keeps what they bought even though the stub was deleted
    user = get_user(client)
    assert user.is_pro
    assert abs(user.pro_until - (time.time() + 31 * DAY)) < 60


def test_customers_delete_only_unlinks_claimed_accounts(app):
    client = TestClient(app)
    signup(client)
    webhook(client, customer(), "customers/update")
    assert get_user(client).shopify_customer_id == "7001"

    webhook(client, {"id": 7001}, "customers/delete")
    user = get_user(client)
    assert user is not None  # app data survives store-side deletion
    assert user.shopify_customer_id is None
    client.post("/logout")
    resp = client.post(
        "/login", data={"email": "buyer@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def test_stub_with_analyses_is_never_deleted(app):
    client = TestClient(app)
    webhook(client, customer(), "customers/create")
    stub = get_user(client)
    client.app.state.jobs.create_session(source_name="clip.mov", user_id=stub.id)

    webhook(client, {"id": 7001}, "customers/delete")
    user = get_user(client)
    assert user is not None and user.id == stub.id
    assert user.shopify_customer_id is None  # unlinked, not destroyed


def test_customers_redact_clears_store_fields_on_claimed_account(app):
    client = TestClient(app)
    webhook(client, customer(), "customers/create")
    signup(client)  # claim -> source == 'shopify', link set
    user = get_user(client)
    assert user.source == "shopify" and user.shopify_customer_id == "7001"

    payload = {"shop_domain": "teststore.myshopify.com", "customer": customer()}
    assert webhook(client, payload, "customers/redact").status_code == 200
    user = get_user(client)
    assert user is not None  # claimed accounts survive redaction
    assert user.shopify_customer_id is None
    assert user.source is None


def test_customers_redact_erases_unclaimed_stub_and_parked_days(app):
    client = TestClient(app)
    webhook(client, pro_order(), "orders/paid")  # parked (no user yet)
    webhook(client, customer(), "customers/create")
    payload = {"shop_domain": "teststore.myshopify.com", "customer": customer()}
    webhook(client, payload, "customers/redact")
    assert get_user(client) is None
    signup(client)  # redaction is erasure: nothing carries over
    assert not get_user(client).is_pro


def test_gdpr_ack_topics_return_200(app):
    client = TestClient(app)
    payload = {"shop_domain": "teststore.myshopify.com", "customer": customer()}
    assert webhook(client, payload, "customers/data_request").status_code == 200
    assert webhook(client, {"shop_domain": "x"}, "shop/redact").status_code == 200


# -- migration -------------------------------------------------------------

def test_existing_db_gains_store_columns_in_place(tmp_path):
    # A database from before account sync (and before Shopify billing —
    # the whole migration chain must run) upgrades on open, keeps its
    # users, and supports the new store-account operations. Opening it
    # again must be a no-op (idempotent).
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
        " VALUES ('u1', 'old@example.com', 'scrypt$x', 0)"
    )
    conn.commit()
    conn.close()

    users = UserStore(old)
    veteran = users.get("u1")
    assert veteran.shopify_customer_id is None and veteran.source is None
    assert veteran.has_password

    stub = users.upsert_store_customer("new@example.com", "42")
    assert stub.source == "shopify" and not stub.has_password

    reopened = UserStore(old)  # second open: migrations must be no-ops
    assert reopened.get("u1") is not None
    assert reopened.get_by_shopify("42").email == "new@example.com"
