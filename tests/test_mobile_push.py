"""Task 7 first slice: device-bound Expo push registration behind default-off flag."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web.app import create_app
from swinglab.web.mobile_schema import VersionedHMAC
from tests.test_mobile_sign_out import FakeRecoveryFenceLedger


EXPO_PROJECT_ID = "11111111-2222-4333-8444-555555555555"
EXPO_TOKEN_A = "ExponentPushToken[aaaaaaaaaaaaaaaaaaaaaa]"
EXPO_TOKEN_B = "ExponentPushToken[bbbbbbbbbbbbbbbbbbbbbb]"
GOOD_KEY = "00112233445566778899aabbccddeeff"


def _keyring(key_id: str = "k1", fill: bytes = b"k") -> VersionedHMAC:
    return VersionedHMAC(key_id, {key_id: fill * 32})


def _identity(
    *,
    environment: str = "development",
    platform: str = "ios",
    version: str = "1.2.3",
    build: str = "42",
    application_id: str = "com.caddieinsight.app.dev",
) -> dict[str, str]:
    return {
        "X-CaddieInsight-Environment": environment,
        "X-CaddieInsight-Platform": platform,
        "X-CaddieInsight-App-Version": version,
        "X-CaddieInsight-App-Build": build,
        "X-CaddieInsight-Application-Id": application_id,
    }


def _app(
    tmp_path,
    *,
    push_enabled: bool = True,
    expo_project_id: str = EXPO_PROJECT_ID,
    ledger=None,
    keyring: VersionedHMAC | None = None,
    require_account: bool = True,
):
    cfg = Config()
    cfg.web["require_account"] = require_account
    cfg.web["mobile_resources_enabled"] = True
    cfg.web["mobile_push_enabled"] = push_enabled
    cfg.web["mobile_push_expo_project_id"] = expo_project_id
    return create_app(
        cfg,
        sessions_dir=tmp_path / "sessions",
        start_background_workers=False,
        start_shopify_sync_worker=False,
        mobile_state_hmac=keyring or _keyring(),
        recovery_fence_ledger=ledger or FakeRecoveryFenceLedger(),
    )


def _close(app) -> None:
    app.state.jobs.close()
    app.state.throttle.close()
    app.state.users.close()
    if app.state.mobile_keyed_throttle is not None:
        app.state.mobile_keyed_throttle.close()


def _issue(users, email: str, label: str):
    user = users.get_by_email(email)
    if user is None:
        user = users.create(email, "longenough")
    raw, token = users.issue_mobile_api_token(
        user.id,
        label,
        expected_auth_epoch=user.auth_epoch,
    )
    return user, raw, token


def _put_body(
    *,
    token: str = EXPO_TOKEN_A,
    platform: str = "ios",
    app_version: str = "1.2.3",
    expo_project_id: str = EXPO_PROJECT_ID,
    practice_reminders_enabled: bool = True,
) -> dict:
    return {
        "provider": "expo",
        "token": token,
        "platform": platform,
        "app_version": app_version,
        "expo_project_id": expo_project_id,
        "practice_reminders_enabled": practice_reminders_enabled,
    }


def _auth_headers(raw_token: str, **identity_kwargs) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {raw_token}",
        **_identity(**identity_kwargs),
    }


def _registration_count(users) -> int:
    return users._conn.execute(
        "SELECT COUNT(*) FROM mobile_push_registrations"
    ).fetchone()[0]


def test_flag_off_returns_404_before_auth_with_zero_writes(tmp_path):
    app = _app(tmp_path, push_enabled=False, expo_project_id="")
    users = app.state.users
    client = TestClient(app)

    put = client.put(
        "/api/v1/devices/push",
        headers=_identity(),
        json=_put_body(),
    )
    assert put.status_code == 404
    assert put.headers["cache-control"] == "no-store"

    patch = client.patch(
        "/api/v1/devices/push/preferences",
        headers=_identity(),
        json={"practice_reminders_enabled": False},
    )
    assert patch.status_code == 404

    deleted = client.delete("/api/v1/devices/push", headers=_identity())
    assert deleted.status_code == 404

    tables = {
        str(row[0])
        for row in users._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    if "mobile_push_registrations" in tables:
        assert _registration_count(users) == 0
    _close(app)


def test_cookie_only_is_rejected(tmp_path):
    app = _app(tmp_path)
    users = app.state.users
    user = users.create("golfer@example.com", "longenough")
    client = TestClient(app)
    # Establish a browser session cookie without a bearer.
    client.post(
        "/login",
        data={"email": user.email, "password": "longenough"},
        follow_redirects=False,
    )

    response = client.put(
        "/api/v1/devices/push",
        headers=_identity(),
        json=_put_body(),
    )
    assert response.status_code == 401
    assert response.json()["code"] == "bearer_required"
    assert _registration_count(users) == 0
    _close(app)


def test_put_registers_delete_removes_and_absent_delete_is_204(tmp_path):
    app = _app(tmp_path)
    users = app.state.users
    _user, raw, _token = _issue(users, "golfer@example.com", "Phone")
    client = TestClient(app)
    headers = _auth_headers(raw)

    registered = client.put(
        "/api/v1/devices/push", headers=headers, json=_put_body()
    )
    assert registered.status_code == 200, registered.text
    body = registered.json()
    assert body["resource_version"] == 1
    assert body["platform"] == "ios"
    assert body["practice_reminders_enabled"] is True
    assert isinstance(body["registered_at"], (int, float))
    assert "token" not in body
    assert EXPO_TOKEN_A not in json.dumps(body)
    assert _registration_count(users) == 1

    removed = client.delete("/api/v1/devices/push", headers=headers)
    assert removed.status_code == 204
    assert removed.content == b""
    assert _registration_count(users) == 0

    absent = client.delete("/api/v1/devices/push", headers=headers)
    assert absent.status_code == 204
    _close(app)


def test_wrong_expo_project_id_is_rejected(tmp_path):
    app = _app(tmp_path)
    users = app.state.users
    _user, raw, _token = _issue(users, "golfer@example.com", "Phone")
    client = TestClient(app)

    response = client.put(
        "/api/v1/devices/push",
        headers=_auth_headers(raw),
        json=_put_body(expo_project_id=str(uuid.uuid4())),
    )
    assert response.status_code in {400, 422}
    assert _registration_count(users) == 0
    _close(app)


def test_body_platform_mismatch_vs_headers_is_rejected(tmp_path):
    app = _app(tmp_path)
    users = app.state.users
    _user, raw, _token = _issue(users, "golfer@example.com", "Phone")
    client = TestClient(app)

    response = client.put(
        "/api/v1/devices/push",
        headers=_auth_headers(raw, platform="ios"),
        json=_put_body(platform="android"),
    )
    assert response.status_code in {400, 422}
    assert _registration_count(users) == 0
    _close(app)


def test_lost_put_replay_same_body_returns_same_sanitized_row(tmp_path):
    app = _app(tmp_path)
    users = app.state.users
    _user, raw, _token = _issue(users, "golfer@example.com", "Phone")
    client = TestClient(app)
    headers = _auth_headers(raw)
    payload = _put_body()

    first = client.put("/api/v1/devices/push", headers=headers, json=payload)
    replay = client.put("/api/v1/devices/push", headers=headers, json=payload)
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert _registration_count(users) == 1
    _close(app)


def test_token_takeover_by_second_device(tmp_path):
    app = _app(tmp_path)
    users = app.state.users
    user, first_raw, first = _issue(users, "golfer@example.com", "First")
    _same, second_raw, second = _issue(users, user.email, "Second")
    assert first.selector != second.selector
    client = TestClient(app)

    first_put = client.put(
        "/api/v1/devices/push",
        headers=_auth_headers(first_raw),
        json=_put_body(token=EXPO_TOKEN_A),
    )
    assert first_put.status_code == 200

    takeover = client.put(
        "/api/v1/devices/push",
        headers=_auth_headers(second_raw),
        json=_put_body(token=EXPO_TOKEN_A),
    )
    assert takeover.status_code == 200
    assert _registration_count(users) == 1
    row = users._conn.execute(
        "SELECT selector FROM mobile_push_registrations"
    ).fetchone()
    assert row[0] == second.selector
    _close(app)


def test_sign_out_clears_selector_registration(tmp_path):
    app = _app(tmp_path)
    users = app.state.users
    user, raw, token = _issue(users, "golfer@example.com", "Phone")
    _same, other_raw, other = _issue(users, user.email, "Other")
    client = TestClient(app)

    assert (
        client.put(
            "/api/v1/devices/push",
            headers=_auth_headers(raw),
            json=_put_body(token=EXPO_TOKEN_A),
        ).status_code
        == 200
    )
    assert (
        client.put(
            "/api/v1/devices/push",
            headers=_auth_headers(other_raw),
            json=_put_body(token=EXPO_TOKEN_B),
        ).status_code
        == 200
    )
    assert _registration_count(users) == 2

    signed_out = client.post(
        "/api/v1/auth/sign-out",
        headers={
            "Authorization": f"Bearer {raw}",
            "Idempotency-Key": GOOD_KEY,
        },
    )
    assert signed_out.status_code == 204
    assert users.authenticate_mobile_api_principal(raw) is None
    remaining = users._conn.execute(
        "SELECT selector FROM mobile_push_registrations"
    ).fetchall()
    assert [row[0] for row in remaining] == [other.selector]
    _close(app)


def test_openapi_documents_push_routes_when_flag_on(tmp_path):
    app = _app(tmp_path)
    try:
        schema = app.openapi()
        push = schema["paths"]["/api/v1/devices/push"]
        preferences = schema["paths"]["/api/v1/devices/push/preferences"]
        assert "put" in push
        assert "delete" in push
        assert "patch" in preferences
        put_params = {
            parameter["name"] for parameter in push["put"].get("parameters", [])
        }
        assert {
            "X-CaddieInsight-Environment",
            "X-CaddieInsight-Platform",
            "X-CaddieInsight-App-Version",
            "X-CaddieInsight-App-Build",
            "X-CaddieInsight-Application-Id",
        } <= put_params
        request_schema = push["put"]["requestBody"]["content"]["application/json"][
            "schema"
        ]
        # Resolve local refs when present.
        if "$ref" in request_schema:
            ref = request_schema["$ref"].removeprefix("#/")
            target = schema
            for part in ref.split("/"):
                target = target[part]
            request_schema = target
        assert "token" not in (
            schema["components"]["schemas"]
            .get("PushRegistrationResponse", {})
            .get("properties", {})
        )
    finally:
        _close(app)


def test_flag_on_requires_valid_expo_project_id(tmp_path):
    with pytest.raises(ValueError, match="expo.project|Expo project|UUID"):
        _app(tmp_path, push_enabled=True, expo_project_id="")


def test_selector_replacement_replaces_prior_token(tmp_path):
    app = _app(tmp_path)
    users = app.state.users
    _user, raw, token = _issue(users, "golfer@example.com", "Phone")
    client = TestClient(app)
    headers = _auth_headers(raw)

    assert (
        client.put(
            "/api/v1/devices/push",
            headers=headers,
            json=_put_body(token=EXPO_TOKEN_A),
        ).status_code
        == 200
    )
    assert (
        client.put(
            "/api/v1/devices/push",
            headers=headers,
            json=_put_body(token=EXPO_TOKEN_B),
        ).status_code
        == 200
    )
    assert _registration_count(users) == 1
    row = users._conn.execute(
        "SELECT token, selector FROM mobile_push_registrations"
    ).fetchone()
    assert row[0] == EXPO_TOKEN_B
    assert row[1] == token.selector
    _close(app)


def test_patch_preferences_absolute_bool_and_same_body_replay(tmp_path):
    app = _app(tmp_path)
    users = app.state.users
    _user, raw, _token = _issue(users, "golfer@example.com", "Phone")
    client = TestClient(app)
    headers = _auth_headers(raw)

    assert (
        client.put(
            "/api/v1/devices/push",
            headers=headers,
            json=_put_body(practice_reminders_enabled=True),
        ).status_code
        == 200
    )
    first = client.patch(
        "/api/v1/devices/push/preferences",
        headers=headers,
        json={"practice_reminders_enabled": False},
    )
    replay = client.patch(
        "/api/v1/devices/push/preferences",
        headers=headers,
        json={"practice_reminders_enabled": False},
    )
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert first.json()["practice_reminders_enabled"] is False
    enabled = users._conn.execute(
        "SELECT practice_reminders_enabled FROM mobile_push_registrations"
    ).fetchone()[0]
    assert int(enabled) == 0
    _close(app)
