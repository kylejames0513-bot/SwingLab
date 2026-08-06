import math

import pytest

from swinglab.caddie_brief import CaddieBrief
from swinglab.coaching import IssueCard
from swinglab.config import Config
from swinglab.report_presenter import (
    EvidenceCandidate,
    UnsupportedPriorityEvidence,
    priority_evidence_rule,
    select_representative_swing,
)
from swinglab.report_view import EvidenceKind, EventId, PhaseId


def brief(*, focus_flag=None, strength_key=None):
    return CaddieBrief(
        strength=None,
        strength_key=strength_key,
        focus_flag=focus_flag,
        focus_name="Focus",
        focus_value=None,
        benchmark_text=None,
        why="why",
        fix="fix",
        drill=None,
        trend=None,
        warning=None,
        recurring_sessions=0,
        remaining_issues=0,
        clean=focus_flag is None,
        refilm_required=False,
    )


def issue(flag, metric, benchmark=1.0, worse_direction="higher"):
    return IssueCard(
        flag=flag,
        metric=metric,
        display_name="Focus",
        unit="SW",
        per_swing=(),
        session_value=1.0,
        session_label="session mean",
        session_text="1.0",
        benchmark_value=benchmark,
        benchmark_text="reference",
        worse_direction=worse_direction,
        severity="warn",
        why="why",
        fix="fix",
        drill_ids=(),
        drill_names=(),
    )


@pytest.mark.parametrize(
    ("key", "metric", "kind", "phase", "event", "basis"),
    [
        ("sway", "head_sway_backswing_sw", EvidenceKind.HEAD_BOUNDARY, PhaseId.GOING_BACK, EventId.TOP, "threshold"),
        ("hip-slide", "hip_slide_backswing_sw", EvidenceKind.HIP_BOUNDARY, PhaseId.GOING_BACK, EventId.TOP, "threshold"),
        ("head-dip", "head_dip_sw", EvidenceKind.HEAD_HEIGHT, PhaseId.IMPACT, EventId.IMPACT, "threshold"),
        ("tempo", "tempo_ratio", EvidenceKind.TEMPO_TIMELINE, PhaseId.TRANSITION_DOWNSWING, None, "session_mean"),
        ("consistency", "tempo_ratio", EvidenceKind.TEMPO_TIMELINE, PhaseId.TRANSITION_DOWNSWING, None, "consistency_median"),
        ("arm-extension", "lead_arm_angle_deg", EvidenceKind.LEAD_ARM_ANGLE, PhaseId.IMPACT, EventId.IMPACT, "threshold"),
        ("balance", "finish_balance_sw", EvidenceKind.FINISH_STABILITY, PhaseId.FINISH, EventId.FINISH, "threshold"),
    ],
)
def test_face_on_priority_maps_to_the_authored_evidence_type(
    key, metric, kind, phase, event, basis
):
    rule = priority_evidence_rule(
        brief(focus_flag=key), [issue(key, metric)], angle="face-on", cfg=Config()
    )
    assert (rule.priority_key, rule.metric_id, rule.kind, rule.phase, rule.event, rule.selection_basis) == (
        key, metric, kind, phase, event, basis
    )


@pytest.mark.parametrize("metric", ["shoulder_tilt_impact_deg", "shoulder_tilt_delta_deg"])
def test_shoulder_tilt_uses_the_metric_selected_by_the_issue_card(metric):
    rule = priority_evidence_rule(
        brief(focus_flag="shoulder-tilt"),
        [issue("shoulder-tilt", metric, benchmark=0.0, worse_direction="lower")],
        angle="face-on",
        cfg=Config(),
    )
    assert rule.metric_id == metric
    assert rule.kind is EvidenceKind.SHOULDER_TILT
    assert rule.phase is PhaseId.IMPACT
    assert rule.event is EventId.IMPACT
    assert rule.selection_basis == (
        "shoulder_tilt_delta_mean" if metric == "shoulder_tilt_delta_deg" else "threshold"
    )


