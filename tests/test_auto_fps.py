"""60 fps analysis (analysis.auto_fps): rate selection, the timing-resolution
win it buys, and the end-to-end path with a real 60 fps fixture.

At 30 fps the downswing is 7-8 frames, so tempo carries a ~13% quantization
error; a high-fps source analyzed at 60 halves the timing grid. The 30 fps
path must stay byte-for-byte compatible (analysis.fps is untouched).
"""

from __future__ import annotations

import json
import re

import pytest

from swinglab import pipeline, pose
from swinglab.config import Config
from swinglab.events import detect_events
from swinglab.frames import FrameSet, pick_analysis_fps
from swinglab.metrics import compute_metrics
from swinglab.pipeline import analyze_video
from tests.conftest import generate_test_video, make_landmarks, needs_ffmpeg


# -- rate selection ----------------------------------------------------------

@pytest.mark.parametrize(
    "source_fps, expected",
    [
        (29.97, 30.0),  # ordinary phone clip: unchanged
        (30.0, 30.0),
        (48.0, 30.0),   # below the 50 fps threshold: unchanged
        (50.0, 50.0),   # at the threshold: use the source rate
        (59.94, 59.94),
        (60.0, 60.0),
        (120.0, 60.0),  # slow-mo sources cap at 60
        (240.0, 60.0),
        (0.0, 30.0),    # degenerate probe: fall back to analysis.fps
    ],
)
def test_pick_analysis_fps(cfg, source_fps, expected):
    assert pick_analysis_fps(cfg, source_fps) == expected


def test_auto_fps_off_always_uses_configured_rate(cfg):
    cfg.analysis["auto_fps"] = False
    assert pick_analysis_fps(cfg, 240.0) == 30.0


def test_operator_configured_higher_rate_is_kept(cfg):
    cfg.analysis["fps"] = 90
    assert pick_analysis_fps(cfg, 120.0) == 90.0  # never lowered by the cap


# -- timing resolution: the same physical swing, sampled at 30 vs 60 --------

TRUE_TOP_S = 61 / 60  # on the 60 fps grid, between two 30 fps frames
TRUE_STRIKE_S = 1.55


def sample_swing(fps: float) -> tuple[list, FrameSet]:
    """One physical motion sampled at ``fps``: hands leave address at 0.4 s,
    rise to the top (minimum image y) at TRUE_TOP_S, then fall to impact."""
    n = int(2.4 * fps)
    tracked = []
    for i in range(n):
        t = i / fps
        if t < 0.4:
            hand_x, hand_y = 500.0, 600.0
        elif t <= TRUE_TOP_S:
            p = (t - 0.4) / (TRUE_TOP_S - 0.4)
            hand_x, hand_y = 500.0 - 150 * p, 600.0 - 400 * p
        elif t <= TRUE_STRIKE_S:
            p = (t - TRUE_TOP_S) / (TRUE_STRIKE_S - TRUE_TOP_S)
            hand_x, hand_y = 350.0 + 150 * p, 200.0 + 400 * p
        else:
            hand_x, hand_y = 550.0, 400.0
        tracked.append(make_landmarks(hand_x=hand_x, hand_y=hand_y))
    frames = FrameSet(
        paths=[f"f{i:03d}.png" for i in range(n)], start_s=0.0, fps=fps
    )
    return tracked, frames


def test_60fps_halves_top_quantization_error(cfg):
    errors = {}
    for fps in (30.0, 60.0):
        tracked, frames = sample_swing(fps)
        ev = detect_events(tracked, frames, TRUE_STRIKE_S, cfg)
        errors[fps] = abs(ev.top_s - TRUE_TOP_S)
    # The true top sits on the 60 fps grid but between 30 fps frames: the
    # finer grid must land strictly closer.
    assert errors[60.0] < errors[30.0]
    assert errors[60.0] <= 1e-9
    assert errors[30.0] >= 1 / 61  # a full 30 fps half-step away


