"""Per-swing metrics and session statistics.

Sign convention for lateral movement: POSITIVE means away from the target
(sway/slide onto the trail side), negative means toward the target. Everything
lateral is expressed in shoulder widths measured at address.

All measurements are 2D image-plane projections from a single hip-height,
face-on camera. Angle metrics (lead-arm angle, shoulder tilt) are the angles
as seen from the camera — never claimed as 3D body angles.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

from . import pose
from .config import Config
from .events import ADDRESS_FRAMES, SwingEvents


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
    head_dip_sw: float = float("nan")  # max head drop address -> impact, >= 0
    lead_arm_angle_deg: float = float("nan")  # at impact; 180 = straight (camera view)
    shoulder_tilt_address_deg: float = float("nan")  # + = trail shoulder lower
    shoulder_tilt_impact_deg: float = float("nan")
    shoulder_tilt_delta_deg: float = float("nan")  # impact - address
    finish_balance_sw: float = float("nan")  # mean ankle drift in the hold; lower = better

    def as_dict(self) -> dict:
        return asdict(self)


def lead_trail_sides(hand: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """((lead shoulder, elbow, wrist), (trail shoulder, elbow, wrist))."""
    left = (pose.LEFT_SHOULDER, pose.LEFT_ELBOW, pose.LEFT_WRIST)
    right = (pose.RIGHT_SHOULDER, pose.RIGHT_ELBOW, pose.RIGHT_WRIST)
    return (left, right) if hand == "right" else (right, left)


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


def _head_dip_sw(
    tracked: list[pose.Landmarks | None], events: SwingEvents, sw: float
) -> float:
    """Max downward head-center displacement address -> impact, in SW.

    Positive = down (image y grows downward); clamped at 0 so a head that only
    rises reads 0.0. Uses pose.head_center (nose + ears / 3) — steadier
    vertically than the nose, whose y swings with head rotation. The baseline
    is the median over the same first-ADDRESS_FRAMES valid frames as the
    shoulder-width baseline. A 3-point positional median kills single-frame
    pose jitter that a raw max would amplify.
    """
    valid_first = [i for i, lm in enumerate(tracked) if lm is not None][:ADDRESS_FRAMES]
    if not valid_first:
        return float("nan")
    baseline_y = float(
        np.median([pose.head_center(tracked[i])[1] for i in valid_first])
    )
    series = [
        float(pose.head_center(tracked[i])[1])
        for i in range(events.address_idx, events.impact_idx + 1)
        if tracked[i] is not None
    ]
    if len(series) < 3:
        return float("nan")
    smoothed = (
        series[:1]
        + [float(np.median(series[k - 1 : k + 2])) for k in range(1, len(series) - 1)]
        + series[-1:]
    )
    return round(max(0.0, max(smoothed) - baseline_y) / sw, 3)


def _lead_arm_angle_deg(
    tracked: list[pose.Landmarks | None], events: SwingEvents, hand: str
) -> float:
    """Lead-arm shoulder-elbow-wrist angle at impact, in degrees, as projected
    in the image (180 = straight, as seen from the camera)."""
    lm = tracked[events.impact_idx] if events.impact_idx < len(tracked) else None
    if lm is None:
        return float("nan")
    (s, e, w), _ = lead_trail_sides(hand)
    u = lm[s] - lm[e]
    v = lm[w] - lm[e]
    nu = float(np.linalg.norm(u))
    nv = float(np.linalg.norm(v))
    if nu < 1e-6 or nv < 1e-6:
        return float("nan")
    cos = float(np.clip(np.dot(u, v) / (nu * nv), -1.0, 1.0))
    return round(math.degrees(math.acos(cos)), 1)


def _shoulder_tilts(
    tracked: list[pose.Landmarks | None], events: SwingEvents, hand: str, sw: float
) -> tuple[float, float, float]:
    """(address, impact, delta) shoulder-line tilt vs horizontal, in degrees,
    measured face-on. Positive = trail shoulder lower than lead (image y grows
    downward)."""
    (lead_sh, _, _), (trail_sh, _, _) = lead_trail_sides(hand)

    def tilt(lm: pose.Landmarks | None) -> float:
        if lm is None:
            return float("nan")
        dx = float(lm[trail_sh][0] - lm[lead_sh][0])
        dy = float(lm[trail_sh][1] - lm[lead_sh][1])  # y down: dy > 0 = trail lower
        if abs(dx) < 0.2 * sw:
            # shoulders nearly stacked in the image — the projected tilt is
            # unstable, refuse it
            return float("nan")
        return round(math.degrees(math.atan2(dy, abs(dx))), 1)

    address = tilt(tracked[events.address_idx])
    impact = tilt(tracked[events.impact_idx])
    delta = (
        round(impact - address, 1)
        if not (math.isnan(address) or math.isnan(impact))
        else float("nan")
    )
    return address, impact, delta


def _finish_balance_sw(
    tracked: list[pose.Landmarks | None], finish_idx: int, sw: float, hold: int
) -> float:
    """Mean ankle-midpoint drift during the finish hold, in SW. Euclidean
    (x and y) on purpose: it catches both a lateral step and a hop. Clamps at
    the end of the window and degrades to NaN — never indexes out of range."""
    last = min(finish_idx + hold, len(tracked) - 1)
    idxs = [i for i in range(finish_idx, last + 1) if tracked[i] is not None]
    if len(idxs) < 3:
        return float("nan")
    ref = pose.midpoint(tracked[idxs[0]], pose.LEFT_ANKLE, pose.RIGHT_ANKLE)
    drift = [
        float(
            np.linalg.norm(
                pose.midpoint(tracked[i], pose.LEFT_ANKLE, pose.RIGHT_ANKLE) - ref
            )
        )
        for i in idxs[1:]
    ]
    return round(float(np.mean(drift)) / sw, 3)


def compute_metrics(
    swing_no: int,
    tracked: list[pose.Landmarks | None],
    events: SwingEvents,
    finish_idx: int,
    hand: str,
    cfg: Config | None = None,
) -> SwingMetrics:
    cfg = cfg or Config()  # pure defaults; keeps every existing call site valid
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

    tilt_address, tilt_impact, tilt_delta = _shoulder_tilts(tracked, events, hand, sw)

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
        head_dip_sw=_head_dip_sw(tracked, events, sw),
        lead_arm_angle_deg=_lead_arm_angle_deg(tracked, events, hand),
        shoulder_tilt_address_deg=tilt_address,
        shoulder_tilt_impact_deg=tilt_impact,
        shoulder_tilt_delta_deg=tilt_delta,
        finish_balance_sw=_finish_balance_sw(
            tracked, finish_idx, sw, int(cfg.analysis["finish_hold_frames"])
        ),
    )


# shoulder_tilt_address_deg is stored and serialized but intentionally not
# here: the report shows impact + delta; address is context.
NUMERIC_FIELDS = (
    "backswing_s",
    "downswing_s",
    "tempo_ratio",
    "head_sway_backswing_sw",
    "head_sway_downswing_sw",
    "hip_slide_backswing_sw",
    "hip_slide_downswing_sw",
    "head_dip_sw",
    "lead_arm_angle_deg",
    "shoulder_tilt_impact_deg",
    "shoulder_tilt_delta_deg",
    "finish_balance_sw",
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
