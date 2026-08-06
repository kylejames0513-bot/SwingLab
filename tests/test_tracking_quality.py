"""Tracking-confidence signaling: core-landmark visibility gating and the
per-swing tracking-quality heuristic that adds an honest low-confidence note
when the pose track can't be trusted (heavy frame loss, or a core landmark
teleporting — the detector locking onto someone else mid-swing)."""

from __future__ import annotations

import json
import re

import numpy as np
import pytest

from swinglab import pipeline, pose
from swinglab.coaching import TRACKING_UNSTABLE_NOTE
from swinglab.config import Config
from swinglab.pipeline import analyze_video
from tests.conftest import generate_test_video, make_landmarks, needs_ffmpeg
from tests.test_pipeline_e2e import FakeTracker


# -- visibility gating (pure helper the tracker consults) --------------------

def full_visibility(value: float) -> dict[int, float]:
    return {i: value for i in pose.CORE_LANDMARKS}


def test_visible_core_passes():
    assert pose.core_visibility_ok(full_visibility(0.9))
    assert pose.core_visibility_ok(full_visibility(pose.CORE_VISIBILITY_FLOOR))


def test_one_occluded_core_landmark_drops_the_frame():
    vis = full_visibility(0.9)
    vis[pose.LEFT_ANKLE] = 0.1  # e.g. legs cut off at the bottom of frame
    assert not pose.core_visibility_ok(vis)


def test_missing_scores_count_as_visible():
    # When the model doesn't say, don't drop — same inert-until-signal rule
    # as everything else.
    assert pose.core_visibility_ok({})
    vis = full_visibility(0.9)
    vis[pose.RIGHT_HIP] = None
    assert pose.core_visibility_ok(vis)


class _RawPoint:
    def __init__(self, x, y, visibility=None):
        self.x, self.y, self.visibility = x, y, visibility


class _Result:
    def __init__(self, raw):
        self.pose_landmarks = [raw]


class _Landmarker:
    def __init__(self, raw):
        self.raw = raw

    def detect(self, _img):
        return _Result(self.raw)


class _Image:
    width, height = 1000, 1000


def _tracker(raw):
    tracker = object.__new__(pose.PoseTracker)
    tracker._mp = type("MP", (), {"Image": type("I", (), {"create_from_file": staticmethod(lambda _: _Image())})})
    tracker._landmarker = _Landmarker(raw)
    return tracker


def _raw_pose(*, wrist=0.8, elbow=0.7, core=0.9):
    lm = make_landmarks()
    return [
        _RawPoint(*(lm.get(i, lm[pose.NOSE]) / 1000), visibility=(
            wrist if i in (pose.LEFT_WRIST, pose.RIGHT_WRIST) else
            elbow if i in (pose.LEFT_ELBOW, pose.RIGHT_ELBOW) else core
        ))
        for i in range(33)
    ]


def test_observation_retains_wrist_and_elbow_visibility():
    observation = _tracker(_raw_pose()).detect_observation("frame.png")
    assert observation is not None
    assert observation.visibility[pose.LEFT_WRIST] == 0.8
    assert observation.visibility[pose.RIGHT_ELBOW] == 0.7
    assert set(observation.visibility) == set(pose.TRACKED)


def test_observation_rejects_low_visibility_core_like_legacy_detect():
    raw = _raw_pose(core=0.49)
    tracker = _tracker(raw)
    assert tracker.detect_observation("frame.png") is None
    assert tracker.detect("frame.png") is None


def test_detect_remains_landmark_compatibility_wrapper():
    tracker = _tracker(_raw_pose())
    observation = tracker.detect_observation("frame.png")
    assert observation is not None
    legacy = tracker.detect("frame.png")
    assert legacy is not None
    assert set(legacy) == set(observation.landmarks)
    np.testing.assert_array_equal(legacy[pose.LEFT_WRIST], observation.landmarks[pose.LEFT_WRIST])


# -- tracking_quality on synthetic landmark sequences ------------------------

SW = 100.0  # make_landmarks' shoulder width in px


def stable_sequence(n=60):
    return [make_landmarks() for _ in range(n)]


def test_stable_sequence_is_good():
    q = pose.tracking_quality(stable_sequence(), SW)
    assert not q.poor
    assert q.dropped_fraction == 0.0
    assert q.max_core_jump_sw == 0.0


