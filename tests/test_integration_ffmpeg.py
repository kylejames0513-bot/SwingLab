"""Integration tests that need real ffmpeg/ffprobe binaries: probing, rotation
metadata on portrait input, audio round-trip, and window extraction."""

from __future__ import annotations

import pytest

from swinglab.audio import detect_strikes, extract_audio
from swinglab.ffmpeg import probe
from swinglab.frames import extract_fullres_frame, extract_window
from tests.conftest import generate_test_video, needs_ffmpeg

pytestmark = needs_ffmpeg

CLICKS = [3.0, 9.5, 16.25]


@pytest.fixture(scope="module")
def landscape_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("vid") / "swing.mov"
    return generate_test_video(path, CLICKS)


def test_probe_basics(landscape_video):
    info = probe(landscape_video)
    assert info.duration_s == pytest.approx(20.0, abs=0.5)
    assert (info.display_width, info.display_height) == (854, 480)
    assert info.fps == pytest.approx(30.0, abs=0.1)
    assert info.rotation == 0
    assert info.has_audio


def test_portrait_rotation_metadata(tmp_path):
    video = generate_test_video(tmp_path / "portrait.mov", [3.0], portrait=True)
    info = probe(video)
    assert info.rotation % 180 == 90, "rotation metadata must be read, not applied"
    # display dimensions swap: this is a portrait clip
    assert info.display_width < info.display_height
    # extraction must let ffmpeg auto-apply rotation: frames come out portrait
    frames = extract_window(video, 3.0, tmp_path / "work", 1, _cfg())
    from PIL import Image

    with Image.open(frames.paths[0]) as im:
        assert im.width < im.height


def _cfg():
    from swinglab.config import Config

    return Config()


def test_audio_roundtrip_detects_clicks(landscape_video, tmp_path, cfg):
    wav = extract_audio(landscape_video, tmp_path / "audio.wav")
    detected = detect_strikes(wav, cfg)
    assert len(detected) == 3
    for expected, got in zip(CLICKS, detected):
        assert abs(got - expected) <= 0.05


def test_window_extraction_count_and_times(landscape_video, tmp_path, cfg):
    frames = extract_window(landscape_video, 9.5, tmp_path / "work", 2, cfg)
    # 2.6 s at 30 fps
    assert 76 <= len(frames.paths) <= 80
    assert frames.start_s == pytest.approx(9.5 - 1.8)
    assert frames.time_of(frames.index_near(9.5)) == pytest.approx(9.5, abs=1 / 30)


def test_window_clamps_at_video_start(landscape_video, tmp_path, cfg):
    frames = extract_window(landscape_video, 0.5, tmp_path / "work", 3, cfg)
    assert frames.start_s == 0.0
    assert len(frames.paths) > 30


def test_fullres_frame_height(landscape_video, tmp_path, cfg):
    from PIL import Image

    out = extract_fullres_frame(landscape_video, 3.0, tmp_path / "full.png", cfg)
    with Image.open(out) as im:
        assert im.height == 1000
