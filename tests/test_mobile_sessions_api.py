"""Owned, allowlisted native session resources."""

from __future__ import annotations

from fastapi.testclient import TestClient

from swinglab.api.contracts import MobileSessionResponse
from swinglab.config import Config
from swinglab.web.app import create_app
from swinglab.web.jobs import DONE, FAILED, PROCESSING


def _app_client(tmp_path):
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["mobile_resources_enabled"] = True
    app = create_app(
        cfg,
        sessions_dir=tmp_path / "sessions",
        start_background_workers=False,
    )
    client = TestClient(app)
    user = app.state.users.create(
        "sessions@example.com", "longenough", email_verified=True
    )
    login = client.post(
        "/login",
        data={"email": user.email, "password": "longenough"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    return app, client, user


def _close(app):
    for resource in (app.state.jobs, app.state.users, app.state.throttle):
        resource.close()


def test_mobile_session_list_and_detail_allowlist_owned_state_and_diagnostics(tmp_path):
    """Catches native session JSON inheriting legacy logs, paths, or tracebacks."""

    app, client, user = _app_client(tmp_path)
    try:
        queued = app.state.jobs.create_session(
            source_name="C:\\private\\swing.mov",
            hand="left",
            angle="dtl",
            club="iron",
            user_id=user.id,
            expected_history_epoch=user.history_epoch,
        )
        failed = app.state.jobs.create_session(
            source_name="secret.mov",
            hand="right",
            angle="face-on",
            club="driver",
            user_id=user.id,
            expected_history_epoch=user.history_epoch,
        )
        failed.status = FAILED
        failed.log = ["ffmpeg -i C:\\private\\secret.mov bearer-token"]
        failed.error = "Traceback: provider stderr /srv/private/report.html"
        failed.report_rel = "../../outside/report.html"
        failed.swings_done = 2
        failed.swings_total = 3
        app.state.jobs._save(failed)

        listing = client.get("/api/v1/mobile/sessions")
        detail = client.get(f"/api/v1/mobile/sessions/{failed.id}")

        assert listing.status_code == detail.status_code == 200
        assert listing.headers["cache-control"] == "no-store"
        assert detail.headers["cache-control"] == "no-store"
        body = listing.json()
        assert body["resource_version"] == 1
        assert {item["id"] for item in body["sessions"]} == {failed.id, queued.id}
        failed_item = next(item for item in body["sessions"] if item["id"] == failed.id)
        assert detail.json() == failed_item
        MobileSessionResponse.model_validate(detail.json())
        assert detail.json() == {
            "resource_version": 1,
            "id": failed.id,
            "status": "failed",
            "created_at": failed.as_dict()["created_at"],
            "source_name": None,
            "club": "driver",
            "hand": "right",
            "angle": "face-on",
            "level": None,
            "fast": False,
            "swings_done": 2,
            "swings_total": 3,
            "queue_position": None,
            "report_url": None,
            "metrics_url": None,
            "outcome": None,
            "failure_code": "analysis_internal_error",
            "retryable": False,
            "retry_expires_at": None,
            "remaining_retry_count": 0,
            "comparison": None,
        }
        encoded = listing.text + detail.text
        for secret in (
            "private",
            "secret.mov",
            "Traceback",
            "ffmpeg",
            "stderr",
            "report.html",
            "bearer-token",
        ):
            assert secret not in encoded

        legacy = client.get(f"/api/v1/sessions/{failed.id}")
        assert legacy.status_code == 200
        assert legacy.json()["log"] == failed.log
        assert legacy.json()["error"] == failed.error
        assert legacy.json()["report"] == failed.report_rel
    finally:
        _close(app)


def test_mobile_session_missing_and_cross_owner_are_same_404(tmp_path):
    """Catches owned identifiers becoming an account enumeration oracle."""

    app, client, user = _app_client(tmp_path)
    try:
        other = app.state.users.create(
            "other-sessions@example.com", "longenough", email_verified=True
        )
        foreign = app.state.jobs.create_session(
            hand="right",
            angle="face-on",
            club="wedge",
            user_id=other.id,
            expected_history_epoch=other.history_epoch,
        )

        missing = client.get("/api/v1/mobile/sessions/missing")
        cross_owner = client.get(f"/api/v1/mobile/sessions/{foreign.id}")

        assert missing.status_code == cross_owner.status_code == 404
        assert missing.json() == cross_owner.json() == {
            "resource_version": 1,
            "code": "not_found",
            "message": "Session not found.",
            "retryable": False,
            "reference_id": None,
        }
        assert missing.headers["cache-control"] == "no-store"
        assert cross_owner.headers["cache-control"] == "no-store"
    finally:
        _close(app)


def test_mobile_sessions_close_every_active_done_and_refilm_state(tmp_path):
    """Catches mobile progress/outcome fields drifting across job states."""

    app, client, user = _app_client(tmp_path)
    try:
        queued = app.state.jobs.create_session(
            hand="right",
            angle="face-on",
            club="driver",
            user_id=user.id,
            expected_history_epoch=user.history_epoch,
        )
        processing = app.state.jobs.create_session(
            hand="left",
            angle="dtl",
            club="iron",
            user_id=user.id,
            expected_history_epoch=user.history_epoch,
        )
        processing.status = PROCESSING
        processing.swings_done = 2
        processing.swings_total = 5
        app.state.jobs._save(processing)
        ready = app.state.jobs.create_session(
            hand="right",
            angle="face-on",
            club="wedge",
            user_id=user.id,
            expected_history_epoch=user.history_epoch,
        )
        refilm = app.state.jobs.create_session(
            hand="left",
            angle="dtl",
            club="hybrid",
            user_id=user.id,
            expected_history_epoch=user.history_epoch,
        )
        for job in (ready, refilm):
            job.status = DONE
            job.report_rel = "out/report.html"
            job.swings_done = job.swings_total = 3
            app.state.jobs._save(job)
        app.state.jobs.coaching_eligible = lambda job: job.id == ready.id

        response = client.get("/api/v1/mobile/sessions")

        assert response.status_code == 200
        sessions = {item["id"]: item for item in response.json()["sessions"]}
        assert sessions[queued.id]["status"] == "queued"
        assert sessions[queued.id]["queue_position"] is not None
        assert sessions[queued.id]["outcome"] is None
        assert sessions[processing.id]["status"] == "processing"
        assert sessions[processing.id]["swings_done"] == 2
        assert sessions[processing.id]["swings_total"] == 5
        assert sessions[processing.id]["queue_position"] is None
        assert sessions[ready.id]["outcome"] == "coaching_ready"
        assert sessions[refilm.id]["outcome"] == "refilm_required"
        for item in sessions.values():
            assert item["failure_code"] is None
            assert item["retryable"] is False
            assert item["retry_expires_at"] is None
            assert item["remaining_retry_count"] == 0
            assert item["report_url"] is None
            assert item["metrics_url"] is None
    finally:
        _close(app)


def test_mobile_session_read_never_falls_back_from_bad_authorization_to_cookie(
    tmp_path,
):
    """Catches an ambient browser session masking a malformed bearer header."""

    app, client, _user = _app_client(tmp_path)
    try:
        response = client.get(
            "/api/v1/mobile/sessions",
            headers={"Authorization": "Basic not-a-mobile-token"},
        )

        assert response.status_code == 401
        assert response.json()["message"] == "Invalid mobile access token."
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"
    finally:
        _close(app)


def test_mobile_session_rechecks_history_epoch_after_authentication(
    tmp_path, monkeypatch
):
    """Catches a reset race delivering a pre-reset owned session snapshot."""

    app, client, user = _app_client(tmp_path)
    try:
        job = app.state.jobs.create_session(
            hand="right",
            angle="face-on",
            club="driver",
            user_id=user.id,
            expected_history_epoch=user.history_epoch,
        )
        original_get = app.state.users.get
        calls = 0

        def racing_get(user_id):
            nonlocal calls
            calls += 1
            if calls == 2:
                with app.state.users._lock:
                    app.state.users._conn.execute(
                        "UPDATE users SET history_epoch = history_epoch + 1"
                        " WHERE id = ?",
                        (user_id,),
                    )
                    app.state.users._conn.commit()
            return original_get(user_id)

        monkeypatch.setattr(app.state.users, "get", racing_get)
        response = client.get(f"/api/v1/mobile/sessions/{job.id}")

        assert response.status_code == 404
        assert response.json()["message"] == "Session not found."
        assert response.headers["cache-control"] == "no-store"
    finally:
        _close(app)
