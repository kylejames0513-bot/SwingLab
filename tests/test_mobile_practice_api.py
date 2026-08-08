"""Guarded native practice evidence writes for Gate 4C."""

from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from swinglab.api.contracts import PracticeEvidenceRequest
from swinglab.config import Config
from swinglab.proof_cycle import ProofMeasurement, ProofTarget, SessionContext
from swinglab.proof_cycle_artifact import (
    ProofCycleArtifact,
    proof_cycle_target_fingerprint,
)
from swinglab.web.app import create_app
from swinglab.web.jobs import DONE
from swinglab.web.mobile_schema import VersionedHMAC
from tests.test_mobile_api_tokens import bearer, issue_token

IDEMPOTENCY_KEY = "0123456789abcdef0123456789abcdef"


def _keyring() -> VersionedHMAC:
    current = base64.b64encode(b"c" * 32).decode("ascii")
    previous = base64.b64encode(b"p" * 32).decode("ascii")
    return VersionedHMAC.from_json(
        json.dumps(
            {
                "version": 1,
                "current_key_id": "current",
                "keys": {"previous": previous, "current": current},
            }
        )
    )


def _app_client(tmp_path, *, practice_writes_enabled: bool = True):
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["mobile_resources_enabled"] = True
    cfg.web["mobile_practice_writes_enabled"] = practice_writes_enabled
    cfg.proof_cycle["enabled"] = True
    app = create_app(
        cfg,
        sessions_dir=tmp_path / "sessions",
        start_background_workers=False,
        mobile_state_hmac=_keyring(),
    )
    client = TestClient(app)
    user = app.state.users.create(
        "practice-writer@example.com",
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


def _seed_active_target(app, user):
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
    return baseline, target


def _practice_body(user, baseline, target, **overrides) -> dict:
    body = {
        "baseline_session_id": baseline.id,
        "target_fingerprint": proof_cycle_target_fingerprint(target),
        "drill_id": "tempo-three-beat-count",
        "minutes": 20,
        "outcome": "completed",
        "reps": 25,
        "feel": "easier",
        "relative_strike": "better",
        "start_line": "target",
        "miss_pattern": "none",
        "expected_history_epoch": user.history_epoch,
    }
    body.update(overrides)
    return body


def _idempotency_headers(token: str, key: str = IDEMPOTENCY_KEY) -> dict:
    return {**bearer(token), "Idempotency-Key": key}


def _evidence_count(app) -> int:
    return int(
        app.state.users._conn.execute(
            "SELECT COUNT(*) FROM proof_cycle_practice_evidence"
        ).fetchone()[0]
    )


def _details_count(app) -> int:
    return int(
        app.state.users._conn.execute(
            "SELECT COUNT(*) FROM mobile_practice_evidence_details"
        ).fetchone()[0]
    )


def test_practice_evidence_request_contract_is_closed():
    valid = PracticeEvidenceRequest.model_validate(
        {
            "baseline_session_id": "baseline-1",
            "target_fingerprint": "a" * 64,
            "drill_id": "tempo-three-beat-count",
            "minutes": 20,
            "outcome": "completed",
            "reps": 25,
            "feel": None,
            "relative_strike": None,
            "start_line": None,
            "miss_pattern": None,
            "expected_history_epoch": 0,
        }
    )
    assert valid.minutes == 20
    assert valid.feel is None

    with pytest.raises(ValidationError):
        PracticeEvidenceRequest.model_validate(
            {**valid.model_dump(), "unexpected": True}
        )
    with pytest.raises(ValidationError):
        PracticeEvidenceRequest.model_validate(
            {**valid.model_dump(), "minutes": 15}
        )
    with pytest.raises(ValidationError):
        PracticeEvidenceRequest.model_validate(
            {**valid.model_dump(), "reps": 0}
        )
    with pytest.raises(ValidationError):
        PracticeEvidenceRequest.model_validate(
            {**valid.model_dump(), "reps": 301}
        )
    with pytest.raises(ValidationError):
        PracticeEvidenceRequest.model_validate(
            {**valid.model_dump(), "outcome": "done"}
        )
    with pytest.raises(ValidationError):
        PracticeEvidenceRequest.model_validate(
            {**valid.model_dump(), "expected_history_epoch": -1}
        )


def test_practice_evidence_flag_off_is_404_before_auth_body_or_writes(tmp_path):
    app, client, user = _app_client(tmp_path, practice_writes_enabled=False)
    try:
        baseline, target = _seed_active_target(app, user)
        before_evidence = _evidence_count(app)
        before_details = _details_count(app)
        token = issue_token(client, "Practice phone")["token"]
        body = _practice_body(user, baseline, target)

        cases = [
            client.post("/api/v1/practice-evidence"),
            client.post(
                "/api/v1/practice-evidence",
                content=b"not-json",
                headers={"Content-Type": "application/json"},
            ),
            client.post("/api/v1/practice-evidence", json={"incomplete": True}),
            client.post(
                "/api/v1/practice-evidence",
                headers=bearer("ciat_not-a-real-token"),
                json=body,
            ),
            client.post(
                "/api/v1/practice-evidence",
                headers=_idempotency_headers(token),
                json=body,
            ),
        ]
        for response in cases:
            assert response.status_code == 404
            assert response.headers["cache-control"] == "no-store"
            assert response.json()["code"] == "not_found"
        assert _evidence_count(app) == before_evidence
        assert _details_count(app) == before_details
    finally:
        _close(app)


def test_practice_evidence_is_strict_bearer_only(tmp_path):
    app, client, user = _app_client(tmp_path)
    try:
        baseline, target = _seed_active_target(app, user)
        body = _practice_body(user, baseline, target)
        before = _evidence_count(app)

        cookie_only = client.post(
            "/api/v1/practice-evidence",
            headers={"Idempotency-Key": IDEMPOTENCY_KEY},
            json=body,
        )
        assert cookie_only.status_code == 401
        assert cookie_only.json()["code"] == "bearer_required"

        invalid = client.post(
            "/api/v1/practice-evidence",
            headers=_idempotency_headers("ciat_this-is-not-a-valid-token"),
            json=body,
        )
        assert invalid.status_code == 401
        assert invalid.json()["message"] == "Invalid mobile access token."
        assert _evidence_count(app) == before
    finally:
        _close(app)


def test_practice_evidence_rejects_invalid_idempotency_key(tmp_path):
    app, client, user = _app_client(tmp_path)
    try:
        baseline, target = _seed_active_target(app, user)
        token = issue_token(client, "Practice phone")["token"]
        body = _practice_body(user, baseline, target)

        missing = client.post(
            "/api/v1/practice-evidence",
            headers=bearer(token),
            json=body,
        )
        assert missing.status_code == 400
        assert missing.json()["code"] == "invalid_idempotency_key"

        bad = client.post(
            "/api/v1/practice-evidence",
            headers={**bearer(token), "Idempotency-Key": "not-hex"},
            json=body,
        )
        assert bad.status_code == 400
        assert bad.json()["code"] == "invalid_idempotency_key"
    finally:
        _close(app)


def test_practice_evidence_success_replay_and_conflict(tmp_path):
    app, client, user = _app_client(tmp_path)
    try:
        baseline, target = _seed_active_target(app, user)
        token = issue_token(client, "Practice phone")["token"]
        body = _practice_body(user, baseline, target)

        first = client.post(
            "/api/v1/practice-evidence",
            headers=_idempotency_headers(token),
            json=body,
        )
        assert first.status_code == 201
        assert first.headers["cache-control"] == "no-store"
        receipt = first.json()
        assert receipt["resource_version"] == 1
        assert receipt["baseline_session_id"] == baseline.id
        assert receipt["drill_id"] == "tempo-three-beat-count"
        assert receipt["minutes"] == 20
        assert receipt["outcome"] == "completed"
        assert receipt["reps"] == 25
        assert isinstance(receipt["receipt_id"], str) and receipt["receipt_id"]
        assert _evidence_count(app) == 1
        assert _details_count(app) == 1

        replay = client.post(
            "/api/v1/practice-evidence",
            headers=_idempotency_headers(token),
            json=body,
        )
        assert replay.status_code == 201
        assert replay.json() == receipt
        assert _evidence_count(app) == 1
        assert _details_count(app) == 1

        conflict = client.post(
            "/api/v1/practice-evidence",
            headers=_idempotency_headers(token),
            json=_practice_body(user, baseline, target, minutes=45),
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] in {
            "idempotency_conflict",
            "conflict",
        }
        assert _evidence_count(app) == 1
        assert _details_count(app) == 1
    finally:
        _close(app)


def test_practice_evidence_history_epoch_conflict_never_writes(tmp_path):
    app, client, user = _app_client(tmp_path)
    try:
        baseline, target = _seed_active_target(app, user)
        token = issue_token(client, "Practice phone")["token"]
        response = client.post(
            "/api/v1/practice-evidence",
            headers=_idempotency_headers(token),
            json=_practice_body(
                user,
                baseline,
                target,
                expected_history_epoch=user.history_epoch + 1,
            ),
        )
        assert response.status_code == 409
        assert response.json()["code"] == "history_epoch_conflict"
        assert _evidence_count(app) == 0
        assert _details_count(app) == 0
    finally:
        _close(app)


def test_practice_evidence_rejects_stale_or_cross_owner_target(tmp_path):
    app, client, user = _app_client(tmp_path)
    try:
        baseline, target = _seed_active_target(app, user)
        token = issue_token(client, "Practice phone")["token"]
        app.state.mobile_resource_service._active_target_provider = (
            lambda _owner, _boundary, _before: None
        )
        response = client.post(
            "/api/v1/practice-evidence",
            headers=_idempotency_headers(token),
            json=_practice_body(user, baseline, target),
        )
        assert response.status_code in {404, 409}
        assert response.headers["cache-control"] == "no-store"
        assert _evidence_count(app) == 0
        assert _details_count(app) == 0
    finally:
        _close(app)


def test_capabilities_expose_independent_practice_writes_flag(tmp_path):
    app, client, _user = _app_client(tmp_path, practice_writes_enabled=True)
    try:
        response = client.get("/api/v1/capabilities")
        assert response.status_code == 200
        assert response.json()["capabilities"]["features"]["practice_writes"] is True
    finally:
        _close(app)

    app_off, client_off, _ = _app_client(
        tmp_path / "flag-off", practice_writes_enabled=False
    )
    try:
        response = client_off.get("/api/v1/capabilities")
        assert response.status_code == 200
        assert (
            response.json()["capabilities"]["features"]["practice_writes"] is False
        )
    finally:
        _close(app_off)


def test_legacy_practice_checkin_remains_compatible_beside_native_route(tmp_path):
    app, client, _user = _app_client(tmp_path)
    try:
        incomplete = client.post("/api/v1/practice-checkins", json={})
        assert incomplete.status_code == 400
        assert incomplete.json() == {"detail": "A session id is required."}
    finally:
        _close(app)
