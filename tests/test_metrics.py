"""Metric computation, sign conventions, and session statistics."""

from __future__ import annotations

import numpy as np
import pytest

from swinglab import pose
from swinglab.events import SwingEvents
from swinglab.metrics import compute_metrics, infer_target_direction, session_stats
from tests.conftest import make_landmarks


def events_for(tracked, shoulder_width=100.0, fps=30.0):
    return SwingEvents(
        address_idx=0,
        takeaway_idx=10,
        top_idx=40,
        impact_idx=54,
        takeaway_s=10 / fps,
        top_s=40 / fps,
        impact_s=54 / fps,
        finish_s=54 / fps + 0.55,
        shoulder_width_px=shoulder_width,
        hand_baseline=np.array([500.0, 600.0]),
    )


def test_tempo_and_sway_signs():
    """Right-handed golfer, target at image right (+x): head drifting -x going
    back is sway AWAY from target and must come out positive."""
    tracked = []
    for i in range(75):
        if i == 0:
            lm = make_landmarks(nose_x=500.0, hip_x=500.0)
        elif i == 40:  # top: head and hips drifted 30px in -x (away from +x target)
            lm = make_landmarks(nose_x=470.0, hip_x=480.0)
        elif i == 54:  # impact: head recovered halfway, hips past address
            lm = make_landmarks(nose_x=485.0, hip_x=510.0)
        elif i >= 70:  # finish: shoulder line collapsed/flipped -> rotated toward +x
            lm = make_landmarks(shoulder_span=-40.0, hand_x=650.0)
        else:
            lm = make_landmarks()
        tracked.append(lm)

    ev = events_for(tracked)
    m = compute_metrics(1, tracked, ev, finish_idx=70, hand="right")

    assert m.target_direction == 1
    assert m.backswing_s == pytest.approx(1.0, abs=0.01)
    assert m.downswing_s == pytest.approx(14 / 30, abs=0.01)
    assert m.tempo_ratio == pytest.approx(2.14, abs=0.02)
    # away from target = positive, in shoulder widths (span 100px)
    assert m.head_sway_backswing_sw == pytest.approx(0.30, abs=0.01)
    assert m.head_sway_downswing_sw == pytest.approx(-0.15, abs=0.01)
    assert m.hip_slide_backswing_sw == pytest.approx(0.20, abs=0.01)
    assert m.hip_slide_downswing_sw == pytest.approx(-0.30, abs=0.01)


def test_target_direction_flips_for_lefty():
    tracked = [make_landmarks() for _ in range(75)]
    tracked[70] = make_landmarks(shoulder_span=-40.0)
    ev = events_for(tracked)
    right = infer_target_direction(tracked, ev, 70, "right")
    left = infer_target_direction(tracked, ev, 70, "left")
    assert right == -left


def test_target_direction_fallback_uses_hand_travel():
    tracked = [make_landmarks() for _ in range(75)]
    # finish shoulder span unchanged (degenerate rotation), hands moved left
    tracked[70] = make_landmarks(hand_x=300.0)
    ev = events_for(tracked)
    assert infer_target_direction(tracked, ev, 70, "right") == -1


def test_session_stats_mean_std():
    tracked = [make_landmarks() for _ in range(75)]
    ev = events_for(tracked)
    metrics = [compute_metrics(i + 1, tracked, ev, 70, "right") for i in range(3)]
    stats = session_stats(metrics)
    assert stats["tempo_ratio"]["std"] == 0.0  # identical swings
    assert stats["backswing_s"]["mean"] == pytest.approx(1.0, abs=0.01)
    assert "head_sway_backswing_sw" in stats


def test_missing_pose_yields_nan_not_crash():
    tracked = [make_landmarks() for _ in range(75)]
    tracked[40] = None  # top frame lost
    ev = events_for(tracked)
    m = compute_metrics(1, tracked, ev, 70, "right")
    assert np.isnan(m.head_sway_backswing_sw)
    assert not np.isnan(m.tempo_ratio)
