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
import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

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


def webhook(
    client,
    payload,
    topic,
    secret=SECRET,
    shop_domain="teststore.myshopify.com",
    webhook_id=None,
):
    body = json.dumps(payload).encode()
    signature = base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()
    delivery_id = webhook_id or hashlib.sha256(
        topic.encode() + b"\0" + body
    ).hexdigest()
    return client.post(
        "/webhooks/shopify",
        content=body,
        headers={
            "X-Shopify-Hmac-Sha256": signature,
            "X-Shopify-Topic": topic,
            "X-Shopify-Shop-Domain": shop_domain,
            "X-Shopify-Webhook-Id": delivery_id,
            "Content-Type": "application/json",
        },
    )


def customer(customer_id=7001, email="buyer@example.com", updated_at=None):
    payload = {"id": customer_id, "email": email, "first_name": "Buyer"}
    if updated_at is not None:
        payload["updated_at"] = updated_at
    return payload


def pro_order(
    order_id=1001,
    email="buyer@example.com",
    sku="SL-PRO-1MO",
    customer_id=None,
):
    order = {
        "id": order_id,
        "email": email,
        "line_items": [{"sku": sku, "quantity": 1}],
    }
    if customer_id is not None:
        order["customer"] = {"id": customer_id, "email": email}
    return order


def signup(
    client,
    email="buyer@example.com",
    password="longenough",
    *,
    verified=True,
):
    resp = client.post(
        "/signup", data={"email": email, "password": password},
        follow_redirects=False,
    )
    if resp.status_code == 503:
        # Account-sync tests do not exercise mail transport. Model successful
        # inbox proof directly when a Shopify identity/value makes it
        # mandatory; the dedicated mailer tests cover the HTTP email flow.
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


def test_customer_gid_is_canonicalized_without_false_conflict_log(app, caplog):
    client = TestClient(app)
    payload = customer(
        customer_id="gid://shopify/Customer/7001",
        email="buyer@example.com",
    )

    with caplog.at_level(logging.INFO, logger="swinglab.web.shopify"):
        assert webhook(client, payload, "customers/create").status_code == 200

    assert get_user(client).shopify_customer_id == "7001"
    assert not any(
        "identity conflict" in record.message for record in caplog.records
    )


def test_shopify_webhook_logs_redact_customer_and_order_data(app, caplog):
    client = TestClient(app)
    customer_id = 987654321
    order_id = 123456789
    refund_id = 456789123
    email = "private.buyer@example.com"

    with caplog.at_level(logging.INFO, logger="swinglab.web.shopify"):
        webhook(
            client,
            customer(customer_id=customer_id, email=email),
            "customers/create",
        )
        webhook(
            client,
            pro_order(
                order_id=order_id,
                email=email,
                customer_id=customer_id,
            ),
            "orders/paid",
        )
        webhook(
            client,
            {
                "id": refund_id,
                "order_id": order_id,
                "refund_line_items": [
                    {
                        "quantity": 1,
                        "line_item": {"sku": "GEAR-ONLY"},
                    }
                ],
            },
            "refunds/create",
        )

    rendered = "\n".join(record.getMessage() for record in caplog.records)
    for protected_value in (email, customer_id, order_id, refund_id):
        assert str(protected_value) not in rendered


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


def test_claimed_customer_email_change_keeps_one_stable_app_identity(app):
    client = TestClient(app)
    signup(client, email="old@example.com")
    webhook(
        client,
        customer(email="old@example.com"),
        "customers/update",
    )
    original = get_user(client, "old@example.com")

    webhook(
        client,
        customer(email="new@example.com"),
        "customers/update",
    )

    same_user = get_user(client, "old@example.com")
    assert same_user.id == original.id
    assert same_user.shopify_customer_id == "7001"
    assert get_user(client, "new@example.com") is None
    assert count_users(client) == 1

    # Checkout email can change, but the stable Shopify id still assigns
    # the purchase to the already-linked CaddieInsight account.
    webhook(
        client,
        pro_order(email="new@example.com", customer_id=7001),
        "orders/paid",
    )
    assert get_user(client, "old@example.com").is_pro


def test_unclaimed_store_email_change_moves_the_same_stub(app):
    client = TestClient(app)
    webhook(client, customer(email="old@example.com"), "customers/create")
    original = get_user(client, "old@example.com")

    webhook(client, customer(email="new@example.com"), "customers/update")

    moved = get_user(client, "new@example.com")
    assert moved.id == original.id
    assert moved.shopify_customer_id == "7001"
    assert get_user(client, "old@example.com") is None
    assert count_users(client) == 1


def test_stale_customer_update_cannot_move_identity_back_to_old_email(app):
    client = TestClient(app)
    webhook(
        client,
        customer(
            email="old@example.com",
            updated_at="2026-07-28T12:00:00Z",
        ),
        "customers/create",
    )
    original = get_user(client, "old@example.com")
    webhook(
        client,
        customer(
            email="new@example.com",
            updated_at="2026-07-28T13:00:00Z",
        ),
        "customers/update",
    )
    webhook(
        client,
        customer(
            email="old@example.com",
            updated_at="2026-07-28T12:30:00Z",
        ),
        "customers/update",
    )

    current = get_user(client, "new@example.com")
    assert current.id == original.id
    assert current.shopify_customer_id == "7001"
    assert get_user(client, "old@example.com") is None


def test_unclaimed_email_change_keeps_pending_order_cancellable(app):
    client = TestClient(app)
    users: UserStore = client.app.state.users
    webhook(
        client,
        pro_order(email="old@example.com", customer_id=7001),
        "orders/paid",
    )
    webhook(client, customer(email="old@example.com"), "customers/create")
    webhook(client, customer(email="new@example.com"), "customers/update")

    webhook(
        client,
        pro_order(email="new@example.com", customer_id=7001),
        "orders/cancelled",
    )

    moved = get_user(client, "new@example.com")
    assert not moved.is_pro
    assert users.pending_grant_days("old@example.com") == 0
    assert users.pending_grant_days("new@example.com") == 0


def test_unclaimed_email_change_moves_unambiguous_guest_order(app):
    client = TestClient(app)
    users: UserStore = client.app.state.users
    webhook(
        client,
        pro_order(order_id=1001, email="old@example.com"),
        "orders/paid",
    )
    webhook(client, customer(email="old@example.com"), "customers/create")
    webhook(client, customer(email="new@example.com"), "customers/update")

    assert users.pending_grant_days("old@example.com") == 0
    assert users.pending_grant_days("new@example.com") == 31
    moved_order = users._conn.execute(
        "SELECT email, shopify_customer_id, pending_days"
        " FROM shopify_orders WHERE order_id = '1001'"
    ).fetchone()
    assert tuple(moved_order) == ("new@example.com", "7001", 31)

    signup(client, email="new@example.com")
    assert get_user(client, "new@example.com").is_pro
    webhook(
        client,
        pro_order(order_id=1001, email="old@example.com"),
        "orders/cancelled",
    )
    assert not get_user(client, "new@example.com").is_pro


