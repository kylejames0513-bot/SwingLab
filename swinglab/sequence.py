"""Kinematic sequence — the ORDER body segments reach peak rotation speed.

Every other metric in this package is positional: where the head was at
impact, how far the hips slid, what the shoulder line measured at address.
This one is temporal. It asks which segment led and which followed, which is
how a swing either transmits speed or leaks it.

An efficient downswing unwinds proximal to distal — pelvis, then torso, then
lead arm — each segment peaking and decelerating to hand its momentum up the
chain. When that order inverts, the fault has a name a golfer recognises:
arms-first, casting, a stalled pelvis. Positional metrics cannot see any of
it, because every one of those swings can pass a head-sway or hip-slide check.

## What this is not

It is not 3D. Segment angles are measured in the image plane, so this is only
meaningful **face-on**; a down-the-line clip returns unavailable rather than a
worse number. It is not a rotational-speed readout — no degrees per second is
reported as truth, because 2D projection foreshortens rotation by an unknown
factor that depends on how square the golfer stood to the lens. Only the
ordering and the gaps between peaks are claimed, because those survive the
projection when the swing is roughly frontal.

It is also frame-rate bound. A downswing lasts roughly a quarter of a second;
at 30 fps that is about eight samples to locate three peaks in. Two peaks
landing in adjacent frames are not a real ordering, so the result says
"too close to call" instead of inventing a winner.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from . import pose
from .events import SwingEvents

# Proximal to distal — the order an efficient downswing unwinds in.
PELVIS = "pelvis"
TORSO = "torso"
LEAD_ARM = "lead_arm"
IDEAL_ORDER: tuple[str, str, str] = (PELVIS, TORSO, LEAD_ARM)

# A downswing shorter than this cannot support peak-finding: with fewer
# samples the argmax is dominated by pose jitter rather than by motion.
MIN_DOWNSWING_SAMPLES = 8

# Two peaks closer together than this many frame periods are reported as
# simultaneous. One frame of separation is the quantisation floor, not a
# measurement, and 1.5 keeps a single jittered sample from inventing an order.
MIN_SEPARATION_FRAMES = 1.5


class SequenceFailure(StrEnum):
    """Why a sequence could not be measured. Never a silent nan."""

    CAMERA_ANGLE = "camera_angle"
    TOO_FEW_SAMPLES = "too_few_samples"
    TRACKING_GAPS = "tracking_gaps"
    NO_FPS = "no_fps"


@dataclass(frozen=True)
class SegmentPeak:
    """When one segment reached its maximum angular speed."""

    segment: str
    #: Seconds before impact. Positive — a peak 0.08 means 80ms before impact.
    before_impact_s: float


@dataclass(frozen=True)
class KinematicSequence:
    """The measured ordering, or an explicit reason there isn't one."""

    peaks: tuple[SegmentPeak, ...] = ()
    #: Observed order, proximal-to-distal when efficient.
    order: tuple[str, ...] = ()
    #: Gaps between consecutive peaks, milliseconds, same order as `order`.
    gaps_ms: tuple[float, ...] = ()
    #: True only when the order is exactly IDEAL_ORDER and every gap resolved.
    is_proximal_to_distal: bool = False
    #: Pairs the frame rate could not separate, as (earlier, later) names.
    unresolved_pairs: tuple[tuple[str, str], ...] = ()
    failure: SequenceFailure | None = None

    @property
    def measured(self) -> bool:
        return self.failure is None and len(self.order) == len(IDEAL_ORDER)


def _segment_angle(lm: pose.Landmarks, a: int, b: int) -> float | None:
    """Image-plane angle of the a->b vector, radians. None if either end is
    missing or the two points coincide (a degenerate, unmeasurable vector)."""
    if lm is None or a not in lm or b not in lm:
        return None
    delta = np.asarray(lm[b], dtype=float) - np.asarray(lm[a], dtype=float)
    if not np.all(np.isfinite(delta)):
        return None
    if float(np.linalg.norm(delta)) < 1e-6:
        return None
    return math.atan2(float(delta[1]), float(delta[0]))


