"""Annotated coach replay: pure-Pillow drawing plus the ffmpeg encode path.

The drawing tests need no ffmpeg (annotate_frame and friends are pure
Pillow/NumPy); the extraction/encode tests carry needs_ffmpeg and auto-skip.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from swinglab import annotate, frames, pose, slowmo
from swinglab.config import Config
from swinglab.drawing import draw_chip, hex_to_rgba, load_font
from swinglab.events import SwingEvents
from swinglab.metrics import SwingMetrics
from tests.conftest import generate_test_video, make_landmarks, needs_ffmpeg

NAN = float("nan")


def scaled_landmarks(scale: float, **kwargs) -> pose.Landmarks:
    """make_landmarks (a 1000px-tall figure) scaled into a smaller frame."""
    return {k: v * scale for k, v in make_landmarks(**kwargs).items()}


def make_metrics(**overrides) -> SwingMetrics:
    base = dict(
        swing=1,
        strike_s=11.8,
        backswing_s=0.80,
        downswing_s=0.27,
        tempo_ratio=3.0,
        head_sway_backswing_sw=0.10,
        head_sway_downswing_sw=-0.12,
        hip_slide_backswing_sw=0.08,
        hip_slide_downswing_sw=0.05,
        target_direction=-1,
        lead_arm_angle_deg=162.3,
        finish_balance_sw=0.08,
    )
    base.update(overrides)
    return SwingMetrics(**base)


def make_events(analysis_frames: frames.FrameSet, strike_s: float) -> SwingEvents:
    impact_idx = analysis_frames.index_near(strike_s)
    top_idx = max(0, impact_idx - 24)
    return SwingEvents(
        address_idx=0,
        takeaway_idx=6,
        top_idx=top_idx,
        impact_idx=impact_idx,
        takeaway_s=analysis_frames.time_of(6),
        top_s=analysis_frames.time_of(top_idx),
        impact_s=strike_s,
        finish_s=strike_s + 0.55,
        shoulder_width_px=100.0,
        hand_baseline=np.array([500.0, 600.0]),
    )


def dummy_frameset(n: int, start_s: float, fps: float = 30.0) -> frames.FrameSet:
    return frames.FrameSet(
        paths=[Path(f"f{i:04d}.png") for i in range(n)], start_s=start_s, fps=fps
    )


# ---------------------------------------------------------------- pure Pillow


def test_hex_to_rgba():
    assert hex_to_rgba("#e8720c", 230) == (232, 114, 12, 230)
    assert hex_to_rgba("#1a5c38") == (26, 92, 56, 255)
    with pytest.raises(ValueError):
        hex_to_rgba("#fff")


def test_draw_chip_size_and_stacking():
    layer = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
    font = load_font(16)
    bg = hex_to_rgba("#1a5c38", 230)
    w, h = draw_chip(layer, (12, 12), "Setup", bg, font)
    assert w > 0 and h > 0
    arr = np.array(layer)
    # chip body carries the background fill at its alpha (sample inside the
    # top padding, clear of both the rounded corner and the text)
    assert arr[12 + 3, 12 + w // 2, 3] == 230
    # ...and nothing is drawn above the margin
    assert arr[:12, :, 3].max() == 0
    # a second chip stacked below the first does not overlap it
    y2 = 12 + h + 6
    w2, h2 = draw_chip(layer, (12, y2), "Top · backswing 0.80 s", bg, font)
    arr = np.array(layer)
    assert arr[y2 + 3, 12 + w2 // 2, 3] == 230
    assert w2 > w  # longer text, wider chip


def test_annotate_frame_draws_on_frame():
    cfg = Config()
    img = Image.new("RGB", (640, 360), (40, 90, 60))
    s = 360 / 1000
    lm = scaled_landmarks(s)
    trail = [(200.0, 220.0, 0.4), (260.0, 190.0, 0.2), (320.0, 200.0, 0.0)]
    out = annotate.annotate_frame(
        img, lm, 100.0 * s, trail, ["Setup"], 320.0, cfg
    )
    assert out.size == img.size
    assert out.tobytes() != img.tobytes()
    # the input image is not mutated
    assert np.array(img).mean() == pytest.approx(
        np.array(Image.new("RGB", (640, 360), (40, 90, 60))).mean()
    )


def test_annotate_frame_without_landmarks_still_renders():
    cfg = Config()
    img = Image.new("RGB", (640, 360), (40, 90, 60))
    trail = [(200.0, 220.0, 0.1), (320.0, 200.0, 0.0)]
    out = annotate.annotate_frame(img, None, 36.0, trail, ["Setup"], None, cfg)
    assert out.size == img.size
    assert out.tobytes() != img.tobytes()


def test_chips_change_top_left_region_only():
    cfg = Config()
    img = Image.new("RGB", (640, 360), (40, 90, 60))
    out = annotate.annotate_frame(img, None, 36.0, [], ["Setup"], None, cfg)
    before = np.array(img)
    after = np.array(out)
    # chip text lands in the top-left corner...
    assert (after[:60, :200] != before[:60, :200]).any()
    # ...and the rest of the frame is untouched
    assert (after[200:, 400:] == before[200:, 400:]).all()


def test_trail_alpha_fades_with_age():
    cfg = Config()  # trail_fade_s = 0.9
    fresh = annotate.trail_layer(
        (300, 120), [(30.0, 60.0, 0.05), (270.0, 60.0, 0.0)], cfg
    )
    stale = annotate.trail_layer(
        (300, 120), [(30.0, 60.0, 0.80), (270.0, 60.0, 0.75)], cfg
    )
    # compare along the segment, excluding the newest-point dot near x=270
    a_fresh = np.array(fresh)[:, :250, 3].max()
    a_stale = np.array(stale)[:, :250, 3].max()
    assert a_fresh > a_stale > 0
    assert a_fresh == round(200 * (1 - 0.05 / 0.9))
    assert a_stale == round(200 * (1 - 0.80 / 0.9))


def test_trail_older_than_fade_draws_nothing():
    cfg = Config()
    layer = annotate.trail_layer(
        (300, 120), [(30.0, 60.0, 1.5), (270.0, 60.0, 1.2)], cfg
    )
    assert np.array(layer)[..., 3].max() == 0


def test_chip_schedule_texts_and_times():
    fs = dummy_frameset(78, start_s=10.0)
    ev = make_events(fs, strike_s=11.8)
    sched = annotate.chip_schedule(fs, ev, make_metrics())
    assert [t for t, _ in sched] == pytest.approx(
        [10.0, fs.time_of(ev.top_idx), 11.8, 12.35]
    )
    texts = [text for _, text in sched]
    assert texts == [
        "Setup",
        "Top · backswing 0.80 s",
        "Impact · lead arm 162° · sway T→I -0.12 SW",
        "Finish · balance 0.08 SW",
    ]


def test_chips_at_is_cumulative():
    fs = dummy_frameset(78, start_s=10.0)
    ev = make_events(fs, strike_s=11.8)
    sched = annotate.chip_schedule(fs, ev, make_metrics())
    texts = [text for _, text in sched]
    assert annotate.chips_at(9.9, sched) == []
    assert annotate.chips_at(10.0, sched) == ["Setup"]
    assert annotate.chips_at(ev.top_s, sched) == texts[:2]
    assert annotate.chips_at(11.8, sched) == texts[:3]
    assert annotate.chips_at(12.35, sched) == texts
    assert annotate.chips_at(99.0, sched) == texts


def test_chip_schedule_degrades_on_nan():
    fs = dummy_frameset(78, start_s=10.0)
    ev = make_events(fs, strike_s=11.8)
    all_nan = make_metrics(
        backswing_s=NAN,
        lead_arm_angle_deg=NAN,
        head_sway_downswing_sw=NAN,
        finish_balance_sw=NAN,
    )
    assert [text for _, text in annotate.chip_schedule(fs, ev, all_nan)] == [
        "Setup",
        "Top",
        "Impact",
        "Finish",
    ]
    one_nan = make_metrics(lead_arm_angle_deg=NAN)
    texts = [text for _, text in annotate.chip_schedule(fs, ev, one_nan)]
    assert texts[2] == "Impact · sway T→I -0.12 SW"


def test_replay_landmarks_mapping_and_scale():
    ana = dummy_frameset(78, start_s=10.0)
    rep = dummy_frameset(72, start_s=10.4)
    tracked: list[pose.Landmarks | None] = [make_landmarks() for _ in range(78)]
    tracked[12] = None  # replay frame 0 lands exactly on analysis frame 12
    lms = annotate.replay_landmarks(rep, ana, tracked, scale=1.5)
    assert len(lms) == 72
    assert lms[0] is None  # pose failed there -> stays None
    np.testing.assert_allclose(
        lms[1][pose.NOSE], tracked[13][pose.NOSE] * 1.5
    )
    # last analysis time is 10.0 + 77/30 ~ 12.567; tolerance is 0.75/30
    # -> replay frames 66.. (t >= 12.6) are beyond the window: None, never a
    # reuse of the clamped last analysis frame
    assert lms[65] is not None
    assert all(lm is None for lm in lms[66:])


def test_make_replay_raises_on_empty_frameset(tmp_path):
    cfg = Config()
    empty = frames.FrameSet(paths=[], start_s=0.0, fps=30.0)
    ana = dummy_frameset(78, start_s=10.0)
    ev = make_events(ana, strike_s=11.8)
    with pytest.raises(ValueError, match="no replay frames"):
        annotate.make_replay(
            empty, ana, [None] * 78, ev, make_metrics(), tmp_path / "r.mp4", cfg
        )


# ------------------------------------------------------------- needs ffmpeg


@needs_ffmpeg
def test_extract_replay_frames(tmp_path):
    cfg = Config()
    cfg.slowmo["height"] = 360  # keep the test light; still even-dimensioned
    video = generate_test_video(tmp_path / "clip.mp4", [3.0], duration_s=8.0)
    fs = slowmo.extract_replay_frames(video, 3.0, tmp_path / "replay", cfg)
    expected = cfg.slowmo["duration_s"] * cfg.analysis["fps"]
    assert abs(len(fs.paths) - expected) <= 2
    assert fs.start_s == pytest.approx(3.0 - cfg.slowmo["pre_s"])
    assert fs.fps == float(cfg.analysis["fps"])
    with Image.open(fs.paths[0]) as im:
        assert im.height == cfg.slowmo["height"]
        assert im.width % 2 == 0 and im.height % 2 == 0


@needs_ffmpeg
def test_make_replay_end_to_end(tmp_path):
    from swinglab import ffmpeg

    cfg = Config()
    cfg.slowmo["height"] = 360
    strike_s = 3.0
    video = generate_test_video(tmp_path / "clip.mp4", [strike_s], duration_s=8.0)
    ana = frames.extract_window(video, strike_s, tmp_path / "work", 1, cfg)
    with Image.open(ana.paths[0]) as im:
        s = im.height / 1000  # make_landmarks lives in a 1000px-tall space
    # synthetic swing: hands go up and come back down; a few pose dropouts
    n = len(ana.paths)
    tracked: list[pose.Landmarks | None] = []
    for i in range(n):
        if i in (20, 40):
            tracked.append(None)
            continue
        hand_y = 600.0 - 350.0 * math.sin(math.pi * i / max(1, n - 1))
        tracked.append(scaled_landmarks(s, hand_y=hand_y, hand_x=500.0 + i))
    ev = make_events(ana, strike_s)
    ev.shoulder_width_px = 100.0 * s
    replay = slowmo.extract_replay_frames(video, strike_s, tmp_path / "replay", cfg)
    out = annotate.make_replay(
        replay, ana, tracked, ev, make_metrics(strike_s=strike_s),
        tmp_path / "replay_s1.mp4", cfg,
    )
    assert out.is_file()
    info = ffmpeg.probe(out)
    assert info.fps == pytest.approx(30, abs=0.5)
    expected_duration = cfg.slowmo["duration_s"] * cfg.slowmo["factor"]
    assert info.duration_s == pytest.approx(expected_duration, abs=0.3)