def test_email_change_never_moves_another_customers_pending_order(app):
    client = TestClient(app)
    users: UserStore = client.app.state.users
    webhook(
        client,
        pro_order(
            order_id=1001,
            email="old@example.com",
            customer_id=7001,
        ),
        "orders/paid",
    )
    webhook(
        client,
        pro_order(
            order_id=1002,
            email="old@example.com",
            customer_id=7002,
        ),
        "orders/paid",
    )
    webhook(client, customer(email="old@example.com"), "customers/create")
    webhook(client, customer(email="new@example.com"), "customers/update")

    assert users.pending_grant_days("new@example.com") == 31
    assert users.pending_grant_days("old@example.com") == 31
    orders = {
        row["order_id"]: row["email"]
        for row in users._conn.execute(
            "SELECT order_id, email FROM shopify_orders"
            " ORDER BY order_id"
        )
    }
    assert orders == {
        "1001": "new@example.com",
        "1002": "old@example.com",
    }

    signup(client, email="new@example.com")
    before = get_user(client, "new@example.com").pro_until
    webhook(
        client,
        pro_order(
            order_id=1002,
            email="old@example.com",
            customer_id=7002,
        ),
        "orders/cancelled",
    )
    assert get_user(client, "new@example.com").pro_until == before


def test_paid_customer_id_conflict_never_grants_email_stub(app):
    client = TestClient(app)
    users: UserStore = client.app.state.users
    webhook(
        client,
        customer(customer_id=7001, email="shared@example.com"),
        "customers/create",
    )

    webhook(
        client,
        pro_order(
            order_id=1002,
            email="shared@example.com",
            customer_id=7002,
        ),
        "orders/paid",
    )

    customer_c = get_user(client, "shared@example.com")
    assert customer_c.shopify_customer_id == "7001"
    assert not customer_c.is_pro
    order_d = users._conn.execute(
        "SELECT user_id, shopify_customer_id, pending_days"
        " FROM shopify_orders WHERE order_id = '1002'"
    ).fetchone()
    assert tuple(order_d) == (None, "7002", 31)

    webhook(
        client,
        customer(customer_id=7001, email="new@example.com"),
        "customers/update",
    )
    order_d = users._conn.execute(
        "SELECT email, user_id, pending_days FROM shopify_orders"
        " WHERE order_id = '1002'"
    ).fetchone()
    assert tuple(order_d) == ("shared@example.com", None, 31)
    assert users.pending_grant_days("shared@example.com") == 31
    assert users.pending_grant_days("new@example.com") == 0


def test_order_before_customer_moves_by_stable_id_to_current_email(app):
    client = TestClient(app)
    users: UserStore = client.app.state.users
    webhook(
        client,
        pro_order(
            order_id=1001,
            email="old@example.com",
            customer_id=7002,
        ),
        "orders/paid",
    )

    webhook(
        client,
        customer(customer_id=7002, email="new@example.com"),
        "customers/create",
    )

    moved = users._conn.execute(
        "SELECT email, shopify_customer_id, pending_days"
        " FROM shopify_orders WHERE order_id = '1001'"
    ).fetchone()
    assert tuple(moved) == ("new@example.com", "7002", 31)
    assert users.pending_grant_days("old@example.com") == 0
    assert users.pending_grant_days("new@example.com") == 31

    signup(client, email="new@example.com")
    user = get_user(client, "new@example.com")
    assert user.shopify_customer_id == "7002"
    assert user.is_pro


def test_cancelling_direct_order_does_not_consume_an_older_pending_order(app):
    client = TestClient(app)
    users: UserStore = client.app.state.users
    # A arrives before the customer account and remains parked.
    webhook(
        client,
        pro_order(
            order_id=1001,
            email="buyer@example.com",
            customer_id=7001,
        ),
        "orders/paid",
    )
    webhook(client, customer(), "customers/create")
    # B arrives after the stub exists and is applied directly to it.
    webhook(
        client,
        pro_order(
            order_id=1002,
            email="buyer@example.com",
            customer_id=7001,
        ),
        "orders/paid",
    )
    users._conn.execute(
        "UPDATE users SET pro_until = pro_until - ? WHERE email = ?",
        (10 * DAY, "buyer@example.com"),
    )
    users._conn.commit()

    webhook(
        client,
        pro_order(
            order_id=1002,
            email="buyer@example.com",
            customer_id=7001,
        ),
        "orders/cancelled",
    )

    assert users.pending_grant_days("buyer@example.com") == 31
    assert not get_user(client).is_pro
    signup(client)
    assert get_user(client).is_pro  # order A is still valid and claimable


# -- claiming --------------------------------------------------------------

def test_signup_claims_stub_and_keeps_everything_bought(app):
    client = TestClient(app)
    webhook(client, customer(), "customers/create")
    webhook(
        client,
        pro_order(customer_id=7001),
        "orders/paid",
    )  # stable customer identity lands on the stub directly
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


def test_delete_before_delayed_create_does_not_recreate_customer(app):
    client = TestClient(app)
    webhook(client, {"id": 7001}, "customers/delete")
    webhook(client, customer(), "customers/create")
    webhook(client, customer(), "customers/update")

    assert get_user(client) is None
    assert count_users(client) == 0


def test_delete_for_different_customer_id_does_not_unlink_same_email(app):
    client = TestClient(app)
    webhook(client, customer(customer_id=7001), "customers/create")

    webhook(
        client,
        customer(customer_id=7002),
        "customers/delete",
    )

    user = get_user(client)
    assert user is not None
    assert user.shopify_customer_id == "7001"


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


def test_deleted_stub_purchase_claim_locks_former_customer_identity(app):
    client = TestClient(app)
    users: UserStore = client.app.state.users
    webhook(client, customer(customer_id=7001), "customers/create")
    webhook(
        client,
        pro_order(order_id=1001, customer_id=7001),
        "orders/paid",
    )
    webhook(client, {"id": 7001}, "customers/delete")
    assert get_user(client) is None

    signup(client)
    account = get_user(client)
    assert account.is_pro
    assert account.shopify_customer_id is None
    assert account.shopify_identity_locked
    former = users._conn.execute(
        "SELECT former_user_id FROM shopify_customer_tombstones"
        " WHERE customer_id = '7001'"
    ).fetchone()
    assert former["former_user_id"] == account.id

    before = account.pro_until
    webhook(
        client,
        pro_order(order_id=1002, customer_id=7002),
        "orders/paid",
    )
    assert get_user(client).pro_until == before
    assert users.claim_pending_grant(account.id, account.email) == 0
    unrelated = users._conn.execute(
        "SELECT user_id, pending_days FROM shopify_orders"
        " WHERE order_id = '1002'"
    ).fetchone()
    assert tuple(unrelated) == (None, 31)


