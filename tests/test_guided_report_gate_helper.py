from __future__ import annotations

import importlib
import json
from pathlib import Path
import re

import pytest

from swinglab.report_view import (
    EvidenceKind,
    MediaRole,
    PhaseId,
    PhaseMethod,
)


def _gate_helper():
    helper_path = Path(__file__).with_name("guided_report_gate_helper.py")
    assert helper_path.is_file(), "guided report gate helper is not implemented"
    return importlib.import_module("tests.guided_report_gate_helper")


def test_face_on_gate_uses_the_real_pipeline_and_validates_one_focused_artifact(
    tmp_path: Path,
):
    gate = _gate_helper()

    result = gate.run_guided_synthetic(tmp_path / "face-on", angle="face-on")
    bundle = gate.assert_bundle(result)

    priority = [
        media for media in bundle.view.media
        if media.role is MediaRole.PRIORITY_EVIDENCE
    ]
    assert len(priority) == 1
    assert bundle.view.visual_evidence.media_key == priority[0].key
    declared = {row.relative_path for row in bundle.manifest.artifacts}
    assert not any(
        token in relative.lower()
        for relative in declared
        for token in ("work/", "raw", "overlay", "strip", "replay")
    )
    assert not (result.session_dir / "work").exists()


def test_dtl_gate_keeps_timing_provenance_and_excludes_body_semantics(
    tmp_path: Path,
):
    gate = _gate_helper()

    result = gate.run_guided_synthetic(tmp_path / "dtl", angle="dtl")
    bundle = gate.assert_bundle(result)

    priority = [
        media for media in bundle.view.media
        if media.role is MediaRole.PRIORITY_EVIDENCE
    ]
    assert len(priority) == 1
    assert bundle.view.visual_evidence.kind is EvidenceKind.TEMPO_TIMELINE
    assert bundle.view.visual_evidence.media_key == priority[0].key
    assert bundle.view.next_move.category is PhaseId.TIMING_RHYTHM
    assert [phase.id for phase in bundle.view.phases] == [PhaseId.TIMING_RHYTHM]
    assert [event.method for event in bundle.view.visual_evidence.events] == [
        PhaseMethod.OPENING_BASELINE,
        PhaseMethod.HIGHEST_TRACKED_HANDS,
        PhaseMethod.MANUAL_STRIKE,
        PhaseMethod.CONFIGURED_FINISH_OFFSET,
    ]

    payload = json.loads(result.metrics_path.read_text(encoding="utf-8"))
    measured = payload["swings"][0]["metrics"]
    assert all(
        measured[key] is not None
        for key in ("backswing_s", "downswing_s", "tempo_ratio")
    )
    body_metrics = {
        "head_sway_backswing_sw",
        "hip_slide_backswing_sw",
        "head_dip_sw",
        "lead_arm_angle_deg",
        "shoulder_tilt_impact_deg",
        "finish_balance_sw",
    }
    assert all(measured[key] is None for key in body_metrics)
    assert not {
        measurement.id
        for phase in bundle.view.phases
        for measurement in phase.measurements
    }.intersection(body_metrics)
    declared = {row.relative_path for row in bundle.manifest.artifacts}
    assert not any(
        token in relative.lower()
        for relative in declared
        for token in ("work/", "raw", "overlay", "strip", "replay")
    )


def test_guided_gate_renderer_only_rejects_an_extra_direct_final_bundle_root(
    tmp_path: Path,
    monkeypatch,
):
    gate = _gate_helper()
    real_run_guided_job = gate.run_guided_job

    def run_with_extra_final(*args, **kwargs):
        row, manager = real_run_guided_job(*args, **kwargs)
        assert row.report_rel is not None
        report_path = row.session_dir / Path(row.report_rel)
        analysis_session = report_path.parent.parent
        (analysis_session / "report-bundle-extra").mkdir()
        return row, manager

    monkeypatch.setattr(gate, "run_guided_job", run_with_extra_final)

    with pytest.raises(AssertionError, match="exactly one final report bundle"):
        gate._run_renderer_only(tmp_path)


def test_gate_cli_writes_one_bounded_privacy_safe_result_for_all_scenarios(
    tmp_path: Path,
    capsys,
):
    gate = _gate_helper()
    assert hasattr(gate, "main"), "guided gate CLI is not implemented"
    out = tmp_path / "guided-gate"

    assert gate.main(["--out", str(out)]) == 0

    result_path = out / "guided-gate-result.json"
    payload_text = result_path.read_text(encoding="utf-8")
    payload = json.loads(payload_text)
    assert payload["result_version"] == "guided-report-local-gate-v1"
    assert payload["media_io"] == "deterministic-no-ffmpeg-harness"
    assert re.fullmatch(r"[0-9a-f]{40}", payload["commit_sha"])
    assert [row["scenario"] for row in payload["scenarios"]] == [
        "face-on",
        "dtl",
        "renderer-only",
        "core-writer",
    ]

    by_scenario = {row["scenario"]: row for row in payload["scenarios"]}
    for scenario in ("face-on", "dtl", "renderer-only"):
        row = by_scenario[scenario]
        assert row["presentation_version"] == "guided-report-v1"
        assert row["view_version"] == "report-view-v1"
        assert row["checksum_verification"]["rows"] > 0
        assert (
            row["checksum_verification"]["verified"]
            == row["checksum_verification"]["rows"]
        )
        assert row["checksum_verification"]["media_resolved"] == len(row["media"])
        assert all(row["canonical_rels"].values())
        assert all("\\" not in relative for relative in row["canonical_rels"].values())

    assert by_scenario["face-on"]["priority_evidence_count"] == 1
    assert by_scenario["dtl"]["priority_evidence_count"] == 1
    assert by_scenario["dtl"]["timing_metrics_present"] is True
    assert by_scenario["dtl"]["body_metrics_null"] is True
    assert by_scenario["renderer-only"]["priority_evidence_count"] == 0
    assert by_scenario["renderer-only"]["visual_evidence_state"] == "unavailable"
    assert by_scenario["renderer-only"]["trust_state"] == "limited"
    assert by_scenario["renderer-only"]["final_bundle_roots"] == 1
    assert by_scenario["renderer-only"]["durable_job"] == {
        "canonical_rels_present": True,
        "status": "done",
        "structured_report": True,
        "usage_this_month": 1,
    }
    assert by_scenario["core-writer"]["canonical_rels"] == {
        "checksums": None,
        "manifest": None,
        "report": None,
        "view": None,
    }
    assert by_scenario["core-writer"]["durable_job"] == {
        "canonical_rels_present": False,
        "status": "failed",
        "structured_report": False,
        "usage_this_month": 0,
    }
    assert by_scenario["core-writer"]["final_bundle_roots"] == 0

    captured = capsys.readouterr()
    combined = payload_text + captured.out + captured.err
    assert str(out.resolve()) not in combined
    assert not re.search(r"[A-Za-z]:[\\/]", combined)
    for forbidden in (
        "gate-media.bin",
        "source_name",
        "raw_landmark",
        "email",
        "deterministic media fixture",
    ):
        assert forbidden not in combined
