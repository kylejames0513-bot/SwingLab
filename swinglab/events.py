"""Swing events from tracked frames.

All lateral measurements are normalized by shoulder width at address (pixel
distance between the shoulders, median of the first frames of the window),
which is what makes numbers comparable across camera distances.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from . import pose
from .frames import FrameSet

ADDRESS_FRAMES = 6  # frames used for the address baseline


class EventFailure(StrEnum):
    INSUFFICIENT_POSE_FRAMES = "insufficient_pose_frames"
    NO_READABLE_SWING = "no_readable_swing"


class EventError(RuntimeError):
    """Raised when a swing window doesn't contain a usable swing."""

    def __init__(self, reason: EventFailure, message: str):
        super().__init__(message)
        self.reason = reason


@dataclass
class SwingEvents:
    """Frame indices (into the tracked list) and video times of key positions."""

    address_idx: int
    takeaway_idx: int
    top_idx: int
    impact_idx: int
    takeaway_s: float
    top_s: float
    impact_s: float
    finish_s: float
    shoulder_width_px: float
    hand_baseline: np.ndarray  # address hand centroid, px


def detect_events(
    tracked: list[pose.Landmarks | None],
    frames: FrameSet,
    strike_s: float,
    cfg,
) -> SwingEvents:
    """Locate address, takeaway, top, and impact within one analysis window.

    ``tracked`` is one entry per frame in ``frames`` (None where pose detection
    failed the sanity check).
    """
    ana = cfg.analysis
    valid = [i for i, lm in enumerate(tracked) if lm is not None]
    if len(valid) < ADDRESS_FRAMES + 2:
        raise EventError(EventFailure.INSUFFICIENT_POSE_FRAMES,
            f"Only {len(valid)} usable pose frames in window — need at least "
            f"{ADDRESS_FRAMES + 2}. Is the golfer fully in frame?"
        )

    first = valid[:ADDRESS_FRAMES]
    shoulder_width = float(
        np.median(
            [
                np.linalg.norm(
                    tracked[i][pose.LEFT_SHOULDER] - tracked[i][pose.RIGHT_SHOULDER]
                )
                for i in first
            ]
        )
    )
    if shoulder_width <= 0:
        raise EventError(EventFailure.INSUFFICIENT_POSE_FRAMES, "Degenerate shoulder width at address.")
    baseline = np.median(
        np.stack([pose.hand_centroid(tracked[i]) for i in first]), axis=0
    )

    impact_idx = frames.index_near(strike_s)

    takeaway_idx = None
    threshold = ana["takeaway_threshold_sw"] * shoulder_width
    for i in valid:
        if i >= impact_idx:
            break
        if np.linalg.norm(pose.hand_centroid(tracked[i]) - baseline) > threshold:
            takeaway_idx = i
            break
    if takeaway_idx is None:
        raise EventError(EventFailure.NO_READABLE_SWING,
            "No takeaway found before impact — the hands never left the "
            "address position."
        )

    # top of backswing: highest hand centroid (minimum image y) before impact
    pre_impact = [i for i in valid if takeaway_idx <= i < impact_idx]
    if not pre_impact:
        raise EventError(EventFailure.NO_READABLE_SWING, "No tracked frames between takeaway and impact.")
    top_idx = min(pre_impact, key=lambda i: pose.hand_centroid(tracked[i])[1])

    return SwingEvents(
        address_idx=first[0],
        takeaway_idx=takeaway_idx,
        top_idx=top_idx,
        impact_idx=impact_idx,
        takeaway_s=frames.time_of(takeaway_idx),
        top_s=frames.time_of(top_idx),
        impact_s=strike_s,
        finish_s=strike_s + ana["finish_offset_s"],
        shoulder_width_px=shoulder_width,
        hand_baseline=baseline,
    )