def test_delete_reclaim_preserves_each_orders_remaining_days(app):
    client = TestClient(app)
    webhook(client, customer(), "customers/create")
    webhook(
        client,
        pro_order(order_id=1001, customer_id=7001),
        "orders/paid",
    )
    webhook(
        client,
        pro_order(order_id=1002, customer_id=7001),
        "orders/paid",
    )
    users: UserStore = client.app.state.users
    now = time.time()
    user = get_user(client)
    chain = "test-two-order-chain"
    users._conn.execute(
        "UPDATE users SET pro_until = ? WHERE id = ?",
        (now + 22 * DAY, user.id),
    )
    users._conn.execute(
        "UPDATE shopify_orders"
        " SET grant_chain = ?, grant_start = ?, grant_end = ?"
        " WHERE order_id = '1001'",
        (chain, now - 40 * DAY, now - 9 * DAY),
    )
    users._conn.execute(
        "UPDATE shopify_orders"
        " SET grant_chain = ?, grant_start = ?, grant_end = ?"
        " WHERE order_id = '1002'",
        (chain, now - 9 * DAY, now + 22 * DAY),
    )
    users._conn.commit()

    webhook(client, {"id": 7001}, "customers/delete")
    parked = {
        row["order_id"]: row["pending_days"]
        for row in users._conn.execute(
            "SELECT order_id, pending_days FROM shopify_orders"
            " WHERE order_id IN ('1001', '1002')"
        )
    }
    assert parked["1001"] == 0
    assert parked["1002"] == pytest.approx(22, abs=0.01)

    signup(client)
    before = get_user(client).pro_until
    webhook(client, pro_order(order_id=1001), "orders/cancelled")

    assert get_user(client).pro_until == pytest.approx(before)
    assert get_user(client).is_pro
    order2 = users._conn.execute(
        "SELECT user_id, grant_start, grant_end FROM shopify_orders"
        " WHERE order_id = '1002'"
    ).fetchone()
    assert order2["user_id"] == get_user(client).id
    assert order2["grant_end"] > order2["grant_start"]


def test_customer_delete_rolls_back_everything_if_parking_fails(app):
    client = TestClient(app)
    webhook(client, customer(), "customers/create")
    webhook(
        client,
        pro_order(customer_id=7001),
        "orders/paid",
    )
    users: UserStore = client.app.state.users
    users._conn.execute(
        "CREATE TRIGGER fail_customer_delete"
        " BEFORE INSERT ON pro_grants"
        " BEGIN SELECT RAISE(ABORT, 'simulated failure'); END"
    )
    users._conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        webhook(client, {"id": 7001}, "customers/delete")

    user = get_user(client)
    assert user is not None
    assert user.shopify_customer_id == "7001"
    assert user.is_pro
    assert users._conn.execute(
        "SELECT 1 FROM shopify_customer_tombstones WHERE customer_id = '7001'"
    ).fetchone() is None

    users._conn.execute("DROP TRIGGER fail_customer_delete")
    users._conn.commit()
    assert webhook(client, {"id": 7001}, "customers/delete").status_code == 200
    assert get_user(client) is None
    assert users.pending_grant_days("buyer@example.com") > 0


