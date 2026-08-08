"""Production-sensitive contracts preserved by the foundation migration."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import threading
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.api.contracts import (
    IdentityResponse,
    LegacySessionResponse,
    LegacySessionsResponse,
    LegacyTodayResponse,
    MobileTokenIssueResponse,
    MobileTokenListResponse,
    MobileTokenRevokeResponse,
    NativeEventResponse,
    PracticeCheckinResponse,
    ProfileResponse,
)
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from swinglab.web.jobs import JobManager
from swinglab.web.mobile_schema import VersionedHMAC
from swinglab.web.users import UserStore
from tests.test_mobile_sign_out import FakeRecoveryFenceLedger
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
    ("/auth/storefront/session", frozenset({"GET"})),
    ("/auth/storefront/session", frozenset({"POST"})),
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
    # Browser token revocation now routes through the recovery-fenced service,
    # so an injected development ledger and keyring keep the revoke durable.
    return create_app(
        cfg,
        sessions_dir=tmp_path / "sessions",
        mobile_state_hmac=VersionedHMAC("k1", {"k1": b"k" * 32}),
        recovery_fence_ledger=FakeRecoveryFenceLedger(),
    )


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

    assert ACCOUNT_AND_API_ROUTES <= actual


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


def test_app_owned_resources_close_idempotently_without_worker_or_sqlite_leaks(tmp_path):
    """Catches contract export leaving an executor or SQLite handle behind."""
    app = create_app(
        Config(), tmp_path / "sessions", start_background_workers=False
    )
    known_workers = set(threading.enumerate())
    app.state.jobs._pool.submit(lambda: None).result()
    owned_workers = set(threading.enumerate()) - known_workers
    for resource in (app.state.jobs, app.state.users, app.state.throttle):
        resource.close()
        resource.close()

    assert not [
        thread
        for thread in owned_workers
        if thread.name.startswith("swinglab-worker") and thread.is_alive()
    ]
    for resource in (app.state.jobs, app.state.users, app.state.throttle):
        with pytest.raises(sqlite3.ProgrammingError):
            resource._conn.execute("SELECT 1")

    database = tmp_path / "sessions" / "swinglab.db"
    moved = tmp_path / "sessions" / "closed-swinglab.db"
    os.replace(database, moved)
    reopened = sqlite3.connect(moved)
    try:
        assert reopened.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally:
        reopened.close()


def test_job_manager_can_defer_interrupted_recovery(tmp_path, monkeypatch):
    """Catches contract-only app construction from submitting recovered work."""
    calls: list[object] = []
    monkeypatch.setattr(
        JobManager, "_requeue_interrupted", lambda self: calls.append(self)
    )
    manager = JobManager(tmp_path / "sessions", Config(), recover_interrupted=False)
    try:
        assert calls == []
    finally:
        manager.close()


def test_job_manager_recovers_interrupted_work_by_default(tmp_path, monkeypatch):
    """Catches a default startup that silently stops recovering prior work."""
    calls: list[object] = []
    monkeypatch.setattr(
        JobManager, "_requeue_interrupted", lambda self: calls.append(self)
    )
    manager = JobManager(tmp_path / "sessions", Config())
    try:
        assert calls == [manager]
    finally:
        manager.close()


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


def test_typed_v1_success_bodies_validate_against_the_frozen_models(contract_app):
    """Catches a JSONResponse bypassing its declared OpenAPI response model."""
    client = TestClient(contract_app)
    _signup(client, "typed-v1@example.com")
    user = _user(client, "typed-v1@example.com")
    job = contract_app.state.jobs.create_session(
        source_name="typed.mov",
        hand="right",
        angle="face-on",
        club="driver",
        level="improving",
        fast=True,
        user_id=user.id,
    )
    origin = {"Origin": "http://testserver"}

    responses = [
        (IdentityResponse, client.get("/api/v1/me")),
        (ProfileResponse, client.get("/api/v1/profile")),
        (LegacyTodayResponse, client.get("/api/v1/today")),
        (LegacySessionsResponse, client.get("/api/v1/sessions")),
        (LegacySessionResponse, client.get(f"/api/v1/sessions/{job.id}")),
        (PracticeCheckinResponse, client.get("/api/v1/practice-checkins")),
        (MobileTokenListResponse, client.get("/api/v1/mobile-tokens", headers=origin)),
        (NativeEventResponse, client.post("/api/v1/events", headers=origin, json={"event": "landing_view"})),
    ]
    token = client.post(
        "/api/v1/mobile-tokens", headers=origin, json={"label": "contract phone"}
    )
    responses.append((MobileTokenIssueResponse, token))
    selector = token.json()["device"]["selector"]
    responses.append(
        (
            MobileTokenRevokeResponse,
            client.delete(f"/api/v1/mobile-tokens/{selector}", headers=origin),
        )
    )

    expected_keys = [
        {"resource_version", "identity", "profile"},
        {"resource_version", "profile"},
        {
            "resource_version",
            "profile",
            "latest_session",
            "caddie_brief",
            "practice_plan",
            "practice_checked_in",
        },
        {"resource_version", "sessions"},
        {
            "resource_version",
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
        },
        {"resource_version", "checkins"},
        {"resource_version", "tokens"},
        {"accepted"},
        {"resource_version", "token", "device"},
        {"resource_version", "revoked"},
    ]
    for (model, response), expected in zip(responses, expected_keys, strict=True):
        assert response.is_success, response.text
        body = response.json()
        assert set(body) == expected
        model.model_validate(body)

    identity = responses[0][1].json()["identity"]
    assert set(identity) == {
        "id",
        "email",
        "email_verified",
        "history_epoch",
        "shopify_customer_linked",
        "shopify_account_state",
    }
    assert responses[0][1].json()["profile"] is None
    assert responses[1][1].json()["profile"] is None

    legacy_session_keys = expected_keys[4]
    assert set(responses[2][1].json()["latest_session"]) == legacy_session_keys
    sessions = responses[3][1].json()["sessions"]
    assert len(sessions) == 1
    assert set(sessions[0]) == legacy_session_keys
    assert set(responses[8][1].json()["device"]) == {
        "selector",
        "label",
        "created_at",
        "last_used_at",
        "expires_at",
        "revoked_at",
        "active",
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
