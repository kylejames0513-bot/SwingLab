"""Plain-English, evidence-labeled interpretation of measured swing data.

The coaching engine remains the source of truth for the one priority and its
drill. This presenter explains the rest of the paid report without adding
unsupported diagnoses or turning context-only measurements into grades.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import Config
from .metrics import ANGLE_DTL, SwingMetrics, finite_float


@dataclass(frozen=True)
class SwingBreakdownCard:
    """One novice-facing answer about a measured part of the swing."""

    key: str
    title: str
    evidence: str
    status: str
    tone: str
    summary: str
    why: str
    limit: str = ""


def _values(metrics: list[SwingMetrics], field: str) -> list[float]:
    return [
        value
        for metric in metrics
        if (value := finite_float(getattr(metric, field, None))) is not None
    ]


def _average(values: list[float]) -> float:
    return math.fsum(value / len(values) for value in values)


def _range_sentence(values: list[float], fmt: str, noun: str) -> str:
    if len(values) < 2:
        return ""
    low, high = min(values), max(values)
    if math.isclose(low, high, abs_tol=0.005):
        return f" It was nearly the same in every readable {noun}."
    return f" Across the readable {noun}s, it ranged from {fmt.format(low)} to {fmt.format(high)}."


def _unavailable(key: str, title: str, evidence: str, why: str) -> SwingBreakdownCard:
    return SwingBreakdownCard(
        key=key,
        title=title,
        evidence=evidence,
        status="Not measured",
        tone="unavailable",
        summary=(
            "This clip did not provide enough trustworthy frames for this "
            "part of the breakdown."
        ),
        why=why,
        limit="The report leaves an unreadable value blank instead of guessing.",
    )


def _rhythm_card(
    metrics: list[SwingMetrics], cfg: Config
) -> SwingBreakdownCard:
    values = _values(metrics, "tempo_ratio")
    if not values:
        return _unavailable(
            "rhythm",
            "Swing rhythm",
            "Timing estimate",
            "Rhythm helps the backswing and downswing work as one repeatable motion.",
        )
    average = _average(values)
    threshold = float(cfg.coaching["tempo_warn_below"])
    needs_attention = any(value < threshold for value in values)
    summary = (
        f"Your backswing lasted about {average:.2f} times as long as your "
        "downswing. In plain terms, that is what the rhythm number means."
        + _range_sentence(values, "{:.2f}-to-1", "swing")
    )
    if needs_attention:
        summary += " At least one swing changed direction sooner than the coaching line."
    else:
        summary += " Every readable swing stayed on the steady side of the coaching line."
    return SwingBreakdownCard(
        key="rhythm",
        title="Swing rhythm",
        evidence="Timing estimate",
        status="Needs attention" if needs_attention else "Looking steady",
        tone="warning" if needs_attention else "good",
        summary=summary,
        why=(
            "A repeatable rhythm gives the body and club time to finish going "
            "back before they change direction — that is what keeps the strike "
            "from becoming a last-second save."
        ),
        limit=(
            "This describes timing from phone video, not how hard you swung "
            "or how fast the clubhead moved."
        ),
    )


def _stance_card(
    metrics: list[SwingMetrics], selected_club: str | None
) -> SwingBreakdownCard:
    values = _values(metrics, "stance_width_sw")
    if not values:
        return _unavailable(
            "stance",
            "Setup and stance",
            "Observed in this view",
            "A repeatable starting base makes follow-up videos easier to compare.",
        )
    average = _average(values)
    if average < 0.80:
        description = "narrower than your shoulders"
    elif average <= 1.20:
        description = "about shoulder-width apart"
    else:
        description = "wider than your shoulders"
    club_context = f" for the {selected_club}" if selected_club else ""
    return SwingBreakdownCard(
        key="stance",
        title="Setup and stance",
        evidence="Observed in this view",
        status="Setup baseline",
        tone="context",
        summary=(
            f"Your feet started {average:.2f} shoulder-widths apart — "
            f"{description}. This is your repeatable setup baseline{club_context}."
            + _range_sentence(values, "{:.2f}", "swing")
        ),
        why=(
            "Starting from the same base makes it easier to tell whether a "
            "practice change actually helped."
        ),
        limit=(
            "Stance width is context, not a pass/fail score. The useful width "
            "changes with the golfer, club, and intended shot, and video cannot "
            "measure pressure under the feet."
        ),
    )


def _signed_move(value: float) -> str:
    direction = "away from the target" if value >= 0 else "toward the target"
    return f"{abs(value):.2f} shoulder-widths {direction}"


def _lateral_move(values: list[float], *, direction_known: bool) -> str:
    if direction_known:
        return _signed_move(_average(values))
    magnitude = _average([abs(value) for value in values])
    return f"{magnitude:.2f} shoulder-widths sideways"


def _body_control_card(
    metrics: list[SwingMetrics], cfg: Config
) -> SwingBreakdownCard:
    head = _values(metrics, "head_sway_backswing_sw")
    hips = _values(metrics, "hip_slide_backswing_sw")
    dip = _values(metrics, "head_dip_sw")
    if not (head or hips or dip):
        return _unavailable(
            "body-control",
            "Body control",
            "Observed in this view",
            "Body movement shows how much timing was needed to return to the ball.",
        )
    threshold = float(cfg.coaching["sway_warn_sw"])
    dip_threshold = float(cfg.coaching["head_dip_warn_sw"])
    contributing = [
        metric
        for metric in metrics
        if any(
            finite_float(getattr(metric, field, None)) is not None
            for field in ("head_sway_backswing_sw", "hip_slide_backswing_sw")
        )
    ]
    direction_known = all(metric.target_confident for metric in contributing)
    lateral_attention = (
        any(value > threshold for value in head)
        or any(value > threshold for value in hips)
    )
    dip_attention = any(value > dip_threshold for value in dip)
    needs_attention = dip_attention or (direction_known and lateral_attention)
    observations: list[str] = []
    if head:
        observations.append(
            f"your head moved {_lateral_move(head, direction_known=direction_known)} "
            "going back"
        )
    if hips:
        observations.append(
            f"your hips moved {_lateral_move(hips, direction_known=direction_known)} "
            "going back"
        )
    if dip:
        observations.append(
            f"your head lowered {_average(dip):.2f} shoulder-widths by impact"
        )
    summary = "On average, " + "; ".join(observations) + "."
    if needs_attention:
        summary += " At least one movement crossed its coaching line."
    elif not direction_known:
        summary += (
            " The target line was not clear enough to label the movement's "
            "direction, so it is left unspecified."
        )
    else:
        summary += " The readable movements stayed inside their coaching lines."
    status = (
        "Needs attention"
        if needs_attention
        else "Direction uncertain"
        if not direction_known
        else "Looking steady"
    )
    tone = "warning" if needs_attention else "context" if not direction_known else "good"
    return SwingBreakdownCard(
        key="body-control",
        title="Body control",
        evidence="Observed in this view",
        status=status,
        tone=tone,
        summary=summary,
        why=(
            "Large shifts make returning to the same strike area more dependent "
            "on last-second timing. Smaller, quieter movement is easier to "
            "repeat under the same camera setup."
        ),
        limit=(
            "These are sideways and vertical movements in a face-on phone "
            "image. They do not measure body rotation, weight transfer, or "
            "ground force. When target direction is uncertain, the report "
            "leaves toward/away labels off instead of guessing."
        ),
    )


def _impact_card(
    metrics: list[SwingMetrics], cfg: Config
) -> SwingBreakdownCard:
    arm = _values(metrics, "lead_arm_angle_deg")
    tilt = _values(metrics, "shoulder_tilt_impact_deg")
    tilt_change = _values(metrics, "shoulder_tilt_delta_deg")
    if not (arm or tilt or tilt_change):
        return _unavailable(
            "impact",
            "Impact body shape",
            "Estimated impact frame",
            "The body shape near impact helps connect a coaching cue to a visible frame.",
        )
    arm_threshold = float(cfg.coaching["lead_arm_warn_deg"])
    tilt_threshold = float(cfg.coaching["shoulder_tilt_impact_min_deg"])
    needs_attention = (
        any(value < arm_threshold for value in arm)
        or any(value < tilt_threshold for value in tilt)
        or any(value < 0 for value in tilt_change)
    )
    observations: list[str] = []
    if arm:
        average_arm = _average(arm)
        if average_arm >= 170:
            shape = "nearly straight"
        elif average_arm >= 150:
            shape = "slightly bent"
        else:
            shape = "clearly bent"
        observations.append(
            f"your lead arm was {average_arm:.0f} degrees, or {shape}"
        )
    if tilt:
        average_tilt = _average(tilt)
        if average_tilt >= 0:
            observations.append(
                f"your trail shoulder was about {average_tilt:.0f} degrees lower"
            )
        else:
            observations.append(
                f"your trail shoulder was about {abs(average_tilt):.0f} degrees higher"
            )
    summary = "Near the likely strike frame, " + "; ".join(observations) + "."
    if needs_attention:
        summary += " At least one swing moved outside an impact-shape coaching line."
    else:
        summary += " The readable positions stayed inside their coaching lines."
    return SwingBreakdownCard(
        key="impact",
        title="Impact body shape",
        evidence="Estimated impact frame",
        status="Needs attention" if needs_attention else "Looking steady",
        tone="warning" if needs_attention else "good",
        summary=summary,
        why=(
            "These positions show how your body arrived at the likely strike "
            "frame and make extension or shoulder-angle cues visible on the "
            "same clip you can re-film."
        ),
        limit=(
            "This does not grade contact. A phone video cannot measure clubface "
            "angle, strike location, ball speed, spin, launch, or carry."
        ),
    )


def _hand_speed_card(metrics: list[SwingMetrics]) -> SwingBreakdownCard:
    values = _values(metrics, "downswing_hand_speed_sw_s")
    if not values:
        return _unavailable(
            "hand-speed",
            "Downswing hand movement",
            "Personal comparison only",
            "A matched-video movement baseline can show whether a change made the motion quicker or slower.",
        )
    average = _average(values)
    return SwingBreakdownCard(
        key="hand-speed",
        title="Downswing hand movement",
        evidence="Personal comparison only",
        status="Movement baseline",
        tone="context",
        summary=(
            f"From the top to the likely strike frame, your hands moved through "
            f"the camera view at an average of {average:.2f} shoulder-widths per "
            "second."
            + _range_sentence(values, "{:.2f}", "swing")
        ),
        why=(
            "Matched re-films can show whether the hand motion became quicker, "
            "slower, or more repeatable after practice. Faster is not automatically better."
        ),
        limit=(
            "This is not clubhead speed or ball speed, and it is never mph. "
            "Compare it only with the same club, view, framing, and similar effort."
        ),
    )


def _finish_card(
    metrics: list[SwingMetrics], cfg: Config
) -> SwingBreakdownCard:
    values = _values(metrics, "finish_balance_sw")
    if not values:
        return _unavailable(
            "finish",
            "Finish base stability",
            "Observed in this view",
            "A held finish is a simple check that the swing ended under control.",
        )
    average = _average(values)
    threshold = float(cfg.coaching["finish_balance_warn_sw"])
    needs_attention = any(value > threshold for value in values)
    if average <= 0.05:
        movement = "hardly shifted"
    elif average <= threshold:
        movement = "shifted only a small amount"
    else:
        movement = "shifted noticeably"
    summary = (
        f"The midpoint of your stance {movement} while you held the finish — "
        "an average of "
        f"{average:.2f} shoulder-widths."
        + _range_sentence(values, "{:.2f}", "swing")
    )
    if needs_attention:
        summary += " At least one swing crossed the base-drift coaching line."
    return SwingBreakdownCard(
        key="finish",
        title="Finish base stability",
        evidence="Observed in this view",
        status="Needs attention" if needs_attention else "Looking steady",
        tone="warning" if needs_attention else "good",
        summary=summary,
        why=(
            "A stance center that stays settled is one visible sign that the "
            "motion ended under control."
        ),
        limit=(
            "This measures ankle-midpoint drift, not every movement of either "
            "foot or pressure under the feet. Equal and opposite foot motion "
            "can cancel, so use the replay as the visual check."
        ),
    )


def build_swing_breakdown(
    metrics: list[SwingMetrics],
    cfg: Config,
    *,
    angle: str,
    selected_club: str | None = None,
) -> list[SwingBreakdownCard]:
    """Build the visible novice layer without changing coaching priority."""
    if not metrics:
        return []
    rhythm = _rhythm_card(metrics, cfg)
    if angle == ANGLE_DTL:
        # DTL remains the deliberately narrow rhythm-only contract. Never
        # render face-on cards full of dashes or stale unsupported values.
        return [rhythm]
    return [
        rhythm,
        _stance_card(metrics, selected_club),
        _body_control_card(metrics, cfg),
        _impact_card(metrics, cfg),
        _hand_speed_card(metrics),
        _finish_card(metrics, cfg),
    ]