def test_customers_delete_only_unlinks_claimed_accounts(app):
    client = TestClient(app)
    signup(client)
    webhook(client, customer(), "customers/update")
    assert get_user(client).shopify_customer_id == "7001"

    webhook(client, {"id": 7001}, "customers/delete")
    user = get_user(client)
    assert user is not None  # app data survives store-side deletion
    assert user.shopify_customer_id is None
    assert user.shopify_identity_locked
    client.post("/logout")
    resp = client.post(
        "/login", data={"email": "buyer@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def test_customers_delete_preserves_authenticated_passwordless_profile(app):
    client = TestClient(app)
    users: UserStore = client.app.state.users
    webhook(client, customer(), "customers/create")
    account = get_user(client)
    account = users.link_shopify_customer_account(
        account.id,
        subject="gid://shopify/Customer/7001",
        customer_id="7001",
        authenticated=True,
    )
    assert account.claimed
    assert not account.has_password
    assert account.email_verified_at is None
    profile = users.upsert_golfer_profile(
        account.id,
        display_name="Shopify Golfer",
        experience_mode="improve",
        handicap_range="",
        primary_goal="consistency",
        practice_minutes=20,
        sessions_per_week=2,
        handedness="right",
        camera_angle="face-on",
        preferred_club="",
    )
    assert client.app.state.jobs.list_recent(user_id=account.id) == []

    assert webhook(
        client, {"id": 7001}, "customers/delete"
    ).status_code == 200

    kept = users.get(account.id)
    assert kept is not None
    assert kept.shopify_customer_id is None
    assert kept.claimed
    assert users.get_golfer_profile(account.id) == profile


def test_deleted_link_cannot_infer_another_customer_from_shared_email(app):
    client = TestClient(app)
    users: UserStore = client.app.state.users
    shared = "shared@example.com"
    webhook(
        client,
        pro_order(order_id=1001, email=shared, customer_id=7001),
        "orders/paid",
    )
    webhook(
        client,
        pro_order(order_id=1002, email=shared, customer_id=7002),
        "orders/paid",
    )
    webhook(
        client,
        customer(customer_id=7001, email=shared),
        "customers/create",
    )
    signup(client, email=shared)
    account = get_user(client, shared)
    assert account.shopify_customer_id == "7001"
    assert account.shopify_identity_locked
    assert account.is_pro

    webhook(client, {"id": 7001}, "customers/delete")
    account = get_user(client, shared)
    assert account.shopify_customer_id is None
    assert account.shopify_identity_locked

    assert users.claim_pending_grant(account.id, shared) == 0
    customer_d = users._conn.execute(
        "SELECT user_id, pending_days FROM shopify_orders"
        " WHERE order_id = '1002'"
    ).fetchone()
    assert tuple(customer_d) == (None, 31)
    assert get_user(client, shared).shopify_customer_id is None


def test_new_customer_order_cannot_take_over_deleted_link_by_email(app):
    client = TestClient(app)
    users: UserStore = client.app.state.users
    shared = "shared@example.com"
    signup(client, email=shared)
    webhook(
        client,
        customer(customer_id=7001, email=shared),
        "customers/create",
    )
    webhook(client, {"id": 7001}, "customers/delete")
    account = get_user(client, shared)
    before = account.pro_until
    assert account.shopify_customer_id is None
    assert account.shopify_identity_locked

    webhook(
        client,
        pro_order(order_id=1002, email=shared, customer_id=7002),
        "orders/paid",
    )

    account = get_user(client, shared)
    assert account.pro_until == before
    assert account.shopify_customer_id is None
    parked = users._conn.execute(
        "SELECT user_id, shopify_customer_id, pending_days"
        " FROM shopify_orders WHERE order_id = '1002'"
    ).fetchone()
    assert tuple(parked) == (None, "7002", 31)


def test_foreign_delete_cannot_bind_to_another_former_customer(app):
    client = TestClient(app)
    users: UserStore = client.app.state.users
    shared = "shared@example.com"
    signup(client, email=shared)
    webhook(
        client,
        customer(customer_id=7002, email=shared),
        "customers/create",
    )
    account = get_user(client, shared)
    webhook(client, {"id": 7002}, "customers/delete")
    assert get_user(client, shared).shopify_customer_id is None

    webhook(
        client,
        customer(customer_id=7001, email=shared),
        "customers/delete",
    )
    foreign_tombstone = users._conn.execute(
        "SELECT former_user_id FROM shopify_customer_tombstones"
        " WHERE customer_id = '7001'"
    ).fetchone()
    assert foreign_tombstone["former_user_id"] is None

    webhook(
        client,
        pro_order(order_id=1001, email=shared, customer_id=7001),
        "orders/paid",
    )
    victim = get_user(client, shared)
    assert victim.id == account.id
    assert not victim.is_pro
    parked = users._conn.execute(
        "SELECT user_id, pending_days FROM shopify_orders"
        " WHERE order_id = '1001'"
    ).fetchone()
    assert tuple(parked) == (None, 31)


def test_late_order_for_same_deleted_customer_returns_to_former_account(app):
    client = TestClient(app)
    users: UserStore = client.app.state.users
    signup(client)
    webhook(client, customer(customer_id=7001), "customers/create")
    account = get_user(client)
    webhook(client, {"id": 7001}, "customers/delete")
    unlinked = get_user(client)
    assert unlinked.id == account.id
    assert unlinked.shopify_customer_id is None
    assert unlinked.shopify_identity_locked

    webhook(
        client,
        pro_order(order_id=1001, customer_id=7001),
        "orders/paid",
    )

    recovered = get_user(client)
    assert recovered.id == account.id
    assert recovered.shopify_customer_id is None
    assert recovered.is_pro
    order = users._conn.execute(
        "SELECT user_id, shopify_customer_id, pending_days"
        " FROM shopify_orders WHERE order_id = '1001'"
    ).fetchone()
    assert tuple(order) == (account.id, "7001", 0)
    assert users.pending_grant_days(account.email) == 0
    tombstone = users._conn.execute(
        "SELECT redacted, former_user_id"
        " FROM shopify_customer_tombstones WHERE customer_id = '7001'"
    ).fetchone()
    assert tuple(tombstone) == (0, account.id)

    payload = {"shop_domain": "teststore.myshopify.com", "customer": customer()}
    webhook(client, payload, "customers/redact")
    tombstone = users._conn.execute(
        "SELECT redacted, former_user_id"
        " FROM shopify_customer_tombstones WHERE customer_id = '7001'"
    ).fetchone()
    assert tuple(tombstone) == (1, None)


def test_customer_order_waits_for_identity_link_then_reconciles(app):
    client = TestClient(app)
    users: UserStore = client.app.state.users
    signup(client)
    account = get_user(client)
    assert account.shopify_customer_id is None
    assert not account.shopify_identity_locked

    webhook(
        client,
        pro_order(order_id=1001, customer_id=7001),
        "orders/paid",
    )
    assert not get_user(client).is_pro
    parked = users._conn.execute(
        "SELECT user_id, pending_days FROM shopify_orders"
        " WHERE order_id = '1001'"
    ).fetchone()
    assert tuple(parked) == (None, 31)

    webhook(client, customer(customer_id=7001), "customers/create")
    account = get_user(client)
    assert account.shopify_customer_id == "7001"
    assert account.shopify_identity_locked
    assert get_user(client).is_pro
    # The link is what the parked order was waiting for, so the customer
    # webhook claims it. Nothing is left for a later login to pick up.
    assert users.claim_pending_grant(account.id, account.email) == 0


def test_paid_before_customer_webhook_grants_without_a_second_visit(app):
    """A buyer is Pro when the webhooks land, not when they next sign in.

    Shopify does not guarantee delivery order, so a customer-bearing
    orders/paid routinely arrives before customers/create. users.py parks it
    on purpose: granting on a matching email alone would let anyone who
    registers an address first inherit a stranger's purchase. The link the
    parking waits for is established by exactly one event — and until now
    nothing claimed the grant when it arrived, so the days sat in pro_grants
    until the buyer happened to log in again. Somebody who paid and then
    closed the tab stayed on Free.
    """
    client = TestClient(app)
    users: UserStore = client.app.state.users
    signup(client)

    webhook(client, pro_order(order_id=1001, customer_id=7001), "orders/paid")
    assert not get_user(client).is_pro  # parked, correctly

    webhook(client, customer(customer_id=7001), "customers/create")

    user = get_user(client)
    assert user.is_pro, "the identity link did not release the parked grant"
    assert abs(user.pro_until - (time.time() + 31 * DAY)) < 60
    assert users.claim_pending_grant(user.id, user.email) == 0

    parked = users._conn.execute(
        "SELECT user_id, pending_days FROM shopify_orders"
        " WHERE order_id = '1001'"
    ).fetchone()
    assert tuple(parked) == (user.id, 0)


def test_customer_webhook_does_not_grant_onto_an_unclaimed_stub(app):
    """The claim-on-link must not fire for a row nobody has proven they own.

    customers/create provisions a stub for an email that has never signed in.
    An unclaimed stub is still allowed to follow a store-side email change in
    place, so granting Pro onto one would let a later customers/update carry
    that Pro to an address the buyer never controlled. The days stay parked
    until signup or an emailed code proves ownership — both of which already
    claim on the way in.
    """
    client = TestClient(app)
    users: UserStore = client.app.state.users

    # A CUSTOMER-BEARING order is what makes this case discriminating. A guest
    # order is already refused for an unverified row by claim_pending_grant
    # itself (it requires email_verified when the order carries no customer
    # id), so it would pass this test with the gate deleted. An order whose
    # customer id matches the stub's link satisfies eligibility on the link
    # alone — the gate here is the only thing standing between it and Pro.
    webhook(client, pro_order(order_id=1001, customer_id=7001), "orders/paid")
    webhook(client, customer(customer_id=7001), "customers/create")

    stub = get_user(client)
    assert stub is not None and not stub.claimed
    assert not stub.is_pro, "Pro landed on a row nobody has claimed"
    assert users.pending_grant_days("buyer@example.com") == 31

    # ...and the moment the owner proves it, they get what they paid for.
    signup(client)
    assert get_user(client).is_pro


def test_customer_webhook_claim_does_not_cross_identities(app):
    """The claim must not become a back door around the parking rule.

    A customer webhook for a DIFFERENT Shopify customer that happens to share
    nothing with the parked order must leave that order parked. This is the
    assertion that keeps the fix from re-opening the takeover hole the
    parking exists to close.
    """
    client = TestClient(app)
    users: UserStore = client.app.state.users
    signup(client)

    webhook(client, pro_order(order_id=1001, customer_id=7001), "orders/paid")
    # A different customer id for the same inbox: not the link we waited for.
    webhook(client, customer(customer_id=8002), "customers/create")

    assert not get_user(client).is_pro
    parked = users._conn.execute(
        "SELECT user_id, pending_days FROM shopify_orders"
        " WHERE order_id = '1001'"
    ).fetchone()
    assert tuple(parked) == (None, 31)


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


def test_redact_after_delete_erases_the_parked_entitlement(app):
    client = TestClient(app)
    webhook(client, customer(), "customers/create")
    webhook(client, pro_order(), "orders/paid")
    webhook(client, {"id": 7001}, "customers/delete")
    assert client.app.state.users.pending_grant_days("buyer@example.com") > 0

    payload = {"shop_domain": "teststore.myshopify.com", "customer": customer()}
    webhook(client, payload, "customers/redact")

    signup(client)
    assert not get_user(client).is_pro
    tombstone = client.app.state.users._conn.execute(
        "SELECT redacted, former_user_id"
        " FROM shopify_customer_tombstones WHERE customer_id = '7001'"
    ).fetchone()
    assert tuple(tombstone) == (1, None)


def test_redact_blocks_delayed_paid_order_from_reintroducing_identity(app):
    client = TestClient(app)
    webhook(client, customer(), "customers/create")
    payload = {"shop_domain": "teststore.myshopify.com", "customer": customer()}
    webhook(client, payload, "customers/redact")

    webhook(
        client,
        pro_order(customer_id=7001),
        "orders/paid",
    )

    users: UserStore = client.app.state.users
    assert get_user(client) is None
    assert users.pending_grant_days("buyer@example.com") == 0
    assert users._conn.execute(
        "SELECT 1 FROM shopify_orders WHERE order_id = '1001'"
    ).fetchone() is None


def test_redact_for_other_customer_id_cannot_erase_same_email_state(app):
    client = TestClient(app)
    webhook(client, customer(customer_id=7001), "customers/create")
    users: UserStore = client.app.state.users
    users.add_pending_grant("buyer@example.com", 31)
    users.issue_email_code("buyer@example.com", "signin")

    payload = {
        "shop_domain": "teststore.myshopify.com",
        "customer": customer(customer_id=7002),
    }
    webhook(client, payload, "customers/redact")

    user = get_user(client)
    assert user is not None and user.shopify_customer_id == "7001"
    assert not user.shopify_sync_blocked
    assert users.pending_grant_days("buyer@example.com") == 31
    assert users._conn.execute(
        "SELECT 1 FROM email_codes WHERE email = 'buyer@example.com'"
    ).fetchone() is not None


def test_redact_removes_only_matching_customer_pending_value(app):
    client = TestClient(app)
    users: UserStore = client.app.state.users
    webhook(
        client,
        pro_order(order_id=1001, customer_id=7001),
        "orders/paid",
    )
    webhook(
        client,
        pro_order(order_id=1002, customer_id=7002),
        "orders/paid",
    )
    webhook(client, customer(customer_id=7001), "customers/create")

    payload = {
        "shop_domain": "teststore.myshopify.com",
        "customer": customer(customer_id=7001),
    }
    webhook(client, payload, "customers/redact")

    assert get_user(client) is None
    assert users.pending_grant_days("buyer@example.com") == 31
    pending = {
        row["order_id"]: row["pending_days"]
        for row in users._conn.execute(
            "SELECT order_id, pending_days FROM shopify_orders"
            " ORDER BY order_id"
        )
    }
    assert pending == {"1002": 31}


def test_gdpr_ack_topics_return_200(app):
    client = TestClient(app)
    payload = {"shop_domain": "teststore.myshopify.com", "customer": customer()}
    assert webhook(client, payload, "customers/data_request").status_code == 200
    assert webhook(client, {"shop_domain": "x"}, "shop/redact").status_code == 200


def test_reverse_order_identity_and_value_wait_for_inbox_proof(app):
    users: UserStore = app.state.users
    email = "reverse-order@example.com"
    attacker_password = "attacker-password"
    attacker = users.create(
        email,
        attacker_password,
        email_verified=False,
    )
    before_epoch = attacker.auth_epoch

    pending = users.upsert_store_customer(
        email,
        "7001",
        updated_at=100,
    )
    assert pending is not None
    assert pending.id == attacker.id
    assert pending.shopify_customer_id is None
    assert pending.shopify_sync_status == "requires_review"
    parked_link = users._conn.execute(
        "SELECT customer_id, email FROM shopify_pending_customer_links"
        " WHERE customer_id = '7001'"
    ).fetchone()
    assert tuple(parked_link) == ("7001", email)

    applied, _, user_id = users.apply_shopify_order(
        "9001",
        email,
        31,
        "7001",
    )
    assert applied and user_id is None
    assert users.claim_pending_grant(attacker.id, email) == 0
    assert not users.get(attacker.id).is_pro

    verified = users.verify_email_signin(email)
    assert verified.email_verified
    assert verified.auth_epoch == before_epoch + 1
    assert not verified.has_password
    assert verified.shopify_customer_id == "7001"
    assert users.authenticate(email, attacker_password) is None
    assert users.claim_pending_grant(verified.id, email) == 31
    assert users.get(verified.id).is_pro

    replay = users.upsert_store_customer(
        email,
        "7001",
        updated_at=100,
    )
    assert replay is not None and replay.id == verified.id
    reapplied, _, _ = users.apply_shopify_order(
        "9001",
        email,
        31,
        "7001",
    )
    assert not reapplied


def test_missing_timestamp_replay_cannot_roll_back_pending_link_email(app):
    users: UserStore = app.state.users
    users.create(
        "current@example.com",
        "unverified-password",
        email_verified=False,
    )
    users.upsert_store_customer(
        "current@example.com",
        "7001",
        updated_at=200,
    )
    users.upsert_store_customer(
        "stale@example.com",
        "7001",
        updated_at=None,
    )
    parked = users._conn.execute(
        "SELECT email, updated_at FROM shopify_pending_customer_links"
        " WHERE customer_id = '7001'"
    ).fetchone()
    assert tuple(parked) == ("current@example.com", 200)


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
    conn.execute(
        "CREATE TABLE shopify_orders ("
        " order_id TEXT PRIMARY KEY, email TEXT NOT NULL,"
        " days REAL NOT NULL, applied_at REAL NOT NULL)"
    )
    conn.execute(
        "INSERT INTO shopify_orders (order_id, email, days, applied_at)"
        " VALUES ('legacy-order', 'old@example.com', 31, 0)"
    )
    conn.execute(
        "CREATE TABLE shopify_customer_tombstones ("
        " customer_id TEXT PRIMARY KEY,"
        " redacted INTEGER NOT NULL DEFAULT 0,"
        " deleted_at REAL NOT NULL)"
    )
    conn.execute(
        "INSERT INTO shopify_customer_tombstones"
        " (customer_id, redacted, deleted_at) VALUES ('deleted-1', 0, 1)"
    )
    conn.commit()
    conn.close()

    users = UserStore(old)
    veteran = users.get("u1")
    assert veteran.shopify_customer_id is None and veteran.source is None
    assert veteran.has_password

    stub = users.upsert_store_customer("new@example.com", "42")
    assert stub.source == "shopify" and not stub.has_password
    order_columns = {
        row["name"]
        for row in users._conn.execute("PRAGMA table_info(shopify_orders)")
    }
    assert {
        "user_id",
        "shopify_customer_id",
        "grant_chain",
        "grant_start",
        "grant_end",
        "pending_days",
        "grant_ambiguous",
        "cancelled_at",
    } <= order_columns
    tombstone_columns = {
        row["name"]
        for row in users._conn.execute(
            "PRAGMA table_info(shopify_customer_tombstones)"
        )
    }
    assert "former_user_id" in tombstone_columns
    migrated_tombstone = users._conn.execute(
        "SELECT redacted, former_user_id"
        " FROM shopify_customer_tombstones"
        " WHERE customer_id = 'deleted-1'"
    ).fetchone()
    assert tuple(migrated_tombstone) == (0, None)
    migrated_order = users._conn.execute(
        "SELECT user_id FROM shopify_orders WHERE order_id = 'legacy-order'"
    ).fetchone()
    assert migrated_order["user_id"] == "u1"

    reopened = UserStore(old)  # second open: migrations must be no-ops
    assert reopened.get("u1") is not None
    assert reopened.get_by_shopify("42").email == "new@example.com"


def test_legacy_stacked_orders_gain_cancellable_intervals(tmp_path):
    db = tmp_path / "legacy-stacked.sqlite"
    now = time.time()
    applied_at = now - 40 * DAY
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,"
        " password_hash TEXT NOT NULL, created_at REAL NOT NULL,"
        " stripe_customer_id TEXT, plan TEXT NOT NULL DEFAULT 'free',"
        " subscription_status TEXT NOT NULL DEFAULT 'none',"
        " pro_until REAL NOT NULL DEFAULT 0)"
    )
    conn.execute(
        "INSERT INTO users"
        " (id, email, password_hash, created_at, pro_until)"
        " VALUES ('u1', 'old@example.com', 'scrypt$x', 0, ?)",
        (now + 22 * DAY,),
    )
    conn.execute(
        "CREATE TABLE shopify_orders ("
        " order_id TEXT PRIMARY KEY, email TEXT NOT NULL,"
        " days REAL NOT NULL, applied_at REAL NOT NULL)"
    )
    conn.executemany(
        "INSERT INTO shopify_orders (order_id, email, days, applied_at)"
        " VALUES (?, 'old@example.com', 31, ?)",
        (("legacy-1", applied_at), ("legacy-2", applied_at)),
    )
    conn.commit()
    conn.close()

    users = UserStore(db)
    orders = users._conn.execute(
        "SELECT * FROM shopify_orders ORDER BY order_id"
    ).fetchall()
    assert orders[0]["grant_chain"] == orders[1]["grant_chain"]
    assert orders[0]["grant_start"] == pytest.approx(applied_at)
    assert orders[0]["grant_end"] == pytest.approx(orders[1]["grant_start"])
    assert orders[1]["grant_end"] == pytest.approx(applied_at + 62 * DAY)

    applied, _, _ = users.cancel_shopify_order("legacy-2")
    assert applied
    assert not users.get("u1").is_pro


def test_legacy_claimed_late_order_uses_claimed_access_window(tmp_path):
    db = tmp_path / "legacy-claimed-late.sqlite"
    now = time.time()
    # Shopify created the stub one day after purchase, but the owner did not
    # claim its parked 31 days until now. Legacy rows had no claimed_at field;
    # pro_until is the authoritative evidence for the actual access window.
    created_at = now - 99 * DAY
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,"
        " password_hash TEXT NOT NULL, created_at REAL NOT NULL,"
        " stripe_customer_id TEXT, plan TEXT NOT NULL DEFAULT 'free',"
        " subscription_status TEXT NOT NULL DEFAULT 'none',"
        " pro_until REAL NOT NULL DEFAULT 0)"
    )
    conn.execute(
        "INSERT INTO users"
        " (id, email, password_hash, created_at, pro_until)"
        " VALUES ('u1', 'old@example.com', 'scrypt$x', ?, ?)",
        (created_at, now + 31 * DAY),
    )
    conn.execute(
        "CREATE TABLE shopify_orders ("
        " order_id TEXT PRIMARY KEY, email TEXT NOT NULL,"
        " days REAL NOT NULL, applied_at REAL NOT NULL)"
    )
    conn.execute(
        "INSERT INTO shopify_orders (order_id, email, days, applied_at)"
        " VALUES ('legacy-late', 'old@example.com', 31, ?)",
        (now - 100 * DAY,),
    )
    conn.commit()
    conn.close()

    users = UserStore(db)
    order = users._conn.execute(
        "SELECT grant_start, grant_end FROM shopify_orders"
        " WHERE order_id = 'legacy-late'"
    ).fetchone()
    assert order["grant_start"] == pytest.approx(now, abs=1)
    assert order["grant_end"] == pytest.approx(now + 31 * DAY, abs=1)

    applied, _, _ = users.cancel_shopify_order("legacy-late")
    assert applied
    assert not users.get("u1").is_pro


