"""Pure choices for the guided report's priority evidence.

This module maps the already-selected coaching priority to visual evidence;
it deliberately does not inspect swing pixels or landmarks.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Literal, Mapping, Sequence, TypeAlias

from .caddie_brief import CaddieBrief
from .coaching import IssueCard, StrengthCard
from .config import Config
from .drills import Drill, drill_presentation
from .metrics import SwingMetrics, finite_float
from .report_view import (
    GUIDED_REPORT_PRESENTATION_VERSION,
    Angle,
    BenchmarkRelation,
    Capabilities,
    CaptureGuidance,
    CaptureOnlyReportView,
    CoachingReportView,
    DrillAlternative,
    EvidenceKind,
    EvidenceView,
    EventId,
    Hand,
    JourneyMode,
    MediaEntry,
    MeasurementDetail,
    MeasurementUnit,
    NextMove,
    PhaseId,
    PhaseStatus,
    PhaseSummary,
    PracticePrescription,
    ReasonCode,
    RefilmProtocol,
    RefilmTarget,
    ReportContext,
    ReportOutcome,
    ReportViewV1,
    TargetComparator,
    TargetWindow,
    TrackingState,
    Trust,
    TrustState,
)


SelectionBasis: TypeAlias = Literal[
    "threshold",
    "session_mean",
    "consistency_median",
    "shoulder_tilt_delta_mean",
    "maintenance_median",
]


class UnsupportedPriorityEvidence(ValueError):
    """The selected coaching priority has no safe visual evidence rule."""


@dataclass(frozen=True)
class PriorityEvidenceRule:
    priority_key: str
    metric_id: str
    kind: EvidenceKind
    phase: PhaseId
    event: EventId | None
    selection_basis: SelectionBasis
    benchmark: float | None
    worse_direction: Literal["higher", "lower"] | None


@dataclass(frozen=True)
class EvidenceCandidate:
    swing: int
    metric_value: float
    eligible: bool
    crossed_line: bool | None


_FACE_ON_RULES: dict[str, tuple[str, EvidenceKind, PhaseId, EventId | None, SelectionBasis]] = {
    "sway": ("head_sway_backswing_sw", EvidenceKind.HEAD_BOUNDARY, PhaseId.GOING_BACK, EventId.TOP, "threshold"),
    "hip-slide": ("hip_slide_backswing_sw", EvidenceKind.HIP_BOUNDARY, PhaseId.GOING_BACK, EventId.TOP, "threshold"),
    "head-dip": ("head_dip_sw", EvidenceKind.HEAD_HEIGHT, PhaseId.IMPACT, EventId.IMPACT, "threshold"),
    "tempo": ("tempo_ratio", EvidenceKind.TEMPO_TIMELINE, PhaseId.TRANSITION_DOWNSWING, None, "session_mean"),
    "consistency": ("tempo_ratio", EvidenceKind.TEMPO_TIMELINE, PhaseId.TRANSITION_DOWNSWING, None, "consistency_median"),
    "arm-extension": ("lead_arm_angle_deg", EvidenceKind.LEAD_ARM_ANGLE, PhaseId.IMPACT, EventId.IMPACT, "threshold"),
    "balance": ("finish_balance_sw", EvidenceKind.FINISH_STABILITY, PhaseId.FINISH, EventId.FINISH, "threshold"),
}

_STRENGTH_RULES: dict[str, tuple[str, PhaseId]] = {
    "sway": ("head_sway_backswing_sw", PhaseId.GOING_BACK),
    "tempo": ("tempo_ratio", PhaseId.TRANSITION_DOWNSWING),
    "hip-slide": ("hip_slide_backswing_sw", PhaseId.GOING_BACK),
    "head-dip": ("head_dip_sw", PhaseId.IMPACT),
    "arm-extension": ("lead_arm_angle_deg", PhaseId.IMPACT),
    "shoulder-tilt": ("shoulder_tilt_impact_deg", PhaseId.IMPACT),
    "balance": ("finish_balance_sw", PhaseId.FINISH),
    "consistency": ("tempo_ratio", PhaseId.TRANSITION_DOWNSWING),
}


def priority_evidence_rule(
    brief: CaddieBrief,
    issues: Sequence[IssueCard],
    *,
    angle: str,
    cfg: Config,
) -> PriorityEvidenceRule:
    """Map one preselected Caddie Brief priority to safe report evidence."""
    del cfg  # Configuration determined the Caddie Brief and IssueCard already.

    if brief.focus_flag is not None:
        key = brief.focus_flag
        card = next((item for item in issues if item.flag == key), None)
        if card is None:
            raise UnsupportedPriorityEvidence(f"No issue card for priority {key!r}")
        if angle == "dtl":
            if key != "tempo":
                raise UnsupportedPriorityEvidence(
                    f"Down-the-line evidence cannot annotate {key!r}"
                )
            return PriorityEvidenceRule(
                key,
                "tempo_ratio",
                EvidenceKind.TEMPO_TIMELINE,
                PhaseId.TIMING_RHYTHM,
                None,
                "session_mean",
                card.benchmark_value,
                card.worse_direction,
            )
        return _improve_rule(key, card)

    if brief.strength_key is not None:
        key = brief.strength_key
        try:
            metric_id, phase = _STRENGTH_RULES[key]
        except KeyError as error:
            raise UnsupportedPriorityEvidence(f"No strength rule for {key!r}") from error
        if angle == "dtl":
            if key != "tempo":
                raise UnsupportedPriorityEvidence(
                    f"Down-the-line evidence cannot annotate {key!r}"
                )
            return PriorityEvidenceRule(
                key, metric_id, EvidenceKind.TEMPO_TIMELINE,
                PhaseId.TIMING_RHYTHM, None, "maintenance_median", None, None,
            )
        return PriorityEvidenceRule(
            key,
            metric_id,
            EvidenceKind.TEMPO_TIMELINE if key == "tempo" else EvidenceKind.STEADY_REFERENCE,
            phase,
            None,
            "maintenance_median",
            None,
            None,
        )

    raise UnsupportedPriorityEvidence("Caddie Brief has no priority or strength")


def _improve_rule(key: str, card: IssueCard) -> PriorityEvidenceRule:
    if key == "shoulder-tilt":
        if card.metric not in {"shoulder_tilt_impact_deg", "shoulder_tilt_delta_deg"}:
            raise UnsupportedPriorityEvidence(
                f"Unsupported shoulder-tilt metric {card.metric!r}"
            )
        return PriorityEvidenceRule(
            key,
            card.metric,
            EvidenceKind.SHOULDER_TILT,
            PhaseId.IMPACT,
            EventId.IMPACT,
            "shoulder_tilt_delta_mean" if card.metric == "shoulder_tilt_delta_deg" else "threshold",
            card.benchmark_value,
            card.worse_direction,  # type: ignore[arg-type]
        )
    try:
        metric_id, kind, phase, event, basis = _FACE_ON_RULES[key]
    except KeyError as error:
        raise UnsupportedPriorityEvidence(f"No issue rule for {key!r}") from error
    if card.metric != metric_id:
        raise UnsupportedPriorityEvidence(
            f"Issue card metric {card.metric!r} does not match {key!r}"
        )
    return PriorityEvidenceRule(
        key, metric_id, kind, phase, event, basis,
        card.benchmark_value, card.worse_direction,  # type: ignore[arg-type]
    )


def _closest(rows: Sequence[EvidenceCandidate], target: float) -> int:
    return min(rows, key=lambda row: (abs(row.metric_value - target), row.swing)).swing


def _median(values: Sequence[float]) -> float:
    return float(statistics.median(values))


def select_representative_swing(
    candidates: Sequence[EvidenceCandidate],
    *,
    basis: SelectionBasis,
    session_value: float | None = None,
) -> int | None:
    """Select one deterministic, annotation-eligible swing from measurements."""
    rows = [
        row for row in candidates
        if row.eligible and math.isfinite(row.metric_value)
    ]
    if not rows:
        return None

    if basis == "threshold":
        crossings = [row for row in rows if row.crossed_line is True]
        selected = crossings or rows
        return _closest(selected, _median([row.metric_value for row in selected]))
    if basis in {"session_mean", "shoulder_tilt_delta_mean"}:
        if session_value is None or not math.isfinite(session_value):
            raise ValueError(f"{basis} selection requires a finite session value")
        return _closest(rows, session_value)
    if basis in {"consistency_median", "maintenance_median"}:
        return _closest(rows, _median([row.metric_value for row in rows]))
    raise ValueError(f"Unsupported selection basis {basis!r}")


@dataclass(frozen=True)
class ReasonCopy:
    label: str
    explanation: str
    remediation: str


# This text is intentionally user-facing.  ReasonCode remains a server-side
# contract and must never leak into a golfer's report.
REASON_COPY: Mapping[ReasonCode, ReasonCopy] = {
    ReasonCode.CAMERA_ANGLE_MISMATCH: ReasonCopy("Camera angle needs a reset", "The selected camera angle does not match what the clip appears to show, so the movement measurements are not safe to coach from.", "Choose the angle that matches the clip, or record a new clip from that view."),
    ReasonCode.TRACKING_UNSTABLE: ReasonCopy("Body tracking did not stay steady", "The body track moved or dropped too much during the swing for a dependable coaching read.", "Re-film with your full body clear, steady lighting, and nobody else in frame."),
    ReasonCode.INSUFFICIENT_POSE_FRAMES: ReasonCopy("Too little of the swing was readable", "There were not enough usable body frames across the swing to measure the motion honestly.", "Keep the whole body in frame from address through finish and re-film."),
    ReasonCode.NO_READABLE_SWING: ReasonCopy("No complete swing was readable", "This clip did not contain a usable full swing for the report to measure.", "Choose a clip with one full swing, from setup through the finish."),
    ReasonCode.NO_RELIABLE_STRIKE_EVENT: ReasonCopy("Impact could not be located reliably", "The report could not place impact confidently enough to judge an impact-based movement.", "Re-film with a clear strike and less background noise or obstruction."),
    ReasonCode.PRIORITY_EVIDENCE_UNRELIABLE: ReasonCopy("The main coaching read is not dependable", "The measurement chosen for the next move was not reliable enough to support a swing change.", "Re-film the same setup with the full motion visible before practicing a correction."),
    ReasonCode.SECONDARY_METRIC_UNAVAILABLE: ReasonCopy("One supporting measurement is unavailable", "The main coaching read is usable, but a secondary measurement could not be completed.", "Use the current next move, then re-film with the full body visible for a more complete read."),
    ReasonCode.TARGET_DIRECTION_UNCERTAIN: ReasonCopy("Target direction is uncertain", "The main pattern is readable, but left-versus-right direction could not be confirmed from this clip.", "Keep the target line and full follow-through visible on the next recording."),
    ReasonCode.HAND_LANDMARKS_UNRELIABLE: ReasonCopy("Hand detail is limited", "The body movement is readable, but the hand landmarks were not steady enough for every supporting detail.", "Re-film with clear hands and club grip visible against a simple background."),
    ReasonCode.EVENT_ESTIMATE_LIMITED: ReasonCopy("Swing timing is estimated", "The movement is usable, but one timing point was estimated rather than observed directly.", "Use a clear full-speed recording with the strike and finish in frame."),
    ReasonCode.FOCUSED_MEDIA_RENDER_FAILED: ReasonCopy("Focused replay is unavailable", "The measurement remains usable, but its focused visual replay could not be prepared.", "Use the coaching step now and re-film later if you want the focused replay."),
}

_REASON_ORDER = (
    ReasonCode.CAMERA_ANGLE_MISMATCH,
    ReasonCode.TRACKING_UNSTABLE,
    ReasonCode.INSUFFICIENT_POSE_FRAMES,
    ReasonCode.NO_READABLE_SWING,
    ReasonCode.NO_RELIABLE_STRIKE_EVENT,
    ReasonCode.PRIORITY_EVIDENCE_UNRELIABLE,
    ReasonCode.SECONDARY_METRIC_UNAVAILABLE,
    ReasonCode.TARGET_DIRECTION_UNCERTAIN,
    ReasonCode.HAND_LANDMARKS_UNRELIABLE,
    ReasonCode.EVENT_ESTIMATE_LIMITED,
    ReasonCode.FOCUSED_MEDIA_RENDER_FAILED,
)
_FATAL_REASONS = frozenset(_REASON_ORDER[:6])


@dataclass(frozen=True)
class ReportContextInput:
    club: str | None
    hand: str
    angle: str
    detected_swings: int
    analysis_fps: float | None


@dataclass(frozen=True)
class ReportSwingSource:
    metrics: Mapping[str, float | None]
    notes: tuple[str, ...]
    key_positions_media_key: str | None = None
    key_positions_alt_text: str | None = None
    slow_motion_media_key: str | None = None
    slow_motion_caption: str | None = None
    coach_replay_media_key: str | None = None
    coach_replay_caption: str | None = None
    locked_replay_explanation: str | None = None
    video_poster_media_key: str | None = None
    video_poster_alt_text: str | None = None
    print_playback_reference: str | None = None


@dataclass(frozen=True)
class ReportPresentationInput:
    context: ReportContextInput
    swings: Sequence[ReportSwingSource]
    stats: Mapping[str, Mapping[str, float]]
    session_notes: Sequence[str]
    brief: CaddieBrief
    issues: Sequence[IssueCard]
    strengths: Sequence[StrengthCard]
    primary_drill: Drill
    alternative_drills: Sequence[Drill]
    visual_evidence: EvidenceView | None
    media: Sequence[MediaEntry]
    reason_codes: Sequence[ReasonCode]
    safe_media_keys: Sequence[str]
    replay_locked: bool
    navigation: object | None


class UnsupportedRefilmTarget(ValueError):
    """No explicit, measurable target is authored for the chosen priority."""


@dataclass(frozen=True)
class _TargetSpec:
    metric_id: str
    comparator: TargetComparator
    coach_key: str | None
    unit: MeasurementUnit
    successes: tuple[int, int] | None = None


_TARGET_SPECS: Mapping[tuple[str, str], _TargetSpec] = {
    ("tempo", "tempo_ratio"): _TargetSpec("tempo_ratio", TargetComparator.COUNT_GTE, "tempo_warn_below", MeasurementUnit.RATIO, (4, 5)),
    ("consistency", "tempo_ratio"): _TargetSpec("tempo_ratio_std", TargetComparator.LTE, "tempo_std_praise", MeasurementUnit.RATIO),
    ("sway", "head_sway_backswing_sw"): _TargetSpec("head_sway_backswing_sw", TargetComparator.ALL_LTE, "sway_warn_sw", MeasurementUnit.SHOULDER_WIDTHS),
    ("hip-slide", "hip_slide_backswing_sw"): _TargetSpec("hip_slide_backswing_sw", TargetComparator.ALL_LTE, "sway_warn_sw", MeasurementUnit.SHOULDER_WIDTHS),
    ("head-dip", "head_dip_sw"): _TargetSpec("head_dip_sw", TargetComparator.ALL_LTE, "head_dip_warn_sw", MeasurementUnit.SHOULDER_WIDTHS),
    ("arm-extension", "lead_arm_angle_deg"): _TargetSpec("lead_arm_angle_deg", TargetComparator.COUNT_GTE, "lead_arm_warn_deg", MeasurementUnit.DEGREES, (4, 5)),
    ("shoulder-tilt", "shoulder_tilt_impact_deg"): _TargetSpec("shoulder_tilt_impact_deg", TargetComparator.ALL_GTE, "shoulder_tilt_impact_min_deg", MeasurementUnit.DEGREES),
    ("shoulder-tilt", "shoulder_tilt_delta_deg"): _TargetSpec("shoulder_tilt_delta_deg", TargetComparator.ALL_GTE, None, MeasurementUnit.DEGREES),
    ("balance", "finish_balance_sw"): _TargetSpec("finish_balance_sw", TargetComparator.ALL_LTE, "finish_balance_warn_sw", MeasurementUnit.SHOULDER_WIDTHS),
}


def _target_text(spec: _TargetSpec, threshold: float) -> str:
    unit = {MeasurementUnit.RATIO: ":1", MeasurementUnit.DEGREES: " degrees", MeasurementUnit.SHOULDER_WIDTHS: " shoulder widths"}[spec.unit]
    number = f"{threshold:g}{unit}"
    if spec.comparator in (TargetComparator.COUNT_GTE, TargetComparator.ALL_GTE):
        phrase = f"at or above {number}"
    else:
        phrase = f"at or below {number}"
    if spec.successes:
        return f"Reach {phrase} on {spec.successes[0]} of {spec.successes[1]} swings."
    return f"Keep the session {phrase}."


def build_refilm_target(
    brief: CaddieBrief,
    issues: Sequence[IssueCard],
    strengths: Sequence[StrengthCard],
    cfg: Config,
) -> RefilmTarget:
    """Build targets only from explicit metric mappings and live config."""
    if brief.focus_flag is not None:
        selected = next((item for item in issues if item.flag == brief.focus_flag), None)
        if selected is None:
            raise UnsupportedRefilmTarget("The selected issue has no report card")
        pair = (selected.flag, selected.metric)
    elif brief.strength_key is not None:
        selected_strength = next((item for item in strengths if item.key == brief.strength_key), None)
        if selected_strength is None:
            raise UnsupportedRefilmTarget("The selected strength has no report card")
        pair = (selected_strength.key, selected_strength.metric)
    else:
        raise UnsupportedRefilmTarget("The report has no selected target")
    spec = _TARGET_SPECS.get(pair)
    if spec is None:
        raise UnsupportedRefilmTarget(f"No explicit re-film target for {pair!r}")
    threshold = 0.0 if spec.coach_key is None else float(cfg.coaching[spec.coach_key])
    successes, attempts = spec.successes if spec.successes else (None, None)
    return RefilmTarget(
        _target_text(spec, threshold), spec.metric_id, spec.comparator, threshold,
        None, spec.unit, successes, attempts, TargetWindow.SESSION,
    )


def _ordered_reasons(reasons: Sequence[ReasonCode]) -> tuple[ReasonCode, ...]:
    present = set(reasons)
    return tuple(reason for reason in _REASON_ORDER if reason in present)


def _context(source: ReportContextInput, readable: int) -> ReportContext:
    angle = Angle(source.angle)
    hand = Hand(source.hand)
    club_label = None if source.club is None else ({"7i": "7 iron", "6i": "6 iron"}.get(source.club.lower(), source.club))
    return ReportContext(source.club, club_label, hand, angle, "Face-on" if angle is Angle.FACE_ON else "Down the line", source.detected_swings, readable, source.analysis_fps)


def _capture_only(source: ReportPresentationInput, context: ReportContext, reasons: tuple[ReasonCode, ...]) -> CaptureOnlyReportView:
    primary = next((reason for reason in reasons if reason in _FATAL_REASONS), ReasonCode.PRIORITY_EVIDENCE_UNRELIABLE)
    copy = REASON_COPY[primary]
    allowed = tuple(key for key in source.safe_media_keys if any(media.key == key for media in source.media))
    media = tuple(media for media in source.media if media.key in allowed)
    guidance = CaptureGuidance(primary, copy.label, copy.explanation, copy.remediation, ("Place the phone on a stable support.", "Keep the full body and club visible from address through finish.", "Record one clear swing in even light."), allowed, "refilm", "Re-film a clear swing", "choose_video", "Choose another clip")
    return CaptureOnlyReportView(
        "report-view-v1", "structured", GUIDED_REPORT_PRESENTATION_VERSION,
        ReportOutcome.CAPTURE_ONLY, JourneyMode.CAPTURE_RETRY,
        Trust(TrustState.REFILM_REQUIRED, copy.label, reasons, copy.explanation),
        context, Capabilities(True, False, False, False, False, False, False, False, True),
        media, (), None, None, (), None, None, guidance,
    )


def _phase_for_metric(metric: str) -> PhaseId:
    if metric in {"head_sway_backswing_sw", "hip_slide_backswing_sw"}:
        return PhaseId.GOING_BACK
    if metric in {"tempo_ratio", "tempo_ratio_std"}:
        return PhaseId.TRANSITION_DOWNSWING
    if metric in {"head_dip_sw", "lead_arm_angle_deg", "shoulder_tilt_impact_deg", "shoulder_tilt_delta_deg"}:
        return PhaseId.IMPACT
    return PhaseId.FINISH


@dataclass(frozen=True)
class _MetricMetadata:
    label: str
    phase: PhaseId
    unit: MeasurementUnit
    benchmark_relation: BenchmarkRelation
    coach_key: str | None
    formatter: str
    explanation: str
    limitation: str


_METRICS: Mapping[str, _MetricMetadata] = {
    "stance_width_sw": _MetricMetadata("Stance width", PhaseId.SETUP, MeasurementUnit.SHOULDER_WIDTHS, BenchmarkRelation.CONTEXT_ONLY, None, "{:.2f} shoulder widths", "This is your setup reference for comparable re-films.", "Setup context only; it is not graded good or bad."),
    "backswing_s": _MetricMetadata("Backswing time", PhaseId.GOING_BACK, MeasurementUnit.SECONDS, BenchmarkRelation.NONE, None, "{:.2f} seconds", "This is the measured time from takeaway to the top.", "Timing is estimated from the detected swing events."),
    "head_sway_backswing_sw": _MetricMetadata("Head movement going back", PhaseId.GOING_BACK, MeasurementUnit.SHOULDER_WIDTHS, BenchmarkRelation.ABOVE, "sway_warn_sw", "{:.2f} shoulder widths", "This is the head's sideways movement from setup to the top.", "Single-camera measurement; re-film face-on for this detail."),
    "hip_slide_backswing_sw": _MetricMetadata("Hip movement going back", PhaseId.GOING_BACK, MeasurementUnit.SHOULDER_WIDTHS, BenchmarkRelation.ABOVE, "sway_warn_sw", "{:.2f} shoulder widths", "This is the hips' sideways movement from setup to the top.", "Single-camera measurement; re-film face-on for this detail."),
    "tempo_ratio": _MetricMetadata("Tempo ratio", PhaseId.TRANSITION_DOWNSWING, MeasurementUnit.RATIO, BenchmarkRelation.BELOW, "tempo_warn_below", "{:.1f}:1", "This compares backswing time with downswing time.", "Timing is estimated from the detected swing events."),
    "tempo_ratio_std": _MetricMetadata("Tempo consistency", PhaseId.TIMING_RHYTHM, MeasurementUnit.RATIO, BenchmarkRelation.BELOW, "tempo_std_praise", "{:.2f}", "This is the swing-to-swing spread in tempo ratio.", "Requires more than one readable swing."),
    "downswing_s": _MetricMetadata("Downswing time", PhaseId.TRANSITION_DOWNSWING, MeasurementUnit.SECONDS, BenchmarkRelation.NONE, None, "{:.2f} seconds", "This is the measured time from the top to estimated impact.", "Timing is estimated from the detected swing events."),
    "downswing_hand_speed_sw_s": _MetricMetadata("Hand movement", PhaseId.TRANSITION_DOWNSWING, MeasurementUnit.SHOULDER_WIDTHS_PER_SECOND, BenchmarkRelation.CONTEXT_ONLY, None, "{:.2f} shoulder widths per second", "This is projected hand movement during the downswing.", "Context only, not clubhead speed or ball speed."),
    "strike_s": _MetricMetadata("Estimated impact", PhaseId.IMPACT, MeasurementUnit.SECONDS, BenchmarkRelation.NONE, None, "{:.2f} seconds", "This is the estimated time of impact in the swing window.", "Impact is estimated, not directly observed."),
    "head_dip_sw": _MetricMetadata("Head dip", PhaseId.IMPACT, MeasurementUnit.SHOULDER_WIDTHS, BenchmarkRelation.ABOVE, "head_dip_warn_sw", "{:.2f} shoulder widths", "This is the measured head drop from setup to impact.", "Single-camera measurement; re-film face-on for this detail."),
    "lead_arm_angle_deg": _MetricMetadata("Lead-arm shape", PhaseId.IMPACT, MeasurementUnit.DEGREES, BenchmarkRelation.BELOW, "lead_arm_warn_deg", "{:.0f} degrees", "This is the lead-arm angle at estimated impact.", "Camera-view angle only; it is not a 3D joint angle."),
    "shoulder_tilt_impact_deg": _MetricMetadata("Shoulder tilt", PhaseId.IMPACT, MeasurementUnit.DEGREES, BenchmarkRelation.BELOW, "shoulder_tilt_impact_min_deg", "{:.0f} degrees", "This is shoulder tilt at estimated impact.", "Camera-view angle only; it is not a 3D joint angle."),
    "finish_balance_sw": _MetricMetadata("Finish-base stability", PhaseId.FINISH, MeasurementUnit.SHOULDER_WIDTHS, BenchmarkRelation.ABOVE, "finish_balance_warn_sw", "{:.2f} shoulder widths", "This is ankle-midpoint drift during the finish hold.", "It measures base drift, not every foot movement or pressure shift."),
}

_FACE_ON_PHASES = (PhaseId.SETUP, PhaseId.GOING_BACK, PhaseId.TRANSITION_DOWNSWING, PhaseId.IMPACT, PhaseId.FINISH)
_DTL_METRICS = ("backswing_s", "downswing_s", "tempo_ratio", "tempo_ratio_std")
_PHASE_LABELS = {PhaseId.SETUP: "Setup", PhaseId.GOING_BACK: "Going back", PhaseId.TRANSITION_DOWNSWING: "Transition & downswing", PhaseId.IMPACT: "Impact", PhaseId.FINISH: "Finish", PhaseId.TIMING_RHYTHM: "Timing & rhythm"}
_LATERAL_METRICS = frozenset({"head_sway_backswing_sw", "hip_slide_backswing_sw"})


def _mean_metric(metric_id: str, metrics: Sequence[SwingMetrics], stats: Mapping[str, Mapping[str, float]]) -> float | None:
    if metric_id == "tempo_ratio_std":
        return finite_float(stats.get("tempo_ratio", {}).get("std"))
    stat_value = finite_float(stats.get(metric_id, {}).get("mean"))
    if stat_value is not None:
        return stat_value
    values = [value for metric in metrics if (value := finite_float(getattr(metric, metric_id, None))) is not None]
    return math.fsum(values) / len(values) if values else None


def measurement_detail(metric_id: str, metrics: Sequence[SwingMetrics], stats: Mapping[str, Mapping[str, float]], cfg: Config, *, angle: str = "face_on") -> MeasurementDetail | None:
    """Present one supported metric without re-running motion analysis."""
    if angle == "dtl" and metric_id not in _DTL_METRICS:
        return None
    meta = _METRICS.get(metric_id)
    if meta is None:
        return None
    value = _mean_metric(metric_id, metrics, stats)
    if value is None:
        return MeasurementDetail(f"measurement-{metric_id}", meta.label, "Not measured", None, meta.unit, meta.benchmark_relation, None, None, None, "This measurement was not available from the readable swings.", meta.limitation)
    benchmark = finite_float(cfg.coaching.get(meta.coach_key)) if meta.coach_key else None
    benchmark_label = None if benchmark is None else f"Configured line: {benchmark:g}"
    return MeasurementDetail(f"measurement-{metric_id}", meta.label, meta.formatter.format(value), value, meta.unit, meta.benchmark_relation, benchmark, None, benchmark_label, meta.explanation, meta.limitation)


def _selected_metric(source: ReportPresentationInput) -> str | None:
    if source.brief.focus_flag is not None:
        selected = next((item for item in source.issues if item.flag == source.brief.focus_flag), None)
        return selected.metric if selected else None
    if source.brief.strength_key is not None:
        selected = next((item for item in source.strengths if item.key == source.brief.strength_key), None)
        return selected.metric if selected else None
    return None


def _phase_status(source: ReportPresentationInput, phase: PhaseId, measurements: tuple[MeasurementDetail, ...], *, protect: bool) -> PhaseStatus:
    selected = _selected_metric(source)
    if protect and selected is not None and _phase_for_metric(selected) is phase:
        return PhaseStatus.STEADY
    if not any(detail.numeric_value is not None for detail in measurements):
        return PhaseStatus.NOT_MEASURED
    if not protect and selected is not None and _phase_for_metric(selected) is phase:
        return PhaseStatus.PRIORITY
    if any(item.metric != selected and _METRICS.get(item.metric, None) and _METRICS[item.metric].phase is phase for item in source.issues):
        return PhaseStatus.REVIEW_LATER
    if all(detail.benchmark_relation is BenchmarkRelation.CONTEXT_ONLY for detail in measurements if detail.numeric_value is not None):
        return PhaseStatus.BASELINE
    return PhaseStatus.STEADY


def build_phase_summaries(source: ReportPresentationInput, cfg: Config) -> tuple[PhaseSummary, ...]:
    """Map supported facts into the fixed camera-angle phase layout."""
    is_dtl = source.context.angle == "dtl"
    phase_ids = (PhaseId.TIMING_RHYTHM,) if is_dtl else _FACE_ON_PHASES
    protect = source.brief.focus_flag is None
    selected = _selected_metric(source)
    uncertain_direction = any(not metric.target_confident for metric in source.swings)
    summaries: list[PhaseSummary] = []
    for phase in phase_ids:
        metric_ids = _DTL_METRICS if is_dtl else tuple(metric_id for metric_id, meta in _METRICS.items() if meta.phase is phase)
        details = tuple(detail for metric_id in metric_ids if (detail := measurement_detail(metric_id, source.swings, source.stats, cfg, angle=source.context.angle)) is not None)
        status = _phase_status(source, phase, details, protect=protect)
        unavailable: list[ReasonCode] = []
        supported = any(detail.numeric_value is not None for detail in details)
        if supported and any(detail.numeric_value is None for detail in details):
            unavailable.append(ReasonCode.SECONDARY_METRIC_UNAVAILABLE)
        if uncertain_direction and any(metric_id in _LATERAL_METRICS for metric_id in metric_ids):
            unavailable.append(ReasonCode.TARGET_DIRECTION_UNCERTAIN)
        expanded = (selected is not None and ((not is_dtl and _phase_for_metric(selected) is phase) or (is_dtl and selected in _DTL_METRICS)))
        if status is PhaseStatus.PRIORITY:
            status_label, summary = "Priority", "Work on this movement."
        elif protect and expanded:
            status_label, summary = "Steady", "Strength to protect. Keep this movement familiar."
        elif status is PhaseStatus.REVIEW_LATER:
            status_label, summary = "Review later", "A secondary measured issue is available to revisit."
        elif status is PhaseStatus.BASELINE:
            status_label, summary = "Baseline", "Context for comparable re-films."
        elif status is PhaseStatus.NOT_MEASURED:
            status_label, summary = "Not measured", "This phase was not measured from the readable swings."
        else:
            status_label, summary = "Steady", "Measured values are steady."
        summaries.append(PhaseSummary(phase, _PHASE_LABELS[phase], status, status_label, summary, len(source.swings), details, tuple(unavailable), f"phase-{phase.value}", expanded))
    return tuple(summaries)


def _practice(
    drill: Drill, alternatives: Sequence[Drill], cfg: Config
) -> PracticePrescription:
    presentation = drill_presentation(drill, cfg)
    return PracticePrescription(
        "practice", drill.id, drill.name, drill.aim,
        presentation.summary_steps, tuple(drill.protocol), presentation.setup,
        presentation.feel_cue, drill.dosage, presentation.equipment, None, None,
        tuple(
            DrillAlternative(item.id, item.name, item.aim, "alternative-drills")
            for item in alternatives
        ),
    )


def build_report_view(source: ReportPresentationInput, cfg: Config) -> ReportViewV1:
    """Turn trusted server decisions into a complete, typed report union."""
    reasons = _ordered_reasons(source.reason_codes)
    selected_issue = next((item for item in source.issues if item.flag == source.brief.focus_flag), None)
    selected_strength = next((item for item in source.strengths if item.key == source.brief.strength_key), None)
    selected_metric = selected_issue.metric if selected_issue else selected_strength.metric if selected_strength else None
    readable = source.visual_evidence.readable_swings if source.visual_evidence is not None else 0
    context = _context(source.context, readable)
    priority_missing = source.brief.refilm_required or selected_metric is None or source.visual_evidence is None or source.visual_evidence.tracking_state is TrackingState.UNAVAILABLE
    if priority_missing and ReasonCode.PRIORITY_EVIDENCE_UNRELIABLE not in reasons:
        reasons = _ordered_reasons((*reasons, ReasonCode.PRIORITY_EVIDENCE_UNRELIABLE))
    if any(reason in _FATAL_REASONS for reason in reasons):
        return _capture_only(source, context, reasons)

    assert selected_metric is not None and source.visual_evidence is not None
    mode = JourneyMode.IMPROVE if selected_issue is not None else JourneyMode.PROTECT
    if selected_issue is not None:
        next_move = NextMove(mode, selected_issue.flag, _phase_for_metric(selected_issue.metric), "Work on now", selected_issue.display_name, selected_issue.why, selected_issue.fix, None, "practice", "refilm")
    else:
        assert selected_strength is not None
        next_move = NextMove(mode, selected_strength.key, _phase_for_metric(selected_strength.metric), "Protect this", selected_strength.display_name, selected_strength.text, "Repeat the same motion under the same setup.", None, "practice", "refilm")
    limited = bool(reasons)
    label = REASON_COPY[reasons[0]].label if limited else "Clear read"
    target = build_refilm_target(source.brief, source.issues, source.strengths, cfg)
    protocol = RefilmProtocol("refilm", ("Use the same club, hand, camera angle, height, framing, and effort.",), target, "Re-film this drill", source.context.club is not None, True, True, True, True, True)
    return CoachingReportView(
        "report-view-v1", "structured", GUIDED_REPORT_PRESENTATION_VERSION,
        ReportOutcome.COACHING_READY, mode,
        Trust(TrustState.LIMITED if limited else TrustState.CLEAR, label, reasons, REASON_COPY[reasons[0]].explanation if limited else None),
        context,
        Capabilities(True, source.visual_evidence.state == "rendered", False, False, False, True, bool(source.alternative_drills), False, True),
        tuple(source.media), (), next_move, source.visual_evidence,
        build_phase_summaries(source, cfg), _practice(source.primary_drill, source.alternative_drills, cfg), protocol,
    )
