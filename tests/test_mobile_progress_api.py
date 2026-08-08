"""Owned comparable-context Progress and exact Proof Cycle launch metadata."""

from __future__ import annotations

from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.proof_cycle import ProofMeasurement, ProofTarget, SessionContext
from swinglab.proof_cycle_artifact import (
    ProofCycleArtifact,
    proof_cycle_target_fingerprint,
)
from swinglab.web.app import create_app
from swinglab.web.jobs import DONE


def _app_client(tmp_path):
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["mobile_resources_enabled"] = True
    cfg.proof_cycle["enabled"] = True
    app = create_app(
        cfg,
        sessions_dir=tmp_path / "sessions",
        start_background_workers=False,
    )
    client = TestClient(app)
    user = app.state.users.create(
        "progress@example.com", "longenough", email_verified=True
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


def _target(user_id: str, baseline_id: str) -> ProofTarget:
    return ProofTarget(
        source_flag="tempo",
        metric="tempo_ratio",
        display_name="Tempo",
        unit=":1",
        worse_direction="lower",
        aggregation="mean",
        benchmark_value=3.0,
        benchmark_text="target 3.0:1",
        drill_ids=("tempo-three-beat-count",),
        drill_names=("Three-beat count",),
        baseline_context=SessionContext(
            session_id=baseline_id,
            user_id=user_id,
            club="iron",
            hand="left",
            angle="dtl",
        ),
        baseline=ProofMeasurement(
            metric="tempo_ratio",
            aggregation="mean",
            value=2.0,
            mean=2.0,
            std=0.1,
            readable_swings=3,
        ),
        baseline_completed=True,
        baseline_coaching_eligible=True,
        baseline_warning=None,
        rule_version=2,
    )


def test_progress_groups_owned_context_and_returns_exact_verified_launch_target(
    tmp_path,
):
    """Catches mobile capture metadata being rebuilt from labels or another owner."""

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
        app.state.jobs.coaching_eligible = lambda job: job.id in {
            baseline.id,
            current.id,
        }
        app.state.mobile_resource_service._proof_artifact_provider = (
            lambda job: artifact if job.id == current.id else None
        )
        app.state.mobile_resource_service._active_target_provider = (
            lambda _owner, _boundary, _before: target
        )

        response = client.get("/api/v1/progress")

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["resource_version"] == 1
        assert len(response.json()["groups"]) == 1
        group = response.json()["groups"][0]
        assert (group["club"], group["hand"], group["angle"]) == (
            "iron",
            "left",
            "dtl",
        )
        assert {item["id"] for item in group["sessions"]} == {
            baseline.id,
            current.id,
        }
        assert group["outcome"] == "no_transfer_yet"
        assert group["decision"] == "continue"
        assert group["outcome_label"] == "No transfer yet"
        assert group["decision_label"] == "Continue"
        assert group["proof_cycle_target"] == {
            "baseline_session_id": baseline.id,
            "target_fingerprint": proof_cycle_target_fingerprint(target),
            "drill_id": "tempo-three-beat-count",
            "club": "iron",
            "hand": "left",
            "angle": "dtl",
        }

        replacement = app.state.jobs.create_session(
            hand="left",
            angle="dtl",
            club="iron",
            user_id=user.id,
            expected_history_epoch=user.history_epoch,
        )
        replacement.status = DONE
        replacement.report_rel = "out/report.html"
        app.state.jobs._save(replacement)
        replacement_target = _target(user.id, replacement.id)
        app.state.mobile_resource_service._active_target_provider = (
            lambda _owner, _boundary, _before: replacement_target
        )
        replaced = client.get("/api/v1/progress")
        assert replaced.status_code == 200
        assert replaced.json()["groups"][0]["proof_cycle_target"] is None

        app.state.mobile_resource_service._active_target_provider = (
            lambda _owner, _boundary, _before: None
        )
        corrupt_newer = client.get("/api/v1/progress")
        assert corrupt_newer.status_code == 200
        assert corrupt_newer.json()["groups"][0]["proof_cycle_target"] is None

        app.state.mobile_resource_service._active_target_provider = (
            lambda _owner, _boundary, _before: target
        )

        baseline.status = "queued"
        app.state.jobs._save(baseline)
        stale_status = client.get("/api/v1/progress")
        assert stale_status.status_code == 200
        assert stale_status.json()["groups"][0]["proof_cycle_target"] is None

        baseline.status = DONE
        app.state.jobs._save(baseline)

        app.state.jobs.discard(baseline)
        stale = client.get("/api/v1/progress")
        assert stale.status_code == 200
        assert stale.json()["groups"][0]["proof_cycle_target"] is None
    finally:
        _close(app)


def test_progress_never_includes_another_accounts_sessions(tmp_path):
    """Catches comparable grouping crossing the authenticated owner boundary."""

    app, client, user = _app_client(tmp_path)
    try:
        own = app.state.jobs.create_session(
            hand="right",
            angle="face-on",
            club="driver",
            user_id=user.id,
            expected_history_epoch=user.history_epoch,
        )
        other = app.state.users.create(
            "foreign-progress@example.com", "longenough", email_verified=True
        )
        foreign = app.state.jobs.create_session(
            hand="right",
            angle="face-on",
            club="driver",
            user_id=other.id,
            expected_history_epoch=other.history_epoch,
        )

        response = client.get("/api/v1/progress")

        assert response.status_code == 200
        encoded = response.text
        assert own.id in encoded
        assert foreign.id not in encoded
    finally:
        _close(app)
