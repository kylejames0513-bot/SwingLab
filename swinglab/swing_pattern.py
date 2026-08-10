"""The Swing Pattern — how this golfer's swing is *built*, not what is wrong.

Every other coaching surface in CaddieInsight answers "what should I fix
next?". This one answers a different question: "what kind of swing is
this?" — body-led or arm-led, centered or sliding, steady-headed or
mobile, quick or deliberate. Two golfers can carry the same flag for
completely different reasons, and the fix that suits a lateral arm-swinger
is not the fix that suits a centered rotator.

Three rules govern every line this module produces.

**It describes, it does not diagnose.** A pattern is a restatement of
measurements the report already shows, grouped so they mean something
together. It never claims a cause, never predicts ball flight, and never
implies the pattern is wrong — "lateral" is a description, not a fault.
The flags remain the only place CaddieInsight says something needs work.

**It reuses the thresholds the coaching already uses.** Where a band edge
exists in `cfg.coaching` (`sway_warn_sw`, `head_dip_warn_sw`,
`tempo_warn_below`, `tempo_target`, `finish_balance_warn_sw`,
`sequence_lead_warn_ms`) this module reads it rather than inventing a
second opinion. A swing cannot be called "steady-headed" here while the
warn note calls its sway a problem. The three genuinely new constants are
named `pattern_*` in the same config block, so they are visible and
tunable next to the lines they extend.

**An unreadable axis is absent, never guessed.** NaN is the honest value
everywhere in this codebase (`metrics.py`: "NaN never fires a flag"), and
it does not become a personality trait here either. A down-the-line clip
has no face-on measurements, so it yields a tempo axis and an explicit
note that the rest needs a face-on view — not a confident portrait built
from one number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import fmean

from .metrics import ANGLE_DTL, SwingMetrics

# Axis identity. These keys are stable — they reach metrics.json consumers
# and the report template, so renaming one is a compatibility change.
AXIS_SEQUENCE = "sequence"
AXIS_LOWER_BODY = "lower_body"
AXIS_HEAD = "head"
AXIS_TEMPO = "tempo"
AXIS_FINISH = "finish"

# Axis order in the report: the two that name the pattern come first, then
# the supporting character axes.
AXIS_ORDER = (AXIS_SEQUENCE, AXIS_LOWER_BODY, AXIS_HEAD, AXIS_TEMPO, AXIS_FINISH)


@dataclass(frozen=True)
class PatternAxis:
    """One measured dimension of how the swing is built.

    ``position`` is the machine-stable band key; ``display`` is the phrase
    a golfer reads. ``detail`` always carries the actual number and the
    line it was compared against — the pattern must be auditable against
    the measurements table further down the same report.
    """

    key: str
    label: str
    position: str
    display: str
    detail: str
    value: float
    unit: str


@dataclass(frozen=True)
class SwingPattern:
    """The session's movement signature.

    ``name`` is built from the axes that were actually readable, so it can
    never imply a dimension the camera could not see.
    """

    name: str
    summary: str
    axes: tuple[PatternAxis, ...]
    measured_swings: int
    unreadable: tuple[str, ...]
    note: str = ""

    @property
    def readable(self) -> bool:
        return bool(self.axes)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "summary": self.summary,
            "measured_swings": self.measured_swings,
            "unreadable": list(self.unreadable),
            "note": self.note,
            "axes": [
                {
                    "key": axis.key,
                    "label": axis.label,
                    "position": axis.position,
                    "display": axis.display,
                    "detail": axis.detail,
                    "value": axis.value,
                    "unit": axis.unit,
                }
                for axis in self.axes
            ],
        }


def _measured(all_metrics: list[SwingMetrics], attr: str) -> list[float]:
    """Every finite value of one field across the session's swings."""

    values: list[float] = []
    for metrics in all_metrics:
        value = getattr(metrics, attr, float("nan"))
        if isinstance(value, (int, float)) and math.isfinite(value):
            values.append(float(value))
    return values


def _mean(values: list[float]) -> float | None:
    return fmean(values) if values else None


