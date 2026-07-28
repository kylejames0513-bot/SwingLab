"""Production-sensitive contracts preserved by the foundation migration."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from swinglab.web.users import UserStore
from tests.test_web import fake_analyze_ok


SHOPIFY_SECRET = "shpss_foundation_contract"
SHOPIFY_PATHS = ("/webhooks/shopify", "/webhooks/shopify/")


@pytest.fixture
def contract_app(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "contract-test.myshopify.com")
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", SHOPIFY_SECRET)
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
    assert response.status_code == 303


def _user(client: TestClient, email: str):
    users: UserStore = client.app.state.users
    return users.get_by_email(email)


def _pro_order(order_id: int, **email_fields: str) -> dict:
    return {
        "id": order_id,
        **email_fields,
        "line_items": [{"sku": "SL-PRO-1MO", "quantity": 1}],
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


def test_unknown_signed_topic_is_acknowledged(contract_app):
    client = TestClient(contract_app)

    response = _signed_json(
        client,
        {"id": 71002, "email": "ignored@example.com"},
        "orders/create",
    )

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
