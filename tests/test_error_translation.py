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
    """Still true for errors written for a human — the rule is unchanged."""
    raw = "The server restarted while this analysis was waiting and the " \
          "uploaded video is gone. Please upload it again."
    help_ = friendly_error(raw)
    assert help_.message == raw
    assert help_.tips == ()
    assert help_.checklist is False


# -- errors written for an operator, not a golfer ---------------------------
# jobs.py deliberately stores traceback.format_exc() on the job so the ops
# JSON and Sentry keep it. Passing that through to the status page disclosed
# absolute server paths, module and function names to whoever's clip tripped
# the bug — and rendered as one collapsed run-on line, because the panel is a
# <p>. Pass-through is right for pipeline text and wrong for these.

TRACEBACK_ERROR = (
    "Unexpected error during analysis:\n"
    "Traceback (most recent call last):\n"
    '  File "/srv/app/swinglab/web/jobs.py", line 1330, in _run\n'
    "    result = analyze(job.source)\n"
    "RuntimeError: boom"
)


def test_a_traceback_never_reaches_the_golfer():
    help_ = friendly_error(TRACEBACK_ERROR)

    assert "Traceback" not in help_.message
    assert "/srv/app" not in help_.message
    assert "jobs.py" not in help_.message
    assert "RuntimeError" not in help_.message
    assert "our side" in help_.message
    # It says the filming was fine, because it was.
    assert "not with your filming" in help_.message
    assert help_.tips


def test_the_traceback_marker_alone_is_enough():
    """No prefix, no known wording — just Python's own text.

    This is the branch that makes the guard hold for code paths written
    later, which will not know this module exists.
    """
    help_ = friendly_error(
        'Traceback (most recent call last):\n  File "x.py", line 1\nOSError'
    )
    assert "Traceback" not in help_.message
    assert "our side" in help_.message


def test_a_traceback_mentioning_a_pipeline_phrase_still_stays_internal():
    """Order matters: internal is checked before the pipeline branches.

    A traceback that happens to contain 'no audio track' must not be dressed
    up as filming advice — the golfer's filming was not the problem.
    """
    help_ = friendly_error(
        "Unexpected error during analysis:\n"
        "Traceback (most recent call last):\n"
        "ValueError: no audio track in probe result"
    )
    assert "Traceback" not in help_.message
    assert "our side" in help_.message
    assert help_.checklist is False


def test_internal_recovery_language_is_translated_too():
    help_ = friendly_error(
        "Report publication could not be validated; this analysis remains "
        "active for safe recovery and no report was exposed."
    )
    assert "publication" not in help_.message.lower()
    assert "our side" in help_.message


def test_empty_error_still_says_something():
    help_ = friendly_error(None)
    assert help_.message
    assert friendly_error("").message