def _sequence_axis(all_metrics: list[SwingMetrics], coach: dict) -> PatternAxis | None:
    """Body-led, arm-led, or too close to call.

    The measurement is the millisecond gap between the pelvis's peak
    rotation speed and the lead arm's (`swinglab.sequence`). Positive means
    the hips peaked first — the proximal-to-distal order. The dead band
    exists because a gap smaller than the separation constant is not a
    style, it is two peaks the frame rate can barely tell apart, and
    `sequence.py` already refuses to resolve peaks closer than 1.5 frame
    periods at all.
    """

    values = _measured(all_metrics, "sequence_pelvis_to_arm_ms")
    mean = _mean(values)
    if mean is None:
        return None

    warn = float(coach.get("sequence_lead_warn_ms", 0.0))
    band = float(coach.get("pattern_sequence_separation_ms", 10.0))

    if mean >= warn + band:
        position, display = "body_led", "Body-led"
        detail = (
            f"The hips reach peak rotation speed {mean:.0f} ms before the lead "
            "arm does — the proximal-to-distal order, body first."
        )
    elif mean <= warn - band:
        position, display = "arm_led", "Arm-led"
        detail = (
            f"The lead arm reaches peak rotation speed {abs(mean):.0f} ms before "
            "the hips do, so the arms are leading the downswing."
        )
    else:
        position, display = "simultaneous", "Near-simultaneous"
        detail = (
            f"Hips and lead arm peak within {abs(mean):.0f} ms of each other — "
            f"inside the {band:.0f} ms this video can meaningfully separate, so "
            "neither is leading by a readable margin."
        )
    return PatternAxis(
        AXIS_SEQUENCE, "Downswing sequence", position, display, detail, mean, "ms"
    )


def _band_axis(
    key: str,
    label: str,
    values: list[float],
    warn: float,
    settled_fraction: float,
    *,
    unit: str,
    low: tuple[str, str],
    mid: tuple[str, str],
    high: tuple[str, str],
    low_detail: str,
    mid_detail: str,
    high_detail: str,
) -> PatternAxis | None:
    """A three-band axis on a lower-is-quieter measurement.

    The high band edge is the coaching threshold itself, so this can never
    call a swing quiet on a number the warn note is flagging. The low edge
    is that same threshold scaled by ``settled_fraction`` — comfortably
    inside the line rather than merely under it.
    """

    mean = _mean(values)
    if mean is None:
        return None
    settled = warn * settled_fraction
    if mean <= settled:
        position, display, detail = low[0], low[1], low_detail
    elif mean >= warn:
        position, display, detail = high[0], high[1], high_detail
    else:
        position, display, detail = mid[0], mid[1], mid_detail
    return PatternAxis(key, label, position, display, detail, mean, unit)


def _lower_body_axis(all_metrics: list[SwingMetrics], coach: dict) -> PatternAxis | None:
    """Centered turn or lateral slide, from hip travel going back."""

    values = _measured(all_metrics, "hip_slide_backswing_sw")
    mean = _mean(values)
    if mean is None:
        return None
    warn = float(coach.get("sway_warn_sw", 0.35))
    fraction = float(coach.get("pattern_settled_fraction", 0.6))
    return _band_axis(
        AXIS_LOWER_BODY, "Lower body", values, warn, fraction, unit="SW",
        low=("centered", "Centered turn"),
        mid=("mixed", "Turning with some slide"),
        high=("lateral", "Lateral slide"),
        low_detail=(
            f"The hips travel {mean:.2f} shoulder widths away from target going "
            f"back — well inside the {warn:.2f} line. This swing turns more than "
            "it slides."
        ),
        mid_detail=(
            f"The hips travel {mean:.2f} shoulder widths going back, under the "
            f"{warn:.2f} line but not by much: a turn with some lateral movement "
            "in it."
        ),
        high_detail=(
            f"The hips travel {mean:.2f} shoulder widths away from target going "
            f"back, at or past the {warn:.2f} line — the lower body moves "
            "laterally before it turns."
        ),
    )


