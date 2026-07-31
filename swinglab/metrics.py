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
import statistics
from dataclasses import asdict, dataclass

import numpy as np

from . import pose
from .config import Config
from .events import ADDRESS_FRAMES, SwingEvents

# Camera angles the pipeline understands. Every lateral/angular metric in
# this module is DEFINED face-on; timing (durations, tempo) is
# camera-angle-agnostic. A down-the-line session keeps its timing numbers
# and reads NaN for everything face-on-only — never a silently wrong number.
ANGLE_FACE_ON = "face-on"
ANGLE_DTL = "dtl"
ANGLES = (ANGLE_FACE_ON, ANGLE_DTL)

# SwingMetrics fields that are only meaningful from a face-on camera.
FACE_ON_ONLY_FIELDS = (
    "head_sway_backswing_sw",
    "head_sway_downswing_sw",
    "hip_slide_backswing_sw",
    "hip_slide_downswing_sw",
    "head_dip_sw",
    "lead_arm_angle_deg",
    "shoulder_tilt_address_deg",
    "shoulder_tilt_impact_deg",
    "shoulder_tilt_delta_deg",
    "finish_balance_sw",
)


def finite_float(value: object) -> float | None:
    """Return one finite numeric value, or ``None`` for unsafe input.

    Persisted JSON can contain arbitrarily large integers. Converting those
    directly to ``float`` raises ``OverflowError`` instead of returning
    infinity, so every reader of restored/legacy metrics uses this one
    defensive conversion.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        converted = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


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
    # False when target-direction inference hit its last-resort fallback —
    # the coaching notes then carry an explicit low-confidence line.
    target_confident: bool = True

    def as_dict(self) -> dict:
        return asdict(self)


def lead_trail_sides(hand: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """((lead shoulder, elbow, wrist), (trail shoulder, elbow, wrist))."""
    left = (pose.LEFT_SHOULDER, pose.LEFT_ELBOW, pose.LEFT_WRIST)
    right = (pose.RIGHT_SHOULDER, pose.RIGHT_ELBOW, pose.RIGHT_WRIST)
    return (left, right) if hand == "right" else (right, left)


def _infer_target_direction(
    tracked: list[pose.Landmarks | None],
    events: SwingEvents,
    finish_idx: int,
    hand: str,
) -> tuple[int, bool]:
    """Which image direction the target is (+1 right, -1 left), plus whether
    the answer was actually inferred (False = last-resort fallback).

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
            return (sign if hand == "right" else -sign), True

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
            return (1 if travel > 0 else -1), True
    # Arbitrary but deterministic. target_confident=False makes the coaching
    # notes carry the promised low-confidence line (see coaching.swing_notes).
    return 1, False


def infer_target_direction(
    tracked: list[pose.Landmarks | None],
    events: SwingEvents,
    finish_idx: int,
    hand: str,
) -> int:
    """Direction only — kept for callers that don't need the confidence."""
    return _infer_target_direction(tracked, events, finish_idx, hand)[0]


# Conservative thresholds for the camera-angle sanity check: the projected
# shoulder-width-to-body-height ratio at address is wide face-on (shoulders
# span the frame) and narrow down the line (shoulders stack toward the
# camera). The wide dead zone between the two means uncertain footage warns
# nobody — false alarms are worse than missed ones here.
APPARENT_DTL_MAX_RATIO = 0.10
APPARENT_FACE_ON_MIN_RATIO = 0.18


def apparent_camera_angle(lm: pose.Landmarks | None) -> str | None:
    """Best-effort guess at the camera angle from one address pose:
    ANGLE_FACE_ON, ANGLE_DTL, or None when the pose doesn't clearly say
    (which is common — this is a cross-check, not a measurement)."""
    if lm is None:
        return None
    shoulder_w = float(
        np.linalg.norm(lm[pose.LEFT_SHOULDER] - lm[pose.RIGHT_SHOULDER])
    )
    height = float(
        pose.midpoint(lm, pose.LEFT_ANKLE, pose.RIGHT_ANKLE)[1]
        - pose.head_center(lm)[1]
    )
    if height <= 0:
        return None
    ratio = shoulder_w / height
    if ratio < APPARENT_DTL_MAX_RATIO:
        return ANGLE_DTL
    if ratio > APPARENT_FACE_ON_MIN_RATIO:
        return ANGLE_FACE_ON
    return None


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
    angle: str = ANGLE_FACE_ON,
    fps: float | None = None,
) -> SwingMetrics:
    """``fps`` is the frame rate ``tracked`` was actually sampled at (the
    FrameSet's rate). analysis.finish_hold_frames is defined against
    analysis.fps, so when auto-fps extracted at a higher rate the hold is
    rescaled to cover the same physical duration. Omitted = analysis.fps
    (every pre-auto-fps call site unchanged)."""
    cfg = cfg or Config()  # pure defaults; keeps every existing call site valid
    base_fps = float(cfg.analysis["fps"])
    hold_frames = int(cfg.analysis["finish_hold_frames"])
    if fps and base_fps > 0 and float(fps) != base_fps:
        hold_frames = max(1, round(hold_frames * float(fps) / base_fps))
    target, target_confident = _infer_target_direction(
        tracked, events, finish_idx, hand
    )
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

    m = SwingMetrics(
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
        target_confident=target_confident,
        head_dip_sw=_head_dip_sw(tracked, events, sw),
        lead_arm_angle_deg=_lead_arm_angle_deg(tracked, events, hand),
        shoulder_tilt_address_deg=tilt_address,
        shoulder_tilt_impact_deg=tilt_impact,
        shoulder_tilt_delta_deg=tilt_delta,
        finish_balance_sw=_finish_balance_sw(tracked, finish_idx, sw, hold_frames),
    )
    if angle == ANGLE_DTL:
        # Every one of these is defined face-on. Down the line they would be
        # numbers that don't mean what they say — NaN is the honest value,
        # and NaN never fires a flag.
        for field_name in FACE_ON_ONLY_FIELDS:
            setattr(m, field_name, float("nan"))
    return m


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
        values = [
            value
            for metric in all_metrics
            if (
                value := finite_float(getattr(metric, field_name))
            )
            is not None
        ]
        if not values:
            continue
        try:
            mean = math.fsum(
                value / len(values) for value in values
            )
            std = statistics.pstdev(values)
        except (OverflowError, TypeError, ValueError):
            continue
        if not math.isfinite(mean) or not math.isfinite(std):
            continue
        stats[field_name] = {
            "mean": round(mean, 3),
            "std": round(std, 3),
        }
    return stats
