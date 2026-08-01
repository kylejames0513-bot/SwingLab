"""End-to-end acceptance: a video with three swings yields three metric rows,
three strips, three slow-motion clips, three overlays, one report.

Pose tracking is the only stage that needs a real human on camera, so it is
replaced with a fake tracker that replays a plausible landmark sequence; every
other stage (probe, audio, ffmpeg extraction, events, metrics, deliverables,
report) runs for real.
"""

from __future__ import annotations

import json
import re

import pytest

from swinglab import pipeline, pose
from swinglab.cli import main as cli_main
from swinglab.config import Config
from swinglab.pipeline import ZeroStrikesError, analyze_video
from tests.conftest import generate_test_video, make_landmarks, needs_ffmpeg

pytestmark = needs_ffmpeg

CLICKS = [3.0, 9.5, 16.25]


class FakeTracker:
    """Replays a synthetic swing keyed on the extracted frame number."""

    def __init__(self, model_path=None):
        pass

    def detect(self, frame_path) -> pose.Landmarks | None:
        name = str(frame_path)
        match = re.search(r"_(\d+)\.png$", name)
        if match is None:  # full-res key frame: a valid address-ish pose
            return make_landmarks()
        i = int(match.group(1)) - 1  # ffmpeg numbers frames from 1
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


@pytest.fixture
def fast_cfg() -> Config:
    cfg = Config()
    # keep the minterpolate stage cheap in tests; product defaults stay 4x/1280
    cfg.slowmo["factor"] = 2
    cfg.slowmo["height"] = 240
    return cfg


@pytest.fixture(autouse=True)
def fake_pose(monkeypatch):
    monkeypatch.setattr(pose, "PoseTracker", FakeTracker)
    monkeypatch.setattr(pipeline.pose, "PoseTracker", FakeTracker)


def test_three_swings_full_run(tmp_path, fast_cfg):
    video = generate_test_video(tmp_path / "threeswings.mov", CLICKS)
    result = analyze_video(video, out_dir=tmp_path / "results", cfg=fast_cfg)

    assert len(result.swings) == 3
    assert sorted(p.name for p in (result.session_dir / "media").iterdir()) == [
        "overlay_s1.png", "overlay_s2.png", "overlay_s3.png",
        "replay_s1.mp4", "replay_s2.mp4", "replay_s3.mp4",
        "slowmo_s1.mp4", "slowmo_s2.mp4", "slowmo_s3.mp4",
        "strip_s1.png", "strip_s2.png", "strip_s3.png",
    ]
    assert result.report_path.is_file()
    assert not (result.session_dir / "work").exists()  # cleaned up

    data = json.loads(result.metrics_path.read_text())
    assert len(data["swings"]) == 3
    strikes = [s["metrics"]["strike_s"] for s in data["swings"]]
    assert strikes == pytest.approx(CLICKS, abs=0.05)
    assert all("replay" in s["deliverables"] for s in data["swings"])
    html = result.report_path.read_text()
    assert html.count("media/strip_s") == 3
    assert html.count("media/replay_s") == 3


def test_annotated_false_disables_replay(tmp_path, fast_cfg):
    fast_cfg.slowmo["annotated"] = False
    video = generate_test_video(tmp_path / "oneswing.mov", [9.5])
    result = analyze_video(video, out_dir=tmp_path / "results", cfg=fast_cfg)

    media = sorted(p.name for p in (result.session_dir / "media").iterdir())
    assert media == ["overlay_s1.png", "slowmo_s1.mp4", "strip_s1.png"]
    data = json.loads(result.metrics_path.read_text())
    assert all("replay" not in s["deliverables"] for s in data["swings"])
    html = result.report_path.read_text()
    assert "media/replay_s" not in html
    assert "Coach replay" not in html  # section absent, template still renders