def test_small_motion_never_flags():
    # Ordinary swing motion: hips/shoulders drift a few px per frame.
    tracked = [
        make_landmarks(hip_x=500.0 + 2.0 * i, nose_x=500.0 + 1.5 * i)
        for i in range(60)
    ]
    assert not pose.tracking_quality(tracked, SW).poor


def test_core_jump_flags_poor():
    tracked = stable_sequence(60)
    # frame 30: the whole skeleton teleports 2 SW sideways — the signature
    # of the detector switching to another person in frame
    jumped = make_landmarks()
    for k in jumped:
        jumped[k] = jumped[k] + np.array([2.0 * SW, 0.0])
    tracked[30] = jumped
    q = pose.tracking_quality(tracked, SW)
    assert q.poor
    assert q.max_core_jump_sw == pytest.approx(2.0, abs=0.01)


def test_heavy_dropout_flags_poor():
    tracked = stable_sequence(60)
    for i in range(0, 60, 2):  # 50% of frames unusable (occlusion)
        tracked[i] = None
    q = pose.tracking_quality(tracked, SW)
    assert q.poor
    assert q.dropped_fraction == pytest.approx(0.5)


def test_moderate_dropout_stays_quiet():
    # Conservative thresholds: a quarter of frames lost is routine outdoor
    # footage, not a warning.
    tracked = stable_sequence(60)
    for i in range(0, 60, 4):
        tracked[i] = None
    assert not pose.tracking_quality(tracked, SW).poor


def test_reacquisition_after_gap_is_not_a_jump():
    # A dropout gap then a shifted pose: re-acquiring after occlusion looks
    # like a jump but is not evidence of a tracker switch — not counted.
    tracked = stable_sequence(60)
    tracked[30] = None
    shifted = make_landmarks()
    for k in shifted:
        shifted[k] = shifted[k] + np.array([2.0 * SW, 0.0])
    for i in range(31, 60):
        tracked[i] = {k: v.copy() for k, v in shifted.items()}
    q = pose.tracking_quality(tracked, SW)
    assert q.max_core_jump_sw == 0.0
    assert not q.poor


def test_empty_sequence_is_poor():
    assert pose.tracking_quality([], SW).poor
    assert pose.tracking_quality([None] * 30, SW).poor


# -- end to end: the honest note reaches the swing's coaching notes ----------

class JumpyTracker(FakeTracker):
    """Mid-backswing, the 'detector' locks onto someone standing 3 SW away
    for a stretch of frames, then snaps back."""

    def detect(self, frame_path):
        lm = super().detect(frame_path)
        if lm is None:
            return None
        match = re.search(r"_(\d+)\.png$", str(frame_path))
        if match and 20 <= int(match.group(1)) - 1 < 30:
            lm = {k: v + np.array([300.0, 0.0]) for k, v in lm.items()}
        return lm


@needs_ffmpeg
def test_unstable_tracking_adds_low_confidence_note(tmp_path, monkeypatch):
    cfg = Config()
    cfg.slowmo["factor"] = 2
    cfg.slowmo["height"] = 240
    cfg.slowmo["annotated"] = False
    monkeypatch.setattr(pose, "PoseTracker", JumpyTracker)
    monkeypatch.setattr(pipeline.pose, "PoseTracker", JumpyTracker)
    video = generate_test_video(tmp_path / "photobomb.mov", [9.5])
    result = analyze_video(video, out_dir=tmp_path / "results", cfg=cfg)

    data = json.loads(result.metrics_path.read_text())
    assert TRACKING_UNSTABLE_NOTE in data["swings"][0]["notes"]
    assert TRACKING_UNSTABLE_NOTE in result.report_path.read_text()


@needs_ffmpeg
def test_stable_tracking_stays_silent(tmp_path, monkeypatch):
    cfg = Config()
    cfg.slowmo["factor"] = 2
    cfg.slowmo["height"] = 240
    cfg.slowmo["annotated"] = False
    monkeypatch.setattr(pose, "PoseTracker", FakeTracker)
    monkeypatch.setattr(pipeline.pose, "PoseTracker", FakeTracker)
    video = generate_test_video(tmp_path / "clean.mov", [9.5])
    result = analyze_video(video, out_dir=tmp_path / "results", cfg=cfg)
    data = json.loads(result.metrics_path.read_text())
    assert TRACKING_UNSTABLE_NOTE not in data["swings"][0]["notes"]
