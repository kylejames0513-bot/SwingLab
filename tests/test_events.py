"""Swing event detection on synthetic landmark sequences."""

from __future__ import annotations

import pytest

from swinglab.events import EventError, EventFailure, detect_events
from swinglab.frames import FrameSet
from tests.conftest import make_landmarks


def build_frameset(n: int, start: float = 0.0, fps: float = 30.0) -> FrameSet:
    return FrameSet(paths=[f"f{i:03d}.png" for i in range(n)], start_s=start, fps=fps)


def synthetic_swing(n: int = 78):
    """Address for 10 frames, hands rise to a top at frame 40, back down."""
    tracked = []
    for i in range(n):
        if i < 10:  # address
            hand_x, hand_y = 500.0, 600.0
        elif i < 40:  # backswing: hands travel up and away from target
            progress = (i - 10) / 30
            hand_x = 500.0 - 150 * progress
            hand_y = 600.0 - 400 * progress
        elif i < 55:  # downswing to impact at frame 54
            progress = (i - 40) / 14
            hand_x = 350.0 + 150 * progress
            hand_y = 200.0 + 400 * progress
        else:  # follow-through
            hand_x, hand_y = 550.0, 400.0
        tracked.append(make_landmarks(hand_y=hand_y, hand_x=hand_x))
    return tracked


def test_events_found(cfg):
    tracked = synthetic_swing()
    frames = build_frameset(len(tracked))
    strike_s = 54 / 30.0
    ev = detect_events(tracked, frames, strike_s, cfg)
    assert ev.address_idx == 0
    assert 10 <= ev.takeaway_idx <= 15  # once hands moved >0.25 SW (25px)
    assert ev.top_idx in range(38, 42)
    assert ev.impact_idx == 54
    assert ev.shoulder_width_px == pytest.approx(100.0, abs=1)
    assert ev.top_s < strike_s
    assert ev.finish_s == pytest.approx(strike_s + 0.55)


def test_no_takeaway_raises(cfg):
    tracked = [make_landmarks() for _ in range(60)]  # statue: hands never move
    frames = build_frameset(len(tracked))
    with pytest.raises(EventError, match="takeaway"):
        detect_events(tracked, frames, 1.5, cfg)
    with pytest.raises(EventError) as exc:
        detect_events(tracked, frames, 1.5, cfg)
    assert exc.value.reason is EventFailure.NO_READABLE_SWING
    assert str(exc.value) == (
        "No takeaway found before impact — the hands never left the "
        "address position."
    )


def test_too_few_tracked_frames_raises(cfg):
    tracked = [make_landmarks() if i % 20 == 0 else None for i in range(60)]
    frames = build_frameset(len(tracked))
    with pytest.raises(EventError, match="usable pose frames"):
        detect_events(tracked, frames, 1.5, cfg)
    with pytest.raises(EventError) as exc:
        detect_events(tracked, frames, 1.5, cfg)
    assert exc.value.reason is EventFailure.INSUFFICIENT_POSE_FRAMES
    assert str(exc.value) == (
        "Only 3 usable pose frames in window — need at least 8. "
        "Is the golfer fully in frame?"
    )


def test_missing_top_to_impact_tracking_is_no_readable_swing(cfg):
    tracked = synthetic_swing()
    for i in range(12, 54):
        tracked[i] = None
    frames = build_frameset(len(tracked))
    with pytest.raises(EventError) as exc:
        detect_events(tracked, frames, 54 / 30.0, cfg)
    assert exc.value.reason is EventFailure.NO_READABLE_SWING
    assert str(exc.value) == (
        "No takeaway found before impact — the hands never left the "
        "address position."
    )


def test_untracked_frames_are_skipped(cfg):
    tracked = synthetic_swing()
    tracked[12] = None  # a dropout mid-backswing must not break detection
    tracked[41] = None
    frames = build_frameset(len(tracked))
    ev = detect_events(tracked, frames, 54 / 30.0, cfg)
    assert ev.top_idx in range(38, 42)