def test_paid_replay_repairs_legacy_ledger_without_entitlement(tmp_path):
    db = tmp_path / "legacy-missing-entitlement.sqlite"
    now = time.time()
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,"
        " password_hash TEXT NOT NULL, created_at REAL NOT NULL,"
        " stripe_customer_id TEXT, plan TEXT NOT NULL DEFAULT 'free',"
        " subscription_status TEXT NOT NULL DEFAULT 'none',"
        " pro_until REAL NOT NULL DEFAULT 0)"
    )
    conn.execute(
        "INSERT INTO users"
        " (id, email, password_hash, created_at, pro_until)"
        " VALUES ('u1', 'old@example.com', 'scrypt$x', ?, 0)",
        (now - 100 * DAY,),
    )
    conn.execute(
        "CREATE TABLE shopify_orders ("
        " order_id TEXT PRIMARY KEY, email TEXT NOT NULL,"
        " days REAL NOT NULL, applied_at REAL NOT NULL)"
    )
    conn.execute(
        "INSERT INTO shopify_orders (order_id, email, days, applied_at)"
        " VALUES ('legacy-crash', 'old@example.com', 31, ?)",
        (now - 60,),
    )
    conn.execute(
        "CREATE TABLE gear_orders ("
        " order_id TEXT NOT NULL, sku TEXT NOT NULL, title TEXT NOT NULL,"
        " quantity INTEGER NOT NULL, email TEXT NOT NULL,"
        " created_at REAL NOT NULL, cancelled_at REAL)"
    )
    conn.execute(
        "INSERT INTO gear_orders"
        " (order_id, sku, title, quantity, email, created_at)"
        " VALUES ('legacy-crash', 'SL-MAT', 'Mat', 1,"
        " 'old@example.com', ?)",
        (now - 60,),
    )
    conn.commit()
    conn.close()

    users = UserStore(db)
    users._conn.execute(
        "UPDATE users SET email_verified_at = created_at WHERE id = 'u1'"
    )
    users._conn.commit()
    applied, _, _ = users.apply_shopify_order(
        "legacy-crash",
        "old@example.com",
        31,
        None,
        gear=[("SL-MAT", "Mat", 1)],
    )
    assert applied
    repaired = users.get("u1")
    assert repaired.is_pro
    first_end = repaired.pro_until
    assert first_end == pytest.approx(now - 60 + 31 * DAY, abs=1)

    replayed, _, _ = users.apply_shopify_order(
        "legacy-crash",
        "old@example.com",
        31,
        None,
        gear=[("SL-MAT", "Mat", 1)],
    )
    assert not replayed
    assert users.get("u1").pro_until == first_end
    assert users._conn.execute(
        "SELECT COUNT(*) FROM gear_orders"
        " WHERE order_id = 'legacy-crash'"
    ).fetchone()[0] == 1


