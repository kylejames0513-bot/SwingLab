from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from swinglab.report_view import (
    ReportViewValidationError,
    UnsupportedReportViewVersion,
    report_view_from_dict,
    report_view_to_dict,
    write_report_view,
)
from tests.report_view_fixtures import report_view_payload


def _coaching():
    payload = copy.deepcopy(report_view_payload("coaching-limited-rendered"))
    payload["phases"][0]["measurements"] = [{"id":"metric","label":"Metric","plain_value":"1","numeric_value":1,"unit":"count","benchmark_relation":"none","benchmark_value":None,"benchmark_upper_value":None,"benchmark_label":None,"explanation":"Measured.","limitation":""}]
    payload["optional_sections"] = [{"id":"measurements","label":"Measurements","available":True,"locked":False,"item_count":0}]
    return payload


@pytest.mark.parametrize("angle, phases", [
    ("face_on", ["setup", "going_back", "transition_downswing", "impact"]),
    ("dtl", ["timing_rhythm", "timing_rhythm"]),
])
def test_coaching_phase_layout_is_angle_specific(angle, phases):
    payload = _coaching()
    payload["context"]["angle"] = angle
    payload["phases"] = [dict(payload["phases"][0], id=phase) for phase in phases]
    with pytest.raises(ReportViewValidationError):
        report_view_from_dict(payload)


def test_coaching_rejects_duplicate_phase_ids():
    payload = _coaching()
    payload["phases"] = [payload["phases"][0], payload["phases"][0]]
    with pytest.raises(ReportViewValidationError):
        report_view_from_dict(payload)


@pytest.mark.parametrize("comparator, upper, successes, attempts", [
    ("between", None, None, None), ("between", 0.2, None, None),
    ("lte", 1.0, None, None), ("lte", None, 3, 2),
])
def test_refilm_target_invariants(comparator, upper, successes, attempts):
    payload = _coaching()
    target = payload["refilm"]["target"]
    target.update(comparator=comparator, upper_threshold=upper, required_successes=successes, required_attempts=attempts)
    with pytest.raises(ReportViewValidationError):
        report_view_from_dict(payload)


def _set(payload, path, value):
    target = payload
    for key in path[:-1]: target = target[key]
    target[path[-1]] = value


def _valid_dtl():
    payload = _coaching()
    payload["context"]["angle"] = "dtl"
    payload["phases"] = [dict(payload["phases"][0], id="timing_rhythm")]
    payload["visual_evidence"].update(kind="tempo_timeline", phase="timing_rhythm")
    return payload


def test_dtl_accepts_only_timeline_evidence_and_timing_phase():
    assert report_view_from_dict(_valid_dtl()).context.angle.value == "dtl"
    payload = _valid_dtl(); payload["visual_evidence"]["kind"] = "head_boundary"
    with pytest.raises(ReportViewValidationError): report_view_from_dict(payload)


@pytest.mark.parametrize("path", [
    ("outcome",), ("journey_mode",), ("trust", "state"), ("context", "hand"), ("context", "angle"),
    ("next_move", "category"), ("visual_evidence", "kind"), ("visual_evidence", "phase_method"),
    ("visual_evidence", "events", 0, "event"), ("visual_evidence", "tracking_state"),
    ("visual_evidence", "tracking_reasons", 0), ("phases", 0, "status"),
    ("phases", 0, "measurements", 0, "unit"), ("phases", 0, "measurements", 0, "benchmark_relation"),
    ("refilm", "target", "comparator"), ("refilm", "target", "window"),
    ("optional_sections", 0, "id"), ("media", 0, "role"), ("media", 0, "entitlement"),
])
def test_every_persisted_enum_rejects_invalid_member(path):
    payload = _coaching()
    _set(payload, path, "not-an-enum-member")
    with pytest.raises(ReportViewValidationError): report_view_from_dict(payload)