def _angular_speed_series(
    tracked: list[pose.Landmarks | None],
    start: int,
    end: int,
    a: int,
    b: int,
    fps: float,
) -> list[tuple[int, float]] | None:
    """(frame index, |angular speed|) across the downswing for one segment.

    Angles are unwrapped before differencing so a segment crossing the atan2
    branch cut does not register a spurious spike, which would otherwise be
    the largest "peak" in the swing. Returns None if the segment is not
    trackable across the whole phase — a peak found inside a fragment is not
    the peak of the movement.
    """
    angles: list[tuple[int, float]] = []
    for index in range(start, end + 1):
        if not 0 <= index < len(tracked):
            return None
        angle = _segment_angle(tracked[index], a, b)
        if angle is None:
            return None
        angles.append((index, angle))

    if len(angles) < MIN_DOWNSWING_SAMPLES:
        return None

    unwrapped = np.unwrap(np.array([angle for _, angle in angles]))
    # Three-point median first: pose jitter on a single frame otherwise
    # differentiates into the largest apparent rotation in the series.
    smoothed = np.copy(unwrapped)
    if len(unwrapped) >= 3:
        smoothed[1:-1] = [
            float(np.median(unwrapped[k - 1 : k + 2]))
            for k in range(1, len(unwrapped) - 1)
        ]

    speeds: list[tuple[int, float]] = []
    for k in range(1, len(smoothed)):
        delta = abs(float(smoothed[k] - smoothed[k - 1])) * fps
        if not math.isfinite(delta):
            return None
        # Attribute the speed to the midpoint of the interval it was
        # measured across, not to either endpoint.
        speeds.append((angles[k][0], delta))
    return speeds or None


def analyze_sequence(
    tracked: list[pose.Landmarks | None],
    events: SwingEvents,
    fps: float,
    hand: str,
    camera_angle: str | None,
) -> KinematicSequence:
    """Measure the downswing kinematic sequence, or say why it cannot be.

    `camera_angle` gates the whole measurement: segment angles live in the
    image plane, so anything but face-on is refused rather than approximated.
    """
    if camera_angle is not None and camera_angle != "face-on":
        return KinematicSequence(failure=SequenceFailure.CAMERA_ANGLE)
    if not math.isfinite(fps) or fps <= 0:
        return KinematicSequence(failure=SequenceFailure.NO_FPS)

    start, end = events.top_idx, events.impact_idx
    if end - start + 1 < MIN_DOWNSWING_SAMPLES:
        return KinematicSequence(failure=SequenceFailure.TOO_FEW_SAMPLES)

    (lead_shoulder, _, lead_wrist), _ = _lead_trail(hand)
    definitions = {
        PELVIS: (pose.RIGHT_HIP, pose.LEFT_HIP),
        TORSO: (pose.RIGHT_SHOULDER, pose.LEFT_SHOULDER),
        LEAD_ARM: (lead_shoulder, lead_wrist),
    }

    peaks: list[SegmentPeak] = []
    for segment, (a, b) in definitions.items():
        series = _angular_speed_series(tracked, start, end, a, b, fps)
        if series is None:
            return KinematicSequence(failure=SequenceFailure.TRACKING_GAPS)
        peak_index = max(series, key=lambda pair: pair[1])[0]
        peaks.append(
            SegmentPeak(
                segment=segment,
                before_impact_s=round((events.impact_idx - peak_index) / fps, 4),
            )
        )

    # Latest-first by time-before-impact means earliest-first in the swing.
    ordered = sorted(peaks, key=lambda peak: -peak.before_impact_s)
    frame_period_s = 1.0 / fps
    floor_s = MIN_SEPARATION_FRAMES * frame_period_s

    gaps: list[float] = []
    unresolved: list[tuple[str, str]] = []
    for earlier, later in zip(ordered, ordered[1:]):
        gap_s = earlier.before_impact_s - later.before_impact_s
        gaps.append(round(gap_s * 1000.0, 1))
        if gap_s < floor_s:
            unresolved.append((earlier.segment, later.segment))

    order = tuple(peak.segment for peak in ordered)
    return KinematicSequence(
        peaks=tuple(ordered),
        order=order,
        gaps_ms=tuple(gaps),
        # An order is only "correct" if the frame rate actually resolved it.
        # A textbook order built from unresolvable gaps is a coin flip.
        is_proximal_to_distal=(order == IDEAL_ORDER and not unresolved),
        unresolved_pairs=tuple(unresolved),
    )


