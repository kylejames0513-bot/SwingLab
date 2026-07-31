"""Strike detection from audio.

Ball strikes are sharp transients against quiet ambient sound; the audio track
is the anchor for every downstream step. The envelope is computed in 10 ms hops
so scipy's find_peaks sees exactly one value per hop (100 values per second).

Memory model: the wav is memory-mapped and processed in bounded chunks, so
peak RAM stays a few MB regardless of clip length (a one-hour 16 kHz mono
track is ~230 MB fully loaded — loading it at once was the prototype's way).
The chunked math is bit-exact with the full-load version: per-hop maxima and
the global peak commute with chunking, and dividing by the peak after taking
the max equals taking the max of the divided samples (division by a positive
float is monotone).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import find_peaks

from .config import Config
from .ffmpeg import run

ENV_RATE = 100  # envelope samples per second (10 ms hops)
CHUNK_HOPS = 4096  # hops per streaming block (~41 s of audio; a few MB peak)


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


def _abs_mono_f32(chunk: np.ndarray) -> np.ndarray:
    """|samples| as float32, folded to mono by per-sample cross-channel max
    (defensive: extraction is mono, but accept any wav)."""
    a = np.abs(np.asarray(chunk).astype(np.float32))
    if a.ndim > 1:
        a = a.max(axis=1)
    return a


def compute_envelope(wav_path: str | Path) -> tuple[np.ndarray, int]:
    """(normalized envelope at ENV_RATE, sample rate), streaming the wav in
    bounded chunks. The envelope is the per-hop max of |samples| divided by
    the global peak (which includes the sub-hop tail, exactly like the
    full-load version did)."""
    try:
        sr, a = wavfile.read(str(wav_path), mmap=True)
    except ValueError:
        # scipy can't memory-map every format (e.g. 24-bit PCM) — fall back
        # to a plain read for those rather than failing.
        sr, a = wavfile.read(str(wav_path))
    hop = sr // ENV_RATE
    n_hops = len(a) // hop if hop else 0
    env = np.empty(n_hops, dtype=np.float32)
    peak = np.float32(0.0)
    block = max(1, hop * CHUNK_HOPS)
    for start in range(0, n_hops * hop, block):
        chunk = _abs_mono_f32(a[start : min(start + block, n_hops * hop)])
        maxima = chunk.reshape(-1, hop).max(axis=1)
        env[start // hop : start // hop + len(maxima)] = maxima
        if len(maxima):
            peak = max(peak, maxima.max())
    tail = _abs_mono_f32(a[n_hops * hop :])  # counts toward the peak only
    if len(tail):
        peak = max(peak, tail.max())
    if peak == 0:
        return env[:0], sr  # silence: empty envelope, caller reports no strikes
    env /= peak
    return env, sr


def detect_strikes(wav_path: str | Path, cfg: Config) -> list[float]:
    """Return strike times in seconds, or [] when nothing clears the thresholds."""
    det = cfg.detection
    env, _sr = compute_envelope(wav_path)
    if not len(env):
        return []
    peaks, props = find_peaks(
        env,
        height=det["audio_height"],
        distance=int(det["min_gap_s"] * ENV_RATE),
        prominence=det["audio_prominence"],
    )
    if len(peaks) == 0:
        return []

    # Ball strikes in one session are consistently the loudest transients;
    # practice swings brushing the ground, teeing up, or handling the phone
    # produce quieter thuds that can still clear the absolute threshold. Keep
    # only peaks within relative_height of the loudest strike.
    heights = props["peak_heights"]
    keep = heights >= det["relative_height"] * heights.max()
    return (peaks[keep] / ENV_RATE).tolist()
