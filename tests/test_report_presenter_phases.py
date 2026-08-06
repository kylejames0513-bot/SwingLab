"""Structured phase summaries keep their own factual measurement contract."""

from __future__ import annotations

from swinglab.caddie_brief import CaddieBrief
from swinglab.coaching import IssueCard, StrengthCard
from swinglab.config import Config
from swinglab.metrics import ANGLE_DTL, SwingMetrics, session_stats
from swinglab.report_presenter import (
    ReportContextInput,
    ReportPresentationInput,
    ReportSwingSource,
    build_phase_summaries,
    build_report_view,
    measurement_detail,
)
from swinglab.report_view import JourneyMode, PhaseId, PhaseStatus, ReasonCode
from tests.test_metrics_depth import make_metrics
from tests.test_report_presenter_states import authored_drill, evidence


def issue(flag: str, metric: str) -> IssueCard:
    return IssueCard(flag, metric, "Legacy title must not decide status", "SW", (), 0.5,
        "session mean", "0.50 SW", 0.35, "configured line", "higher", "warn",
        "Why.", "Fix.", (), ())


def report_source(
    metrics: list[SwingMetrics], *, angle: str = "face_on", focus: str | None = "sway",
    strength: str | None = None, issues: tuple[IssueCard, ...] | None = None,
    reasons: tuple[ReasonCode, ...] = (),
) -> ReportPresentationInput:
    selected_metric = "head_sway_backswing_sw" if focus or strength == "sway" else "tempo_ratio"
    return ReportPresentationInput(
        ReportContextInput("7i", "right", angle, len(metrics), 30.0), metrics,
        session_stats(metrics), (),
        CaddieBrief(None, strength, focus, "Priority", None, None, "Why.", "Fix.",
            authored_drill(), None, None, 0, 0, focus is None, False),
        issues if issues is not None else ((issue(focus, selected_metric),) if focus else ()),
        (StrengthCard(strength, selected_metric, "Strength", "Strong."),) if strength else (),
        authored_drill(), (), evidence(), (), reasons, (), False, None,
    )


def complete_metrics(*, target_confident: bool = True, **overrides: object) -> SwingMetrics:
    values = dict(stance_width_sw=0.95, backswing_s=0.9, downswing_s=0.3,
        tempo_ratio=3.0, head_sway_backswing_sw=0.2, hip_slide_backswing_sw=0.1,
        downswing_hand_speed_sw_s=4.0, strike_s=1.2, head_dip_sw=0.1,
        lead_arm_angle_deg=170.0, shoulder_tilt_impact_deg=25.0,
        finish_balance_sw=0.1, target_confident=target_confident)
    values.update(overrides)
    return make_metrics(1, **values)


def test_face_on_has_five_ordered_rows_with_one_owned_copy_of_every_measurement():
    source = report_source([complete_metrics()])
    phases = build_phase_summaries(source, Config())
    assert [phase.label for phase in phases] == [
        "Setup", "Going back", "Transition & downswing", "Impact", "Finish",
    ]
    assert [phase.detail_section_id for phase in phases] == [
        "phase-setup", "phase-going_back", "phase-transition_downswing", "phase-impact", "phase-finish",
    ]
    owned = {phase.id: [detail.id for detail in phase.measurements] for phase in phases}
    assert owned == {
        PhaseId.SETUP: ["measurement-stance_width_sw"],
        PhaseId.GOING_BACK: ["measurement-backswing_s", "measurement-head_sway_backswing_sw", "measurement-hip_slide_backswing_sw"],
        PhaseId.TRANSITION_DOWNSWING: ["measurement-tempo_ratio", "measurement-downswing_s", "measurement-downswing_hand_speed_sw_s"],
        PhaseId.IMPACT: ["measurement-strike_s", "measurement-head_dip_sw", "measurement-lead_arm_angle_deg", "measurement-shoulder_tilt_impact_deg"],
        PhaseId.FINISH: ["measurement-finish_balance_sw"],
    }
    flat = [metric for values in owned.values() for metric in values]
    assert len(flat) == len(set(flat))
    assert phases[0].status is PhaseStatus.BASELINE
    assert phases[0].measurements[0].benchmark_relation.value == "context_only"
    assert all(detail.numeric_value is not None for detail in phases[3].measurements)
    assert sum(phase.expanded_by_default for phase in phases) == 1
    assert phases[1].expanded_by_default is True


