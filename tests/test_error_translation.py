"""The web error-translation layer: pipeline exception text becomes plain
guidance on the status page (no config keys, no CLI flags), while the CLI
and the JSON API keep the detailed originals."""

from __future__ import annotations

from swinglab.web.humanize import friendly_error

# The real messages the pipeline raises (see pipeline.py / events.py).
ZERO_STRIKES = (
    "No ball strikes detected in swing.mov. If the video does contain "
    'swings, lower detection.audio_height in config, or pass times '
    'manually: --strikes "12.5,31.0".'
)
NO_AUDIO = (
    "swing.mov has no audio track, so strikes cannot be detected. Pass "
    "strike times manually with --strikes."
)
POSE_FAILED = (
    "Strikes were detected but no swing could be analyzed (pose tracking "
    "failed in every window). Check that the golfer is fully visible; "
    "details: Swing 1 at 3.00s skipped: Only 2 usable pose frames in window"
)


def _no_jargon(help_):
    text = help_.message + " ".join(help_.tips)
    assert "--strikes" not in text
    assert "audio_height" not in text
    assert "config" not in text.lower()


def test_zero_strikes_translates_to_sound_guidance():
    help_ = friendly_error(ZERO_STRIKES)
    assert "No ball strikes" in help_.message  # keeps the honest headline
    _no_jargon(help_)
    tips = " ".join(help_.tips)
    assert "sound ON" in tips
    assert "practice swings are silent" in tips
    assert "closer" in tips
    assert "Advanced options" in tips  # manual times live in the web form
    assert help_.checklist is True


def test_no_audio_track_translates_to_sound_guidance():
    help_ = friendly_error(NO_AUDIO)
    assert "no sound" in help_.message
    _no_jargon(help_)
    assert help_.checklist is True


def test_pose_failure_translates_to_framing_guidance():
    help_ = friendly_error(POSE_FAILED)
    assert "couldn't be tracked" in help_.message
    _no_jargon(help_)
    tips = " ".join(help_.tips)
    assert "whole body" in tips
    assert "hip height" in tips
    assert help_.checklist is True


def test_unknown_errors_pass_through_untranslated():
    raw = "The server restarted while this analysis was waiting and the " \
          "uploaded video is gone. Please upload it again."
    help_ = friendly_error(raw)
    assert help_.message == raw
    assert help_.tips == ()
    assert help_.checklist is False


def test_empty_error_still_says_something():
    help_ = friendly_error(None)
    assert help_.message
    assert friendly_error("").message
