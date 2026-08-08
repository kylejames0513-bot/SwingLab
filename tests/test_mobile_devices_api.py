"""Gate 4D: recovery-fenced device list/revoke and fenced legacy token revoke.

These tests drive the bearer-only ``/api/v1/devices`` surface and the fenced
routing of the legacy browser ``/api/v1/mobile-tokens`` revoke through the same
recovery-fenced credential-revocation service.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web.app import create_app
from swinglab.web.mobile_schema import VersionedHMAC
from tests.test_mobile_sign_out import FakeRecoveryFenceLedger


GOOD_KEY = "00112233445566778899aabbccddeeff"
OTHER_KEY = "ffeeddccbbaa99887766554433221100"


def _keyring(key_id: str = "k1", fill: bytes = b"k") -> VersionedHMAC:
    return VersionedHMAC(key_id, {key_id: fill * 32})


def _app(
    tmp_path,
    *,
    ledger,
    keyring: VersionedHMAC | None,
    device_management: bool = True,
    resources: bool = True,
    require_account: bool = True,
):
    cfg = Config()
    cfg.web["require_account"] = require_account
    cfg.web["mobile_resources_enabled"] = resources
    cfg.web["mobile_device_management_enabled"] = device_management
    return create_app(
        cfg,
        sessions_dir=tmp_path / "sessions",
        start_background_workers=False,
        start_shopify_sync_worker=False,
        mobile_state_hmac=keyring,
        recovery_fence_ledger=ledger,
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


def _delete_headers(raw_token: str, key: str = GOOD_KEY) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {raw_token}",
        "Idempotency-Key": key,
    }


def test_flag_off_returns_no_store_404_before_auth_with_zero_writes(tmp_path):
    app = _app(
        tmp_path,
        ledger=FakeRecoveryFenceLedger(),
        keyring=_keyring(),
        device_management=False,
    )
    users = app.state.users
    client = TestClient(app)

    listed = client.get("/api/v1/devices")
    assert listed.status_code == 404
    assert listed.headers["cache-control"] == "no-store"

    deleted = client.delete(
        "/api/v1/devices/somefakeselector0001",
        headers={"Idempotency-Key": GOOD_KEY},
    )
    assert deleted.status_code == 404
    assert deleted.headers["cache-control"] == "no-store"

    assert users._conn.execute(
        "SELECT COUNT(*) FROM mobile_device_revoke_journals"
    ).fetchone()[0] == 0
    _close(app)


def test_bearer_lists_owned_devices_and_rejects_cookie_only(tmp_path):
    app = _app(tmp_path, ledger=FakeRecoveryFenceLedger(), keyring=_keyring())
    users = app.state.users
    user, first_raw, first = _issue(users, "golfer@example.com", "First")
    _same, _second_raw, second = _issue(users, user.email, "Second")
    client = TestClient(app)

    listed = client.get(
        "/api/v1/devices", headers={"Authorization": f"Bearer {first_raw}"}
    )
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    payload = listed.json()
    assert payload["resource_version"] == 1
    assert {device["selector"] for device in payload["devices"]} == {
        first.selector,
        second.selector,
    }

    cookie_only = client.get("/api/v1/devices")
    assert cookie_only.status_code == 401
    assert cookie_only.json()["code"] == "bearer_required"
    _close(app)


def test_delete_requires_one_strict_128_bit_idempotency_key(tmp_path):
    app = _app(tmp_path, ledger=FakeRecoveryFenceLedger(), keyring=_keyring())
    _user, raw, token = _issue(app.state.users, "golfer@example.com", "Phone")
    client = TestClient(app)

    missing = client.delete(
        f"/api/v1/devices/{token.selector}",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert missing.status_code == 400
    assert missing.json()["message"] == "Invalid Idempotency-Key."

    invalid = client.delete(
        f"/api/v1/devices/{token.selector}",
        headers=_delete_headers(raw, "not-a-valid-key"),
    )
    assert invalid.status_code == 400
    assert app.state.users.authenticate_mobile_api_principal(raw) is not None
    _close(app)


def test_delete_other_device_revokes_only_the_target(tmp_path):
    ledger = FakeRecoveryFenceLedger()
    app = _app(tmp_path, ledger=ledger, keyring=_keyring())
    users = app.state.users
    user, initiator_raw, initiator = _issue(users, "golfer@example.com", "Initiator")
    _same, target_raw, target = _issue(users, user.email, "Target")
    client = TestClient(app)

    response = client.delete(
        f"/api/v1/devices/{target.selector}",
        headers=_delete_headers(initiator_raw),
    )
    assert response.status_code == 204
    assert response.content == b""
    assert users.authenticate_mobile_api_principal(target_raw) is None
    assert users.authenticate_mobile_api_principal(initiator_raw) is not None
    assert len(ledger.events) == 1
    assert initiator.selector != target.selector
    _close(app)


def test_self_delete_exact_replay_after_revocation_still_completes(tmp_path):
    ledger = FakeRecoveryFenceLedger()
    app = _app(tmp_path, ledger=ledger, keyring=_keyring())
    users = app.state.users
    _user, raw, token = _issue(users, "golfer@example.com", "Phone")
    client = TestClient(app)

    first = client.delete(
        f"/api/v1/devices/{token.selector}", headers=_delete_headers(raw)
    )
    assert first.status_code == 204
    assert users.authenticate_mobile_api_principal(raw) is None

    replay = client.delete(
        f"/api/v1/devices/{token.selector}", headers=_delete_headers(raw)
    )
    assert replay.status_code == 204
    _close(app)


def test_publish_outage_returns_bounded_202_and_retry_finishes(tmp_path):
    ledger = FakeRecoveryFenceLedger(outage=True)
    app = _app(tmp_path, ledger=ledger, keyring=_keyring())
    users = app.state.users
    _user, raw, token = _issue(users, "golfer@example.com", "Phone")
    client = TestClient(app)

    pending = client.delete(
        f"/api/v1/devices/{token.selector}", headers=_delete_headers(raw)
    )
    assert pending.status_code == 202
    assert pending.json()["status"] == "pending"
    assert pending.json()["retry_after_seconds"] >= 1
    assert pending.headers["retry-after"] == str(pending.json()["retry_after_seconds"])
    assert pending.headers["cache-control"] == "no-store"
    assert users.authenticate_mobile_api_principal(raw) is None

    ledger.outage = False
    finished = client.delete(
        f"/api/v1/devices/{token.selector}", headers=_delete_headers(raw)
    )
    assert finished.status_code == 204
    _close(app)


def test_capabilities_reflect_device_management_flag(tmp_path):
    enabled = _app(tmp_path / "on", ledger=FakeRecoveryFenceLedger(), keyring=_keyring())
    _user, raw, _token = _issue(enabled.state.users, "golfer@example.com", "Phone")
    features = (
        TestClient(enabled)
        .get("/api/v1/capabilities", headers={"Authorization": f"Bearer {raw}"})
        .json()["capabilities"]["features"]
    )
    assert features["device_management"] is True
    _close(enabled)

    disabled = _app(
        tmp_path / "off",
        ledger=FakeRecoveryFenceLedger(),
        keyring=_keyring(),
        device_management=False,
    )
    _user2, raw2, _token2 = _issue(disabled.state.users, "golfer@example.com", "Phone")
    disabled_features = (
        TestClient(disabled)
        .get("/api/v1/capabilities", headers={"Authorization": f"Bearer {raw2}"})
        .json()["capabilities"]["features"]
    )
    assert disabled_features["device_management"] is False
    _close(disabled)


def test_legacy_token_revoke_is_recovery_fenced(tmp_path):
    outage = FakeRecoveryFenceLedger(outage=True)
    app = _app(
        tmp_path,
        ledger=outage,
        keyring=_keyring(),
        device_management=False,
    )
    client = TestClient(app)
    assert client.post(
        "/signup",
        data={"email": "golfer@example.com", "password": "longenough"},
        follow_redirects=False,
    ).status_code == 303
    issued = client.post("/api/v1/mobile-tokens", json={"label": "Kyle's iPhone"})
    assert issued.status_code == 201
    selector = issued.json()["device"]["selector"]
    raw_token = issued.json()["token"]

    blocked = client.delete(f"/api/v1/mobile-tokens/{selector}")
    assert blocked.status_code == 503
    assert "detail" in blocked.json()
    assert app.state.users.authenticate_mobile_api_principal(raw_token) is None

    outage.outage = False
    completed = client.delete(f"/api/v1/mobile-tokens/{selector}")
    assert completed.status_code == 200
    assert completed.json()["revoked"] is True
    assert completed.json()["resource_version"] == 1
    assert app.state.users.authenticate_mobile_api_principal(raw_token) is None
    _close(app)


def test_startup_requires_recovery_readiness_when_device_management_enabled(tmp_path):
    with pytest.raises(RuntimeError):
        _app(tmp_path, ledger=None, keyring=_keyring(), device_management=True)
