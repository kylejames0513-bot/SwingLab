"""Secure storefront visibility into the existing app browser session."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.integrations.shopify.customer_accounts import CustomerAccountIdentity
from swinglab.web.app import create_app


APP_ORIGIN = "https://app.caddieinsight.com"
STOREFRONT_ORIGIN = "https://caddieinsight.com"
SESSION_PATH = "/auth/storefront/session"


@pytest.fixture
def app(tmp_path, monkeypatch):
    for name in (
        "RESEND_API_KEY",
        "SWINGLAB_SMTP_URL",
        "SWINGLAB_MAIL_FROM",
        "SHOPIFY_STORE_DOMAIN",
        "SHOPIFY_WEBHOOK_SECRET",
        "SHOPIFY_CUSTOMER_ACCOUNTS_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PUBLIC_BASE_URL", APP_ORIGIN)
    monkeypatch.setenv("SWINGLAB_SECRET", "storefront-session-test-secret")
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["passwordless_login"] = False
    cfg.shop["store_url"] = STOREFRONT_ORIGIN
    return create_app(
        cfg,
        sessions_dir=tmp_path / "sessions",
        start_shopify_sync_worker=False,
    )


@pytest.fixture
def client(app):
    return TestClient(app, base_url=APP_ORIGIN)


def _storefront_headers(**extra):
    return {"Origin": STOREFRONT_ORIGIN, **extra}


def _signup_and_profile(client, app):
    signup = client.post(
        "/signup",
        data={"email": "golfer@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    assert signup.status_code == 303
    saved = client.post(
        "/onboarding",
        data={
            "display_name": "Kyle",
            "experience_mode": "improve",
            "handicap_range": "10_to_14",
            "primary_goal": "consistency",
            "practice_minutes": "20",
            "sessions_per_week": "2",
            "handedness": "right",
            "camera_angle": "face-on",
            "preferred_club": "driver",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303
    user = app.state.users.get_by_email("golfer@example.com")
    assert user is not None
    return user


def test_storefront_session_status_is_minimal_private_and_exact_origin(client):
    response = client.get(SESSION_PATH, headers=_storefront_headers())

    assert response.status_code == 200
    assert response.json() == {"authenticated": False}
    assert response.headers["access-control-allow-origin"] == STOREFRONT_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["cross-origin-resource-policy"] == "same-site"
    assert response.headers["pragma"] == "no-cache"
    assert {value.strip() for value in response.headers["vary"].split(",")} == {
        "Origin",
        "Cookie",
    }
    assert "set-cookie" not in response.headers

    rejected = client.get(
        SESSION_PATH,
        headers={"Origin": "https://attacker.example"},
    )
    assert rejected.status_code == 403
    assert "access-control-allow-origin" not in rejected.headers
    assert client.get(SESSION_PATH).status_code == 403


def test_storefront_reads_name_and_plan_without_exposing_account_data(client, app):
    user = _signup_and_profile(client, app)
    app.state.users.set_plan(user.id, "pro", "active")

    response = client.get(SESSION_PATH, headers=_storefront_headers())

    assert response.status_code == 200
    assert response.json() == {
        "authenticated": True,
        "display_name": "Kyle",
        "is_pro": True,
    }
    serialized = response.text.lower()
    assert "golfer@example.com" not in serialized
    assert "user_id" not in serialized
    assert "shopify" not in serialized

    existing_api = client.get("/api/v1/me", headers=_storefront_headers())
    assert existing_api.status_code == 200
    assert "access-control-allow-origin" not in existing_api.headers


def test_storefront_logout_requires_exact_origin_and_clears_only_after_success(
    client, app
):
    _signup_and_profile(client, app)

    for headers in (
        {},
        {"Origin": "https://attacker.example"},
        _storefront_headers(Authorization="Bearer not-accepted"),
    ):
        rejected = client.post(
            SESSION_PATH,
            headers=headers,
            follow_redirects=False,
        )
        assert rejected.status_code == 403
        assert client.get("/account").status_code == 200

    logged_out = client.post(
        SESSION_PATH,
        headers=_storefront_headers(),
        follow_redirects=False,
    )

    assert logged_out.status_code == 303
    assert logged_out.headers["location"] == STOREFRONT_ORIGIN
    assert logged_out.headers["cache-control"] == "no-store"
    assert "session=null" in logged_out.headers["set-cookie"].lower()
    account = client.get("/account", follow_redirects=False)
    assert account.status_code == 303
    assert account.headers["location"] == "/login"


def test_storefront_bridge_is_inert_without_a_configured_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", APP_ORIGIN)
    monkeypatch.setenv("SWINGLAB_SECRET", "storefront-session-test-secret")
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.shop["store_url"] = ""
    app = create_app(
        cfg,
        sessions_dir=tmp_path / "sessions",
        start_shopify_sync_worker=False,
    )
    client = TestClient(app, base_url=APP_ORIGIN)

    response = client.get(SESSION_PATH, headers=_storefront_headers())

    assert response.status_code == 404
    assert "access-control-allow-origin" not in response.headers


def test_storefront_logout_uses_provider_handoff_for_a_shopify_login(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PUBLIC_BASE_URL", APP_ORIGIN)
    monkeypatch.setenv("SWINGLAB_SECRET", "storefront-session-test-secret")
    monkeypatch.setenv("SHOPIFY_CUSTOMER_ACCOUNTS_ENABLED", "true")
    monkeypatch.setenv(
        "SHOPIFY_CUSTOMER_ACCOUNT_STOREFRONT_DOMAIN",
        "store.example.test",
    )
    monkeypatch.setenv("SHOPIFY_CUSTOMER_ACCOUNT_CLIENT_ID", "customer-client")
    monkeypatch.setenv(
        "SHOPIFY_CUSTOMER_ACCOUNT_CLIENT_SECRET",
        "customer-secret",
    )
    monkeypatch.setenv(
        "SHOPIFY_CUSTOMER_ACCOUNT_REDIRECT_URI",
        f"{APP_ORIGIN}/auth/shopify/callback",
    )
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.shop["store_url"] = STOREFRONT_ORIGIN
    app = create_app(
        cfg,
        sessions_dir=tmp_path / "sessions",
        start_shopify_sync_worker=False,
    )
    users = app.state.users
    stub = users.upsert_store_customer(
        "shopify-only@example.com",
        "77",
        updated_at=100,
    )
    assert stub is not None
    state = "s" * 43
    users.issue_shopify_customer_account_oauth_state(
        state=state,
        verifier="v" * 43,
        nonce="n" * 43,
        user_id=None,
        mode="login",
    )
    identity = CustomerAccountIdentity(
        subject="gid://shopify/Customer/77",
        customer_id="77",
        email="shopify-only@example.com",
        id_token="provider-id-token",
        expires_at=10**12,
    )
    customer_accounts = app.state.shopify_customer_accounts
    monkeypatch.setattr(
        customer_accounts,
        "authenticate_callback",
        lambda **_kwargs: identity,
    )
    seen_tokens = []

    def provider_logout_url(*, id_token):
        seen_tokens.append(id_token)
        return "https://accounts.example.test/logout"

    monkeypatch.setattr(customer_accounts, "logout_url", provider_logout_url)
    client = TestClient(app, base_url=APP_ORIGIN)
    callback = client.get(
        "/auth/shopify/callback",
        params={"state": state, "code": "provider-code"},
        follow_redirects=False,
    )
    assert callback.status_code == 303

    signed_in_shell = client.get("/", follow_redirects=True).text
    assert signed_in_shell.count('action="/auth/shopify/logout" method="post"') == 2
    assert 'action="/logout" method="post"' not in signed_in_shell

    logged_out = client.post(
        SESSION_PATH,
        headers=_storefront_headers(),
        follow_redirects=False,
    )

    assert logged_out.status_code == 303
    assert logged_out.headers["location"] == "https://accounts.example.test/logout"
    assert seen_tokens == ["provider-id-token"]
    assert client.get("/account", follow_redirects=False).status_code == 303
    second_logout = client.post(
        SESSION_PATH,
        headers=_storefront_headers(),
        follow_redirects=False,
    )
    assert second_logout.headers["location"] == STOREFRONT_ORIGIN
