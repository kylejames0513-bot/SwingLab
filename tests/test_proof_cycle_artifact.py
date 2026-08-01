"""Proof Cycle PR 2: durable sidecars and conservative result copy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from swinglab.config import Config
from swinglab.proof_cycle_artifact import (
    ARTIFACT_FILENAME,
    active_proof_cycle_target_for_context,
    artifact_as_dict,
    build_proof_cycle_artifact,
    load_proof_cycle_artifact,
    proof_cycle_artifact_path,
    proof_cycle_enabled,
    proof_cycle_view,
    proof_session_from_job,
    proof_target_from_job,
    verified_proof_cycle_artifact,
    write_proof_cycle_artifact,
)


@dataclass
class FakeJob:
    id: str
    session_dir: Path
    created_at: float
    hand: str = "right"
    angle: str = "face-on"
    club: str | None = "Driver"
    user_id: str | None = "golfer-1"
    status: str = "done"
    report_rel: str | None = "out/source/report.html"


def cfg() -> Config:
    configured = Config()
    configured.proof_cycle["enabled"] = True
    return configured


def test_bare_config_keeps_proof_cycle_off_until_an_operator_enables_it():
    assert proof_cycle_enabled(Config()) is False


def row(
    *,
    tempo_ratio: float = 3.0,
    head_sway: float = 0.10,
    hip_slide: float = 0.10,
) -> dict:
    return {
        "tempo_ratio": tempo_ratio,
        "head_sway_backswing_sw": head_sway,
        "hip_slide_backswing_sw": hip_slide,
        "head_dip_sw": 0.10,
        "lead_arm_angle_deg": 175.0,
        "shoulder_tilt_impact_deg": 20.0,
        "shoulder_tilt_delta_deg": 10.0,
        "finish_balance_sw": 0.10,
    }


def make_job(
    tmp_path: Path,
    job_id: str,
    created_at: float,
    rows: list[dict],
    **overrides,
) -> FakeJob:
    job = FakeJob(id=job_id, session_dir=tmp_path / job_id, created_at=created_at)
    for key, value in overrides.items():
        setattr(job, key, value)
    deliverables = job.session_dir / "out" / "source"
    deliverables.mkdir(parents=True)
    (deliverables / "report.html").write_text("<html>report</html>")
    payload = {
        "meta": {"club": "Payload club", "hand": "left", "angle": "dtl"},
        "swings": [{"metrics": item} for item in rows],
        "session_stats": {},
    }
    (deliverables / "metrics.json").write_text(json.dumps(payload))
    return job


def build_and_write(job: FakeJob, prior: list[FakeJob], configured: Config):
    artifact = build_proof_cycle_artifact(job, prior, configured)
    write_proof_cycle_artifact(job, artifact)
    return artifact


def refresh_target_fingerprint(data: dict) -> None:
    data["target_fingerprint"] = hashlib.sha256(
        json.dumps(
            data["target"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def test_adapter_uses_job_context_not_metrics_metadata(tmp_path):
    job = make_job(
        tmp_path,
        "baseline",
        1.0,
        [row(head_sway=0.50) for _ in range(3)],
        club="7 iron",
        hand="right",
        angle="face-on",
    )

    session = proof_session_from_job(job, cfg())
    target = proof_target_from_job(job, cfg())

    assert session is not None
    assert session.context.club == "7 iron"
    assert session.context.hand == "right"
    assert session.context.angle == "face-on"
    assert target is not None
    assert target.baseline_context.club == "7 iron"
    assert target.metric == "head_sway_backswing_sw"


def test_dtl_adapter_scopes_stale_face_on_metrics(tmp_path):
    job = make_job(
        tmp_path,
        "dtl",
        1.0,
        [row(tempo_ratio=2.0, head_sway=0.80, hip_slide=0.80) for _ in range(3)],
        angle="dtl",
    )

    target = proof_target_from_job(job, cfg())

    assert target is not None
    assert target.metric == "tempo_ratio"
    assert target.baseline_context.angle == "dtl"


def test_unavailable_inputs_never_start_a_cycle(tmp_path):
    configured = cfg()
    safe_rows = [row(head_sway=0.50) for _ in range(3)]
    for name, overrides in (
        ("queued", {"status": "queued"}),
        ("ownerless", {"user_id": None}),
        ("clubless", {"club": None}),
        ("bad-hand", {"hand": "ambidextrous"}),
        ("bad-angle", {"angle": "overhead"}),
    ):
        job = make_job(tmp_path, name, 1.0, safe_rows, **overrides)
        artifact = build_proof_cycle_artifact(job, [], configured)
        assert artifact.stage == "unavailable"
        assert artifact.target is None


def test_first_selected_issue_sets_baseline_without_verdict(tmp_path):
    job = make_job(
        tmp_path, "baseline", 1.0, [row(head_sway=0.50) for _ in range(3)]
    )

    artifact = build_and_write(job, [], cfg())

    assert artifact.stage == "baseline"
    assert artifact.comparison is None
    assert artifact.target is not None
    assert artifact.target.baseline_context.session_id == "baseline"
    assert proof_cycle_view(artifact).heading == "Proof target set"
    assert proof_cycle_artifact_path(job).name == ARTIFACT_FILENAME


def test_refilm_carries_original_target_and_confirms_only_after_two(tmp_path):
    configured = cfg()
    baseline = make_job(
        tmp_path, "baseline", 1.0, [row(head_sway=0.50) for _ in range(3)]
    )
    baseline_artifact = build_and_write(baseline, [], configured)

    # Hip slide becomes the new report priority, but the Proof Cycle must keep
    # measuring the original head-sway target instead of silently resetting.
    first = make_job(
        tmp_path,
        "refilm-one",
        2.0,
        [row(head_sway=0.40, hip_slide=0.80) for _ in range(3)],
    )
    first_artifact = build_and_write(first, [baseline], configured)

    second = make_job(
        tmp_path,
        "refilm-two",
        3.0,
        [row(head_sway=0.39, hip_slide=0.80) for _ in range(3)],
    )
    second_artifact = build_and_write(second, [first, baseline], configured)

    assert baseline_artifact.target is not None
    assert first_artifact.target is not None
    assert second_artifact.target is not None
    assert first_artifact.target.metric == "head_sway_backswing_sw"
    assert second_artifact.target.metric == "head_sway_backswing_sw"
    assert (
        first_artifact.target.baseline_context.session_id
        == second_artifact.target.baseline_context.session_id
        == "baseline"
    )
    assert first_artifact.comparison.verdict == "early_signal"
    assert second_artifact.comparison.verdict == "improved"
    assert proof_cycle_view(first_artifact).heading == "Early signal — keep testing"
    assert proof_cycle_view(second_artifact).heading == "Matched improvement confirmed"


def test_history_excludes_mismatched_context_and_future_sessions(tmp_path):
    configured = cfg()
    baseline = make_job(
        tmp_path, "baseline", 1.0, [row(head_sway=0.50) for _ in range(3)]
    )
    build_and_write(baseline, [], configured)
    other_club = make_job(
        tmp_path,
        "other-club",
        2.0,
        [row(head_sway=0.20) for _ in range(3)],
        club="7 iron",
    )
    build_and_write(other_club, [], configured)
    other_hand = make_job(
        tmp_path,
        "other-hand",
        2.5,
        [row(head_sway=0.20) for _ in range(3)],
        hand="left",
    )
    build_and_write(other_hand, [], configured)
    future = make_job(
        tmp_path,
        "future",
        5.0,
        [row(head_sway=0.20) for _ in range(3)],
    )
    build_and_write(future, [baseline], configured)
    current = make_job(
        tmp_path,
        "current",
        3.0,
        [row(head_sway=0.40) for _ in range(3)],
    )

    artifact = build_proof_cycle_artifact(
        current, [future, other_hand, other_club, baseline, current], configured
    )

    assert artifact.comparison is not None
    assert artifact.comparison.verdict == "early_signal"
    assert artifact.comparison.accepted_refilm_count == 1


def test_prospective_transfer_target_uses_only_verified_exact_context(tmp_path):
    configured = cfg()
    baseline = make_job(
        tmp_path, "baseline", 1.0, [row(head_sway=0.50) for _ in range(3)]
    )
    build_and_write(baseline, [], configured)

    target = active_proof_cycle_target_for_context(
        [baseline],
        configured,
        user_id="golfer-1",
        club="Driver",
        hand="right",
        angle="face-on",
        before=2.0,
    )
    wrong_angle = active_proof_cycle_target_for_context(
        [baseline],
        configured,
        user_id="golfer-1",
        club="Driver",
        hand="right",
        angle="dtl",
        before=2.0,
    )
    proof_cycle_artifact_path(baseline).write_text("not json")
    corrupt = active_proof_cycle_target_for_context(
        [baseline],
        configured,
        user_id="golfer-1",
        club="Driver",
        hand="right",
        angle="face-on",
        before=2.0,
    )

    assert target is not None
    assert target.baseline_context.session_id == "baseline"
    assert wrong_angle is None
    assert corrupt is None


def test_sidecar_source_provenance_rejects_a_valid_tampered_baseline(tmp_path):
    configured = cfg()
    baseline = make_job(
        tmp_path, "baseline", 1.0, [row(head_sway=0.50) for _ in range(3)]
    )
    build_and_write(baseline, [], configured)
    refilm = make_job(
        tmp_path, "refilm", 2.0, [row(head_sway=0.40) for _ in range(3)]
    )
    build_and_write(refilm, [baseline], configured)

    path = proof_cycle_artifact_path(baseline)
    data = json.loads(path.read_text())
    # These fields remain schema-valid and retain the source metrics digest,
    # but they are not the issue card actually derived from baseline metrics.
    data["target"]["drill_ids"] = ["edited-drill"]
    data["target"]["drill_names"] = ["Edited drill"]
    refresh_target_fingerprint(data)
    path.write_text(json.dumps(data))

    target = active_proof_cycle_target_for_context(
        [baseline],
        configured,
        user_id="golfer-1",
        club="Driver",
        hand="right",
        angle="face-on",
        before=3.0,
    )
    later = make_job(
        tmp_path, "later", 3.0, [row(head_sway=0.30) for _ in range(3)]
    )

    assert load_proof_cycle_artifact(baseline) is not None
    assert target is None
    assert build_proof_cycle_artifact(
        later, [baseline, refilm], configured
    ).reason == "existing_cycle_unreadable"
    assert verified_proof_cycle_artifact(refilm, [baseline], configured) is None


def test_sidecar_source_provenance_rejects_a_valid_tampered_refilm(tmp_path):
    configured = cfg()
    baseline = make_job(
        tmp_path, "baseline", 1.0, [row(head_sway=0.50) for _ in range(3)]
    )
    build_and_write(baseline, [], configured)
    refilm = make_job(
        tmp_path, "refilm", 2.0, [row(head_sway=0.40) for _ in range(3)]
    )
    build_and_write(refilm, [baseline], configured)

    path = proof_cycle_artifact_path(refilm)
    data = json.loads(path.read_text())
    # Keep every provenance field valid while changing only the compact
    # historical measurement a later comparison would otherwise consume.
    data["refilm"]["measurement"]["value"] = 0.10
    data["refilm"]["measurement"]["mean"] = 0.10
    path.write_text(json.dumps(data))

    later = make_job(
        tmp_path, "later", 3.0, [row(head_sway=0.30) for _ in range(3)]
    )
    target = active_proof_cycle_target_for_context(
        [baseline, refilm],
        configured,
        user_id="golfer-1",
        club="Driver",
        hand="right",
        angle="face-on",
        before=3.0,
    )

    assert load_proof_cycle_artifact(refilm) is not None
    assert target is None
    later_artifact = build_proof_cycle_artifact(
        later, [baseline, refilm], configured
    )
    assert later_artifact.comparison is not None
    assert later_artifact.comparison.verdict == "inconclusive"


def test_active_target_resolves_an_out_of_window_baseline_safely(tmp_path):
    configured = cfg()
    baseline = make_job(
        tmp_path, "baseline", 1.0, [row(head_sway=0.50) for _ in range(3)]
    )
    build_and_write(baseline, [], configured)
    refilm = make_job(
        tmp_path, "refilm", 2.0, [row(head_sway=0.40) for _ in range(3)]
    )
    build_and_write(refilm, [baseline], configured)

    target = active_proof_cycle_target_for_context(
        [refilm],
        configured,
        user_id="golfer-1",
        club="Driver",
        hand="right",
        angle="face-on",
        before=3.0,
        baseline_job_for_id=lambda job_id: baseline if job_id == "baseline" else None,
    )

    assert target is not None
    assert target.baseline_context.session_id == "baseline"


def test_out_of_order_worker_completion_keeps_the_newer_active_baseline(tmp_path):
    configured = cfg()
    # In production two workers can finish uploads out of capture order.  The
    # older upload can become a standalone baseline after the newer upload has
    # already completed; the newer matching target remains the active cycle.
    earlier = make_job(
        tmp_path, "earlier", 1.0, [row(head_sway=0.50) for _ in range(3)]
    )
    newer = make_job(
        tmp_path, "newer", 2.0, [row(head_sway=0.50) for _ in range(3)]
    )
    build_and_write(newer, [], configured)
    build_and_write(earlier, [], configured)
    current = make_job(
        tmp_path, "current", 3.0, [row(head_sway=0.40) for _ in range(3)]
    )

    artifact = build_proof_cycle_artifact(current, [newer, earlier], configured)

    assert artifact.target is not None
    assert artifact.target.baseline_context.session_id == "newer"
    assert artifact.comparison is not None
    assert artifact.comparison.verdict == "early_signal"


def test_corrupt_or_stale_prior_sidecar_fails_closed(tmp_path):
    configured = cfg()
    baseline = make_job(
        tmp_path, "baseline", 1.0, [row(head_sway=0.50) for _ in range(3)]
    )
    build_and_write(baseline, [], configured)
    proof_cycle_artifact_path(baseline).write_text("not json")
    current = make_job(
        tmp_path, "current", 2.0, [row(head_sway=0.30) for _ in range(3)]
    )

    artifact = build_proof_cycle_artifact(current, [baseline], configured)

    assert artifact.stage == "unavailable"
    assert artifact.reason == "existing_cycle_unreadable"
    assert proof_cycle_view(artifact) is None


def test_loader_rejects_a_refilm_snapshot_copied_from_another_session(tmp_path):
    configured = cfg()
    baseline = make_job(
        tmp_path, "baseline", 1.0, [row(head_sway=0.50) for _ in range(3)]
    )
    build_and_write(baseline, [], configured)
    refilm = make_job(
        tmp_path, "refilm", 2.0, [row(head_sway=0.40) for _ in range(3)]
    )
    build_and_write(refilm, [baseline], configured)
    path = proof_cycle_artifact_path(refilm)
    data = json.loads(path.read_text())
    data["refilm"]["context"]["session_id"] = "other-session"
    path.write_text(json.dumps(data))

    assert load_proof_cycle_artifact(refilm) is None


def test_result_surface_rebuild_rejects_a_structurally_valid_forged_verdict(
    tmp_path,
):
    configured = cfg()
    baseline = make_job(
        tmp_path, "baseline", 1.0, [row(head_sway=0.50) for _ in range(3)]
    )
    build_and_write(baseline, [], configured)
    refilm = make_job(
        tmp_path, "refilm", 2.0, [row(head_sway=0.40) for _ in range(3)]
    )
    build_and_write(refilm, [baseline], configured)
    path = proof_cycle_artifact_path(refilm)
    data = json.loads(path.read_text())
    # This remains schema-valid and passes simple source/target checks.  The
    # web surface must still recompute the evidence instead of trusting it.
    data["comparison"]["verdict"] = "improved"
    data["comparison"]["accepted_refilm_count"] = 2
    path.write_text(json.dumps(data))

    assert load_proof_cycle_artifact(refilm) is not None
    assert verified_proof_cycle_artifact(refilm, [baseline], configured) is None


def test_result_copy_uses_the_persisted_confirmation_policy(tmp_path):
    configured = cfg()
    configured.proof_cycle["minimum_refilms_for_improved"] = 3
    baseline = make_job(
        tmp_path, "baseline", 1.0, [row(head_sway=0.50) for _ in range(3)]
    )
    build_and_write(baseline, [], configured)
    first = make_job(
        tmp_path, "first", 2.0, [row(head_sway=0.40) for _ in range(3)]
    )
    first_artifact = build_and_write(first, [baseline], configured)
    second = make_job(
        tmp_path, "second", 3.0, [row(head_sway=0.39) for _ in range(3)]
    )
    second_artifact = build_and_write(second, [first, baseline], configured)
    third = make_job(
        tmp_path, "third", 4.0, [row(head_sway=0.38) for _ in range(3)]
    )
    third_artifact = build_and_write(third, [second, first, baseline], configured)

    assert proof_cycle_view(first_artifact).next_step.startswith("Make 2 more")
    assert proof_cycle_view(second_artifact).next_step.startswith("Make one more")
    assert proof_cycle_view(third_artifact).summary.startswith("3 matched")


def test_sidecar_is_strict_private_and_invalidates_when_metrics_change(tmp_path):
    job = make_job(
        tmp_path, "baseline", 1.0, [row(head_sway=0.50) for _ in range(3)]
    )
    artifact = build_and_write(job, [], cfg())
    path = proof_cycle_artifact_path(job)
    raw = path.read_text()

    assert json.loads(raw)["format"] == "caddieinsight-proof-cycle"
    assert "golfer-1" not in raw
    assert "source.mov" not in raw
    assert '"swings":' not in raw
    assert not list(path.parent.glob("*.tmp"))
    assert load_proof_cycle_artifact(job) == artifact

    metrics = path.with_name("metrics.json")
    metrics.write_text(json.dumps({"swings": []}))
    assert load_proof_cycle_artifact(job) is None


def test_repeated_build_does_not_count_the_current_job_twice(tmp_path):
    configured = cfg()
    baseline = make_job(
        tmp_path, "baseline", 1.0, [row(head_sway=0.50) for _ in range(3)]
    )
    build_and_write(baseline, [], configured)
    current = make_job(
        tmp_path, "current", 2.0, [row(head_sway=0.40) for _ in range(3)]
    )

    first = build_proof_cycle_artifact(current, [baseline, current], configured)
    second = build_proof_cycle_artifact(current, [current, baseline], configured)

    assert artifact_as_dict(first) == artifact_as_dict(second)
    assert first.comparison is not None
    assert first.comparison.accepted_refilm_count == 1
