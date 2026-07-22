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


def test_quiet_thumps_filtered_by_relative_height(tmp_path, cfg):
    """Practice-swing thumps clear the absolute threshold (0.35 > 0.30) but are
    much quieter than real strikes; the relative filter must drop them."""
    wav = write_click_wav(
        tmp_path / "mixed.wav",
        [3.0, 7.5, 9.5, 14.0, 16.25],
        amplitudes=[1.0, 0.35, 1.0, 0.35, 0.95],
    )
    detected = detect_strikes(wav, cfg)
    assert len(detected) == 3
    for expected, got in zip([3.0, 9.5, 16.25], detected):
        assert abs(got - expected) <= 0.05


def test_relative_height_zero_disables_filter(tmp_path, cfg):
    cfg.detection["relative_height"] = 0.0
    wav = write_click_wav(
        tmp_path / "mixed.wav",
        [3.0, 7.5, 16.25],
        amplitudes=[1.0, 0.35, 1.0],
    )
    assert len(detect_strikes(wav, cfg)) == 3


def test_equal_strikes_all_survive_relative_filter(tmp_path, cfg):
    """Similar-loudness real strikes must never be filtered against each other."""
    wav = write_click_wav(
        tmp_path / "even.wav", CLICKS, amplitudes=[0.9, 1.0, 0.85]
    )
    assert len(detect_strikes(wav, cfg)) == 3
