"""Small immutable evidence bundle retained while temporary pose frames exist."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Sequence

import numpy as np

from . import pose
from .events import SwingEvents
from .frames import FrameSet
from .metrics import SwingMetrics, lead_trail_sides
from .report_view import EventId, PhaseMethod, ReasonCode


VISIBILITY_FLOOR = 0.5


@dataclass(frozen=True)
class EventSnapshot:
    event: EventId
    frame_index: int
    timestamp_ms: int
    method: PhaseMethod
    label: str


@dataclass(frozen=True)
class AnnotationGate:
    metric_id: str
    readable: bool
    reasons: tuple[ReasonCode, ...]


@dataclass(frozen=True)
class EvidenceSnapshot:
    swing: int
    metrics: SwingMetrics
    events: tuple[EventSnapshot, ...]
    event_frames: Mapping[EventId, Path]
    event_landmarks: Mapping[EventId, pose.Landmarks | None]
    finish_ankle_midpoints: tuple[tuple[float, float], ...]
    annotation_gates: Mapping[str, AnnotationGate]
    tracking_quality: pose.TrackingQuality
    target_direction: int
    target_confident: bool
    shoulder_width_px: float


def _copy_landmarks(landmarks: pose.Landmarks | None) -> pose.Landmarks | None:
    if landmarks is None:
        return None
    copied: dict[int, np.ndarray] = {}
    for index, point in landmarks.items():
        value = np.array(point, dtype=np.float64, copy=True)
        value.setflags(write=False)
        copied[index] = value
    return MappingProxyType(copied)  # type: ignore[return-value]


def _visible(observations: Sequence[pose.PoseObservation | None], indexes: Sequence[int], frame_indexes: Sequence[int]) -> bool:
    for frame_index in frame_indexes:
        if frame_index < 0 or frame_index >= len(observations):
            return False
        observation = observations[frame_index]
        if observation is None:
            return False
        for index in indexes:
            score = observation.visibility.get(index)
            if score is None or score < VISIBILITY_FLOOR:
                return False
    return True


def _gate(metric_id: str, readable: bool, *, hand_detail: bool, poor: bool) -> AnnotationGate:
    reasons: list[ReasonCode] = []
    if not readable:
        reasons.append(ReasonCode.HAND_LANDMARKS_UNRELIABLE if hand_detail else ReasonCode.SECONDARY_METRIC_UNAVAILABLE)
    if poor:
        reasons.append(ReasonCode.TRACKING_UNSTABLE)
    return AnnotationGate(metric_id, readable, tuple(reasons))


def _image_size(path: Path) -> tuple[int, int] | None:
    if not path.is_file():
        return None
    try:
        from PIL import Image
        with Image.open(path) as image:
            return image.size
    except (OSError, ValueError):
        return None


def _finish_midpoints(
    frameset: FrameSet, observations: Sequence[pose.PoseObservation | None], finish_idx: int, finish_frame: Path | None,
) -> tuple[tuple[float, float], ...]:
    target_size = _image_size(finish_frame) if finish_frame is not None else None
    points: list[tuple[float, float]] = []
    for index in range(max(0, finish_idx), len(observations)):
        observation = observations[index]
        if observation is None:
            continue
        midpoint = pose.midpoint(observation.landmarks, pose.LEFT_ANKLE, pose.RIGHT_ANKLE)
        sx = sy = 1.0
        source_size = _image_size(frameset.paths[index]) if index < len(frameset.paths) else None
        if source_size and target_size and source_size[0] > 0 and source_size[1] > 0:
            sx, sy = target_size[0] / source_size[0], target_size[1] / source_size[1]
        points.append((float(midpoint[0] * sx), float(midpoint[1] * sy)))
    return tuple(points)


def build_evidence_snapshot(
    *, swing: int, frameset: FrameSet, observations: Sequence[pose.PoseObservation | None], events: SwingEvents,
    finish_idx: int, metrics: SwingMetrics, event_frames: Mapping[EventId, Path],
    event_landmarks: Mapping[EventId, pose.Landmarks | None], impact_method: PhaseMethod,
    tracking_quality: pose.TrackingQuality, hand: str,
) -> EvidenceSnapshot:
    """Copy only evidence needed after transient analysis frames are deleted."""
    shoulder_width = float(events.shoulder_width_px)
    if not math.isfinite(shoulder_width) or shoulder_width <= 0:
        raise ValueError("shoulder width must be finite and positive")

    event_indices = {
        EventId.ADDRESS: events.address_idx,
        EventId.TOP: events.top_idx,
        EventId.IMPACT: events.impact_idx,
        EventId.FINISH: finish_idx,
    }
    event_times = {
        EventId.ADDRESS: frameset.time_of(events.address_idx),
        EventId.TOP: frameset.time_of(events.top_idx),
        EventId.IMPACT: events.impact_s,
        EventId.FINISH: events.finish_s,
    }
    methods = {
        EventId.ADDRESS: PhaseMethod.OPENING_BASELINE,
        EventId.TOP: PhaseMethod.HIGHEST_TRACKED_HANDS,
        EventId.IMPACT: impact_method,
        EventId.FINISH: PhaseMethod.CONFIGURED_FINISH_OFFSET,
    }
    labels = {EventId.ADDRESS: "Address", EventId.TOP: "Top", EventId.IMPACT: "Impact", EventId.FINISH: "Finish"}
    snapshots = tuple(EventSnapshot(event, event_indices[event], round(event_times[event] * 1000), methods[event], labels[event]) for event in EventId)

    address, top, impact, finish = (event_indices[event] for event in EventId)
    head = (pose.NOSE, pose.LEFT_EAR, pose.RIGHT_EAR)
    hips = (pose.LEFT_HIP, pose.RIGHT_HIP)
    shoulders = (pose.LEFT_SHOULDER, pose.RIGHT_SHOULDER)
    ankles = (pose.LEFT_ANKLE, pose.RIGHT_ANKLE)
    lead_arm, _ = lead_trail_sides(hand)
    finish_window = tuple(range(max(0, finish), len(observations)))
    gates = {
        "head_sway_backswing_sw": _gate("head_sway_backswing_sw", _visible(observations, head, (address, top)), hand_detail=False, poor=tracking_quality.poor),
        "head_sway_downswing_sw": _gate("head_sway_downswing_sw", _visible(observations, head, (top, impact)), hand_detail=False, poor=tracking_quality.poor),
        "hip_slide_backswing_sw": _gate("hip_slide_backswing_sw", _visible(observations, hips, (address, top)), hand_detail=False, poor=tracking_quality.poor),
        "hip_slide_downswing_sw": _gate("hip_slide_downswing_sw", _visible(observations, hips, (top, impact)), hand_detail=False, poor=tracking_quality.poor),
        "head_dip_sw": _gate("head_dip_sw", _visible(observations, head, tuple(range(address, impact + 1))), hand_detail=False, poor=tracking_quality.poor),
        "lead_arm_angle_deg": _gate("lead_arm_angle_deg", _visible(observations, lead_arm, (impact,)), hand_detail=True, poor=tracking_quality.poor),
        "shoulder_tilt_address_deg": _gate("shoulder_tilt_address_deg", _visible(observations, shoulders, (address,)), hand_detail=False, poor=tracking_quality.poor),
        "shoulder_tilt_impact_deg": _gate("shoulder_tilt_impact_deg", _visible(observations, shoulders, (impact,)), hand_detail=False, poor=tracking_quality.poor),
        "shoulder_tilt_delta_deg": _gate("shoulder_tilt_delta_deg", _visible(observations, shoulders, (address, impact)), hand_detail=False, poor=tracking_quality.poor),
        "finish_balance_sw": _gate("finish_balance_sw", _visible(observations, ankles, finish_window), hand_detail=False, poor=tracking_quality.poor),
        "stance_width_sw": _gate("stance_width_sw", _visible(observations, ankles, (address,)), hand_detail=False, poor=tracking_quality.poor),
        "downswing_hand_speed_sw_s": _gate("downswing_hand_speed_sw_s", _visible(observations, (pose.LEFT_WRIST, pose.RIGHT_WRIST), tuple(range(top, impact + 1))), hand_detail=True, poor=tracking_quality.poor),
        "tempo_ratio": _gate("tempo_ratio", _visible(observations, (pose.LEFT_WRIST, pose.RIGHT_WRIST), (address, top, impact, finish)), hand_detail=True, poor=tracking_quality.poor),
    }
    copied_landmarks = MappingProxyType({event: _copy_landmarks(event_landmarks.get(event)) for event in EventId})
    copied_frames = MappingProxyType({event: Path(event_frames[event]) for event in EventId if event in event_frames})
    return EvidenceSnapshot(
        swing=swing, metrics=metrics, events=snapshots, event_frames=copied_frames,
        event_landmarks=copied_landmarks,
        finish_ankle_midpoints=_finish_midpoints(frameset, observations, finish_idx, copied_frames.get(EventId.FINISH)),
        annotation_gates=MappingProxyType(gates), tracking_quality=tracking_quality,
        target_direction=metrics.target_direction, target_confident=metrics.target_confident,
        shoulder_width_px=shoulder_width,
    )