@pytest.mark.parametrize("mutate", [
    lambda p: _set(p, ("context", "analysis_fps"), float("nan")),
    lambda p: _set(p, ("visual_evidence", "timestamp_ms"), -1),
    lambda p: _set(p, ("visual_evidence", "swing"), 0),
    lambda p: _set(p, ("context", "detected_swings"), -1),
    lambda p: _set(p, ("practice", "summary_steps", 0), ""),
    lambda p: p["media"].append(copy.deepcopy(p["media"][0])),
    lambda p: p["trust"].update(reasons=["secondary_metric_unavailable", "secondary_metric_unavailable"]),
    lambda p: p["phases"].append(copy.deepcopy(p["phases"][0])),
    lambda p: _set(p, ("media", 0, "relative_path"), "../escape.jpg"),
    lambda p: _set(p, ("visual_evidence", "media_key"), "missing"),
    lambda p: _set(p, ("capture_guidance",), {}),
])
def test_known_version_malformed_payloads_fail_closed(mutate):
    payload = _coaching(); mutate(payload)
    with pytest.raises(ReportViewValidationError): report_view_from_dict(payload)


@pytest.mark.parametrize("mutate", [
    lambda p: p["visual_evidence"].pop("media_key"),
    lambda p: _set(p, ("visual_evidence", "render_reasons"), ["focused_media_render_failed"]),
    lambda p: _set(p, ("capture_guidance",), {"unexpected": True}),
])
def test_coaching_union_members_fail_closed(mutate):
    payload = copy.deepcopy(report_view_payload("coaching-improve-clear")); mutate(payload)
    with pytest.raises(ReportViewValidationError): report_view_from_dict(payload)


@pytest.mark.parametrize("mutate", [
    lambda p: _set(p, ("visual_evidence", "media_key"), "focus-1"),
    lambda p: _set(p, ("visual_evidence", "render_reasons"), []),
    lambda p: _set(p, ("visual_evidence", "render_reasons"), ["focused_media_render_failed", "tracking_unstable"]),
])
def test_unavailable_evidence_union_members_fail_closed(mutate):
    payload = copy.deepcopy(report_view_payload("coaching-limited-visual-unavailable")); mutate(payload)
    with pytest.raises(ReportViewValidationError): report_view_from_dict(payload)


def test_clear_coaching_rejects_unavailable_evidence():
    payload = copy.deepcopy(report_view_payload("coaching-improve-clear"))
    unavailable = report_view_payload("coaching-limited-visual-unavailable")["visual_evidence"]
    payload["visual_evidence"] = unavailable
    with pytest.raises(ReportViewValidationError): report_view_from_dict(payload)


@pytest.mark.parametrize("reason", ["no_readable_swing", "no_reliable_strike_event", "priority_evidence_unreliable"])
def test_limited_coaching_rejects_fatal_reason(reason):
    payload = copy.deepcopy(report_view_payload("coaching-limited-rendered")); payload["trust"]["reasons"] = [reason]
    with pytest.raises(ReportViewValidationError): report_view_from_dict(payload)


@pytest.mark.parametrize("field, value", [
    ("next_move", {"mode":"improve"}), ("visual_evidence", {"state":"rendered"}),
    ("phases", [{"id":"setup"}]), ("practice", {"section_id":"practice"}),
    ("refilm", {"section_id":"refilm"}),
])
def test_capture_only_rejects_all_coaching_content(field, value):
    payload = copy.deepcopy(report_view_payload("capture-only")); payload[field] = value
    with pytest.raises(ReportViewValidationError): report_view_from_dict(payload)


@pytest.mark.parametrize(
    "name",
    (
        "coaching-improve-clear",
        "coaching-protect-clear",
        "coaching-limited-rendered",
        "coaching-limited-visual-unavailable",
        "capture-only",
    ),
)
def test_report_view_v1_fixtures_round_trip(name):
    payload = report_view_payload(name)
    view = report_view_from_dict(payload)
    assert report_view_to_dict(view) == payload


def test_write_report_view_emits_exact_canonical_utf8_lf_bytes(tmp_path: Path):
    payload = copy.deepcopy(report_view_payload("coaching-improve-clear"))
    payload["context"]["angle_label"] = "Face-on café ⛳"
    view = report_view_from_dict(payload)
    expected = (
        json.dumps(
            report_view_to_dict(view),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    path = tmp_path / "report-view.json"

    write_report_view(path, view)

    assert path.read_bytes() == expected
    assert b"\r\n" not in path.read_bytes()


def test_unknown_report_view_version_fails_closed():
    payload = report_view_payload()
    payload["version"] = "report-view-v2"
    with pytest.raises(UnsupportedReportViewVersion):
        report_view_from_dict(payload)