@pytest.mark.parametrize(
    ("strength_key", "metric", "kind", "phase"),
    [
        ("sway", "head_sway_backswing_sw", EvidenceKind.STEADY_REFERENCE, PhaseId.GOING_BACK),
        ("tempo", "tempo_ratio", EvidenceKind.TEMPO_TIMELINE, PhaseId.TRANSITION_DOWNSWING),
        ("hip-slide", "hip_slide_backswing_sw", EvidenceKind.STEADY_REFERENCE, PhaseId.GOING_BACK),
        ("head-dip", "head_dip_sw", EvidenceKind.STEADY_REFERENCE, PhaseId.IMPACT),
        ("arm-extension", "lead_arm_angle_deg", EvidenceKind.STEADY_REFERENCE, PhaseId.IMPACT),
        ("shoulder-tilt", "shoulder_tilt_impact_deg", EvidenceKind.STEADY_REFERENCE, PhaseId.IMPACT),
        ("balance", "finish_balance_sw", EvidenceKind.STEADY_REFERENCE, PhaseId.FINISH),
        ("consistency", "tempo_ratio", EvidenceKind.STEADY_REFERENCE, PhaseId.TRANSITION_DOWNSWING),
    ],
)
def test_protect_uses_the_selected_strength_metric(strength_key, metric, kind, phase):
    rule = priority_evidence_rule(
        brief(strength_key=strength_key), [], angle="face-on", cfg=Config()
    )
    assert (rule.priority_key, rule.metric_id, rule.kind, rule.phase, rule.event, rule.selection_basis) == (
        strength_key, metric, kind, phase, None, "maintenance_median"
    )


@pytest.mark.parametrize("brief_value", [brief(focus_flag="tempo"), brief(strength_key="tempo")])
def test_dtl_uses_tempo_timeline_for_selected_tempo(brief_value):
    rule = priority_evidence_rule(brief_value, [issue("tempo", "tempo_ratio")], angle="dtl", cfg=Config())
    assert rule.kind is EvidenceKind.TEMPO_TIMELINE
    assert rule.phase is PhaseId.TIMING_RHYTHM
    assert rule.event is None


@pytest.mark.parametrize("brief_value", [brief(focus_flag="sway"), brief(strength_key="sway")])
def test_dtl_fails_closed_for_stale_face_on_priority(brief_value):
    with pytest.raises(UnsupportedPriorityEvidence):
        priority_evidence_rule(brief_value, [issue("sway", "head_sway_backswing_sw")], angle="dtl", cfg=Config())


def candidates(*rows):
    return [EvidenceCandidate(*row) for row in rows]


def test_threshold_uses_eligible_crossings_and_lowest_swing_breaks_ties():
    rows = candidates((1, 100.0, False, True), (7, -2.0, True, True), (3, 2.0, True, True), (2, 9.0, True, False))
    assert select_representative_swing(rows, basis="threshold") == 3


def test_session_mean_ignores_ineligible_non_finite_and_uses_lowest_tied_swing():
    rows = candidates((1, 99.0, False, None), (9, math.inf, True, None), (8, -1.0, True, None), (4, 5.0, True, None), (2, 5.0, True, None))
    assert select_representative_swing(rows, basis="session_mean", session_value=4.0) == 2


def test_consistency_median_selects_the_eligible_median_with_negative_values():
    rows = candidates((9, 999.0, False, None), (7, -5.0, True, None), (3, -1.0, True, None), (1, 3.0, True, None))
    assert select_representative_swing(rows, basis="consistency_median") == 3


def test_shoulder_delta_mean_selects_closest_eligible_value():
    rows = candidates((8, -10.0, True, None), (2, -4.0, True, None), (5, 0.0, True, None))
    assert select_representative_swing(rows, basis="shoulder_tilt_delta_mean", session_value=-2.0) == 2


def test_maintenance_median_selects_lowest_swing_on_an_exact_tie():
    rows = candidates((6, 1.0, True, None), (2, 3.0, True, None), (4, 5.0, True, None), (1, math.nan, True, None))
    assert select_representative_swing(rows, basis="maintenance_median") == 2


def test_selection_returns_none_without_an_eligible_finite_candidate():
    rows = candidates((1, 1.0, False, True), (2, math.nan, True, False))
    assert select_representative_swing(rows, basis="threshold") is None
