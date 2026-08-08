"""Native Today is an owned, server-time coaching snapshot."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from swinglab.api.contracts import MobileTodayResponse
from swinglab.config import Config
from swinglab.web.app import create_app
from swinglab.web.jobs import DONE


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
        "today@example.com", "longenough", email_verified=True
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


def _set_created_at(manager, job_id: str, value: float):
    with manager._lock:
        manager._conn.execute(
            "UPDATE jobs SET created_at = ? WHERE id = ?", (value, job_id)
        )
        manager._conn.commit()


def test_mobile_today_is_empty_before_first_coaching_analysis(tmp_path):
    """Catches account creation or device time activating the coaching cohort."""

    app, client, _user = _app_client(tmp_path)
    try:
        legacy_before = client.get("/api/v1/today")
        response = client.get("/api/v1/mobile/today")
        legacy_after = client.get("/api/v1/today")

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "resource_version": 1,
            "profile": None,
            "latest_session": None,
            "caddie_brief": None,
            "practice_plan": [],
            "practice_checked_in": False,
            "cohort_day_since_first_analysis": None,
        }
        MobileTodayResponse.model_validate(response.json())
        assert legacy_before.status_code == legacy_after.status_code == 200
        assert legacy_before.content == legacy_after.content
    finally:
        _close(app)


def test_mobile_today_cohort_uses_earliest_current_coaching_job_and_server_utc(
    tmp_path,
):
    """Catches queued/capture jobs or client-clock skew deciding week two."""

    app, client, user = _app_client(tmp_path)
    try:
        capture = app.state.jobs.create_session(
            hand="right",
            angle="face-on",
            club="wedge",
            user_id=user.id,
            expected_history_epoch=user.history_epoch,
        )
        capture.status = DONE
        capture.report_rel = "out/report.html"
        app.state.jobs._save(capture)
        baseline = app.state.jobs.create_session(
            hand="left",
            angle="dtl",
            club="iron",
            user_id=user.id,
            expected_history_epoch=user.history_epoch,
        )
        baseline.status = DONE
        baseline.report_rel = "out/report.html"
        app.state.jobs._save(baseline)
        latest = app.state.jobs.create_session(
            source_name="C:\\private\\provider-secret.mov",
            hand="right",
            angle="face-on",
            club="driver",
            user_id=user.id,
            expected_history_epoch=user.history_epoch,
        )
        latest.log = ["Traceback ffmpeg /srv/private"]
        latest.error = "provider stderr"
        app.state.jobs._save(latest)
        first = 1_700_000_000.0
        _set_created_at(app.state.jobs, capture.id, first - 86400)
        _set_created_at(app.state.jobs, baseline.id, first)
        _set_created_at(app.state.jobs, latest.id, first + 10)
        app.state.jobs.coaching_eligible = lambda job: job.id == baseline.id

        for elapsed, expected_day in (
            (-3600, 0),
            (0, 0),
            (7 * 86400 + 86399, 7),
            (8 * 86400, 8),
            (14 * 86400 + 1, 14),
            (15 * 86400, 15),
        ):
            app.state.mobile_resource_service._clock = (
                lambda value=first + elapsed: value
            )
            response = client.get("/api/v1/mobile/today")
            assert response.status_code == 200
            assert response.json()["cohort_day_since_first_analysis"] == expected_day
            assert response.json()["latest_session"]["id"] == latest.id
            assert response.json()["latest_session"]["status"] == "queued"
            assert response.json()["caddie_brief"] is None
            assert response.json()["practice_plan"] == []
            for secret in (
                "provider-secret",
                "Traceback",
                "ffmpeg",
                "stderr",
                "/srv/private",
            ):
                assert secret not in response.text
            MobileTodayResponse.model_validate(response.json())
    finally:
        _close(app)


def test_mobile_today_cohort_origin_is_not_truncated_by_the_session_list_limit(
    tmp_path,
):
    """Catches a long history silently moving or clearing the activation day."""

    app, client, user = _app_client(tmp_path)
    try:
        baseline = app.state.jobs.create_session(
            hand="left",
            angle="dtl",
            club="iron",
            user_id=user.id,
            expected_history_epoch=user.history_epoch,
        )
        baseline.status = DONE
        baseline.report_rel = "out/report.html"
        app.state.jobs._save(baseline)
        for _ in range(55):
            app.state.jobs.create_session(
                hand="right",
                angle="face-on",
                club="driver",
                user_id=user.id,
                expected_history_epoch=user.history_epoch,
            )
        app.state.jobs.coaching_eligible = lambda job: job.id == baseline.id
        app.state.mobile_resource_service._clock = (
            lambda: baseline.created_at + 9 * 86400
        )

        response = client.get("/api/v1/mobile/today")

        assert response.status_code == 200
        assert response.json()["cohort_day_since_first_analysis"] == 9
    finally:
        _close(app)


@pytest.mark.parametrize("owner_change", ("history_reset", "account_deleted"))
def test_mobile_today_rechecks_owner_after_authentication(
    tmp_path, monkeypatch, owner_change
):
    """Catches reset or deletion racing a server-owned Today snapshot."""

    app, client, _user = _app_client(tmp_path)
    original_get = app.state.users.get
    calls = 0

    def racing_get(user_id):
        nonlocal calls
        calls += 1
        if calls != 2:
            return original_get(user_id)
        if owner_change == "account_deleted":
            return None
        with app.state.users._lock:
            app.state.users._conn.execute(
                "UPDATE users SET history_epoch = history_epoch + 1 WHERE id = ?",
                (user_id,),
            )
            app.state.users._conn.commit()
        return original_get(user_id)

    monkeypatch.setattr(app.state.users, "get", racing_get)
    try:
        response = client.get("/api/v1/mobile/today")

        assert response.status_code == 404
        assert response.json()["code"] == "not_found"
        assert response.headers["cache-control"] == "no-store"
    finally:
        _close(app)