def _head_axis(all_metrics: list[SwingMetrics], coach: dict) -> PatternAxis | None:
    """Steady head or mobile head, from sway going back and vertical dip.

    Two independent measurements share one axis because a golfer reads them
    as one thing — "does my head stay put?". Each is scored against its own
    coaching line and the axis takes the *worse* of the two, so a big dip
    is never hidden behind a small sway.
    """

    sway = _measured(all_metrics, "head_sway_backswing_sw")
    dip = _measured(all_metrics, "head_dip_sw")
    sway_mean = _mean(sway)
    dip_mean = _mean(dip)
    if sway_mean is None and dip_mean is None:
        return None

    sway_warn = float(coach.get("sway_warn_sw", 0.35))
    dip_warn = float(coach.get("head_dip_warn_sw", 0.25))
    fraction = float(coach.get("pattern_settled_fraction", 0.6))

    # Score each present measurement as a fraction of its own line, then
    # let the worse one set the band. Comparing raw shoulder-width numbers
    # across two different thresholds would be meaningless.
    ratios = []
    if sway_mean is not None:
        ratios.append(sway_mean / sway_warn if sway_warn else 0.0)
    if dip_mean is not None:
        ratios.append(dip_mean / dip_warn if dip_warn else 0.0)
    worst = max(ratios)

    parts = []
    if sway_mean is not None:
        parts.append(f"{sway_mean:.2f} SW sideways (line {sway_warn:.2f})")
    if dip_mean is not None:
        parts.append(f"{dip_mean:.2f} SW down (line {dip_warn:.2f})")
    measured_text = " and ".join(parts)

    if worst <= fraction:
        position, display = "steady", "Steady head"
        detail = (
            f"The head moves {measured_text} — comfortably inside both lines. "
            "This is a steady-headed swing."
        )
    elif worst >= 1.0:
        position, display = "mobile", "Mobile head"
        detail = (
            f"The head moves {measured_text}, reaching or passing its line. "
            "The head travels with the swing rather than staying put."
        )
    else:
        position, display = "moderate", "Some head movement"
        detail = (
            f"The head moves {measured_text} — under the line, with movement "
            "that is visible but not flagged."
        )
    value = sway_mean if sway_mean is not None else dip_mean
    return PatternAxis(AXIS_HEAD, "Head stability", position, display, detail, float(value), "SW")


def _tempo_axis(all_metrics: list[SwingMetrics], coach: dict) -> PatternAxis | None:
    """Quick, measured, or deliberate — the one axis a DTL clip still has."""

    values = _measured(all_metrics, "tempo_ratio")
    mean = _mean(values)
    if mean is None:
        return None
    warn_below = float(coach.get("tempo_warn_below", 2.4))
    target = float(coach.get("tempo_target", 3.0))
    deliberate_above = float(coach.get("pattern_tempo_deliberate_above", 3.4))

    if mean < warn_below:
        position, display = "quick", "Quick transition"
        detail = (
            f"The backswing takes {mean:.2f} times the downswing, under the "
            f"{warn_below:.1f}:1 line — the change of direction is hurried "
            f"relative to the {target:.1f}:1 reference."
        )
    elif mean > deliberate_above:
        position, display = "deliberate", "Deliberate backswing"
        detail = (
            f"The backswing takes {mean:.2f} times the downswing, above the "
            f"{target:.1f}:1 reference — a long, unhurried move back."
        )
    else:
        position, display = "measured", "Measured tempo"
        detail = (
            f"The backswing takes {mean:.2f} times the downswing, in the band "
            f"around the {target:.1f}:1 reference."
        )
    return PatternAxis(AXIS_TEMPO, "Tempo character", position, display, detail, mean, ":1")


def _finish_axis(all_metrics: list[SwingMetrics], coach: dict) -> PatternAxis | None:
    """How settled the base is in the hold after the strike."""

    values = _measured(all_metrics, "finish_balance_sw")
    mean = _mean(values)
    if mean is None:
        return None
    warn = float(coach.get("finish_balance_warn_sw", 0.15))
    fraction = float(coach.get("pattern_settled_fraction", 0.6))
    return _band_axis(
        AXIS_FINISH, "Finish", values, warn, fraction, unit="SW",
        low=("held", "Held finish"),
        mid=("settling", "Finish settles late"),
        high=("unsettled", "Unsettled finish"),
        low_detail=(
            f"The ankle midpoint drifts {mean:.2f} shoulder widths through the "
            f"hold, inside the {warn:.2f} line — the finish is held."
        ),
        mid_detail=(
            f"The ankle midpoint drifts {mean:.2f} shoulder widths through the "
            f"hold, under the {warn:.2f} line but still moving."
        ),
        high_detail=(
            f"The ankle midpoint drifts {mean:.2f} shoulder widths through the "
            f"hold, at or past the {warn:.2f} line — the base is still moving "
            "after the strike."
        ),
    )


