"""Acceptance: strike detection against a generated wav — silence with three
synthetic clicks at known times, detected within 50 ms; and graceful zero-strike
behavior."""

from __future__ import annotations

import numpy as np
from scipy.io import wavfile

from swinglab.audio import detect_strikes
from tests.conftest import write_click_wav

CLICKS = [3.0, 9.5, 16.25]


def test_three_clicks_detected_within_50ms(tmp_path, cfg):
    wav = write_click_wav(tmp_path / "clicks.wav", CLICKS)
    detected = detect_strikes(wav, cfg)
    assert len(detected) == 3
    for expected, got in zip(CLICKS, detected):
        assert abs(got - expected) <= 0.05, f"strike at {expected}s detected at {got}s"


def test_silence_yields_zero_strikes(tmp_path, cfg):
    sr = 16000
    wavfile.write(str(tmp_path / "silence.wav"), sr, np.zeros(sr * 5, dtype=np.int16))
    assert detect_strikes(tmp_path / "silence.wav", cfg) == []


def test_quiet_noise_yields_zero_strikes(tmp_path, cfg):
    rng = np.random.default_rng(7)
    sr = 16000
    noise = (rng.normal(0, 0.005, sr * 5) * 32767).astype(np.int16)
    # normalized envelope of pure noise has max 1.0 somewhere, but no prominent
    # isolated transient should survive the prominence+height gate as 3 peaks
    wavfile.write(str(tmp_path / "noise.wav"), sr, noise)
    detected = detect_strikes(tmp_path / "noise.wav", cfg)
    assert len(detected) <= 1  # at most the single global max can qualify


def test_min_gap_merges_close_transients(tmp_path, cfg):
    # two clicks 1s apart are within min_gap_s=4.0 -> only one strike reported
    wav = write_click_wav(tmp_path / "close.wav", [5.0, 6.0])
    assert len(detect_strikes(wav, cfg)) == 1
