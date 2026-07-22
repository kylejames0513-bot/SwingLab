"""Strike detection from audio.

Ball strikes are sharp transients against quiet ambient sound; the audio track
is the anchor for every downstream step. The envelope is computed in 10 ms hops
so scipy's find_peaks sees exactly one value per hop (100 values per second).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import find_peaks

from .config import Config
from .ffmpeg import run

ENV_RATE = 100  # envelope samples per second (10 ms hops)


def extract_audio(video: str | Path, wav_path: str | Path) -> Path:
    """Extract the first audio track as 16 kHz mono wav."""
    wav_path = Path(wav_path)
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(video),
            "-vn",
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(wav_path),
        ]
    )
    return wav_path


def detect_strikes(wav_path: str | Path, cfg: Config) -> list[float]:
    """Return strike times in seconds, or [] when nothing clears the thresholds."""
    det = cfg.detection
    sr, a = wavfile.read(str(wav_path))
    a = np.abs(a.astype(np.float32))
    if a.ndim > 1:  # defensive: extraction is mono, but accept any wav
        a = a.max(axis=1)
    peak = a.max()
    if peak == 0:
        return []
    a /= peak

    hop = sr // ENV_RATE
    env = a[: len(a) // hop * hop].reshape(-1, hop).max(axis=1)
    peaks, _ = find_peaks(
        env,
        height=det["audio_height"],
        distance=int(det["min_gap_s"] * ENV_RATE),
        prominence=det["audio_prominence"],
    )
    return (peaks / ENV_RATE).tolist()