def test_legacy_cancellation_blocks_ambiguous_replay_repair(tmp_path):
    db = tmp_path / "legacy-cancelled-chain.sqlite"
    now = time.time()
    first_paid = now - 20 * DAY
    second_paid = now - 10 * DAY
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,"
        " password_hash TEXT NOT NULL, created_at REAL NOT NULL,"
        " stripe_customer_id TEXT, plan TEXT NOT NULL DEFAULT 'free',"
        " subscription_status TEXT NOT NULL DEFAULT 'none',"
        " pro_until REAL NOT NULL DEFAULT 0)"
    )
    conn.execute(
        "INSERT INTO users"
        " (id, email, password_hash, created_at, pro_until)"
        " VALUES ('u1', 'old@example.com', 'scrypt$x', ?, ?)",
        (now - 100 * DAY, now + 11 * DAY),
    )
    conn.execute(
        "CREATE TABLE shopify_orders ("
        " order_id TEXT PRIMARY KEY, email TEXT NOT NULL,"
        " days REAL NOT NULL, applied_at REAL NOT NULL)"
    )
    conn.executemany(
        "INSERT INTO shopify_orders (order_id, email, days, applied_at)"
        " VALUES (?, 'old@example.com', ?, ?)",
        (
            ("legacy-cancelled", 0, first_paid),
            ("legacy-active", 31, second_paid),
        ),
    )
    conn.commit()
    conn.close()

    users = UserStore(db)
    before = users.get("u1").pro_until
    applied, _, _ = users.apply_shopify_order(
        "legacy-active",
        "old@example.com",
        31,
        None,
    )

    assert not applied
    assert users.get("u1").pro_until == before
    assert users.get("u1").pro_until == pytest.approx(now + 11 * DAY)


