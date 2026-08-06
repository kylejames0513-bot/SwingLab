from __future__ import annotations

import pytest
import copy

from swinglab.report_view import (
    UnsupportedReportViewVersion,
    report_view_from_dict,
    report_view_to_dict,
)
from tests.report_view_fixtures import report_view_payload


def _coaching():
    return copy.deepcopy(report_view_payload("coaching-limited-rendered"))


@pytest.mark.parametrize("angle, phases", [
    ("face_on", ["setup", "going_back", "transition_downswing", "impact"]),
    ("dtl", ["timing_rhythm", "timing_rhythm"]),
])
def test_coaching_phase_layout_is_angle_specific(angle, phases):
    payload = _coaching()
    payload["context"]["angle"] = angle
    payload["phases"] = [dict(payload["phases"][0], id=phase) for phase in phases]
    with pytest.raises(Exception):
        report_view_from_dict(payload)


def test_coaching_rejects_duplicate_phase_ids():
    payload = _coaching()
    payload["phases"] = [payload["phases"][0], payload["phases"][0]]
    with pytest.raises(Exception):
        report_view_from_dict(payload)


@pytest.mark.parametrize("comparator, upper, successes, attempts", [
    ("between", None, None, None), ("between", 0.2, None, None),
    ("lte", 1.0, None, None), ("lte", None, 3, 2),
])
def test_refilm_target_invariants(comparator, upper, successes, attempts):
    payload = _coaching()
    target = payload["refilm"]["target"]
    target.update(comparator=comparator, upper_threshold=upper, required_successes=successes, required_attempts=attempts)
    with pytest.raises(Exception):
        report_view_from_dict(payload)


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


def test_unknown_report_view_version_fails_closed():
    payload = report_view_payload()
    payload["version"] = "report-view-v2"
    with pytest.raises(UnsupportedReportViewVersion):
        report_view_from_dict(payload)
