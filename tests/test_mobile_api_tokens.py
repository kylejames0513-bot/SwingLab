"""Personal device-token boundaries for the mobile API.

These tests intentionally combine store-level lifecycle checks with HTTP
checks.  A correct token table alone is not enough if a malformed Authorization
header can fall back to a signed browser cookie, or if a bearer token reaches a
route outside its small mobile/report/upload surface.
"""

from __future__ import annotations

import json
import time

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from swinglab.web.users import (
    MOBILE_API_TOKEN_ACTIVE_LIMIT,
    MOBILE_API_TOKEN_TTL_S,
    MobileAPITokenLimitError,
    UserStore,
)
from tests.test_web import fake_analyze_ok


STORE = "mobile-privacy-test.myshopify.com"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["free_per_month"] = 10
    return create_app(cfg, sessions_dir=tmp_path / "sessions")


def signup(client: TestClient, email: str = "golfer@example.com") -> None:
    response = client.post(
        "/signup",
        data={"email": email, "password": "longenough"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def issue_token(client: TestClient, label: str = "Kyle's iPhone") -> dict:
    response = client.post("/api/v1/mobile-tokens", json={"label": label})
    assert response.status_code == 201
    return response.json()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def issue_store_token(
    users: UserStore,
    user_id: str,
    label: str,
    *,
    now: float,
):
    user = users.get(user_id)
    assert user is not None
    return users.issue_mobile_api_token(
        user_id,
        label,
        expected_auth_epoch=user.auth_epoch,
        now=now,
    )


def wait_for_mobile_session(
    client: TestClient, token: str, job_id: str, timeout: float = 5.0
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(
            f"/api/v1/sessions/{job_id}", headers=bearer(token)
        )
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"done", "failed"}:
            return payload
        time.sleep(0.02)
    raise TimeoutError("mobile analysis did not finish")


def profile_payload() -> dict:
    return {
        "display_name": "Mobile Golfer",
        "experience_mode": "improve",
        "handicap_range": "20_to_29",
        "primary_goal": "consistency",
        "practice_minutes": 20,
        "sessions_per_week": 2,
        "handedness": "right",
        "camera_angle": "face-on",
        "preferred_club": "driver",
        "reduced_motion": False,
        "marketing_email_opt_in": False,
    }


def test_browser_issues_one_time_hashed_token_and_lists_metadata(app):
    client = TestClient(app)
    signup(client)

    issued = issue_token(client)
    raw_token = issued["token"]
    device = issued["device"]
    assert raw_token.startswith("ciat_")
    assert device["active"] is True
    assert "token_hash" not in issued

    issue_response = client.post(
        "/api/v1/mobile-tokens", json={"label": "MacBook"}
    )
    assert issue_response.status_code == 201
    assert issue_response.headers["cache-control"] == "no-store"
    assert issue_response.headers["pragma"] == "no-cache"
    assert "access-control-allow-origin" not in issue_response.headers

    users: UserStore = app.state.users
    row = users._conn.execute(
        "SELECT selector, token_hash, auth_epoch FROM mobile_api_tokens"
        " WHERE selector = ?",
        (device["selector"],),
    ).fetchone()
    assert row is not None
    assert row["selector"] == device["selector"]
    assert row["token_hash"] != raw_token
    assert len(row["token_hash"]) == 64
    assert row["auth_epoch"] == users.get_by_email("golfer@example.com").auth_epoch

    listed = client.get("/api/v1/mobile-tokens")
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    list_payload = listed.json()
    assert list_payload["resource_version"] == 1
    assert {
        item["selector"] for item in list_payload["tokens"]
    } == {device["selector"], issue_response.json()["device"]["selector"]}
    encoded = json.dumps(list_payload)
    assert raw_token not in encoded
    assert row["token_hash"] not in encoded

    revoked = client.delete(f"/api/v1/mobile-tokens/{device['selector']}")
    assert revoked.status_code == 200
    assert revoked.headers["cache-control"] == "no-store"
    assert TestClient(app).get("/api/v1/me", headers=bearer(raw_token)).status_code == 401
    after_revoke = client.get("/api/v1/mobile-tokens").json()["tokens"]
    revoked_device = next(
        item for item in after_revoke if item["selector"] == device["selector"]
    )
    assert revoked_device["active"] is False
    assert revoked_device["revoked_at"] is not None


def test_bearer_is_limited_to_owned_mobile_session_report_and_upload_routes(app):
    browser = TestClient(app)
    signup(browser)
    issued = issue_token(browser, "Range phone")
    token = issued["token"]
    mobile = TestClient(app)

    me = mobile.get("/api/v1/me", headers=bearer(token))
    assert me.status_code == 200
    assert me.json()["identity"]["email"] == "golfer@example.com"

    uploaded = mobile.post(
        "/upload",
        files={"video": ("range.mov", b"fake video bytes", "video/quicktime")},
        data={"hand": "right", "angle": "face-on", "club": "driver"},
        headers={**bearer(token), "Accept": "application/json"},
    )
    assert uploaded.status_code == 200
    job_id = uploaded.json()["id"]
    completed = wait_for_mobile_session(mobile, token, job_id)
    assert completed["status"] == "done"
    assert completed["club"] == "driver"

    assert mobile.get(
        f"/session/{job_id}", headers=bearer(token)
    ).status_code == 200
    report = mobile.get(
        f"/session/{job_id}/report",
        headers=bearer(token),
        follow_redirects=False,
    )
    assert report.status_code in {302, 303, 307, 308}
    assert mobile.get(report.headers["location"], headers=bearer(token)).status_code == 200
    assert mobile.get("/account", follow_redirects=False).status_code == 303

    other_browser = TestClient(app)
    signup(other_browser, "other@example.com")
    other_user = app.state.users.get_by_email("other@example.com")
    other_job = app.state.jobs.create_session(
        source_name="other.mov", user_id=other_user.id
    )
    assert mobile.get(
        f"/api/v1/sessions/{other_job.id}", headers=bearer(token)
    ).status_code == 404

    # Even a valid browser cookie cannot rescue a bad Authorization header.
    assert browser.get(
        f"/session/{job_id}", headers=bearer("ciat_not-a-real-token")
    ).status_code == 401


def test_bearer_upload_requires_canonical_club_without_reserving_quota(app):
    browser = TestClient(app)
    signup(browser)
    token = issue_token(browser, "Range phone")["token"]
    user = app.state.users.get_by_email("golfer@example.com")
    mobile = TestClient(app)

    invalid_payloads = (
        {},
        {"club": ""},
        {"club": "   "},
        {"club": "fairway_wood"},
    )
    for payload in invalid_payloads:
        response = mobile.post(
            "/upload",
            files={"video": ("range.mov", b"fake", "video/quicktime")},
            data=payload,
            headers={**bearer(token), "Accept": "application/json"},
        )
        assert response.status_code == 400
        assert response.json()["detail"].startswith("club must be one of:")

    assert app.state.jobs.sessions_count() == 0
    assert app.state.jobs.usage_this_month(user.id) == 0


def test_bad_authorization_never_falls_back_and_cookie_csrf_stays_enabled(app):
    browser = TestClient(app)
    signup(browser)
    issued = issue_token(browser, "Trusted device")
    token = issued["token"]
    bad_headers = bearer("ciat_this-is-not-a-valid-token")

    assert browser.get("/api/v1/me", headers=bad_headers).status_code == 401
    assert browser.get(
        "/api/v1/mobile-tokens", headers=bearer(token)
    ).status_code == 401
    assert browser.post(
        "/api/v1/mobile-tokens", json={"label": "Attack device"}, headers=bad_headers
    ).status_code == 401
    bad_upload = browser.post(
        "/upload",
        files={"video": ("nope.mov", b"x", "video/quicktime")},
        headers=bad_headers,
    )
    assert bad_upload.status_code == 401
    assert bad_upload.json()["detail"] == "Invalid mobile access token."

    csrf_upload = browser.post(
        "/upload",
        files={"video": ("nope.mov", b"x", "video/quicktime")},
        headers={"Origin": "https://attacker.example"},
    )
    assert csrf_upload.status_code == 403
    assert csrf_upload.json()["detail"] == "Invalid request origin."
    assert app.state.jobs.sessions_count() == 0

    assert browser.post(
        "/api/v1/mobile-tokens",
        json={"label": "Cross-site"},
        headers={"Origin": "https://attacker.example"},
    ).status_code == 403
    assert browser.put(
        "/api/v1/profile",
        json=profile_payload(),
        headers={"Origin": "https://attacker.example"},
    ).status_code == 403

    # Authorization is explicit rather than ambient, so native writes do not
    # require a browser origin while response CORS policy remains unchanged.
    bearer_update = browser.put(
        "/api/v1/profile",
        json=profile_payload(),
        headers={**bearer(token), "Origin": "https://attacker.example"},
    )
    assert bearer_update.status_code == 200
    assert "access-control-allow-origin" not in bearer_update.headers


def test_issue_refuses_auth_epoch_change_while_request_body_is_read(
    app, monkeypatch
):
    browser = TestClient(app)
    signup(browser)
    users: UserStore = app.state.users
    user = users.get_by_email("golfer@example.com")
    assert user is not None

    original_issue = users.issue_mobile_api_token

    def reset_password_before_issue(*args, **kwargs):
        users.set_password(user.id, "replacement-password")
        return original_issue(*args, **kwargs)

    monkeypatch.setattr(
        users,
        "issue_mobile_api_token",
        reset_password_before_issue,
    )
    response = browser.post(
        "/api/v1/mobile-tokens", json={"label": "Delayed phone"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Log in again before adding a device."
    assert users._conn.execute(
        "SELECT COUNT(*) FROM mobile_api_tokens WHERE user_id = ?", (user.id,)
    ).fetchone()[0] == 0
    assert browser.get("/api/v1/me").status_code == 401


def test_store_principal_lifecycle_enforces_selector_expiry_revocation_epoch_and_active_cap(
    tmp_path,
):
    users = UserStore(tmp_path / "tokens.sqlite")
    user = users.create("token-owner@example.com", "longenough")

    expired_raw, expired = issue_store_token(
        users, user.id, "Expired phone", now=-MOBILE_API_TOKEN_TTL_S
    )
    assert users.authenticate_mobile_api_principal(expired_raw, now=0) is None
    assert users.list_mobile_api_tokens(user.id, now=0)[0].active is False

    raw_tokens = []
    for number in range(MOBILE_API_TOKEN_ACTIVE_LIMIT):
        raw, metadata = issue_store_token(
            users, user.id, f"Device {number}", now=100.0
        )
        raw_tokens.append((raw, metadata))
    with pytest.raises(MobileAPITokenLimitError):
        issue_store_token(users, user.id, "One too many", now=101.0)

    statements = []
    users._conn.set_trace_callback(statements.append)
    try:
        assert users.authenticate_mobile_api_principal("not a token", now=101.0) is None
        invalid = f"ciat_{'A' * 24}.{'B' * 43}"
        assert users.authenticate_mobile_api_principal(invalid, now=101.0) is None
    finally:
        users._conn.set_trace_callback(None)
    assert not any("BEGIN IMMEDIATE" in statement for statement in statements)

    principal = users.authenticate_mobile_api_principal(raw_tokens[0][0], now=101.0)
    assert principal is not None
    assert principal.user.id == user.id
    assert principal.selector == raw_tokens[0][1].selector
    assert principal.auth_epoch == user.auth_epoch
    assert principal.installation_key is None
    first_used = users._conn.execute(
        "SELECT last_used_at FROM mobile_api_tokens WHERE selector = ?",
        (raw_tokens[0][1].selector,),
    ).fetchone()["last_used_at"]
    assert first_used == 101.0
    sampled_principal = users.authenticate_mobile_api_principal(
        raw_tokens[0][0], now=120.0
    )
    assert sampled_principal is not None
    assert sampled_principal.selector == raw_tokens[0][1].selector
    sampled_used = users._conn.execute(
        "SELECT last_used_at FROM mobile_api_tokens WHERE selector = ?",
        (raw_tokens[0][1].selector,),
    ).fetchone()["last_used_at"]
    assert sampled_used == first_used
    refreshed_principal = users.authenticate_mobile_api_principal(
        raw_tokens[0][0], now=162.0
    )
    assert refreshed_principal is not None
    assert refreshed_principal.selector == raw_tokens[0][1].selector
    refreshed_used = users._conn.execute(
        "SELECT last_used_at FROM mobile_api_tokens WHERE selector = ?",
        (raw_tokens[0][1].selector,),
    ).fetchone()["last_used_at"]
    assert refreshed_used == 162.0

    users.set_password(user.id, "replacement-password")
    assert users.authenticate_mobile_api_principal(raw_tokens[0][0], now=102.0) is None

    replacement_raw, replacement = issue_store_token(
        users, user.id, "Replacement phone", now=102.0
    )
    replacement_principal = users.authenticate_mobile_api_principal(
        replacement_raw, now=103.0
    )
    assert replacement_principal is not None
    assert replacement_principal.selector == replacement.selector
    assert users.authenticate_mobile_api_token(replacement_raw, now=103.0).id == user.id
    assert users.revoke_mobile_api_token(user.id, replacement.selector, now=104.0)
    assert users.authenticate_mobile_api_principal(replacement_raw, now=105.0) is None
    assert users.revoke_mobile_api_token(user.id, "not-a-selector") is False
    assert users._conn.execute(
        "SELECT token_hash FROM mobile_api_tokens WHERE selector = ?",
        (replacement.selector,),
    ).fetchone()["token_hash"] != replacement_raw


def test_privacy_export_contains_only_device_metadata_and_redaction_cleans_tokens(tmp_path):
    users = UserStore(tmp_path / "privacy.sqlite")
    email = "mobile-privacy@example.com"
    customer_id = "7011"
    user = users.create(email, "longenough", email_verified=True)
    linked = users.upsert_store_customer(email, customer_id)
    assert linked is not None and linked.id == user.id
    raw_token, device = issue_store_token(
        users, user.id, "Privacy phone", now=10.0
    )

    request = users.capture_shopify_data_request(
        shop_domain=STORE,
        configured_shop_domain=STORE,
        customer_id=customer_id,
        order_ids=[],
        event_id="mobile-device-export",
        now=20.0,
    )
    assert request is not None
    snapshot = users.export_shopify_privacy_request(request.request_id, now=21.0)
    assert snapshot is not None
    assert snapshot["mobile_api_tokens"] == [
        {
            "selector": device.selector,
            "user_id": user.id,
            "label": "Privacy phone",
            "created_at": 10.0,
            "last_used_at": None,
            "expires_at": 10.0 + MOBILE_API_TOKEN_TTL_S,
            "revoked_at": None,
        }
    ]
    encoded = json.dumps(snapshot)
    assert raw_token not in encoded
    assert "token_hash" not in encoded
    assert "auth_epoch" not in snapshot["mobile_api_tokens"][0]

    assert users.remove_shopify_customer(
        customer_id,
        email,
        redact=True,
        privacy_event_id="mobile-device-redact",
        privacy_shop_domain=STORE,
    ) == "unlinked"
    assert users.authenticate_mobile_api_token(raw_token, now=22.0) is None
    assert users._conn.execute(
        "SELECT COUNT(*) FROM mobile_api_tokens WHERE user_id = ?", (user.id,)
    ).fetchone()[0] == 0
    assert users.export_shopify_privacy_request(request.request_id, now=22.0) is None

    direct = users.create("deleted-owner@example.com", "longenough")
    issue_store_token(users, direct.id, "Deleted phone", now=30.0)
    users.delete_user(direct.id)
    assert users._conn.execute(
        "SELECT COUNT(*) FROM mobile_api_tokens WHERE user_id = ?", (direct.id,)
    ).fetchone()[0] == 0