def pelvis_to_arm_lead_ms(sequence: KinematicSequence, fps: float) -> float | None:
    """Milliseconds by which the pelvis's speed peak leads the lead arm's.

    Positive means the pelvis peaked first — the efficient, proximal-to-distal
    direction. Negative means the arms peaked first, which is casting.

    This is the one number from the ordering worth carrying into coaching. The
    full three-segment order is richer, but a golfer cannot re-film against a
    tuple; they can re-film against "get the hips peaking before the hands".
    Naming the outer pair also picks the most defensible gap in the series: it
    spans both intervals, so it is the pair the frame rate is most likely to
    resolve, and it is the one whose sign has an unambiguous fault attached.

    Returns None — never a number — when the sequence was not measured, or
    when the two peaks fall inside the same separation floor that
    :func:`analyze_sequence` applies to adjacent pairs. A lead the frame rate
    cannot resolve is a coin flip, and a coin flip must not reach a report.
    """
    if not sequence.measured:
        return None
    if not math.isfinite(fps) or fps <= 0:
        return None
    at = {peak.segment: peak.before_impact_s for peak in sequence.peaks}
    if PELVIS not in at or LEAD_ARM not in at:
        return None
    # before_impact_s counts backwards from impact, so the earlier peak has
    # the larger value: pelvis-minus-arm is positive when the pelvis led.
    lead_s = at[PELVIS] - at[LEAD_ARM]
    if abs(lead_s) < MIN_SEPARATION_FRAMES / fps:
        return None
    return round(lead_s * 1000.0, 1)


def _lead_trail(hand: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Local copy of the lead/trail split to keep this module free of a
    circular import with metrics."""
    left = (pose.LEFT_SHOULDER, pose.LEFT_ELBOW, pose.LEFT_WRIST)
    right = (pose.RIGHT_SHOULDER, pose.RIGHT_ELBOW, pose.RIGHT_WRIST)
    return (left, right) if hand == "right" else (right, left)


def describe(sequence: KinematicSequence) -> str | None:
    """One plain sentence a golfer can act on, or None when unmeasured.

    Deliberately names the fault rather than reciting the ordering: "your arms
    lead the downswing" is actionable, "lead_arm, torso, pelvis" is not.
    """
    if not sequence.measured:
        return None
    if sequence.is_proximal_to_distal:
        return (
            "Your downswing unwinds in the efficient order — hips, then chest, "
            "then arms."
        )
    if sequence.unresolved_pairs:
        return (
            "Your segments peak too close together to separate at this frame "
            "rate. Film at a higher frame rate for a clearer read."
        )
    if sequence.order[0] == LEAD_ARM:
        return (
            "Your arms peak before your body — the classic casting pattern. "
            "Speed spends itself early instead of arriving at the ball."
        )
    if sequence.order.index(PELVIS) > sequence.order.index(TORSO):
        return (
            "Your chest leads your hips. The lower body is arriving late, so "
            "the swing loses the ground-up sequence it is built on."
        )
    return (
        "Your downswing runs out of order, so speed is not being handed up "
        "the chain."
    )