def test_single_expired_legacy_order_cannot_own_unrelated_live_tail(tmp_path):
    db = tmp_path / "legacy-single-unrelated-tail.sqlite"
    now = time.time()
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,"
        " password_hash TEXT NOT NULL, created_at REAL NOT NULL,"
        " stripe_customer_id TEXT, plan TEXT NOT NULL DEFAULT 'free',"
        " subscription_status TEXT NOT NULL DEFAULT 'none',"
        " pro_until REAL NOT NULL DEFAULT 0)"
    )
    conn.execute(
        "INSERT INTO users"
        " (id, email, password_hash, created_at, pro_until)"
        " VALUES ('u1', 'old@example.com', 'scrypt$x', ?, ?)",
        (now - 200 * DAY, now + 100 * DAY),
    )
    conn.execute(
        "CREATE TABLE shopify_orders ("
        " order_id TEXT PRIMARY KEY, email TEXT NOT NULL,"
        " days REAL NOT NULL, applied_at REAL NOT NULL)"
    )
    conn.execute(
        "INSERT INTO shopify_orders (order_id, email, days, applied_at)"
        " VALUES ('expired-shopify', 'old@example.com', 31, ?)",
        (now - 100 * DAY,),
    )
    conn.commit()
    conn.close()

    users = UserStore(db)
    migrated = users._conn.execute(
        "SELECT grant_ambiguous, grant_end FROM shopify_orders"
        " WHERE order_id = 'expired-shopify'"
    ).fetchone()
    assert migrated["grant_ambiguous"] == 1
    assert migrated["grant_end"] < now

    before = users.get("u1").pro_until
    applied, _, _ = users.cancel_shopify_order("expired-shopify")
    assert applied
    assert users.get("u1").pro_until == before


def test_mixed_legacy_claim_history_is_marked_ambiguous(tmp_path):
    db = tmp_path / "legacy-ambiguous-chain.sqlite"
    now = time.time()
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,"
        " password_hash TEXT NOT NULL, created_at REAL NOT NULL,"
        " stripe_customer_id TEXT, plan TEXT NOT NULL DEFAULT 'free',"
        " subscription_status TEXT NOT NULL DEFAULT 'none',"
        " pro_until REAL NOT NULL DEFAULT 0)"
    )
    conn.execute(
        "INSERT INTO users"
        " (id, email, password_hash, created_at, pro_until)"
        " VALUES ('u1', 'old@example.com', 'scrypt$x', ?, ?)",
        (now - 99 * DAY, now + 31 * DAY),
    )
    conn.execute(
        "CREATE TABLE shopify_orders ("
        " order_id TEXT PRIMARY KEY, email TEXT NOT NULL,"
        " days REAL NOT NULL, applied_at REAL NOT NULL)"
    )
    conn.executemany(
        "INSERT INTO shopify_orders (order_id, email, days, applied_at)"
        " VALUES (?, 'old@example.com', 31, ?)",
        (
            ("parked-before-stub", now - 100 * DAY),
            ("direct-after-stub", now - 50 * DAY),
        ),
    )
    conn.commit()
    conn.close()

    users = UserStore(db)
    flags = {
        row["order_id"]: row["grant_ambiguous"]
        for row in users._conn.execute(
            "SELECT order_id, grant_ambiguous FROM shopify_orders"
        )
    }
    assert flags == {
        "parked-before-stub": 1,
        "direct-after-stub": 1,
    }

    before = users.get("u1").pro_until
    applied, _, _ = users.cancel_shopify_order("direct-after-stub")
    assert applied
    assert users.get("u1").pro_until == before