def test_metrics_timing_derives_from_frameset_fps(cfg):
    """No hardcoded 30s anywhere downstream: backswing/downswing at 60 fps
    are quantized on the 1/60 grid and the finish-hold window keeps its
    physical duration (finish_hold_frames is defined against analysis.fps)."""
    tracked, frames = sample_swing(60.0)
    ev = detect_events(tracked, frames, TRUE_STRIKE_S, cfg)
    finish_idx = frames.index_near(ev.finish_s)
    m = compute_metrics(1, tracked, ev, finish_idx, "right", cfg=cfg, fps=60.0)
    # SwingMetrics rounds to 3 decimals, so "on the 1/60 grid" holds to 5e-4.
    for value in (m.backswing_s, m.downswing_s):
        assert value == pytest.approx(round(value * 60) / 60, abs=5e-4)
    # The top landed on an odd 60th (61/60 s) — a time a 30 fps grid cannot
    # represent — and flowed into the metrics unquantized-to-30.
    assert ev.top_s == pytest.approx(61 / 60, abs=1e-9)
    assert m.tempo_ratio == pytest.approx(
        m.backswing_s / m.downswing_s, abs=0.01
    )


# -- end to end with a real 60 fps fixture -----------------------------------

class FpsAwareTracker:
    """Replays the standard synthetic swing at any analysis fps (the stock
    FakeTracker's frame-number thresholds assume 30)."""

    def __init__(self, fps: float, model_path=None):
        self.fps = fps

    def detect(self, frame_path) -> pose.Landmarks | None:
        name = str(frame_path)
        match = re.search(r"_(\d+)\.png$", name)
        if match is None:
            return make_landmarks()
        i = (int(match.group(1)) - 1) * (30.0 / self.fps)
        if i < 10:
            hand_x, hand_y = 500.0, 600.0
        elif i < 40:
            progress = (i - 10) / 30
            hand_x, hand_y = 500.0 - 150 * progress, 600.0 - 400 * progress
        elif i < 55:
            progress = (i - 40) / 14
            hand_x, hand_y = 350.0 + 150 * progress, 200.0 + 400 * progress
        else:
            hand_x, hand_y = 550.0, 400.0
        return make_landmarks(hand_x=hand_x, hand_y=hand_y)

    def close(self):
        pass


@needs_ffmpeg
def test_60fps_source_analyzed_at_60_end_to_end(tmp_path, monkeypatch):
    cfg = Config()
    cfg.slowmo["factor"] = 2
    cfg.slowmo["height"] = 240
    cfg.slowmo["annotated"] = False  # keep the fixture cheap

    def make_tracker(model_path=None):
        return FpsAwareTracker(60.0)

    monkeypatch.setattr(pose, "PoseTracker", make_tracker)
    monkeypatch.setattr(pipeline.pose, "PoseTracker", make_tracker)

    video = generate_test_video(tmp_path / "sixty.mov", [9.5], fps=60)
    result = analyze_video(video, out_dir=tmp_path / "results", cfg=cfg)

    data = json.loads(result.metrics_path.read_text())
    assert data["meta"]["analysis_fps"] == 60.0
    assert data["video"]["fps"] == pytest.approx(60.0, abs=0.1)
    m = data["swings"][0]["metrics"]
    # timing really is on the 1/60 grid (to the stored 3-decimal rounding)
    for key in ("backswing_s", "downswing_s"):
        assert m[key] == pytest.approx(round(m[key] * 60) / 60, abs=5e-4)
    html = result.report_path.read_text()
    assert "Analyzed at" in html and "60 fps" in html


@needs_ffmpeg
def test_30fps_source_keeps_30_and_no_report_noise(tmp_path, monkeypatch):
    cfg = Config()
    cfg.slowmo["factor"] = 2
    cfg.slowmo["height"] = 240
    cfg.slowmo["annotated"] = False

    def make_tracker(model_path=None):
        return FpsAwareTracker(30.0)

    monkeypatch.setattr(pose, "PoseTracker", make_tracker)
    monkeypatch.setattr(pipeline.pose, "PoseTracker", make_tracker)

    video = generate_test_video(tmp_path / "thirty.mov", [9.5], fps=30)
    result = analyze_video(video, out_dir=tmp_path / "results", cfg=cfg)
    data = json.loads(result.metrics_path.read_text())
    assert data["meta"]["analysis_fps"] == 30.0
    assert "Analyzed at" in result.report_path.read_text()  # stated either way
