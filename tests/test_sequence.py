"""Kinematic sequence: ordering, and the many cases that must refuse to answer.

The refusals carry more weight than the happy path. A wrong sequence claim is
worse than no claim — it sends a golfer to fix a fault they do not have — so
most of this file is about the measurement declining to guess.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from swinglab import pose, sequence
from swinglab.events import SwingEvents

FPS = 120.0
FRAMES = 30


def _frame(pelvis_deg: float, torso_deg: float, arm_deg: float) -> pose.Landmarks:
    """One synthetic pose with each segment rotated to a commanded angle."""

    def pair(centre_x: float, centre_y: float, half: float, deg: float):
        rad = math.radians(deg)
        offset = np.array([half * math.cos(rad), half * math.sin(rad)])
        centre = np.array([centre_x, centre_y])
        return centre - offset, centre + offset

    right_hip, left_hip = pair(500, 600, 50, pelvis_deg)
    right_shoulder, left_shoulder = pair(500, 400, 70, torso_deg)
    # Lead arm is measured shoulder -> wrist, so the wrist alone carries it.
    arm_rad = math.radians(arm_deg)
    lead_shoulder = left_shoulder
    lead_wrist = lead_shoulder + np.array(
        [160 * math.cos(arm_rad), 160 * math.sin(arm_rad)]
    )

    return {
        pose.LEFT_HIP: left_hip,
        pose.RIGHT_HIP: right_hip,
        pose.LEFT_SHOULDER: left_shoulder,
        pose.RIGHT_SHOULDER: right_shoulder,
        pose.LEFT_WRIST: lead_wrist,
        pose.RIGHT_WRIST: np.array([420.0, 520.0]),
        pose.LEFT_ELBOW: np.array([560.0, 500.0]),
        pose.RIGHT_ELBOW: np.array([440.0, 500.0]),
    }


def _ramp(peak_at: int, count: int = FRAMES) -> list[float]:
    """Cumulative angle whose frame-to-frame delta peaks at `peak_at`.

    A triangular speed profile integrates to this, which is what a segment
    that accelerates then decelerates actually looks like.
    """
    speeds = [max(0.0, 12.0 - 1.4 * abs(k - peak_at)) for k in range(count)]
    angles, total = [], 0.0
    for speed in speeds:
        total += speed
        angles.append(total)
    return angles


def _tracked(pelvis_peak: int, torso_peak: int, arm_peak: int):
    pelvis, torso, arm = _ramp(pelvis_peak), _ramp(torso_peak), _ramp(arm_peak)
    return [_frame(pelvis[k], torso[k], arm[k]) for k in range(FRAMES)]


def _events(top: int = 0, impact: int = FRAMES - 1) -> SwingEvents:
    return SwingEvents(
        address_idx=0,
        takeaway_idx=0,
        top_idx=top,
        impact_idx=impact,
        takeaway_s=0.0,
        top_s=0.0,
        impact_s=(impact - top) / FPS,
        finish_s=1.0,
        shoulder_width_px=140.0,
        hand_baseline=np.array([500.0, 520.0]),
    )


def _analyze(tracked, events=None, fps=FPS, angle="face-on"):
    return sequence.analyze_sequence(
        tracked, events or _events(), fps, "right", angle
    )


# -- ordering ------------------------------------------------------------

def test_proximal_to_distal_swing_is_recognised():
    result = _analyze(_tracked(pelvis_peak=6, torso_peak=13, arm_peak=20))

    assert result.measured
    assert result.order == sequence.IDEAL_ORDER
    assert result.is_proximal_to_distal
    assert not result.unresolved_pairs
    assert "efficient order" in sequence.describe(result)


def test_arms_first_is_named_as_casting():
    """The fault positional metrics cannot see: this swing can pass every
    head-sway and hip-slide check while leaking all its speed early."""
    result = _analyze(_tracked(pelvis_peak=20, torso_peak=13, arm_peak=6))

    assert result.measured
    assert result.order[0] == sequence.LEAD_ARM
    assert not result.is_proximal_to_distal
    assert "casting" in sequence.describe(result)


def test_chest_leading_hips_is_distinguished_from_casting():
    result = _analyze(_tracked(pelvis_peak=13, torso_peak=6, arm_peak=20))

    assert result.order.index(sequence.TORSO) < result.order.index(sequence.PELVIS)
    assert "chest leads your hips" in sequence.describe(result)


def test_gaps_are_reported_in_milliseconds_between_consecutive_peaks():
    result = _analyze(_tracked(pelvis_peak=6, torso_peak=13, arm_peak=20))

    assert len(result.gaps_ms) == len(sequence.IDEAL_ORDER) - 1
    # 7 frames at 120fps is ~58ms; allow a frame of slack for smoothing.
    for gap in result.gaps_ms:
        assert 40.0 <= gap <= 75.0, result.gaps_ms


def test_peaks_are_timed_relative_to_impact():
    result = _analyze(_tracked(pelvis_peak=6, torso_peak=13, arm_peak=20))

    for peak in result.peaks:
        assert peak.before_impact_s > 0
    # Proximal segments peak earliest, so furthest from impact.
    assert result.peaks[0].before_impact_s > result.peaks[-1].before_impact_s


# -- refusals ------------------------------------------------------------

def test_down_the_line_is_refused_rather_than_approximated():
    """Segment angles are image-plane, so they mean nothing off face-on."""
    result = _analyze(_tracked(6, 13, 20), angle="down-the-line")

    assert result.failure is sequence.SequenceFailure.CAMERA_ANGLE
    assert not result.measured
    assert sequence.describe(result) is None


def test_a_downswing_too_short_to_sample_is_refused():
    events = _events(top=0, impact=sequence.MIN_DOWNSWING_SAMPLES - 2)
    result = _analyze(_tracked(2, 3, 4), events=events)

    assert result.failure is sequence.SequenceFailure.TOO_FEW_SAMPLES


def test_missing_landmarks_anywhere_in_the_phase_refuse():
    """A peak located inside a readable fragment is not the peak of the
    movement, so a partial track is refused rather than trimmed."""
    tracked = _tracked(6, 13, 20)
    tracked[10] = None
    assert _analyze(tracked).failure is sequence.SequenceFailure.TRACKING_GAPS

    dropped = _tracked(6, 13, 20)
    del dropped[12][pose.LEFT_HIP]
    assert _analyze(dropped).failure is sequence.SequenceFailure.TRACKING_GAPS


def test_missing_frame_rate_is_refused():
    for bad in (0.0, -1.0, float("nan")):
        assert _analyze(_tracked(6, 13, 20), fps=bad).failure is (
            sequence.SequenceFailure.NO_FPS
        )


def test_peaks_within_the_frame_rates_resolution_are_not_claimed_as_an_order():
    """Three peaks a frame apart is quantisation, not sequencing."""
    result = _analyze(_tracked(pelvis_peak=12, torso_peak=13, arm_peak=14))

    assert result.unresolved_pairs
    # Even though the raw order happens to be textbook, it is not claimed.
    assert not result.is_proximal_to_distal
    assert "too close together" in sequence.describe(result)


def test_a_textbook_order_built_on_unresolved_gaps_is_not_called_efficient():
    result = _analyze(_tracked(pelvis_peak=12, torso_peak=13, arm_peak=14))

    assert result.order == sequence.IDEAL_ORDER
    assert not result.is_proximal_to_distal


# -- robustness ----------------------------------------------------------

def test_angle_wraparound_does_not_register_as_the_peak():
    """A segment crossing the atan2 branch cut jumps by 2pi between frames.
    Unwrapped that is nothing; left wrapped it is by far the largest apparent
    rotation in the swing, and would capture the peak every time.

    The pelvis here peaks genuinely at frame 6 and separately crosses the cut
    around frame 20, so a wrap-driven spike is directly distinguishable from
    the real peak.
    """
    pelvis_ramp = _ramp(6)
    # Place the branch-cut crossing at frame ~22, far from the genuine peak at
    # frame 6, so a wrap-driven spike is unmistakably distinguishable from it.
    # Must sit inside the region where the ramp is still moving — the
    # triangular speed profile clamps to zero past frame ~15, and an angle
    # parked exactly on 180 never crosses anything.
    crossing_frame = 13
    pelvis_angles = [180.0 - pelvis_ramp[crossing_frame] + v for v in pelvis_ramp]
    torso, arm = _ramp(13), _ramp(20)
    tracked = [
        _frame(pelvis_angles[k], torso[k], arm[k]) for k in range(FRAMES)
    ]

    # Confirm the fixture really does cross the cut, or the test proves nothing.
    wrapped = [math.atan2(math.sin(math.radians(a)), math.cos(math.radians(a)))
               for a in pelvis_angles]
    jumps = [abs(b - a) for a, b in zip(wrapped, wrapped[1:])]
    assert max(jumps) > math.pi, "fixture never crosses the branch cut"
    # jumps[k] spans frames k -> k+1, and the angle sits exactly on 180 at
    # crossing_frame, so the crossing lands in that interval.
    assert jumps.index(max(jumps)) == crossing_frame

    result = _analyze(tracked)
    assert result.measured
    pelvis_peak = next(p for p in result.peaks if p.segment == sequence.PELVIS)
    # Pin to the real peak at frame 6 within a frame and a half. A wrap spike
    # would drag this to frame 22, roughly 0.13s later.
    assert pelvis_peak.before_impact_s == pytest.approx(
        (FRAMES - 1 - 6) / FPS, abs=1.5 / FPS
    ), result.peaks


def test_left_handed_golfer_measures_the_other_arm():
    """Lead arm is the LEFT arm for a right-handed golfer. Swapping hand must
    change which wrist is read, not merely re-label the output."""
    tracked = _tracked(6, 13, 20)
    # Freeze the left wrist so the left arm has no rotation at all; only a
    # read that actually uses the left wrist can notice.
    for frame in tracked:
        frame[pose.LEFT_WRIST] = np.array([660.0, 400.0])

    right = sequence.analyze_sequence(tracked, _events(), FPS, "right", "face-on")
    left = sequence.analyze_sequence(tracked, _events(), FPS, "left", "face-on")

    # Right-handed reads the (now static) left arm; left-handed reads the
    # moving right arm. Their arm peaks must therefore differ.
    right_arm = next(p for p in right.peaks if p.segment == sequence.LEAD_ARM)
    left_arm = next(p for p in left.peaks if p.segment == sequence.LEAD_ARM)
    assert right_arm.before_impact_s != left_arm.before_impact_s


def test_unknown_camera_angle_is_allowed_through():
    """None means the angle was never established, not that it is wrong.
    Gating on it would refuse every clip that skipped angle detection."""
    result = _analyze(_tracked(6, 13, 20), angle=None)
    assert result.failure is None


def test_describe_never_returns_a_sentence_for_an_unmeasured_sequence():
    for failure in sequence.SequenceFailure:
        assert sequence.describe(sequence.KinematicSequence(failure=failure)) is None
