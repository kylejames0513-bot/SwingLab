"""First-sale journey, versioned PWA boundary, and Customer Account gates."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.integrations.shopify.customer_accounts import (
    CustomerAccountSettings,
    ShopifyCustomerAccountClient,
)
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from swinglab.web.users import UserStore

from tests.test_web import fake_analyze_ok


@pytest.fixture
def account_client(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    cfg = Config()
    cfg.web["require_account"] = True
    app = create_app(cfg, sessions_dir=tmp_path / "sessions")
    client = TestClient(app)
    response = client.post(
        "/signup",
        data={"email": "golfer@example.com", "password": "longenough"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client


def _profile_payload():
    return {
        "experience_mode": "improve",
        "handicap_range": "20_to_29",
        "primary_goal": "consistency",
        "practice_minutes": 20,
        "sessions_per_week": 2,
        "handedness": "left",
        "camera_angle": "face-on",
        "preferred_club": "iron",
        "reduced_motion": True,
        "marketing_email_opt_in": False,
    }


def test_profile_today_pwa_and_versioned_api(account_client):
    updated = account_client.put("/api/v1/profile", json=_profile_payload())
    assert updated.status_code == 200
    assert updated.json()["profile"]["is_complete"] is True

    today = account_client.get("/today")
    assert today.status_code == 200
    assert "Film a baseline" in today.text
    assert "Make your next step fit your game" not in today.text

    home = account_client.get("/")
    assert 'option value="left" selected' in home.text
    manifest = account_client.get("/app.webmanifest")
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    assert manifest.json()["start_url"] == "/today"
    worker = account_client.get("/service-worker.js")
    assert worker.status_code == 200
    assert "Personal reports" in worker.text
    assert account_client.get("/api/v1/today").json()["resource_version"] == 1


def test_first_party_events_are_minimal_and_admin_guarded(
    account_client, monkeypatch
):
    assert account_client.post(
        "/api/v1/events", json={"event": "pro_clicked"}
    ).status_code == 202
    # Arbitrary client metadata is intentionally not an analytics sink.
    assert account_client.post(
        "/api/v1/events",
        json={"event": "pro_clicked", "email": "nope@example.com"},
    ).status_code == 400
    assert account_client.get("/admin/product-events").status_code == 404

    monkeypatch.setenv("SWINGLAB_ADMIN_TOKEN", "operator-token")
    response = account_client.get(
        "/admin/product-events",
        headers={"Authorization": "Bearer operator-token"},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["events"]["pro_clicked"] == 1
    assert payload["shopify_customer_account_migration"]["unclassified"] == 0


def test_customer_account_state_is_one_use_and_never_email_linked(tmp_path):
    store = UserStore(tmp_path / "accounts.db")
    user = store.create("golfer@example.com", "longenough")
    state = "s" * 43
    store.issue_shopify_customer_account_oauth_state(
        state=state,
        verifier="v" * 43,
        nonce="n" * 43,
        user_id=user.id,
        mode="link",
        now=100.0,
    )
    consumed = store.consume_shopify_customer_account_oauth_state(
        state, now=101.0
    )
    assert consumed is not None and consumed.user_id == user.id
    assert store.consume_shopify_customer_account_oauth_state(state, now=101.0) is None

    linked = store.link_shopify_customer_account(
        user.id,
        subject="gid://shopify/Customer/77",
        customer_id="77",
        authenticated=True,
        now=102.0,
    )
    assert linked.shopify_account_migration_state == "shopify_authenticated"
    assert store.get_by_shopify_account_subject("gid://shopify/Customer/77").id == user.id

    other = store.create("other@example.com", "longenough")
    with pytest.raises(ValueError, match="manual account review"):
        store.link_shopify_customer_account(
            other.id,
            subject="gid://shopify/Customer/77",
            customer_id="77",
            authenticated=True,
        )


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, amount=-1):
        return json.dumps(self.payload).encode("utf-8")


def test_customer_account_client_uses_discovery_pkce_and_raw_api_token():
    settings = CustomerAccountSettings.from_env(
        {
            "SHOPIFY_CUSTOMER_ACCOUNTS_ENABLED": "true",
            "SHOPIFY_CUSTOMER_ACCOUNT_STOREFRONT_DOMAIN": "store.example.test",
            "SHOPIFY_CUSTOMER_ACCOUNT_CLIENT_ID": "customer-client",
            "SHOPIFY_CUSTOMER_ACCOUNT_CLIENT_SECRET": "customer-secret",
            "PUBLIC_BASE_URL": "https://app.example.test",
            "SHOPIFY_CUSTOMER_ACCOUNT_REDIRECT_URI": (
                "https://app.example.test/auth/shopify/callback"
            ),
        }
    )
    assert settings is not None
    seen = []

    def opener(request, timeout):
        seen.append((request.full_url, dict(request.headers), request.data))
        if "openid-configuration" in request.full_url:
            return _Response(
                {
                    "authorization_endpoint": "https://accounts.example.test/authorize",
                    "token_endpoint": "https://accounts.example.test/token",
                    "end_session_endpoint": "https://accounts.example.test/logout",
                }
            )
        if "customer-account-api" in request.full_url:
            return _Response({"graphql_api": "https://accounts.example.test/graphql"})
        if request.full_url.endswith("/token"):
            token_payload = base64.urlsafe_b64encode(
                json.dumps(
                    {"nonce": "n" * 43, "aud": "customer-client"}
                ).encode("utf-8")
            ).decode("ascii").rstrip("=")
            return _Response(
                {
                    "access_token": "customer-access-token",
                    "id_token": f"header.{token_payload}.signature",
                    "expires_in": 3600,
                }
            )
        return _Response(
            {
                "data": {
                    "customer": {
                        "id": "gid://shopify/Customer/77",
                        "emailAddress": {"emailAddress": "g@example.test"},
                    }
                }
            }
        )

    client = ShopifyCustomerAccountClient(settings, opener=opener, now=lambda: 10.0)
    auth_url = client.authorization_url(
        state="s" * 43, nonce="n" * 43, verifier="v" * 43
    )
    query = parse_qs(urlsplit(auth_url).query)
    assert query["scope"] == ["openid email customer-account-api:full"]
    assert query["code_challenge_method"] == ["S256"]

    identity = client.authenticate_callback(
        code="code", verifier="v" * 43, nonce="n" * 43
    )
    assert identity.customer_id == "77"
    assert identity.subject == "gid://shopify/Customer/77"
    assert seen[-1][1]["Authorization"] == "customer-access-token"
