"""Production-sensitive contracts preserved by the foundation migration."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from swinglab.web.users import UserStore
from tests.test_web import fake_analyze_ok


SHOPIFY_SECRET = "foundation-contract-secret"
SHOPIFY_PATHS = ("/webhooks/shopify", "/webhooks/shopify/")
ACCOUNT_AND_API_ROUTES = {
    ("/login", frozenset({"GET"})),
    ("/login", frozenset({"POST"})),
    ("/login/email", frozenset({"POST"})),
    ("/login/code", frozenset({"POST"})),
    ("/signup", frozenset({"GET"})),
    ("/signup", frozenset({"POST"})),
    ("/reset", frozenset({"GET"})),
    ("/reset/request", frozenset({"POST"})),
    ("/reset/confirm", frozenset({"POST"})),
    ("/logout", frozenset({"POST"})),
    ("/account", frozenset({"GET"})),
    ("/account/password", frozenset({"POST"})),
    ("/account/digest", frozenset({"POST"})),
    ("/email/unsubscribe", frozenset({"GET"})),
    ("/api/session/{job_id}", frozenset({"GET"})),
    ("/api/sessions", frozenset({"GET"})),
    ("/api/v1/me", frozenset({"GET"})),
    ("/api/v1/mobile-tokens", frozenset({"GET"})),
    ("/api/v1/mobile-tokens", frozenset({"POST"})),
    ("/api/v1/mobile-tokens/{selector}", frozenset({"DELETE"})),
    ("/api/v1/profile", frozenset({"GET"})),
    ("/api/v1/profile", frozenset({"PUT"})),
    ("/api/v1/today", frozenset({"GET"})),
    ("/api/v1/sessions", frozenset({"GET"})),
    ("/api/v1/sessions/{job_id}", frozenset({"GET"})),
    ("/api/v1/sessions/{job_id}/brief", frozenset({"GET"})),
    ("/api/v1/practice-checkins", frozenset({"GET"})),
    ("/api/v1/practice-checkins", frozenset({"POST"})),
    ("/api/v1/events", frozenset({"POST"})),
}


@pytest.fixture
def contract_app(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "contract-test.myshopify.com")
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", SHOPIFY_SECRET)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("SWINGLAB_SMTP_URL", raising=False)
    monkeypatch.delenv("SWINGLAB_MAIL_FROM", raising=False)

    cfg = Config()
    cfg.web["require_account"] = True
    return create_app(cfg, sessions_dir=tmp_path / "sessions")


def _signed_post(
    client: TestClient,
    body: bytes,
    topic: str,
    path: str = SHOPIFY_PATHS[0],
):
    signature = base64.b64encode(
        hmac.new(SHOPIFY_SECRET.encode(), body, hashlib.sha256).digest()
    ).decode()
    return client.post(
        path,
        content=body,
        headers={
            "X-Shopify-Hmac-Sha256": signature,
            "X-Shopify-Topic": topic,
            "X-Shopify-Shop-Domain": "contract-test.myshopify.com",
            "X-Shopify-Webhook-Id": "foundation-contract-test-delivery",
            "Content-Type": "application/json",
        },
        follow_redirects=False,
    )


def _signed_json(
    client: TestClient,
    payload: dict,
    topic: str,
    path: str = SHOPIFY_PATHS[0],
):
    return _signed_post(client, json.dumps(payload).encode(), topic, path)


def _signup(client: TestClient, email: str) -> None:
    response = client.post(
        "/signup",
        data={"email": email, "password": "longenough"},
        follow_redirects=False,
    )
    if response.status_code == 503:
        users: UserStore = client.app.state.users
        intent = users.issue_signup_intent(email, "longenough")
        code = users.issue_email_code(email, "claim")
        assert code is not None
        users.complete_signup_intent_with_code(intent, code)
        response = client.post(
            "/login",
            data={"email": email, "password": "longenough"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    users: UserStore = client.app.state.users
    if not users.get_by_email(email).email_verified:
        users.verify_email_signin(email)


def _user(client: TestClient, email: str):
    users: UserStore = client.app.state.users
    return users.get_by_email(email)


def _pro_order(order_id: int, **email_fields: str) -> dict:
    return {
        "id": order_id,
        **email_fields,
        "line_items": [{"sku": "SL-PRO-1MO", "quantity": 1}],
    }


def _pro_refund(order_id: int, refund_id: int) -> dict:
    return {
        "id": refund_id,
        "order_id": order_id,
        "refund_line_items": [
            {
                "quantity": 1,
                "line_item": {"sku": "SL-PRO-1MO"},
            }
        ],
    }


def test_shopify_webhook_routes_are_exact_post_pair(contract_app):
    routes = sorted(
        (route.path, frozenset(route.methods or ()))
        for route in contract_app.routes
        if getattr(route, "path", "").rstrip("/") == "/webhooks/shopify"
    )

    assert routes == [
        ("/webhooks/shopify", frozenset({"POST"})),
        ("/webhooks/shopify/", frozenset({"POST"})),
    ]


def test_account_passwordless_and_api_routes_are_stable(contract_app):
    actual = {
        (route.path, frozenset(route.methods or ()))
        for route in contract_app.routes
        if (
            getattr(route, "path", "").startswith("/api/")
            or getattr(route, "path", "") in {path for path, _ in ACCOUNT_AND_API_ROUTES}
        )
    }

    assert actual == ACCOUNT_AND_API_ROUTES


def test_sqlite_state_remains_in_sessions_swinglab_db(contract_app):
    expected = (contract_app.state.jobs.sessions_dir / "swinglab.db").resolve()
    connections = (
        contract_app.state.jobs._conn,
        contract_app.state.users._conn,
    )

    for connection in connections:
        database = connection.execute("PRAGMA database_list").fetchone()
        assert database["name"] == "main"
        assert database["file"]
        assert Path(database["file"]).resolve() == expected

    tables = {
        row[0]
        for row in contract_app.state.jobs._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {"jobs", "users", "auth_attempts"} <= tables


def test_api_response_keys_are_stable(contract_app):
    client = TestClient(contract_app)
    email = "api-contract@example.com"
    _signup(client, email)
    user = _user(client, email)
    job = contract_app.state.jobs.create_session(
        source_name="contract.mov",
        hand="left",
        angle="dtl",
        club="driver",
        level="improving",
        fast=True,
        user_id=user.id,
    )

    detail = client.get(f"/api/session/{job.id}")
    assert detail.status_code == 200
    assert set(detail.json()) == {
        "id",
        "status",
        "created_at",
        "source_name",
        "hand",
        "angle",
        "club",
        "level",
        "fast",
        "log",
        "error",
        "report",
        "swings_done",
        "swings_total",
        "queue_position",
    }

    index = client.get("/api/sessions")
    assert index.status_code == 200
    assert set(index.json()) == {"sessions"}
    assert len(index.json()["sessions"]) == 1
    assert set(index.json()["sessions"][0]) == {
        "id",
        "status",
        "created_at",
        "source_name",
        "swings_done",
        "swings_total",
    }


def test_valid_signed_payload_has_path_parity(contract_app):
    client = TestClient(contract_app)
    body = json.dumps({"shop_domain": "contract-test.myshopify.com"}).encode()

    responses = [
        _signed_post(client, body, "customers/data_request", path)
        for path in SHOPIFY_PATHS
    ]

    assert [
        (response.status_code, response.json(), response.history)
        for response in responses
    ] == [
        (200, {"received": True}, []),
        (200, {"received": True}, []),
    ]


def test_uppercase_orders_paid_grants_pro(contract_app):
    client = TestClient(contract_app)
    email = "uppercase-topic@example.com"
    _signup(client, email)

    response = _signed_json(
        client,
        _pro_order(71001, email=email),
        "ORDERS_PAID",
    )

    assert response.status_code == 200
    assert _user(client, email).is_pro


def test_uppercase_refunds_create_revokes_pro(contract_app):
    client = TestClient(contract_app)
    email = "uppercase-refund@example.com"
    _signup(client, email)
    _signed_json(
        client,
        _pro_order(71005, email=email),
        "ORDERS_PAID",
    )
    assert _user(client, email).is_pro

    response = _signed_json(
        client,
        _pro_refund(71005, 81005),
        "REFUNDS_CREATE",
    )

    assert response.status_code == 200
    assert not _user(client, email).is_pro


def test_unknown_signed_topic_is_acknowledged(contract_app):
    client = TestClient(contract_app)
    email = "unknown-topic@example.com"
    _signup(client, email)

    response = _signed_json(
        client,
        _pro_order(71002, email=email),
        "orders/create",
    )

    assert response.status_code == 200
    assert response.json() == {"received": True}
    assert not _user(client, email).is_pro


@pytest.mark.parametrize("path", SHOPIFY_PATHS)
def test_invalid_hmac_is_rejected_on_both_paths(contract_app, path):
    client = TestClient(contract_app)
    response = client.post(
        path,
        content=b'{"id": 71004}',
        headers={
            "X-Shopify-Hmac-Sha256": "invalid",
            "X-Shopify-Topic": "orders/paid",
            "Content-Type": "application/json",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid Shopify webhook signature"}


def test_whitespace_around_webhook_secret_is_ignored(contract_app, monkeypatch):
    client = TestClient(contract_app)
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", f"  {SHOPIFY_SECRET}\n")
    body = json.dumps({"shop_domain": "contract-test.myshopify.com"}).encode()

    response = _signed_post(client, body, "customers/data_request")

    assert response.status_code == 200
    assert response.json() == {"received": True}


def test_signed_invalid_json_returns_400(contract_app):
    client = TestClient(contract_app)

    response = _signed_post(client, b'{"id":', "ORDERS_PAID")

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid Shopify webhook payload"}


def test_contact_email_fallback_grants_pro(contract_app):
    client = TestClient(contract_app)
    email = "contact-only@example.com"
    _signup(client, email)

    response = _signed_json(
        client,
        _pro_order(71003, contact_email="Contact-Only@Example.com"),
        "orders/paid",
    )

    assert response.status_code == 200
    assert _user(client, email).is_pro
