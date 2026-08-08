"""Server-owned native capability discovery contracts."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web.app import create_app
from swinglab.web.mobile_resources import validate_mobile_resource_settings


def _authenticated_client(tmp_path, *, resources_enabled: bool = True):
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["mobile_resources_enabled"] = resources_enabled
    app = create_app(
        cfg,
        sessions_dir=tmp_path / "sessions",
        start_background_workers=False,
    )
    client = TestClient(app)
    user = app.state.users.create(
        "capabilities@example.com",
        "longenough",
        email_verified=True,
    )
    login = client.post(
        "/login",
        data={"email": user.email, "password": "longenough"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    return app, client, user


def _close(app) -> None:
    for resource in (app.state.jobs, app.state.users, app.state.throttle):
        resource.close()


def test_capabilities_publish_server_limits_canonical_domains_and_free_quota(tmp_path):
    """Catches a client having to invent upload, enum, or allowance policy."""

    app, client, _user = _authenticated_client(tmp_path)
    try:
        response = client.get("/api/v1/capabilities")

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"
        assert response.json() == {
            "resource_version": 1,
            "capabilities": {
                "upload": {
                    "max_bytes": 524_288_000,
                    "max_video_seconds": 300,
                    "chunk_bytes": 5_242_880,
                    "active_limit": 2,
                    "allowed_suffixes": [".avi", ".m4v", ".mkv", ".mov", ".mp4"],
                },
                "canonical": {
                    "hands": ["left", "right"],
                    "angles": ["face-on", "dtl"],
                    "clubs": ["driver", "fairway-wood", "hybrid", "iron", "wedge"],
                    "analysis_states": [
                        "queued",
                        "processing",
                        "done",
                        "failed",
                    ],
                },
                "quota": {
                    "plan": "free",
                    "monthly_limit": 1,
                    "used": 0,
                    "remaining": 1,
                },
                "features": {
                    "native_auth": False,
                    "resources": True,
                    "profile_writes": False,
                    "practice_writes": False,
                    "device_management": False,
                    "resumable_upload": False,
                    "privacy": False,
                    "events": False,
                    "push": False,
                    "native_billing": False,
                    "proof_cycle": False,
                },
                "physical_store_url": None,
            },
        }
        flattened = repr(response.json())
        assert "SWINGLAB_SECRET" not in flattened
        assert "MOBILE_STATE_HMAC" not in flattened
    finally:
        _close(app)


def test_capabilities_quota_snapshot_is_zero_write_and_leaves_cleanup_to_legacy(
    tmp_path,
):
    """Catches a native GET deleting expired durable usage receipts."""

    app, client, user = _authenticated_client(tmp_path)
    manager = app.state.jobs
    try:
        with manager._lock:
            manager._conn.execute(
                "INSERT INTO analysis_usage_monthly"
                " (user_hash, month_start, coaching_eligible, refilm_rejections,"
                " expires_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("ab" * 32, 0, 1, 1, time.time() - 1, time.time() - 2),
            )
            manager._conn.commit()
            changes_before = manager._conn.total_changes

        response = client.get("/api/v1/capabilities")

        assert response.status_code == 200
        assert response.json()["capabilities"]["quota"]["used"] == 0
        with manager._lock:
            assert manager._conn.total_changes == changes_before
            assert manager._conn.execute(
                "SELECT COUNT(*) FROM analysis_usage_monthly"
            ).fetchone()[0] == 1

        assert manager.usage_this_month(user.id) == 0
        with manager._lock:
            assert manager._conn.execute(
                "SELECT COUNT(*) FROM analysis_usage_monthly"
            ).fetchone()[0] == 0
    finally:
        _close(app)


def test_capabilities_publish_pro_unlimited_quota_and_canonical_gear_url(tmp_path):
    """Catches entitlement/store policy being inferred or rebuilt by the client."""

    app, client, user = _authenticated_client(tmp_path)
    try:
        app.state.users.set_plan(user.id, "pro", "active")
        app.state.cfg.shop["store_url"] = "https://shop.example.test/"

        response = client.get("/api/v1/capabilities")

        assert response.status_code == 200
        capabilities = response.json()["capabilities"]
        assert capabilities["quota"] == {
            "plan": "pro",
            "monthly_limit": None,
            "used": 0,
            "remaining": None,
        }
        assert (
            capabilities["physical_store_url"]
            == "https://shop.example.test/collections/swinglab-gear"
        )
    finally:
        _close(app)


@pytest.mark.parametrize(
    "store_url",
    (
        "https://operator:secret@example.test",
        "http://[invalid-host",
        "https://example.test:not-a-port",
        "https://example.test/\nsecret",
    ),
)
def test_capabilities_omit_invalid_or_credentialed_store_urls(tmp_path, store_url):
    """Catches malformed configuration leaking credentials or breaking discovery."""

    app, client, _user = _authenticated_client(tmp_path)
    try:
        app.state.cfg.shop["store_url"] = store_url

        response = client.get("/api/v1/capabilities")

        assert response.status_code == 200
        assert response.json()["capabilities"]["physical_store_url"] is None
        assert "operator" not in response.text
        assert "secret" not in response.text
    finally:
        _close(app)


def test_task4_settings_are_explicit_in_shipped_config_and_strictly_bounded():
    """Catches an implicit mutation rollout or unbounded upload policy."""

    shipped = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))["web"]
    assert {
        name: shipped[name]
        for name in (
            "mobile_resources_enabled",
            "mobile_profile_writes_enabled",
            "mobile_practice_writes_enabled",
            "mobile_device_management_enabled",
            "mobile_resumable_upload_enabled",
            "mobile_privacy_enabled",
            "mobile_events_enabled",
            "mobile_push_enabled",
            "mobile_native_billing_enabled",
        )
    } == {
        "mobile_resources_enabled": False,
        "mobile_profile_writes_enabled": False,
        "mobile_practice_writes_enabled": False,
        "mobile_device_management_enabled": False,
        "mobile_resumable_upload_enabled": False,
        "mobile_privacy_enabled": False,
        "mobile_events_enabled": False,
        "mobile_push_enabled": False,
        "mobile_native_billing_enabled": False,
    }
    assert shipped["mobile_upload_chunk_mb"] == 5
    assert shipped["mobile_active_uploads_per_user"] == 2
    assert shipped["mobile_upload_ttl_seconds"] == 86400

    for name, invalid in (
        ("mobile_resources_enabled", 1),
        ("mobile_upload_chunk_mb", 0),
        ("mobile_active_uploads_per_user", True),
        ("mobile_upload_ttl_seconds", -1),
    ):
        web = dict(Config().web)
        web[name] = invalid
        with pytest.raises(ValueError):
            validate_mobile_resource_settings(web)


def test_capabilities_flag_off_is_no_store_404_before_auth_or_quota_work(tmp_path):
    """Catches a disabled read surface authenticating or touching job state."""

    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["mobile_resources_enabled"] = False
    app = create_app(
        cfg,
        sessions_dir=tmp_path / "sessions",
        start_background_workers=False,
    )
    client = TestClient(app)

    def unexpected_usage(_user_id):
        raise AssertionError("disabled capabilities touched quota state")

    app.state.jobs.usage_this_month_snapshot = unexpected_usage
    try:
        response = client.get("/api/v1/capabilities")

        assert response.status_code == 404
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"
        assert response.json() == {
            "resource_version": 1,
            "code": "not_found",
            "message": "Mobile resources are not enabled.",
            "retryable": False,
            "reference_id": None,
        }
    finally:
        _close(app)


def test_capabilities_rechecks_history_epoch_before_reading_quota(
    tmp_path, monkeypatch
):
    """Catches a reset race delivering capability state for a stale identity."""

    app, client, user = _authenticated_client(tmp_path)
    original_get = app.state.users.get
    calls = 0

    def racing_get(user_id):
        nonlocal calls
        calls += 1
        if calls == 2:
            with app.state.users._lock:
                app.state.users._conn.execute(
                    "UPDATE users SET history_epoch = history_epoch + 1 WHERE id = ?",
                    (user_id,),
                )
                app.state.users._conn.commit()
        return original_get(user_id)

    def unexpected_usage(_user_id):
        raise AssertionError("stale capability request touched quota state")

    monkeypatch.setattr(app.state.users, "get", racing_get)
    monkeypatch.setattr(
        app.state.jobs, "usage_this_month_snapshot", unexpected_usage
    )
    try:
        response = client.get("/api/v1/capabilities")

        assert response.status_code == 404
        assert response.json()["code"] == "not_found"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"
    finally:
        _close(app)


@pytest.mark.parametrize(
    "path",
    (
        "/api/v1/capabilities",
        "/api/v1/progress",
        "/api/v1/mobile/sessions",
        "/api/v1/mobile/sessions/missing",
        "/api/v1/mobile/sessions/missing/brief",
        "/api/v1/mobile/today",
    ),
)
def test_every_resource_flag_off_precedes_even_malformed_authorization(tmp_path, path):
    """Catches disabled routes touching bearer state before the rollout gate."""

    cfg = Config()
    cfg.web["require_account"] = True
    app = create_app(
        cfg,
        sessions_dir=tmp_path / "sessions",
        start_background_workers=False,
    )
    client = TestClient(app)
    try:
        response = client.get(path, headers={"Authorization": "Basic invalid"})
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"
        assert response.headers["cache-control"] == "no-store"
    finally:
        _close(app)
