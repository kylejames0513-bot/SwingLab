"""Shared fixtures: synthetic audio, synthetic landmark sequences, configs."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile

from swinglab import pose
from swinglab.config import Config

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")


@pytest.fixture
def cfg() -> Config:
    return Config()  # pure defaults


def write_click_wav(
    path: Path,
    click_times: list[float],
    duration_s: float = 20.0,
    sr: int = 16000,
    amplitudes: list[float] | None = None,
) -> Path:
    """Silence with sharp synthetic clicks (decaying noise burst) at known times."""
    rng = np.random.default_rng(42)
    samples = np.zeros(int(duration_s * sr), dtype=np.float32)
    amplitudes = amplitudes or [1.0] * len(click_times)
    for t, amp in zip(click_times, amplitudes):
        start = int(t * sr)
        length = int(0.01 * sr)  # 10 ms transient
        burst = amp * rng.uniform(-1, 1, length) * np.exp(-np.linspace(0, 6, length))
        samples[start : start + length] += burst.astype(np.float32)
    samples = np.clip(samples, -1, 1)
    wavfile.write(str(path), sr, (samples * 32767).astype(np.int16))
    return path


def make_landmarks(
    hand_y: float = 600.0,
    hand_x: float = 500.0,
    nose_x: float = 500.0,
    hip_x: float = 500.0,
    shoulder_span: float = 100.0,
) -> pose.Landmarks:
    """A plausible upright golfer skeleton in a 1000px-tall frame."""
    cx = 500.0

    def pt(x: float, y: float) -> np.ndarray:
        return np.array([x, y], dtype=np.float64)

    lm = {
        pose.NOSE: pt(nose_x, 100),
        pose.LEFT_EAR: pt(nose_x + 25, 105),
        pose.RIGHT_EAR: pt(nose_x - 25, 105),
        pose.LEFT_SHOULDER: pt(cx + shoulder_span / 2, 250),
        pose.RIGHT_SHOULDER: pt(cx - shoulder_span / 2, 250),
        pose.LEFT_ELBOW: pt(cx + 70, 400),
        pose.RIGHT_ELBOW: pt(cx - 70, 400),
        pose.LEFT_WRIST: pt(hand_x + 5, hand_y),
        pose.RIGHT_WRIST: pt(hand_x - 5, hand_y),
        pose.LEFT_HIP: pt(hip_x + 40, 500),
        pose.RIGHT_HIP: pt(hip_x - 40, 500),
        pose.LEFT_KNEE: pt(cx + 40, 700),
        pose.RIGHT_KNEE: pt(cx - 40, 700),
        pose.LEFT_ANKLE: pt(cx + 45, 900),
        pose.RIGHT_ANKLE: pt(cx - 45, 900),
    }
    return lm


def generate_test_video(
    path: Path,
    click_times: list[float],
    duration_s: float = 20.0,
    portrait: bool = False,
    silent: bool = False,
    fps: int = 30,
) -> Path:
    """A synthetic test video: moving test pattern plus click audio."""
    wav = path.with_suffix(".wav")
    if silent:
        sr = 16000
        wavfile.write(
            str(wav), sr, np.zeros(int(duration_s * sr), dtype=np.int16)
        )
    else:
        write_click_wav(wav, click_times, duration_s)
    target = path.with_suffix(".flat.mov") if portrait else path
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi",
            "-i", f"testsrc=size=854x480:rate={fps}:duration={duration_s}",
            "-i", str(wav),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(target),
        ],
        check=True, capture_output=True,
    )
    if portrait:
        # iPhone portrait clips store a landscape buffer plus rotation metadata
        # (a display matrix); a stream-copy remux attaches it without re-encoding
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-y", "-display_rotation", "-90",
                "-i", str(target), "-c", "copy", str(path),
            ],
            check=True, capture_output=True,
        )
        target.unlink()
    wav.unlink()
    return path
