"""Input caps: analysis.max_video_s (refuse marathon clips before any work)
and detection.max_strikes (analyze the first N, say so honestly)."""

from __future__ import annotations

import json

import pytest

from swinglab import pipeline, pose
from swinglab.config import Config
from swinglab.pipeline import VideoTooLongError, analyze_video
from swinglab.web.humanize import friendly_error
from tests.conftest import generate_test_video, needs_ffmpeg
from tests.test_pipeline_e2e import FakeTracker

CLICKS = [3.0, 9.5, 16.25]


@pytest.fixture
def fast_cfg() -> Config:
    cfg = Config()
    cfg.slowmo["factor"] = 2
    cfg.slowmo["height"] = 240
    cfg.slowmo["annotated"] = False
    return cfg


@pytest.fixture(autouse=True)
def fake_pose(monkeypatch):
    monkeypatch.setattr(pose, "PoseTracker", FakeTracker)
    monkeypatch.setattr(pipeline.pose, "PoseTracker", FakeTracker)


# -- max_video_s -------------------------------------------------------------

@needs_ffmpeg
def test_over_length_clip_refused_before_any_work(tmp_path, fast_cfg):
    fast_cfg.analysis["max_video_s"] = 10
    video = generate_test_video(tmp_path / "long.mov", [3.0], duration_s=20.0)
    out = tmp_path / "results"
    with pytest.raises(VideoTooLongError, match="analysis limit"):
        analyze_video(video, out_dir=out, cfg=fast_cfg)
    assert not out.exists()  # refused before creating the session folder


@needs_ffmpeg
def test_zero_disables_length_cap(tmp_path, fast_cfg):
    fast_cfg.analysis["max_video_s"] = 0
    video = generate_test_video(tmp_path / "long.mov", [9.5], duration_s=20.0)
    result = analyze_video(video, out_dir=tmp_path / "results", cfg=fast_cfg)
    assert len(result.swings) == 1


def test_too_long_error_translates_without_jargon():
    raw = (
        "clip.mov is 3600 seconds long — over the 300-second analysis "
        "limit. Trim the clip to the swings you want analyzed and try "
        "again. (Operators: the limit is analysis.max_video_s in config; "
        "0 disables it.)"
    )
    help_ = friendly_error(raw)
    assert "3600 seconds" in help_.message  # keeps the honest numbers
    assert "300-second" in help_.message
    text = help_.message + " ".join(help_.tips)
    assert "max_video_s" not in text and "config" not in text.lower()
    assert any("Trim" in tip for tip in help_.tips)


# -- max_strikes -------------------------------------------------------------

@needs_ffmpeg
def test_strike_cap_analyzes_first_n_with_honest_note(tmp_path, fast_cfg):
    fast_cfg.detection["max_strikes"] = 2
    video = generate_test_video(tmp_path / "three.mov", CLICKS)
    result = analyze_video(video, out_dir=tmp_path / "results", cfg=fast_cfg)

    assert len(result.swings) == 2  # the FIRST two, in clip order
    strikes = [s["metrics"].strike_s for s in result.swings]
    assert strikes == pytest.approx(CLICKS[:2], abs=0.05)

    data = json.loads(result.metrics_path.read_text())
    note = next(
        (n for n in data["session_notes"] if "analyzed the first 2" in n), None
    )
    assert note is not None and "3 strikes" in note
    assert note in result.report_path.read_text()  # the report says so too


@needs_ffmpeg
def test_strike_cap_zero_and_under_limit_are_untouched(tmp_path, fast_cfg):
    fast_cfg.detection["max_strikes"] = 0
    video = generate_test_video(tmp_path / "three.mov", CLICKS)
    result = analyze_video(video, out_dir=tmp_path / "results", cfg=fast_cfg)
    assert len(result.swings) == 3
    data = json.loads(result.metrics_path.read_text())
    assert not any("analyzed the first" in n for n in data["session_notes"])


@needs_ffmpeg
def test_strike_cap_applies_to_manual_strikes_too(tmp_path, fast_cfg):
    fast_cfg.detection["max_strikes"] = 1
    video = generate_test_video(tmp_path / "silent.mov", [], silent=True)
    result = analyze_video(
        video, out_dir=tmp_path / "results", cfg=fast_cfg,
        manual_strikes=[9.5, 16.25],
    )
    assert len(result.swings) == 1
    assert result.swings[0]["metrics"].strike_s == pytest.approx(9.5)
