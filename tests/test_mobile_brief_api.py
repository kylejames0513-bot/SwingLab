"""Structured, bounded native Caddie Brief resources."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from swinglab.caddie_brief import CaddieBrief
from swinglab.config import Config
from swinglab.drills import Drill
from swinglab.proof_cycle_artifact import (
    ProofCycleArtifact,
    proof_cycle_target_fingerprint,
)
from swinglab.web.app import create_app
from swinglab.web.jobs import DONE
from tests.test_mobile_progress_api import _target


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
        "brief@example.com", "longenough", email_verified=True
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


def _coaching_brief() -> CaddieBrief:
    return CaddieBrief(
        strength="Stable head position",
        focus_flag="tempo",
        focus_name="Tempo",
        focus_value="2.00:1",
        benchmark_text="flagged below 2.4:1",
        why="The transition starts before the backswing settles.",
        fix="Let the backswing finish before starting down.",
        drill=Drill(
            id="tempo-three-beat-count",
            name="Three-beat count",
            aim="Give the backswing time to finish.",
            protocol=("one", "two", "three"),
            dosage="3 x 10 swings",
            success_metric="Re-film five matching swings above 2.4:1.",
            gear_tag="swinglab:tempo",
        ),
        trend="Tempo is holding across two sessions.",
        warning="Traceback /srv/private stderr provider-secret",
        recurring_sessions=2,
        remaining_issues=1,
        clean=False,
        refilm_required=False,
    )


def test_mobile_brief_returns_one_closed_coaching_decision_without_report_data(
    tmp_path,
):
    """Catches native Briefs becoming report/log/artifact passthroughs."""

    app, client, user = _app_client(tmp_path)
    try:
        job = app.state.jobs.create_session(
            source_name="C:\\private\\source.mov",
            hand="left",
            angle="dtl",
            club="iron",
            user_id=user.id,
            expected_history_epoch=user.history_epoch,
        )
        job.status = DONE
        job.report_rel = "out/report.html"
        job.log = ["ffmpeg command provider-secret"]
        job.error = "Traceback /srv/private"
        app.state.jobs._save(job)
        app.state.jobs.coaching_eligible = lambda candidate: candidate.id == job.id
        app.state.mobile_resource_service._brief_provider = lambda candidate: (
            _coaching_brief() if candidate.id == job.id else None
        )

        response = client.get(f"/api/v1/mobile/sessions/{job.id}/brief")

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "resource_version": 1,
            "status": "coaching_ready",
            "priority": {
                "key": "tempo",
                "name": "Tempo",
                "value": "2.00:1",
                "benchmark": "flagged below 2.4:1",
            },
            "evidence": {
                "strength": "Stable head position",
                "trend": "Tempo is holding across two sessions.",
                "recurring_sessions": 2,
                "remaining_issues": 1,
            },
            "confidence": "limited",
            "hypothesis": "The transition starts before the backswing settles.",
            "cue": "Let the backswing finish before starting down.",
            "prescribed_drill": {
                "id": "tempo-three-beat-count",
                "name": "Three-beat count",
                "aim": "Give the backswing time to finish.",
                "dosage": "3 x 10 swings",
                "pass_mark": "Re-film five matching swings above 2.4:1.",
            },
            "measurement_boundary": {
                "club": "iron",
                "hand": "left",
                "angle": "dtl",
            },
            "proof_cycle_target": None,
            "message": None,
        }
        encoded = response.text
        for secret in (
            "Traceback",
            "provider-secret",
            "stderr",
            "ffmpeg",
            "source.mov",
            "report.html",
            "/srv/private",
        ):
            assert secret not in encoded
    finally:
        _close(app)


def test_mobile_brief_has_safe_typed_pending_and_refilm_states(tmp_path):
    """Catches incomplete or capture-only jobs exposing partial coaching data."""

    app, client, user = _app_client(tmp_path)
    try:
        queued = app.state.jobs.create_session(
            hand="right",
            angle="face-on",
            club="driver",
            user_id=user.id,
            expected_history_epoch=user.history_epoch,
        )
        refilm = app.state.jobs.create_session(
            hand="right",
            angle="face-on",
            club="wedge",
            user_id=user.id,
            expected_history_epoch=user.history_epoch,
        )
        refilm.status = DONE
        refilm.report_rel = "out/report.html"
        refilm.log = ["partial coaching provider-secret"]
        app.state.jobs._save(refilm)
        app.state.jobs.coaching_eligible = lambda _candidate: False
        app.state.mobile_resource_service._brief_provider = (
            lambda _candidate: _coaching_brief()
        )

        pending = client.get(f"/api/v1/mobile/sessions/{queued.id}/brief")
        needs_refilm = client.get(f"/api/v1/mobile/sessions/{refilm.id}/brief")

        assert pending.status_code == needs_refilm.status_code == 200
        assert pending.json()["status"] == "brief_not_ready"
        assert pending.json()["message"] == "Analysis is still in progress."
        assert needs_refilm.json()["status"] == "refilm_required"
        assert (
            needs_refilm.json()["message"]
            == "Capture a clearer matching swing video."
        )
        for body in (pending.json(), needs_refilm.json()):
            assert body["priority"] is None
            assert body["evidence"] is None
            assert body["prescribed_drill"] is None
            assert body["proof_cycle_target"] is None
            assert "provider-secret" not in repr(body)
    finally:
        _close(app)


def test_mobile_brief_missing_and_cross_owner_are_indistinguishable(tmp_path):
    """Catches a session identifier becoming an account enumeration oracle."""

    app, client, _user = _app_client(tmp_path)
    try:
        other = app.state.users.create(
            "foreign-brief@example.com", "longenough", email_verified=True
        )
        foreign = app.state.jobs.create_session(
            hand="right",
            angle="face-on",
            club="driver",
            user_id=other.id,
            expected_history_epoch=other.history_epoch,
        )

        missing = client.get("/api/v1/mobile/sessions/missing/brief")
        cross_owner = client.get(
            f"/api/v1/mobile/sessions/{foreign.id}/brief"
        )

        assert missing.status_code == cross_owner.status_code == 404
        assert missing.content == cross_owner.content
        assert missing.headers["cache-control"] == "no-store"
        assert cross_owner.headers["cache-control"] == "no-store"
    finally:
        _close(app)


@pytest.mark.parametrize("owner_change", ("history_reset", "account_deleted"))
def test_mobile_brief_rechecks_owner_after_authentication(
    tmp_path, monkeypatch, owner_change
):
    """Catches reset or deletion racing a structured coaching read."""

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
            if calls != 2:
                return original_get(user_id)
            if owner_change == "account_deleted":
                return None
            with app.state.users._lock:
                app.state.users._conn.execute(
                    "UPDATE users SET history_epoch = history_epoch + 1"
                    " WHERE id = ?",
                    (user_id,),
                )
                app.state.users._conn.commit()
            return original_get(user_id)

        monkeypatch.setattr(app.state.users, "get", racing_get)
        response = client.get(f"/api/v1/mobile/sessions/{job.id}/brief")

        assert response.status_code == 404
        assert response.json()["code"] == "not_found"
        assert response.headers["cache-control"] == "no-store"
    finally:
        _close(app)


def test_mobile_brief_reuses_the_exact_owned_proof_cycle_launch_target(tmp_path):
    """Catches Brief capture metadata diverging from the Progress contract."""

    app, client, user = _app_client(tmp_path)
    try:
        baseline = app.state.jobs.create_session(
            hand="left",
            angle="dtl",
            club="iron",
            user_id=user.id,
            expected_history_epoch=user.history_epoch,
        )
        current = app.state.jobs.create_session(
            hand="left",
            angle="dtl",
            club="iron",
            user_id=user.id,
            expected_history_epoch=user.history_epoch,
        )
        for job in (baseline, current):
            job.status = DONE
            job.report_rel = "out/report.html"
            app.state.jobs._save(job)
        target = _target(user.id, baseline.id)
        artifact = ProofCycleArtifact(
            source_session_id=current.id,
            source_metrics_sha256="0" * 64,
            stage="baseline",
            target=target,
            refilm=None,
            comparison=None,
            policy=None,
        )
        app.state.jobs.coaching_eligible = lambda _candidate: True
        app.state.cfg.proof_cycle["enabled"] = True
        app.state.mobile_resource_service._brief_provider = (
            lambda _candidate: _coaching_brief()
        )
        app.state.mobile_resource_service._proof_artifact_provider = (
            lambda job: artifact if job.id == current.id else None
        )
        app.state.mobile_resource_service._active_target_provider = (
            lambda _owner, _boundary, _before: target
        )

        response = client.get(f"/api/v1/mobile/sessions/{current.id}/brief")

        assert response.status_code == 200
        assert response.json()["proof_cycle_target"] == {
            "baseline_session_id": baseline.id,
            "target_fingerprint": proof_cycle_target_fingerprint(target),
            "drill_id": "tempo-three-beat-count",
            "club": "iron",
            "hand": "left",
            "angle": "dtl",
        }

        replacement_target = _target(user.id, current.id)
        app.state.mobile_resource_service._active_target_provider = (
            lambda _owner, _boundary, _before: replacement_target
        )
        replaced = client.get(f"/api/v1/mobile/sessions/{current.id}/brief")
        assert replaced.status_code == 200
        assert replaced.json()["proof_cycle_target"] is None
    finally:
        _close(app)
