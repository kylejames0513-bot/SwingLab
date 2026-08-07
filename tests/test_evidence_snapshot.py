from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from swinglab import pose
from swinglab.evidence import build_evidence_snapshot
from swinglab.events import SwingEvents
from swinglab.frames import FrameSet
from swinglab.metrics import SwingMetrics
from swinglab.report_view import EventId, PhaseMethod, ReasonCode
from tests.conftest import make_landmarks


def _metrics(**overrides):
    values = dict(swing=2, strike_s=4.12, backswing_s=1.1, downswing_s=.3,
        tempo_ratio=3.7, head_sway_backswing_sw=.2, head_sway_downswing_sw=.1,
        hip_slide_backswing_sw=.1, hip_slide_downswing_sw=.2, target_direction=1)
    values.update(overrides)
    return SwingMetrics(**values)


def _events():
    return SwingEvents(1, 3, 5, 7, 1.01, 1.07, 1.12, 1.67, 100.0, np.array([1., 2.]))


def _observations(n=12, visibility=0.9):
    return tuple(pose.PoseObservation(make_landmarks(), {i: visibility for i in pose.TRACKED}) for _ in range(n))


def _snapshot(**overrides):
    frameset = FrameSet([Path(f"analysis-{i}.png") for i in range(12)], start_s=0.8764, fps=20.0)
    events = _events()
    landmarks = {event: make_landmarks() for event in EventId}
    event_frames = {event: Path(f"full-{event.value}.png") for event in EventId}
    args = dict(swing=2, frameset=frameset, observations=_observations(), events=events,
        finish_idx=10, metrics=_metrics(), event_frames=event_frames,
        event_landmarks=landmarks, impact_method=PhaseMethod.DETECTED_AUDIO,
        tracking_quality=pose.TrackingQuality(.0, .0, False), hand="right")
    args.update(overrides)
    return build_evidence_snapshot(**args)


def test_snapshot_has_exact_four_events_methods_and_absolute_timestamps():
    snap = _snapshot()
    assert tuple((event.event, event.frame_index, event.timestamp_ms, event.method) for event in snap.events) == (
        (EventId.ADDRESS, 1, 926, PhaseMethod.OPENING_BASELINE),
        (EventId.TOP, 5, 1126, PhaseMethod.HIGHEST_TRACKED_HANDS),
        (EventId.IMPACT, 7, 1120, PhaseMethod.DETECTED_AUDIO),
        (EventId.FINISH, 10, 1670, PhaseMethod.CONFIGURED_FINISH_OFFSET),
    )


@pytest.mark.parametrize("impact_method", [PhaseMethod.DETECTED_AUDIO, PhaseMethod.MANUAL_STRIKE])
def test_snapshot_uses_supplied_impact_method_without_inference(impact_method):
    assert _snapshot(impact_method=impact_method).events[2].method is impact_method


@pytest.mark.parametrize("joint", [pose.LEFT_ELBOW, pose.LEFT_WRIST])
def test_annotation_gates_require_exact_visible_lead_arm_joint_evidence(joint):
    observations = list(_observations())
    observations[7] = pose.PoseObservation(make_landmarks(), {i: .9 for i in pose.TRACKED} | {joint: .49})
    snap = _snapshot(observations=observations)
    assert snap.annotation_gates["lead_arm_angle_deg"].readable is False
    assert snap.annotation_gates["lead_arm_angle_deg"].reasons == (ReasonCode.HAND_LANDMARKS_UNRELIABLE,)


def test_finish_midpoints_scale_to_finish_frame_coordinates(tmp_path):
    from PIL import Image
    analysis = tmp_path / "analysis.png"
    finish = tmp_path / "finish.png"
    Image.new("RGB", (100, 100)).save(analysis)
    Image.new("RGB", (200, 300)).save(finish)
    frameset = FrameSet([analysis] * 12, start_s=.0, fps=20.)
    snap = _snapshot(
        frameset=frameset,
        event_frames={event: (finish if event is EventId.FINISH else Path(f"{event}.png")) for event in EventId},
    )
    assert snap.finish_ankle_midpoints[0] == pytest.approx((1000.0, 2700.0))


def test_tracking_failure_and_target_uncertainty_are_separate_from_head_and_timing():
    snap = _snapshot(metrics=_metrics(target_confident=False), tracking_quality=pose.TrackingQuality(.6, 0., True))
    assert snap.annotation_gates["head_dip_sw"].readable
    assert snap.annotation_gates["tempo_ratio"].readable
    assert snap.annotation_gates["head_dip_sw"].reasons == (ReasonCode.TRACKING_UNSTABLE,)
    assert snap.annotation_gates["tempo_ratio"].reasons == (ReasonCode.TRACKING_UNSTABLE,)
    assert snap.target_confident is False


def test_head_sway_requires_nose_but_head_dip_requires_ears_too():
    observations = [
        pose.PoseObservation(
            make_landmarks(),
            {i: .9 for i in pose.TRACKED} | {pose.LEFT_EAR: .49, pose.RIGHT_EAR: None},
        )
        for _ in range(12)
    ]
    snap = _snapshot(observations=observations)
    assert snap.annotation_gates["head_sway_backswing_sw"].readable
    assert snap.annotation_gates["head_sway_downswing_sw"].readable
    assert not snap.annotation_gates["head_dip_sw"].readable


def test_snapshot_copies_landmarks_and_finish_ankle_midpoints_immutably():
    event_lm = {event: make_landmarks() for event in EventId}
    observations = list(_observations())
    snap = _snapshot(event_landmarks=event_lm, observations=observations)
    event_lm[EventId.TOP][pose.NOSE][0] = 9999
    observations[10].landmarks[pose.LEFT_ANKLE][0] = 9999
    assert snap.event_landmarks[EventId.TOP][pose.NOSE][0] != 9999
    assert snap.finish_ankle_midpoints[0][0] != 9999
    with pytest.raises(TypeError):
        snap.event_frames[EventId.TOP] = Path("other.png")


def test_rejects_nonpositive_or_nonfinite_shoulder_width():
    for width in (0.0, -1.0, math.inf, math.nan):
        with pytest.raises(ValueError, match="shoulder width"):
            _snapshot(events=SwingEvents(1, 3, 5, 7, 1., 1., 1., 1.5, width, np.array([1., 2.])))


@pytest.mark.parametrize("hand", ["right", "left"])
def test_snapshot_retains_validated_handedness(hand):
    assert _snapshot(hand=hand).hand == hand


def test_snapshot_rejects_unknown_handedness():
    with pytest.raises(ValueError, match="hand"):
        _snapshot(hand="ambidextrous")
