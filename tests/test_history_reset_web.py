"""Customer-facing swing-history reset and its account-preservation fence."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.backups.core import (
    DATABASE_BUNDLE_PATH,
    create_backup,
    restore_backup,
)
from swinglab.web import app as app_module
from swinglab.web import jobs as jobs_module
from swinglab.web import mailer
from swinglab.web.app import create_app
from swinglab.web.jobs import _SCHEMA as JOBS_SCHEMA
from swinglab.web.throttle import _SCHEMA as THROTTLE_SCHEMA
from swinglab.web.users import _SCHEMA as USERS_SCHEMA
from swinglab.web.users import HistoryEpochError, PasswordAddConflict
from tests.test_web import fake_analyze_ok, wait_for


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    for name in (
        "RESEND_API_KEY",
        "SWINGLAB_SMTP_URL",
        "SWINGLAB_MAIL_FROM",
        "SHOPIFY_STORE_DOMAIN",
        "SHOPIFY_WEBHOOK_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["passwordless_login"] = False
    cfg.web["history_reset_enabled"] = True
    cfg.billing["free_per_month"] = 2
    return create_app(cfg, sessions_dir=tmp_path / "sessions")


def signup(client: TestClient, email: str = "reset@example.com") -> None:
    response = client.post(
        "/signup",
        data={"email": email, "password": "longenough"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def confirmation_nonce(client: TestClient) -> tuple[str, str]:
    page = client.get("/account/history/delete")
    assert page.status_code == 200
    match = re.search(r'name="nonce" value="([^"]+)"', page.text)
    assert match is not None
    return match.group(1), page.text


def upload_finished_swing(client: TestClient) -> str:
    response = client.post(
        "/upload",
        files={"video": ("baseline.mov", b"fake video bytes", "video/quicktime")},
        data={"club": "iron"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    job_id = response.headers["location"].rsplit("/", 1)[-1]
    assert wait_for(client, job_id)["status"] == "done"
    return job_id


@pytest.mark.parametrize("flag_value", [False, "false", 1, None])
def test_reset_surface_is_inert_until_the_compatibility_floor_is_live(
    tmp_path, monkeypatch, flag_value
):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["passwordless_login"] = False
    assert cfg.web["history_reset_enabled"] is False
    cfg.web["history_reset_enabled"] = flag_value
    floor_app = create_app(cfg, sessions_dir=tmp_path / "floor-sessions")
    client = TestClient(floor_app)
    signup(client, "floor@example.com")

    account = client.get("/account")

    assert account.status_code == 200
    assert "/account/history/delete" not in account.text
    assert client.get("/account/history/delete").status_code == 404
    assert client.post("/account/history/delete").status_code == 404


def test_restored_pre_feature_database_migrates_on_web_boot(tmp_path):
    sessions = tmp_path / "legacy-sessions"
    sessions.mkdir()
    database = sessions / "swinglab.db"
    connection = sqlite3.connect(database)
    connection.executescript(USERS_SCHEMA + JOBS_SCHEMA + THROTTLE_SCHEMA)
    connection.execute("DROP TABLE analysis_usage_monthly")
    connection.execute("DROP TABLE history_reset_operations")
    connection.execute("ALTER TABLE users DROP COLUMN history_epoch")
    connection.execute(
        "INSERT INTO users"
        " (id, email, password_hash, created_at, plan, subscription_status)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            "legacy-user",
            "legacy@example.invalid",
            "legacy-hash",
            1.0,
            "free",
            "none",
        ),
    )
    connection.execute(
        "INSERT INTO jobs"
        " (id, status, created_at, updated_at, user_id)"
        " VALUES (?, ?, ?, ?, ?)",
        ("legacyjob", "failed", 1.0, 2.0, "legacy-user"),
    )
    connection.commit()
    connection.close()
    captured_at = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    bundle = tmp_path / "legacy-bundle"
    create_backup(sessions, bundle, now=captured_at)
    scratch = tmp_path / "legacy-restore"
    scratch.mkdir()
    restored = restore_backup(bundle, scratch)
    boot_sessions = tmp_path / "boot-restored-legacy"
    boot_sessions.mkdir()
    shutil.copy2(
        restored["restore_dir"] / DATABASE_BUNDLE_PATH,
        boot_sessions / "swinglab.db",
    )

    restored_app = create_app(Config(), sessions_dir=boot_sessions)
    with TestClient(restored_app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["history_cleanup_pending"] == 0
        restored_user = restored_app.state.users.get("legacy-user")
        assert restored_user is not None and restored_user.history_epoch == 0
        assert restored_app.state.jobs.get("legacyjob") is not None
    migrated = sqlite3.connect(boot_sessions / "swinglab.db")
    assert "history_epoch" in {
        row[1] for row in migrated.execute("PRAGMA table_info(users)")
    }
    assert {
        "analysis_usage_monthly",
        "history_reset_operations",
    }.issubset(
        {
            row[0]
            for row in migrated.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    )
    migrated.close()


def test_reset_page_is_private_cookie_only_and_explains_scope(app):
    client = TestClient(app)
    assert (
        client.get("/account/history/delete", follow_redirects=False).status_code
        == 303
    )
    signup(client)

    _, html = confirmation_nonce(client)

    assert "Delete swing history and start over" in html
    assert "What stays" in html
    assert "membership and purchases" in html
    assert "analysis allowance" in html
    response = client.get(
        "/account/history/delete",
        headers={"Authorization": "Bearer not-a-browser-credential"},
    )
    assert response.status_code == 401
    assert "no-store" in client.get("/account/history/delete").headers[
        "cache-control"
    ]


def test_reset_deletes_swing_history_but_preserves_account_value_and_usage(app):
    client = TestClient(app)
    signup(client)
    users = app.state.users
    manager = app.state.jobs
    user = users.get_by_email("reset@example.com")
    assert user is not None
    users.set_plan(user.id, "pro", "active")
    users.upsert_golfer_profile(
        user.id,
        display_name="Reset Golfer",
        experience_mode="improve",
        handicap_range="20_to_29",
        primary_goal="consistency",
        practice_minutes=20,
        sessions_per_week=2,
        handedness="right",
        camera_angle="face-on",
        preferred_club="iron",
    )
    raw_device_token, _ = users.issue_mobile_api_token(
        user.id,
        "Test phone",
        expected_auth_epoch=user.auth_epoch,
    )
    job_id = upload_finished_swing(client)
    job = manager.get(job_id)
    assert job is not None
    job_dir = job.session_dir
    owned_report = client.get(
        f"/session/{job_id}/report", follow_redirects=True
    )
    assert owned_report.status_code == 200
    assert owned_report.headers["cache-control"] == "private, no-store"
    bearer_client = TestClient(app)
    bearer_status = bearer_client.get(
        f"/session/{job_id}",
        headers={"Authorization": f"Bearer {raw_device_token}"},
    )
    assert bearer_status.status_code == 200
    assert bearer_status.headers["cache-control"] == "private, no-store"
    for path in (
        "/api/v1/profile",
        "/api/v1/today",
        "/api/v1/sessions",
        f"/api/v1/sessions/{job_id}",
        "/api/v1/practice-checkins",
        f"/api/session/{job_id}",
    ):
        history_response = client.get(path)
        assert history_response.status_code == 200, path
        assert "no-store" in history_response.headers["cache-control"], path
    users.record_practice_checkin(user.id, job_id)
    users.record_product_event(
        "brief_viewed", user_id=user.id, session_id=job_id
    )
    # Session telemetry can outlive a job row after automatic retention; a
    # customer reset must remove that orphaned history too.
    users.record_product_event(
        "repeat_analysis", user_id=user.id, session_id="expired-session"
    )
    users.record_product_event("pro_clicked", user_id=user.id)
    users._conn.execute(
        "INSERT INTO shopify_orders"
        " (order_id, email, days, applied_at, user_id) VALUES (?, ?, ?, ?, ?)",
        ("order-preserved", user.email, 31.0, time.time(), user.id),
    )
    privacy_snapshot = json.dumps(
        {
            "schema_version": 1,
            "accounts": [{"id": user.id}],
            "analyses": [{"user_id": user.id}],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    users._conn.execute(
        "INSERT INTO shopify_privacy_requests"
        " (request_id, shop_domain, status, snapshot_json, snapshot_sha256,"
        "  record_count, snapshot_bytes, created_at, completed_at, expires_at,"
        "  delivered_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "spr_history_reset",
            "example.myshopify.com",
            "delivered",
            privacy_snapshot,
            hashlib.sha256(privacy_snapshot.encode()).hexdigest(),
            2,
            len(privacy_snapshot.encode()),
            time.time(),
            time.time(),
            time.time() + 3600,
            time.time(),
        ),
    )
    users._conn.commit()
    usage_before = manager.usage_this_month(user.id)
    assert usage_before == 1

    nonce, _ = confirmation_nonce(client)
    response = client.post(
        "/account/history/delete",
        data={
            "nonce": nonce,
            "confirmation": "START OVER",
            "password": "longenough",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/account"
    assert response.headers["clear-site-data"] == '"cache"'
    assert manager.get(job_id) is None
    assert not job_dir.exists()
    assert manager.usage_this_month(user.id) == usage_before
    preserved = users.get(user.id)
    assert preserved is not None
    assert preserved.is_pro
    assert preserved.history_epoch == user.history_epoch + 1
    assert users.authenticate(user.email, "longenough") is not None
    assert users.authenticate_mobile_api_token(raw_device_token) is not None
    assert users.get_golfer_profile(user.id).display_name == "Reset Golfer"
    assert users._conn.execute(
        "SELECT COUNT(*) FROM shopify_orders WHERE order_id = 'order-preserved'"
    ).fetchone()[0] == 1
    assert users._conn.execute(
        "SELECT COUNT(*) FROM shopify_privacy_requests"
        " WHERE request_id = 'spr_history_reset'"
    ).fetchone()[0] == 0
    assert users.list_practice_checkins(user.id) == []
    event_rows = users._conn.execute(
        "SELECT event_name, session_id FROM product_events WHERE user_id = ?",
        (user.id,),
    ).fetchall()
    events = [(row["event_name"], row["session_id"]) for row in event_rows]
    assert ("brief_viewed", job_id) not in events
    assert ("repeat_analysis", "expired-session") not in events
    assert ("pro_clicked", None) in events

    account = client.get(response.headers["location"])
    assert account.status_code == 200
    assert "Your swing history has been deleted" in account.text
    assert re.search(
        r"this month(?:&#39;|')s\s+allowance are unchanged",
        account.text,
    )
    assert "Reset Golfer" in account.text
    mobile_me = client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {raw_device_token}"}
    )
    assert mobile_me.status_code == 200
    assert mobile_me.json()["identity"]["history_epoch"] == preserved.history_epoch
    assert mobile_me.headers["cache-control"] == "no-store"


def test_active_analysis_blocks_reset_without_changing_history_epoch(app):
    client = TestClient(app)
    signup(client)
    user = app.state.users.get_by_email("reset@example.com")
    assert user is not None
    job = app.state.jobs.create_session(
        source_name="still-uploading.mov",
        user_id=user.id,
        club="iron",
    )
    original_epoch = user.history_epoch
    nonce, _ = confirmation_nonce(client)

    response = client.post(
        "/account/history/delete",
        data={
            "nonce": nonce,
            "confirmation": "START OVER",
            "password": "longenough",
        },
    )

    assert response.status_code == 409
    assert "still uploading or processing" in response.text
    assert app.state.jobs.get(job.id) is not None
    assert job.session_dir.exists()
    assert app.state.users.get(user.id).history_epoch == original_epoch


def test_reset_requires_nonce_exact_phrase_and_current_password(app):
    client = TestClient(app)
    signup(client)
    user = app.state.users.get_by_email("reset@example.com")
    assert user is not None

    expired = client.post(
        "/account/history/delete",
        data={
            "nonce": "made-up",
            "confirmation": "START OVER",
            "password": "longenough",
        },
    )
    assert expired.status_code == 400
    assert "confirmation expired" in expired.text

    nonce, _ = confirmation_nonce(client)
    wrong_phrase = client.post(
        "/account/history/delete",
        data={
            "nonce": nonce,
            "confirmation": "start over",
            "password": "longenough",
        },
    )
    assert wrong_phrase.status_code == 400
    assert "exactly" in wrong_phrase.text

    nonce, _ = confirmation_nonce(client)
    wrong_password = client.post(
        "/account/history/delete",
        data={
            "nonce": nonce,
            "confirmation": "START OVER",
            "password": "not-the-password",
        },
    )
    assert wrong_password.status_code == 400
    assert "password did not match" in wrong_password.text
    assert app.state.users.get(user.id).history_epoch == 0


def test_auth_recovery_during_reset_rolls_back_and_requires_login(
    app, monkeypatch
):
    client = TestClient(app)
    signup(client)
    users = app.state.users
    manager = app.state.jobs
    user = users.get_by_email("reset@example.com")
    assert user is not None
    job_id = upload_finished_swing(client)
    job = manager.get(job_id)
    assert job is not None
    original_reset = manager.reset_user_history

    def revoke_then_reset(*args, **kwargs):
        users.set_password(user.id, "new-password-after-recovery")
        return original_reset(*args, **kwargs)

    monkeypatch.setattr(manager, "reset_user_history", revoke_then_reset)
    nonce, _ = confirmation_nonce(client)
    response = client.post(
        "/account/history/delete",
        data={
            "nonce": nonce,
            "confirmation": "START OVER",
            "password": "longenough",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert manager.get(job_id) is not None
    assert job.session_dir.is_dir()
    assert users.get(user.id).history_epoch == user.history_epoch


def test_saved_confirmation_cookie_cannot_delete_post_reset_history(app):
    client = TestClient(app)
    signup(client)
    manager = app.state.jobs
    user = app.state.users.get_by_email("reset@example.com")
    assert user is not None
    first_job_id = upload_finished_swing(client)
    nonce, _ = confirmation_nonce(client)
    saved_session_cookie = client.cookies.get("session")
    assert saved_session_cookie
    form = {
        "nonce": nonce,
        "confirmation": "START OVER",
        "password": "longenough",
    }

    first = client.post(
        "/account/history/delete", data=form, follow_redirects=False
    )
    assert first.status_code == 303
    assert manager.get(first_job_id) is None
    # The browser that performed the reset advances with the account and can
    # deliberately start another confirmation without signing in again.
    assert client.get("/account/history/delete").status_code == 200
    created_after = manager.create_session(
        source_name="after-reset.mov",
        user_id=user.id,
        club="iron",
    )
    created_after.status = jobs_module.FAILED
    manager._save(created_after)

    stale_get_client = TestClient(app)
    stale_get_client.cookies.set("session", saved_session_cookie)
    stale_get = stale_get_client.get(
        "/account/history/delete", follow_redirects=False
    )
    stale_post_client = TestClient(app)
    stale_post_client.cookies.set("session", saved_session_cookie)
    replay = stale_post_client.post(
        "/account/history/delete", data=form, follow_redirects=False
    )

    assert stale_get.status_code == 303
    assert stale_get.headers["location"] == "/login"
    assert replay.status_code == 303
    assert replay.headers["location"] == "/login"
    assert manager.get(created_after.id) is not None
    assert created_after.session_dir.is_dir()
    assert app.state.users.get(user.id).history_epoch == 1


def test_transaction_rejects_a_stale_confirmation_generation(app):
    client = TestClient(app)
    signup(client)
    users = app.state.users
    manager = app.state.jobs
    user = users.get_by_email("reset@example.com")
    assert user is not None
    job_id = upload_finished_swing(client)
    job = manager.get(job_id)
    assert job is not None

    with pytest.raises(jobs_module.HistoryResetError) as error:
        manager.reset_user_history(
            user.id,
            delete_related=lambda connection, user_id: (
                users.delete_swing_history_related(
                    connection,
                    user_id,
                    expected_auth_epoch=user.auth_epoch,
                    expected_history_epoch=user.history_epoch + 1,
                )
            ),
        )

    assert isinstance(error.value.__cause__, HistoryEpochError)
    assert manager.get(job_id) is not None
    assert job.session_dir.is_dir()
    assert users.get(user.id).history_epoch == user.history_epoch


def test_undelivered_shopify_privacy_export_blocks_reset_and_stays_replayable(
    app
):
    client = TestClient(app)
    users = app.state.users
    manager = app.state.jobs
    user = users.create(
        "privacy-pending@example.com",
        "longenough",
        email_verified=True,
    )
    users.upsert_store_customer(user.email, "7001")
    login = client.post(
        "/login",
        data={"email": user.email, "password": "longenough"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    job_id = upload_finished_swing(client)
    request, replayed = users.capture_shopify_data_request(
        shop_domain="example.myshopify.com",
        configured_shop_domain="example.myshopify.com",
        customer_id="7001",
        order_ids=[],
        event_id="history-reset-pending-export",
        include_replay_status=True,
    )
    assert request is not None and not replayed
    assert users.export_shopify_privacy_request(request.request_id) is not None
    nonce, _ = confirmation_nonce(client)

    reset = client.post(
        "/account/history/delete",
        data={
            "nonce": nonce,
            "confirmation": "START OVER",
            "password": "longenough",
        },
    )

    assert reset.status_code == 409
    assert "privacy export" in reset.text
    assert manager.get(job_id) is not None
    assert manager.get(job_id).session_dir.is_dir()
    assert users.export_shopify_privacy_request(request.request_id) is not None
    replay_request, replayed = users.capture_shopify_data_request(
        shop_domain="example.myshopify.com",
        configured_shop_domain="example.myshopify.com",
        customer_id="7001",
        order_ids=[],
        event_id="history-reset-pending-export",
        include_replay_status=True,
    )
    assert replayed
    assert replay_request is not None
    assert replay_request.request_id == request.request_id


def test_old_history_epoch_cannot_write_after_reset(app):
    client = TestClient(app)
    signup(client)
    users = app.state.users
    user = users.get_by_email("reset@example.com")
    assert user is not None
    nonce, _ = confirmation_nonce(client)
    response = client.post(
        "/account/history/delete",
        data={
            "nonce": nonce,
            "confirmation": "START OVER",
            "password": "longenough",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with pytest.raises(HistoryEpochError):
        users.record_product_event(
            "pro_clicked",
            user_id=user.id,
            expected_history_epoch=user.history_epoch,
        )
    current = users.get(user.id)
    assert current is not None
    assert users.record_product_event(
        "pro_clicked",
        user_id=user.id,
        expected_history_epoch=current.history_epoch,
    )


def test_passwordless_reset_requires_a_fresh_sign_in(
    tmp_path, monkeypatch
):
    sent: list[str] = []
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    monkeypatch.setenv(
        "SWINGLAB_SMTP_URL", "smtp+starttls://u:p@mail.test:587"
    )
    monkeypatch.setenv(
        "SWINGLAB_MAIL_FROM", "CaddieInsight <no-reply@test.example>"
    )
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("SHOPIFY_STORE_DOMAIN", raising=False)
    monkeypatch.delenv("SHOPIFY_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(
        mailer,
        "send",
        lambda _to, _subject, body: sent.append(body),
    )
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["history_reset_enabled"] = True
    client = TestClient(
        create_app(cfg, sessions_dir=tmp_path / "passwordless-sessions")
    )

    requested = client.post(
        "/login/email", data={"email": "passwordless@example.com"}
    )
    assert requested.status_code == 200
    code = re.search(r"\b(\d{6})\b", sent[-1]).group(1)
    signed_in = client.post(
        "/login/code",
        data={"email": "passwordless@example.com", "code": code},
        follow_redirects=False,
    )
    assert signed_in.status_code == 303
    user = client.app.state.users.get_by_email("passwordless@example.com")
    assert user is not None and not user.has_password

    monkeypatch.setattr(app_module, "HISTORY_RESET_RECENT_AUTH_S", -1)
    stale = client.get("/account/history/delete")
    assert stale.status_code == 200
    assert "authenticate again" in stale.text
    assert 'action="/account/history/delete"' not in stale.text

    monkeypatch.setattr(app_module, "HISTORY_RESET_RECENT_AUTH_S", 15 * 60)
    nonce, page = confirmation_nonce(client)
    assert "Current password" not in page
    reset = client.post(
        "/account/history/delete",
        data={"nonce": nonce, "confirmation": "START OVER"},
        follow_redirects=False,
    )
    assert reset.status_code == 303


def test_stale_passwordless_cookie_cannot_create_a_reset_credential(
    tmp_path, monkeypatch
):
    sent: list[str] = []
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    monkeypatch.setenv(
        "SWINGLAB_SMTP_URL", "smtp+starttls://u:p@mail.test:587"
    )
    monkeypatch.setenv(
        "SWINGLAB_MAIL_FROM", "CaddieInsight <no-reply@test.example>"
    )
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("SHOPIFY_STORE_DOMAIN", raising=False)
    monkeypatch.delenv("SHOPIFY_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(
        mailer,
        "send",
        lambda _to, _subject, body: sent.append(body),
    )
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["history_reset_enabled"] = True
    reset_app = create_app(
        cfg, sessions_dir=tmp_path / "stale-passwordless-sessions"
    )
    owner = TestClient(reset_app)
    email = "stale-passwordless@example.com"
    assert owner.post("/login/email", data={"email": email}).status_code == 200
    code = re.search(r"\b(\d{6})\b", sent[-1]).group(1)
    signed_in = owner.post(
        "/login/code",
        data={"email": email, "code": code},
        follow_redirects=False,
    )
    assert signed_in.status_code == 303
    saved_session_cookie = owner.cookies.get("session")
    assert saved_session_cookie
    user = reset_app.state.users.get_by_email(email)
    assert user is not None and not user.has_password

    nonce, _ = confirmation_nonce(owner)
    first = owner.post(
        "/account/history/delete",
        data={"nonce": nonce, "confirmation": "START OVER"},
        follow_redirects=False,
    )
    assert first.status_code == 303
    created_after = reset_app.state.jobs.create_session(
        source_name="after-passwordless-reset.mov",
        user_id=user.id,
        club="iron",
    )
    created_after.status = jobs_module.FAILED
    reset_app.state.jobs._save(created_after)

    stale = TestClient(reset_app)
    stale.cookies.set("session", saved_session_cookie)
    add_password = stale.post(
        "/account/password",
        data={"password": "attacker-created", "return_to": ""},
        follow_redirects=False,
    )

    assert add_password.status_code == 303
    assert add_password.headers["location"] == "/login"
    assert not reset_app.state.users.get(user.id).has_password
    attempted_login = TestClient(reset_app).post(
        "/login",
        data={"email": email, "password": "attacker-created"},
        follow_redirects=False,
    )
    assert attempted_login.status_code == 200
    stale_reset = TestClient(reset_app)
    stale_reset.cookies.set("session", saved_session_cookie)
    blocked = stale_reset.get(
        "/account/history/delete", follow_redirects=False
    )
    assert blocked.status_code == 303
    assert blocked.headers["location"] == "/login"
    assert reset_app.state.jobs.get(created_after.id) is not None
    assert created_after.session_dir.is_dir()


def test_password_add_and_history_reset_are_one_epoch_atomic_choice(
    tmp_path, monkeypatch
):
    sent: list[str] = []
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    monkeypatch.setenv(
        "SWINGLAB_SMTP_URL", "smtp+starttls://u:p@mail.test:587"
    )
    monkeypatch.setenv(
        "SWINGLAB_MAIL_FROM", "CaddieInsight <no-reply@test.example>"
    )
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("SHOPIFY_STORE_DOMAIN", raising=False)
    monkeypatch.delenv("SHOPIFY_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(
        mailer,
        "send",
        lambda _to, _subject, body: sent.append(body),
    )
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["history_reset_enabled"] = True
    race_app = create_app(
        cfg, sessions_dir=tmp_path / "password-reset-race-sessions"
    )
    reset_client = TestClient(race_app)
    email = "password-race@example.com"
    assert reset_client.post(
        "/login/email", data={"email": email}
    ).status_code == 200
    code = re.search(r"\b(\d{6})\b", sent[-1]).group(1)
    assert reset_client.post(
        "/login/code",
        data={"email": email, "code": code},
        follow_redirects=False,
    ).status_code == 303
    user = race_app.state.users.get_by_email(email)
    assert user is not None and not user.has_password
    nonce, _ = confirmation_nonce(reset_client)
    add_client = TestClient(race_app)
    add_client.cookies.set("session", reset_client.cookies.get("session"))
    add_reached_cas = threading.Event()
    release_add = threading.Event()
    original_add = race_app.state.users.add_password
    result: dict[str, object] = {}
    errors: list[BaseException] = []

    def paused_add(*args, **kwargs):
        add_reached_cas.set()
        assert release_add.wait(5)
        return original_add(*args, **kwargs)

    def request_add():
        try:
            result["response"] = add_client.post(
                "/account/password",
                data={"password": "racing-password", "return_to": ""},
                follow_redirects=False,
            )
        except BaseException as exc:  # surface thread failures in the test
            errors.append(exc)

    monkeypatch.setattr(race_app.state.users, "add_password", paused_add)
    thread = threading.Thread(target=request_add)
    thread.start()
    assert add_reached_cas.wait(5)

    reset = reset_client.post(
        "/account/history/delete",
        data={"nonce": nonce, "confirmation": "START OVER"},
        follow_redirects=False,
    )
    assert reset.status_code == 303
    release_add.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert errors == []
    added = result["response"]
    assert added.status_code == 303
    assert added.headers["location"] == "/login"
    current = race_app.state.users.get(user.id)
    assert current is not None
    assert current.history_epoch == user.history_epoch + 1
    assert current.auth_epoch == user.auth_epoch
    assert not current.has_password
    assert race_app.state.users.authenticate(
        email, "racing-password"
    ) is None


def test_add_password_cannot_overwrite_concurrent_ownership_recovery(app):
    users = app.state.users
    stub = users.upsert_store_customer(
        "password-recovery-race@example.com", "8001"
    )
    assert not stub.has_password
    users.set_password(stub.id, "recovered-password")

    with pytest.raises(PasswordAddConflict):
        users.add_password(
            stub.id,
            "stale-session-password",
            expected_auth_epoch=stub.auth_epoch,
            expected_history_epoch=stub.history_epoch,
        )

    assert users.authenticate(stub.email, "recovered-password") is not None
    assert users.authenticate(stub.email, "stale-session-password") is None