def test_paid_replay_repairs_recent_missing_pending_grant(tmp_path):
    db = tmp_path / "legacy-missing-pending.sqlite"
    now = time.time()
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE shopify_orders ("
        " order_id TEXT PRIMARY KEY, email TEXT NOT NULL,"
        " days REAL NOT NULL, applied_at REAL NOT NULL)"
    )
    conn.execute(
        "INSERT INTO shopify_orders (order_id, email, days, applied_at)"
        " VALUES ('legacy-pending-crash', 'old@example.com', 31, ?)",
        (now - 60,),
    )
    conn.commit()
    conn.close()

    users = UserStore(db)
    repaired, _, _ = users.apply_shopify_order(
        "legacy-pending-crash",
        "old@example.com",
        31,
        "7001",
    )
    assert repaired
    assert users.pending_grant_days("old@example.com") == 31
    row = users._conn.execute(
        "SELECT pending_days, shopify_customer_id FROM shopify_orders"
        " WHERE order_id = 'legacy-pending-crash'"
    ).fetchone()
    assert tuple(row) == (31, "7001")

    replayed, _, _ = users.apply_shopify_order(
        "legacy-pending-crash",
        "old@example.com",
        31,
        "7001",
    )
    assert not replayed
    assert users.pending_grant_days("old@example.com") == 31

    stub = users.upsert_store_customer("old@example.com", "7001")
    assert users.claim_pending_grant(stub.id, stub.email) == 31
    assert users.get(stub.id).is_pro


def test_paid_replay_repairs_missing_grant_after_stub_arrives(tmp_path):
    db = tmp_path / "legacy-missing-grant-with-stub.sqlite"
    now = time.time()
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE shopify_orders ("
        " order_id TEXT PRIMARY KEY, email TEXT NOT NULL,"
        " days REAL NOT NULL, applied_at REAL NOT NULL)"
    )
    conn.execute(
        "INSERT INTO shopify_orders (order_id, email, days, applied_at)"
        " VALUES ('legacy-stub-crash', 'old@example.com', 31, ?)",
        (now - 60,),
    )
    conn.commit()
    conn.close()

    users = UserStore(db)
    stub = users.upsert_store_customer("old@example.com", "7001")
    assert not users.get(stub.id).is_pro

    repaired, _, repaired_user_id = users.apply_shopify_order(
        "legacy-stub-crash",
        "old@example.com",
        31,
        "7001",
    )
    assert repaired
    assert repaired_user_id == stub.id
    assert users.get(stub.id).is_pro
    row = users._conn.execute(
        "SELECT user_id, shopify_customer_id, pending_days,"
        " grant_start, grant_end FROM shopify_orders"
        " WHERE order_id = 'legacy-stub-crash'"
    ).fetchone()
    assert row["user_id"] == stub.id
    assert row["shopify_customer_id"] == "7001"
    assert row["pending_days"] == 0
    assert row["grant_end"] > row["grant_start"]

    first_end = users.get(stub.id).pro_until
    replayed, _, _ = users.apply_shopify_order(
        "legacy-stub-crash",
        "old@example.com",
        31,
        "7001",
    )
    assert not replayed
    assert users.get(stub.id).pro_until == first_end


def test_legacy_pending_tail_is_attributed_to_newest_order(tmp_path):
    db = tmp_path / "legacy-pending.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE shopify_orders ("
        " order_id TEXT PRIMARY KEY, email TEXT NOT NULL,"
        " days REAL NOT NULL, applied_at REAL NOT NULL)"
    )
    conn.executemany(
        "INSERT INTO shopify_orders (order_id, email, days, applied_at)"
        " VALUES (?, 'old@example.com', 31, ?)",
        (("legacy-1", 1), ("legacy-2", 2)),
    )
    conn.execute(
        "CREATE TABLE pro_grants (email TEXT PRIMARY KEY, days REAL NOT NULL)"
    )
    conn.execute(
        "INSERT INTO pro_grants (email, days)"
        " VALUES ('old@example.com', 22)"
    )
    conn.commit()
    conn.close()

    users = UserStore(db)
    parked = {
        row["order_id"]: row["pending_days"]
        for row in users._conn.execute(
            "SELECT order_id, pending_days FROM shopify_orders"
        )
    }
    assert parked == {"legacy-1": 0, "legacy-2": 22}


def test_pending_grant_claim_is_atomic(app):
    client = TestClient(app)
    signup(client)
    user = get_user(client)
    users: UserStore = client.app.state.users
    users.add_pending_grant(user.email, 31)
    users._conn.execute(
        "CREATE TRIGGER fail_pending_grant"
        " BEFORE UPDATE OF pro_until ON users"
        " BEGIN SELECT RAISE(ABORT, 'simulated failure'); END"
    )
    users._conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        users.claim_pending_grant(user.id, user.email)

    assert users.pending_grant_days(user.email) == 31
    assert not users.get(user.id).is_pro

    users._conn.execute("DROP TRIGGER fail_pending_grant")
    users._conn.commit()
    assert users.claim_pending_grant(user.id, user.email) == 31
    assert users.get(user.id).is_pro
    assert users.pending_grant_days(user.email) == 0


def test_concurrent_customer_upserts_keep_one_shopify_identity(tmp_path):
    db = tmp_path / "concurrent.sqlite"
    first = UserStore(db)
    second = UserStore(db)
    barrier = threading.Barrier(2)

    def upsert(store, email):
        barrier.wait()
        return store.upsert_store_customer(email, "7001")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda args: upsert(*args),
                (
                    (first, "first@example.com"),
                    (second, "second@example.com"),
                ),
            )
        )

    rows = first._conn.execute(
        "SELECT id, email, shopify_customer_id FROM users"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["shopify_customer_id"] == "7001"
    assert results[0].id == results[1].id == rows[0]["id"]


def test_concurrent_store_open_migrates_legacy_order_table_once(tmp_path):
    db = tmp_path / "legacy-concurrent.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,"
        " password_hash TEXT NOT NULL, created_at REAL NOT NULL,"
        " stripe_customer_id TEXT, plan TEXT NOT NULL DEFAULT 'free',"
        " subscription_status TEXT NOT NULL DEFAULT 'none')"
    )
    conn.execute(
        "CREATE TABLE shopify_orders ("
        " order_id TEXT PRIMARY KEY, email TEXT NOT NULL,"
        " days REAL NOT NULL, applied_at REAL NOT NULL)"
    )
    conn.commit()
    conn.close()
    barrier = threading.Barrier(2)

    def open_store():
        barrier.wait()
        return UserStore(db)

    with ThreadPoolExecutor(max_workers=2) as pool:
        stores = list(pool.map(lambda _: open_store(), range(2)))

    columns = {
        row["name"]
        for row in stores[0]._conn.execute("PRAGMA table_info(shopify_orders)")
    }
    assert {
        "user_id",
        "shopify_customer_id",
        "grant_chain",
        "grant_start",
        "grant_end",
        "pending_days",
        "grant_ambiguous",
        "cancelled_at",
    } <= columns