def test_status_precedence_and_unavailable_semantics_are_deterministic():
    secondary = issue("head-dip", "head_dip_sw")
    source = report_source([complete_metrics(head_dip_sw=float("nan"))], issues=(issue("sway", "head_sway_backswing_sw"), secondary))
    phases = {phase.id: phase for phase in build_phase_summaries(source, Config())}
    assert phases[PhaseId.GOING_BACK].status is PhaseStatus.PRIORITY
    assert phases[PhaseId.IMPACT].status is PhaseStatus.REVIEW_LATER
    assert phases[PhaseId.IMPACT].measurements[1].numeric_value is None
    assert phases[PhaseId.IMPACT].unavailable_reasons == (ReasonCode.SECONDARY_METRIC_UNAVAILABLE,)
    assert phases[PhaseId.TRANSITION_DOWNSWING].status is PhaseStatus.STEADY
    assert phases[PhaseId.SETUP].status is PhaseStatus.BASELINE


def test_empty_category_is_not_measured_and_one_unavailable_secondary_does_not_downgrade_supported_category():
    metric = complete_metrics(stance_width_sw=float("nan"), head_dip_sw=float("nan"))
    phases = {phase.id: phase for phase in build_phase_summaries(report_source([metric]), Config())}
    assert phases[PhaseId.SETUP].status is PhaseStatus.NOT_MEASURED
    assert phases[PhaseId.IMPACT].status is PhaseStatus.STEADY
    assert phases[PhaseId.IMPACT].unavailable_reasons == (ReasonCode.SECONDARY_METRIC_UNAVAILABLE,)


def test_protect_expands_selected_phase_but_keeps_ordinary_steady_status_and_owned_sublabel():
    phases = build_phase_summaries(report_source([complete_metrics()], focus=None, strength="sway"), Config())
    selected = next(phase for phase in phases if phase.id is PhaseId.GOING_BACK)
    assert selected.expanded_by_default is True
    assert selected.status is PhaseStatus.STEADY
    assert "Strength to protect" in selected.summary
    assert all(phase.status is not PhaseStatus.PRIORITY for phase in phases)


def test_dtl_is_timing_only_and_drops_stale_face_on_values():
    phases = build_phase_summaries(report_source([complete_metrics()], angle=ANGLE_DTL, focus="tempo"), Config())
    assert len(phases) == 1
    assert phases[0].id is PhaseId.TIMING_RHYTHM
    assert phases[0].label == "Timing & rhythm"
    assert [detail.id for detail in phases[0].measurements] == [
        "measurement-backswing_s", "measurement-downswing_s", "measurement-tempo_ratio", "measurement-tempo_ratio_std",
    ]


def test_target_direction_uncertainty_removes_directional_language_without_unmeasuring_tempo():
    source = report_source([complete_metrics(target_confident=False)])
    phases = build_phase_summaries(source, Config())
    going_back = next(phase for phase in phases if phase.id is PhaseId.GOING_BACK)
    tempo = measurement_detail("tempo_ratio", source.swings, source.stats, Config())
    assert ReasonCode.TARGET_DIRECTION_UNCERTAIN in going_back.unavailable_reasons
    assert "toward" not in going_back.measurements[1].explanation.lower()
    assert "away" not in going_back.measurements[1].explanation.lower()
    assert tempo is not None and tempo.numeric_value == 3.0


def test_phase_builder_reads_production_report_swing_source_mappings_without_session_means():
    wrapped = ReportSwingSource(
        {"backswing_s": 0.9, "downswing_s": 0.3, "tempo_ratio": 3.0,
         "head_sway_backswing_sw": 0.2, "hip_slide_backswing_sw": None,
         "target_confident": False}, (),
    )
    source = report_source([complete_metrics()])
    source = ReportPresentationInput(
        source.context, (wrapped,), {}, source.session_notes, source.brief,
        source.issues, source.strengths, source.primary_drill,
        source.alternative_drills, source.visual_evidence, source.media,
        source.reason_codes, source.safe_media_keys, source.replay_locked,
        source.navigation,
    )
    phases = {phase.id: phase for phase in build_phase_summaries(source, Config())}
    assert phases[PhaseId.TRANSITION_DOWNSWING].measurements[0].numeric_value == 3.0
    assert phases[PhaseId.GOING_BACK].measurements[2].numeric_value is None
    assert ReasonCode.TARGET_DIRECTION_UNCERTAIN in phases[PhaseId.GOING_BACK].unavailable_reasons


def test_dtl_selected_tempo_uses_timing_rhythm_for_improve_and_protect():
    improve_source = report_source(
        [complete_metrics()], angle=ANGLE_DTL, focus="tempo",
        issues=(issue("tempo", "tempo_ratio"),),
    )
    improve = build_report_view(improve_source, Config())
    assert improve.next_move.category is PhaseId.TIMING_RHYTHM
    assert improve.phases[0].status is PhaseStatus.PRIORITY
    assert improve.phases[0].expanded_by_default is True

    protect_source = report_source([complete_metrics()], angle=ANGLE_DTL, focus=None, strength="tempo")
    protect = build_report_view(protect_source, Config())
    assert protect.next_move.category is PhaseId.TIMING_RHYTHM
    assert protect.phases[0].status is PhaseStatus.STEADY
    assert protect.phases[0].expanded_by_default is True
