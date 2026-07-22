"""Per-swing metrics and session statistics.

Sign convention for lateral movement: POSITIVE means away from the target
(sway/slide onto the trail side), negative means toward the target. Everything
lateral is expressed in shoulder widths measured at address.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from . import pose
from .events import SwingEvents


@dataclass
class SwingMetrics:
    swing: int
    strike_s: float
    backswing_s: float
    downswing_s: float
    tempo_ratio: float
    head_sway_backswing_sw: float  # address -> top
    head_sway_downswing_sw: float  # top -> impact
    hip_slide_backswing_sw: float
    hip_slide_downswing_sw: float
    target_direction: int  # +1: target is image right, -1: image left

    def as_dict(self) -> dict:
        return asdict(self)


def infer_target_direction(
    tracked: list[pose.Landmarks | None],
    events: SwingEvents,
    finish_idx: int,
    hand: str,
) -> int:
    """Which image direction the target is (+1 right, -1 left).

    Primary signal: shoulder-line rotation after impact. A golfer finishes
    rotated toward the target, so the signed image-x span of the shoulder line
    (left shoulder minus right shoulder) collapses from its address value; the
    sign of that change flips with handedness. Fallback when the finish pose is
    missing or the rotation is degenerate: the hands travel toward the target
    through the follow-through.
    """

    def shoulder_span_x(lm: pose.Landmarks) -> float:
        return float(lm[pose.LEFT_SHOULDER][0] - lm[pose.RIGHT_SHOULDER][0])

    address_lm = tracked[events.address_idx]
    finish_lm = tracked[finish_idx] if finish_idx < len(tracked) else None
    if finish_lm is not None and address_lm is not None:
        delta = shoulder_span_x(finish_lm) - shoulder_span_x(address_lm)
        if abs(delta) > 0.15 * events.shoulder_width_px:
            sign = -1 if delta > 0 else 1
            return sign if hand == "right" else -sign

    # fallback: follow-through hand travel
    impact_lm = tracked[events.impact_idx]
    late = next(
        (
            tracked[i]
            for i in range(min(finish_idx, len(tracked) - 1), events.impact_idx, -1)
            if tracked[i] is not None
        ),
        None,
    )
    if impact_lm is not None and late is not None:
        travel = pose.hand_centroid(late)[0] - pose.hand_centroid(impact_lm)[0]
        if travel:
            return 1 if travel > 0 else -1
    return 1  # arbitrary but deterministic; flagged in coaching notes as low confidence


def compute_metrics(
    swing_no: int,
    tracked: list[pose.Landmarks | None],
    events: SwingEvents,
    finish_idx: int,
    hand: str,
) -> SwingMetrics:
    target = infer_target_direction(tracked, events, finish_idx, hand)
    sw = events.shoulder_width_px

    def lateral(a_idx: int, b_idx: int, point) -> float:
        """Signed movement a->b in shoulder widths; positive = away from target."""
        a, b = tracked[a_idx], tracked[b_idx]
        if a is None or b is None:
            return float("nan")
        return float(-target * (point(b)[0] - point(a)[0]) / sw)

    def nose(lm: pose.Landmarks) -> np.ndarray:
        return lm[pose.NOSE]

    def hip_mid(lm: pose.Landmarks) -> np.ndarray:
        return pose.midpoint(lm, pose.LEFT_HIP, pose.RIGHT_HIP)

    backswing = events.top_s - events.takeaway_s
    downswing = events.impact_s - events.top_s

    return SwingMetrics(
        swing=swing_no,
        strike_s=round(events.impact_s, 3),
        backswing_s=round(backswing, 3),
        downswing_s=round(downswing, 3),
        tempo_ratio=round(backswing / downswing, 2) if downswing > 0 else float("nan"),
        head_sway_backswing_sw=round(
            lateral(events.address_idx, events.top_idx, nose), 3
        ),
        head_sway_downswing_sw=round(
            lateral(events.top_idx, events.impact_idx, nose), 3
        ),
        hip_slide_backswing_sw=round(
            lateral(events.address_idx, events.top_idx, hip_mid), 3
        ),
        hip_slide_downswing_sw=round(
            lateral(events.top_idx, events.impact_idx, hip_mid), 3
        ),
        target_direction=target,
    )


NUMERIC_FIELDS = (
    "backswing_s",
    "downswing_s",
    "tempo_ratio",
    "head_sway_backswing_sw",
    "head_sway_downswing_sw",
    "hip_slide_backswing_sw",
    "hip_slide_downswing_sw",
)


def session_stats(all_metrics: list[SwingMetrics]) -> dict[str, dict[str, float]]:
    """Mean and standard deviation of each metric across swings.

    Low variance is itself a finding worth reporting, so std is always included.
    """
    stats: dict[str, dict[str, float]] = {}
    for field_name in NUMERIC_FIELDS:
        values = np.array(
            [getattr(m, field_name) for m in all_metrics], dtype=np.float64
        )
        values = values[~np.isnan(values)]
        if len(values) == 0:
            continue
        stats[field_name] = {
            "mean": round(float(values.mean()), 3),
            "std": round(float(values.std()), 3),
        }
    return stats