def test_dtl_angle_honest_end_to_end(tmp_path, fast_cfg):
    """Down the line: timing survives, every face-on metric is null in
    metrics.json, the session note says so, and meta records angle + club."""
    video = generate_test_video(tmp_path / "dtl.mov", [9.5])
    result = analyze_video(
        video, out_dir=tmp_path / "results", cfg=fast_cfg,
        angle="dtl", club="iron",
    )
    data = json.loads(result.metrics_path.read_text())
    assert data["meta"] == {
        "camera_angle": "dtl", "club": "iron", "level": None, "hand": "right",
        "analysis_fps": 30.0,
    }
    m = data["swings"][0]["metrics"]
    assert m["tempo_ratio"] is not None
    for key in ("head_sway_backswing_sw", "hip_slide_backswing_sw",
                "head_dip_sw", "lead_arm_angle_deg",
                "shoulder_tilt_impact_deg", "finish_balance_sw"):
        assert m[key] is None, key
    assert data["session_notes"][0].startswith("Filmed down the line")
    html = result.report_path.read_text()
    assert "tempo and rhythm only" in html
    assert "Iron" in html  # the club chip


def test_angle_mismatch_warns_when_footage_looks_face_on(tmp_path, fast_cfg,
                                                         monkeypatch):
    """Wide-shouldered (face-on-looking) footage uploaded as down the line
    gets the low-confidence mismatch note; the honest-looking default
    skeleton (dead zone) never does."""

    class WideTracker(FakeTracker):
        def detect(self, frame_path):
            lm = super().detect(frame_path)
            if lm is None:
                return None
            import numpy as np
            lm[pose.LEFT_SHOULDER] = lm[pose.LEFT_SHOULDER] + np.array([60.0, 0.0])
            lm[pose.RIGHT_SHOULDER] = lm[pose.RIGHT_SHOULDER] - np.array([60.0, 0.0])
            return lm

    monkeypatch.setattr(pose, "PoseTracker", WideTracker)
    monkeypatch.setattr(pipeline.pose, "PoseTracker", WideTracker)
    video = generate_test_video(tmp_path / "wide.mov", [9.5])
    result = analyze_video(
        video, out_dir=tmp_path / "results", cfg=fast_cfg, angle="dtl"
    )
    data = json.loads(result.metrics_path.read_text())
    assert any(
        "looks like it was filmed face-on" in note
        for note in data["session_notes"]
    )


def test_uncertain_footage_never_false_alarms(tmp_path, fast_cfg):
    # The stock FakeTracker skeleton sits in the heuristic's dead zone:
    # no opinion either way, so neither angle choice draws a warning.
    video = generate_test_video(tmp_path / "meh.mov", [9.5])
    result = analyze_video(
        video, out_dir=tmp_path / "results", cfg=fast_cfg, angle="face-on"
    )
    data = json.loads(result.metrics_path.read_text())
    assert not any(
        "looks like it was filmed" in note for note in data["session_notes"]
    )
    assert data["meta"]["camera_angle"] == "face-on"


def test_manual_strikes_override(tmp_path, fast_cfg):
    video = generate_test_video(tmp_path / "silent.mov", [], silent=True)
    result = analyze_video(
        video, out_dir=tmp_path / "results", manual_strikes=[9.5], cfg=fast_cfg
    )
    assert len(result.swings) == 1
    assert result.swings[0]["metrics"].strike_s == pytest.approx(9.5)


def test_zero_strikes_is_graceful(tmp_path, fast_cfg):
    video = generate_test_video(tmp_path / "silent.mov", [], silent=True)
    with pytest.raises(ZeroStrikesError, match="--strikes"):
        analyze_video(video, out_dir=tmp_path / "results", cfg=fast_cfg)


def test_cli_zero_strikes_message_and_exit_code(tmp_path, capsys):
    video = generate_test_video(tmp_path / "silent.mov", [], silent=True)
    code = cli_main([
        "analyze", str(video), "--club", "driver",
        "--out", str(tmp_path / "results"),
    ])
    assert code == 1
    err = capsys.readouterr().err
    assert "No ball strikes detected" in err and "--strikes" in err


def test_cli_summary_output(tmp_path, capsys, monkeypatch):
    video = generate_test_video(tmp_path / "one.mov", [9.5])
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("slowmo:\n  factor: 2\n  height: 240\n")
    code = cli_main(
        ["analyze", str(video), "--out", str(tmp_path / "results"),
         "--config", str(cfg_file), "--club", "hybrid"]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Report:" in out and "report.html" in out
    assert re.search(r"Swing\s+Strike\s+Tempo", out)
    data = json.loads((tmp_path / "results" / "one" / "metrics.json").read_text())
    assert data["meta"]["club"] == "hybrid"
