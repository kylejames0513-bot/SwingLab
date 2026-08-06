"""Pure choices for the guided report's priority evidence.

This module maps the already-selected coaching priority to visual evidence;
it deliberately does not inspect swing pixels or landmarks.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Literal, Sequence, TypeAlias

from .caddie_brief import CaddieBrief
from .coaching import IssueCard
from .config import Config
from .report_view import EvidenceKind, EventId, PhaseId


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
