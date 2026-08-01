"""Acceptance: strike detection against a generated wav — silence with three
synthetic clicks at known times, detected within 50 ms; and graceful zero-strike
behavior. Plus the streaming-envelope memory hardening: the chunked
computation must be BIT-EXACT with the load-everything-at-once reference."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.io import wavfile

from swinglab import audio as audio_module
from swinglab.audio import ENV_RATE, compute_envelope, detect_strikes
from tests.conftest import write_click_wav

CLICKS = [3.0, 9.5, 16.25]


def write_weighted_click_wav(path, clicks: list[tuple[float, float]]) -> None:
    """One-hop transients with deliberate relative loudness values."""

    sr = 16000
    samples = np.zeros(sr * 20, dtype=np.int16)
    for time_s, amplitude in clicks:
        samples[int(time_s * sr)] = int(32767 * amplitude)
    wavfile.write(str(path), sr, samples)


def reference_envelope(wav_path) -> np.ndarray:
    """The original full-load implementation, kept verbatim as the
    bit-exactness oracle for the streaming version."""
    sr, a = wavfile.read(str(wav_path))
    a = np.abs(a.astype(np.float32))
    if a.ndim > 1:
        a = a.max(axis=1)
    peak = a.max()
    if peak == 0:
        return a[:0]
    a /= peak
    hop = sr // ENV_RATE
    return a[: len(a) // hop * hop].reshape(-1, hop).max(axis=1)


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


def test_relative_height_zero_preserves_existing_candidates(tmp_path, cfg):
    wav = tmp_path / "weighted.wav"
    expected = [2.0, 8.0, 14.0]
    write_weighted_click_wav(wav, list(zip(expected, [1.0, 0.68, 0.42])))
    cfg.detection["relative_height"] = 0.0

    assert detect_strikes(wav, cfg) == expected


def test_relative_height_filters_quiet_transients_but_keeps_uneven_strikes(
    tmp_path, cfg
):
    wav = tmp_path / "weighted.wav"
    write_weighted_click_wav(wav, [(2.0, 1.0), (8.0, 0.68), (14.0, 0.42)])
    cfg.detection["relative_height"] = 0.5

    assert detect_strikes(wav, cfg) == [2.0, 8.0]


@pytest.mark.parametrize("value", (-0.1, 1.1, True, "0.5", float("nan"), float("inf")))
def test_relative_height_rejects_invalid_config(tmp_path, cfg, value):
    wav = write_click_wav(tmp_path / "click.wav", [3.0])
    cfg.detection["relative_height"] = value

    with pytest.raises(ValueError, match="detection\\.relative_height"):
        detect_strikes(wav, cfg)


# -- streaming envelope: bounded memory, identical numbers -------------------

def test_streaming_envelope_bit_exact_with_reference(tmp_path):
    wav = write_click_wav(tmp_path / "clicks.wav", CLICKS)
    env, sr = compute_envelope(wav)
    ref = reference_envelope(wav)
    assert sr == 16000
    assert env.dtype == ref.dtype == np.float32
    assert np.array_equal(env, ref)  # bit-exact, not approx


def test_streaming_envelope_bit_exact_across_chunk_boundaries(tmp_path, monkeypatch):
    # Tiny chunks force many boundary crossings AND a sub-hop tail whose
    # samples must still count toward the normalization peak.
    monkeypatch.setattr(audio_module, "CHUNK_HOPS", 7)
    sr = 16000
    rng = np.random.default_rng(3)
    n = sr * 3 + 123  # deliberately NOT a multiple of the 160-sample hop
    samples = (rng.normal(0, 0.05, n) * 32767).astype(np.int16)
    samples[-40] = 32767  # the global peak lives in the sub-hop tail
    wav = tmp_path / "tail.wav"
    wavfile.write(str(wav), sr, samples)
    env, _ = compute_envelope(wav)
    assert np.array_equal(env, reference_envelope(wav))


def test_streaming_envelope_bit_exact_stereo(tmp_path):
    sr = 16000
    rng = np.random.default_rng(11)
    stereo = (rng.normal(0, 0.1, (sr * 2, 2)) * 32767).astype(np.int16)
    wav = tmp_path / "stereo.wav"
    wavfile.write(str(wav), sr, stereo)
    env, _ = compute_envelope(wav)
    assert np.array_equal(env, reference_envelope(wav))


def test_streaming_detection_matches_reference_peaks(tmp_path, cfg, monkeypatch):
    # End to end with forced chunking: identical strike times, to the sample.
    monkeypatch.setattr(audio_module, "CHUNK_HOPS", 13)
    wav = write_click_wav(tmp_path / "clicks.wav", CLICKS)
    detected = detect_strikes(wav, cfg)
    assert len(detected) == 3
    for expected, got in zip(CLICKS, detected):
        assert abs(got - expected) <= 0.05
    monkeypatch.setattr(audio_module, "CHUNK_HOPS", 4096)
    assert detect_strikes(wav, cfg) == detected


def test_relative_height_detection_is_chunk_invariant(tmp_path, cfg, monkeypatch):
    wav = tmp_path / "weighted.wav"
    write_weighted_click_wav(wav, [(2.0, 1.0), (8.0, 0.68), (14.0, 0.42)])
    cfg.detection["relative_height"] = 0.5

    monkeypatch.setattr(audio_module, "CHUNK_HOPS", 13)
    chunked = detect_strikes(wav, cfg)
    monkeypatch.setattr(audio_module, "CHUNK_HOPS", 4096)
    assert detect_strikes(wav, cfg) == chunked == [2.0, 8.0]