# The pattern name comes from the two axes that describe how the swing is
# DRIVEN (sequence) and how the base BEHAVES (lower body). Head stability
# stands in for either when it is unreadable, because "steady-headed" is
# the one phrase golfers already use about themselves.
_NAME_BY_SEQUENCE = {
    "body_led": "body-led",
    "arm_led": "arm-led",
    "simultaneous": "evenly timed",
}
_NAME_BY_LOWER_BODY = {
    "centered": "Centered",
    "mixed": "Mostly centered",
    "lateral": "Lateral",
}
_NAME_BY_HEAD = {
    "steady": "Steady-headed",
    "moderate": "Mobile-headed",
    "mobile": "Mobile-headed",
}


def _pattern_name(axes: dict[str, PatternAxis]) -> str:
    """A short, honest name built only from readable axes."""

    lower = axes.get(AXIS_LOWER_BODY)
    sequence = axes.get(AXIS_SEQUENCE)
    head = axes.get(AXIS_HEAD)
    tempo = axes.get(AXIS_TEMPO)

    base = None
    if lower is not None:
        base = _NAME_BY_LOWER_BODY.get(lower.position)
    elif head is not None:
        base = _NAME_BY_HEAD.get(head.position)

    driver = _NAME_BY_SEQUENCE.get(sequence.position) if sequence is not None else None

    if base and driver:
        return f"{base}, {driver} swing"
    if base:
        return f"{base} swing"
    if driver:
        return f"{driver.capitalize()} swing"
    if tempo is not None:
        return f"{tempo.display} swing"
    return "Swing pattern"


def build_swing_pattern(
    all_metrics: list[SwingMetrics],
    cfg,
    *,
    angle: str = "face-on",
) -> SwingPattern | None:
    """Describe how this session's swing is built, or return ``None``.

    ``None`` means nothing was readable at all — the caller renders no
    section rather than an empty one. A partial read is a real result: the
    axes that were measured appear, and the rest are named in
    ``unreadable`` so the golfer knows what the clip could not show.
    """

    if not all_metrics:
        return None

    coach = cfg.coaching if hasattr(cfg, "coaching") else dict(cfg)
    builders = (
        (AXIS_SEQUENCE, "Downswing sequence", _sequence_axis),
        (AXIS_LOWER_BODY, "Lower body", _lower_body_axis),
        (AXIS_HEAD, "Head stability", _head_axis),
        (AXIS_TEMPO, "Tempo character", _tempo_axis),
        (AXIS_FINISH, "Finish", _finish_axis),
    )

    found: dict[str, PatternAxis] = {}
    unreadable: list[str] = []
    for key, label, builder in builders:
        axis = builder(all_metrics, coach)
        if axis is None:
            unreadable.append(label)
        else:
            found[key] = axis

    if not found:
        return None

    ordered = tuple(found[key] for key in AXIS_ORDER if key in found)
    name = _pattern_name(found)

    # The summary names the pattern in one sentence a golfer can repeat.
    lead = [axis.display.lower() for axis in ordered[:2]]
    summary = (
        f"Across {len(all_metrics)} measured "
        f"{'swing' if len(all_metrics) == 1 else 'swings'}, this session reads as "
        f"{' with a '.join(lead) if len(lead) > 1 else lead[0]}."
    )

    note = ""
    if angle == ANGLE_DTL:
        note = (
            "Filmed down the line, so only timing is measurable — the movement "
            "axes need a face-on clip."
        )
    elif unreadable:
        note = (
            "Axes the clip could not measure are listed rather than guessed."
        )

    return SwingPattern(
        name=name,
        summary=summary,
        axes=ordered,
        measured_swings=len(all_metrics),
        unreadable=tuple(unreadable),
        note=note,
    )
