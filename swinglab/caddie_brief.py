"""A concise, evidence-first coaching summary for one measured session.

The report engine already knows how to identify issues, praise measurements
that are inside their reference lines, and select drills.  This module turns
those existing facts into one next action without adding another model,
inventing golfer attributes, or persisting a second source of truth.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Iterable, Mapping

from .coaching import (
    DTL_SESSION_NOTE,
    IssueCard,
    issue_cards,
    strength_cards,
)
from .config import Config
from .drills import Drill, practice_plan
from .metrics import (
    ANGLE_DTL,
    FACE_ON_ONLY_FIELDS,
    SwingMetrics,
    finite_float,
    session_stats,
)


_COACH_FIELDS = (
    "tempo_ratio",
    "head_sway_backswing_sw",
    "hip_slide_backswing_sw",
    "head_dip_sw",
    "lead_arm_angle_deg",
    "shoulder_tilt_impact_deg",
    "shoulder_tilt_delta_deg",
    "finish_balance_sw",
)

_CLUB_CONTEXT_UNSET = object()


@dataclass(frozen=True)
class CaddieBrief:
    """The smallest useful coaching decision for a completed session."""

    strength: str | None
    strength_key: str | None
    focus_flag: str | None
    focus_name: str
    focus_value: str | None
    benchmark_text: str | None
    why: str
    fix: str
    drill: Drill | None
    trend: str | None
    warning: str | None
    recurring_sessions: int
    remaining_issues: int
    clean: bool
    refilm_required: bool


def build_caddie_brief(
    all_metrics: list[SwingMetrics],
    stats: dict[str, dict[str, float]],
    cfg: Config,
    *,
    previous_flag_counts: Mapping[str, int] | None = None,
    trend: str | None = None,
    warning: str | None = None,
    angle: str | None = None,
    club: str | None = None,
    rule_version: int | None = None,
) -> CaddieBrief | None:
    """Build one honest next step from already-measured session data.

    The configured, versioned report priority is preserved so the results page
    and static report can never contradict each other. Comparable history adds
    recurrence and trend context, but does not silently change the current
    session's first action. No measurable coaching fields means no brief rather
    than a fabricated clean bill of health.
    """

    if warning_requires_refilm(warning):
        return _refilm_brief(warning)

    if angle == ANGLE_DTL:
        all_metrics = scope_metrics_for_angle(all_metrics, angle)
        stats = session_stats(all_metrics)

    if not all_metrics or not any(
        not math.isnan(getattr(metric, field))
        for metric in all_metrics
        for field in _COACH_FIELDS
    ):
        return _refilm_brief(
            "This clip did not produce enough readable motion data to choose "
            "a swing change."
        )

    cards = issue_cards(
        all_metrics,
        stats,
        cfg,
        club=club,
        rule_version=rule_version,
    )
    strengths = strength_cards(all_metrics, cfg, stats)
    prior = previous_flag_counts or {}

    focus: IssueCard | None = cards[0] if cards else None

    plan = practice_plan([focus.flag] if focus else [], cfg)
    drill = plan[0]["drills"][0]

    if focus is None:
        dtl = angle == ANGLE_DTL
        tempo_measured = any(
            not math.isnan(metric.tempo_ratio) for metric in all_metrics
        )
        full_baseline = tempo_measured and all(
            any(not math.isnan(getattr(metric, field)) for metric in all_metrics)
            for field in (
                "head_sway_backswing_sw",
                "hip_slide_backswing_sw",
            )
        )
        limited_baseline = dtl or not full_baseline
        if limited_baseline and tempo_measured:
            maintenance_drill = rhythm_maintenance_drill(cfg)
        elif limited_baseline:
            maintenance_drill = readability_maintenance_drill()
        else:
            maintenance_drill = drill
        return CaddieBrief(
            strength=strengths[0].text if strengths else None,
            strength_key=strengths[0].key if strengths else None,
            focus_flag=None,
            focus_name=(
                "Protect your tempo baseline"
                if limited_baseline and tempo_measured
                else "Complete your baseline"
                if limited_baseline
                else "Protect this baseline"
            ),
            focus_value=None,
            benchmark_text=None,
            why=(
                (
                    "No tempo issue crossed its coaching line in this "
                    "down-the-line session. This angle does not support honest "
                    "sway or slide maintenance checks yet."
                )
                if dtl
                else (
                    "The readable measurements stayed inside their coaching "
                    "lines, but this session did not produce a complete "
                    "tempo, sway and slide baseline."
                )
                if limited_baseline
                else (
                    "No measured issue crossed its coaching line in this "
                    "session. Keep the motion familiar and re-film under the "
                    "same setup so future drift is easy to spot."
                )
            ),
            fix=(
                (
                    "Repeat the rhythm check from this angle, or switch the "
                    "next baseline clip to face-on for body-motion coaching."
                )
                if dtl
                else (
                    "Keep the readable rhythm, then re-film face-on for a "
                    "complete body-motion baseline."
                )
                if limited_baseline and tempo_measured
                else (
                    "Re-film face-on with the full body visible before treating "
                    "this as a maintenance baseline."
                )
                if limited_baseline
                else "Run the maintenance drill, then keep the baseline current."
            ),
            drill=maintenance_drill,
            trend=trend,
            warning=warning,
            recurring_sessions=0,
            remaining_issues=0,
            clean=True,
            refilm_required=False,
        )

    return CaddieBrief(
        strength=strengths[0].text if strengths else None,
        strength_key=None,
        focus_flag=focus.flag,
        focus_name=focus.display_name,
        focus_value=_focus_value(focus, all_metrics),
        benchmark_text=focus.benchmark_text,
        why=focus.why,
        fix=focus.fix,
        drill=drill,
        trend=trend,
        warning=warning,
        recurring_sessions=int(prior.get(focus.flag, 0)) + 1,
        remaining_issues=max(0, len(cards) - 1),
        clean=False,
        refilm_required=False,
    )


def build_caddie_brief_from_payload(
    payload: dict,
    cfg: Config,
    *,
    previous_flag_counts: Mapping[str, int] | None = None,
    trend: str | None = None,
    angle: str | None = None,
    club: object = _CLUB_CONTEXT_UNSET,
    rule_version: int | None = None,
) -> CaddieBrief | None:
    """Build a brief from an existing ``metrics.json`` payload.

    Partial and older payloads are tolerated.  Missing values stay missing;
    nothing is guessed to make a card render.
    """

    resolved_angle = _camera_angle(payload, angle)
    resolved_club = _club_context(payload, club)
    warning = quality_warning_from_payload(payload, resolved_angle)
    metrics = metrics_from_payload(payload)
    brief = build_caddie_brief(
        metrics,
        session_stats(metrics),
        cfg,
        previous_flag_counts=previous_flag_counts,
        trend=trend,
        warning=warning,
        angle=resolved_angle,
        club=resolved_club,
        rule_version=rule_version,
    )
    return brief


def metrics_from_payload(payload: dict) -> list[SwingMetrics]:
    """Return the readable per-swing metrics stored in ``metrics.json``.

    This is intentionally public because the result surface, trends, and
    Proof Cycle must all adapt the persisted measurements identically.  It
    does not invent values for partial or legacy payloads.
    """
    rows: list[SwingMetrics] = []
    swings = payload.get("swings") or []
    if not isinstance(swings, list):
        return rows
    for index, swing in enumerate(swings, start=1):
        if not isinstance(swing, dict):
            continue
        raw = swing.get("metrics") or {}
        if not isinstance(raw, dict):
            continue
        swing_number = finite_float(raw.get("swing"))
        measured = SwingMetrics(
            swing=(
                int(swing_number)
                if swing_number is not None
                else index
            ),
            strike_s=_number(raw, "strike_s"),
            backswing_s=_number(raw, "backswing_s"),
            downswing_s=_number(raw, "downswing_s"),
            tempo_ratio=_number(raw, "tempo_ratio"),
            head_sway_backswing_sw=_number(raw, "head_sway_backswing_sw"),
            head_sway_downswing_sw=_number(raw, "head_sway_downswing_sw"),
            hip_slide_backswing_sw=_number(raw, "hip_slide_backswing_sw"),
            hip_slide_downswing_sw=_number(raw, "hip_slide_downswing_sw"),
            target_direction=-1 if raw.get("target_direction") == -1 else 1,
            head_dip_sw=_number(raw, "head_dip_sw"),
            lead_arm_angle_deg=_number(raw, "lead_arm_angle_deg"),
            shoulder_tilt_address_deg=_number(
                raw, "shoulder_tilt_address_deg"
            ),
            shoulder_tilt_impact_deg=_number(
                raw, "shoulder_tilt_impact_deg"
            ),
            shoulder_tilt_delta_deg=_number(
                raw, "shoulder_tilt_delta_deg"
            ),
            finish_balance_sw=_number(raw, "finish_balance_sw"),
            target_confident=raw.get("target_confident") is not False,
            stance_width_sw=_number(raw, "stance_width_sw"),
            downswing_hand_speed_sw_s=_number(
                raw, "downswing_hand_speed_sw_s"
            ),
        )
        if any(
            not math.isnan(getattr(measured, field))
            for field in _COACH_FIELDS
        ):
            rows.append(measured)
    return rows


def _number(raw: dict, key: str) -> float:
    value = finite_float(raw.get(key))
    return value if value is not None else float("nan")


def scope_metrics_for_angle(
    all_metrics: list[SwingMetrics], angle: str | None
) -> list[SwingMetrics]:
    """Remove measurements the selected camera angle cannot support."""
    if angle != ANGLE_DTL:
        return all_metrics
    unavailable = {
        field: float("nan") for field in FACE_ON_ONLY_FIELDS
    }
    return [replace(metric, **unavailable) for metric in all_metrics]


def quality_warning_from_payload(
    payload: dict, angle: str | None = None
) -> str | None:
    angle = _camera_angle(payload, angle)
    def note_list(value: object) -> list:
        if isinstance(value, str):
            return [value]
        return list(value) if isinstance(value, (list, tuple)) else []

    notes = note_list(payload.get("session_notes"))
    swings = payload.get("swings")
    if not isinstance(swings, list):
        swings = []
    notes.extend(
        [
        note
        for swing in swings
        if isinstance(swing, dict)
        for note in note_list(swing.get("notes"))
        ]
    )
    return quality_warning(angle, notes)


def _camera_angle(
    payload: dict, authoritative: str | None = None
) -> str | None:
    if isinstance(authoritative, str) and authoritative:
        return authoritative
    meta = payload.get("meta") or {}
    if not isinstance(meta, dict):
        return None
    angle = meta.get("angle") or meta.get("camera_angle")
    return angle if isinstance(angle, str) else None


def _club_context(payload: dict, authoritative: object) -> str | None:
    """Use explicit job context verbatim; only absent callers trust metadata."""

    if authoritative is not _CLUB_CONTEXT_UNSET:
        return (
            authoritative
            if isinstance(authoritative, str) and authoritative
            else None
        )
    meta = payload.get("meta") or {}
    if not isinstance(meta, dict):
        return None
    club = meta.get("club")
    return club if isinstance(club, str) and club else None


def rhythm_maintenance_drill(cfg: Config) -> Drill:
    """Maintenance action limited to a trustworthy tempo/rhythm read."""
    tempo_warn = float(cfg.coaching["tempo_warn_below"])
    return Drill(
        id="rhythm-baseline-refilm",
        name="Rhythm baseline re-film",
        aim="Keep the tempo this camera angle can measure honestly.",
        protocol=(
            "Use the same club and camera height; choose face-on when you want "
            "the full body-motion baseline.",
            "Make three swings with the same count and effort.",
            "Keep the rhythm identical from swing to swing.",
        ),
        dosage="3 swings, monthly",
        success_metric=(
            f"Keep every measured tempo ratio at or above {tempo_warn:.1f}:1."
        ),
        gear_tag="swinglab:tempo",
    )


def readability_maintenance_drill() -> Drill:
    """Capture action when clean-but-partial fields cannot form a baseline."""
    return Drill(
        id="readability-baseline-refilm",
        name="Complete baseline re-film",
        aim="Capture enough readable motion before treating this as a baseline.",
        protocol=(
            "Film face-on from hip height with the full body in frame.",
            "Use bright, even light and keep other people out of the view.",
            "Make three swings with the same club and camera position.",
        ),
        dosage="3 swings, next session",
        success_metric=(
            "The next report reads tempo plus sway and slide before setting a "
            "maintenance target."
        ),
        gear_tag="swinglab:general",
    )


def quality_warning(angle: str | None, notes: Iterable) -> str | None:
    """Return the strongest real scope/tracking caveat for a coach-first surface."""
    quality_notes = [
        note
        for note in notes
        if isinstance(note, str)
        and (
            "low confidence" in note.lower()
            or "tracking was unstable" in note.lower()
        )
    ]
    for note in quality_notes:
        if warning_requires_refilm(note):
            return note
    for note in quality_notes:
        if isinstance(note, str) and (
            "low confidence" in note.lower()
            or "tracking was unstable" in note.lower()
        ):
            return note
    if angle == "dtl":
        return DTL_SESSION_NOTE
    return None


def warning_requires_refilm(warning: str | None) -> bool:
    """True when measured coaching should stop until the golfer re-films."""
    if not warning:
        return False
    lowered = warning.lower()
    return (
        "numbers may not mean what they say" in lowered
        or "tracking was unstable" in lowered
        or "numbers may be off" in lowered
    )


def _refilm_brief(warning: str) -> CaddieBrief:
    """One safe next action when the measurements cannot support coaching."""
    return CaddieBrief(
        strength=None,
        strength_key=None,
        focus_flag=None,
        focus_name="Get a trustworthy baseline",
        focus_value=None,
        benchmark_text=None,
        why=(
            "This clip is not reliable enough to choose a swing change. "
            "Practicing from these numbers could send you in the wrong "
            "direction."
        ),
        fix=(
            "Check the selected camera angle, film your full body clearly "
            "from hip height, and upload the clip again."
        ),
        drill=None,
        trend=None,
        warning=warning,
        recurring_sessions=0,
        remaining_issues=0,
        clean=False,
        refilm_required=True,
    )


def payload_requires_refilm(
    payload: dict, *, angle: str | None = None
) -> bool:
    """Apply the same re-film decision to any persisted metrics payload."""
    return warning_requires_refilm(quality_warning_from_payload(payload, angle))


def payload_is_coaching_eligible(
    payload: dict,
    cfg: Config,
    *,
    angle: str | None = None,
    club: object = _CLUB_CONTEXT_UNSET,
    rule_version: int | None = None,
) -> bool:
    """True only when a persisted result can honestly power coaching."""
    brief = build_caddie_brief_from_payload(
        payload,
        cfg,
        angle=angle,
        club=club,
        rule_version=rule_version,
    )
    return brief is not None and not brief.refilm_required


def payload_structure_is_valid(payload: object) -> bool:
    """Whether a metrics payload is structurally safe to expose as JSON."""
    if not isinstance(payload, dict):
        return False
    swings = payload.get("swings")
    if not isinstance(swings, list):
        return False
    for swing in swings:
        if not isinstance(swing, dict):
            return False
        metrics = swing.get("metrics")
        if metrics is not None and not isinstance(metrics, dict):
            return False
    stats = payload.get("session_stats")
    return stats is None or isinstance(stats, dict)


def payload_has_coachable_data(
    payload: dict, *, angle: str | None = None
) -> bool:
    """Whether at least one field supported by the chosen angle is readable."""
    metrics = metrics_from_payload(payload)
    if _camera_angle(payload, angle) == ANGLE_DTL:
        return any(
            not math.isnan(metric.tempo_ratio) for metric in metrics
        )
    return bool(metrics)


def payload_has_unsupported_angle_data(
    payload: dict, *, angle: str | None = None
) -> bool:
    """Whether raw JSON contains values this camera angle cannot support.

    Coaching can still use the supported tempo fields, but the raw payload is
    withheld so stale legacy face-on values cannot contradict a DTL report.
    """
    if _camera_angle(payload, angle) != ANGLE_DTL:
        return False
    swings = payload.get("swings")
    if not isinstance(swings, list):
        return False
    if any(
        field in metrics and metrics[field] is not None
        for swing in swings
        if isinstance(swing, dict)
        for metrics in [swing.get("metrics")]
        if isinstance(metrics, dict)
        for field in FACE_ON_ONLY_FIELDS
    ):
        return True
    raw_stats = payload.get("session_stats") or {}
    if not isinstance(raw_stats, dict):
        return False
    return any(
        field in raw_stats and raw_stats[field] not in (None, {})
        for field in FACE_ON_ONLY_FIELDS
    )


def _focus_value(card: IssueCard, metrics: list[SwingMetrics]) -> str:
    """Lead with the breached swing when a safe mean hides the actual flag."""
    measured = [
        (index, value)
        for index, value in enumerate(card.per_swing)
        if value is not None
    ]

    def named_worst(text: str) -> str:
        if not measured:
            return text
        worst_index, _ = (
            max(measured, key=lambda item: item[1])
            if card.worse_direction == "higher"
            else min(measured, key=lambda item: item[1])
        )
        swing_number = (
            metrics[worst_index].swing
            if worst_index < len(metrics)
            else worst_index + 1
        )
        return f"Swing {swing_number}: {text}"

    if card.session_label == "worst swing":
        return named_worst(card.session_text)
    if card.benchmark_value is None or card.session_value is None:
        return card.session_text
    mean_breaches = (
        card.session_value > card.benchmark_value
        if card.worse_direction == "higher"
        else card.session_value < card.benchmark_value
    )
    if mean_breaches:
        return card.session_text
    if not measured:
        return card.session_text
    worst = (
        max(value for _, value in measured)
        if card.worse_direction == "higher"
        else min(value for _, value in measured)
    )
    breached = (
        worst > card.benchmark_value
        if card.worse_direction == "higher"
        else worst < card.benchmark_value
    )
    if not breached:
        return card.session_text
    if card.unit == "SW":
        text = f"{worst:.2f} SW"
    elif card.unit == ":1":
        text = f"{worst:.2f}:1"
    elif card.unit == "\N{DEGREE SIGN}":
        text = f"{worst:.0f}\N{DEGREE SIGN}"
    else:
        text = f"{worst:g}{card.unit}"
    return named_worst(text)
