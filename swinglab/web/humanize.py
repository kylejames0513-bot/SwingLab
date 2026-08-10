"""Translate pipeline errors into plain guidance for the web UI.

The pipeline's exception messages are written for the CLI, where config
keys and ``--strikes`` flags are the right level of detail. On the web
status page they read as jargon, so this layer maps the known failure
modes to plain-English guidance plus a pointer at the filming checklist.
The CLI keeps its detailed messages; the JSON API keeps the raw error too
(machine consumers may want it) — only the rendered page is translated.

Unknown *pipeline* errors pass through untouched: honest raw beats invented
friendly. That rule holds for messages written for a human — "No audio track",
"analysis limit" — and breaks for messages written for a machine.

Two kinds of error reach this function that were never meant for a golfer: a
Python traceback (``jobs.py`` stores ``traceback.format_exc()`` on the job so
the ops JSON and Sentry keep it) and internal recovery language about report
publication. Passing those through disclosed absolute server paths, module
names and internal function names on the status page — and, because the panel
renders them in a ``<p>``, collapsed a multi-line traceback into one unreadable
run-on line. ``_is_internal`` catches both and substitutes a generic apology.

The raw text is deliberately NOT scrubbed anywhere else: ``job.error`` keeps
it, ``/api/session/{id}`` keeps it, and the process log keeps the full
untruncated traceback. Only the rendered page is translated.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorHelp:
    message: str                       # plain-English what happened
    tips: tuple[str, ...] = ()         # concrete things to try
    checklist: bool = False            # link the filming checklist?


_SOUND_TIPS = (
    "Film with the sound ON — strikes are found by ear, from the crack of "
    "impact.",
    "Make sure the clip has real ball strikes in it (practice swings are "
    "silent).",
    "Try filming a little closer so the strike is clearly audible.",
    "If the strikes are definitely there, type their times into "
    "“Advanced options” when you upload (seconds into the clip, "
    "e.g. 12.5, 31).",
)

_BODY_TIPS = (
    "Keep your whole body in the frame — head to feet — from setup through "
    "the finish.",
    "Put the phone at about hip height, a few steps back.",
    "Good, even light helps the tracking; avoid filming straight into the "
    "sun.",
)

_TOO_LONG_TIPS = (
    "Trim the clip to just the swings you want analyzed and upload it "
    "again — your phone's editor can do this in seconds.",
    "A few swings per clip is the sweet spot; shorter clips also process "
    "much faster.",
)

_RETRY_TIPS = (
    "Upload the same clip again — most of these clear on a second run.",
    "If it fails again, email support and mention roughly when you uploaded; "
    "the failure is already recorded on our side.",
)

# Substrings that mark an error as written for an operator, not a golfer. The
# traceback marker is the load-bearing one: it is the text Python itself emits,
# so it catches any future code path that stores a formatted exception without
# that path having to know this module exists.
_INTERNAL_MARKERS = (
    "traceback (most recent call last)",
    "unexpected error during analysis",
    "report publication could not be validated",
    "report presentation version",
)


def _is_internal(low: str) -> bool:
    return any(marker in low for marker in _INTERNAL_MARKERS)


def friendly_error(raw: str | None) -> ErrorHelp:
    """Plain guidance for a failed job's error text. Known pipeline
    failures get translated (no config keys, no CLI flags); anything
    unrecognized is returned as-is."""
    raw = (raw or "").strip()
    low = raw.lower()

    # Checked before the pipeline branches, not after: if the text is a
    # traceback, nothing it happens to contain should steer the message.
    if _is_internal(low):
        return ErrorHelp(
            message=(
                "Something went wrong on our side while analyzing this clip "
                "— not with your filming. The failure has been recorded."
            ),
            tips=_RETRY_TIPS,
        )

    if "analysis limit" in low:
        # VideoTooLongError — keep the honest headline (it carries the real
        # numbers) but strip the config-key pointer meant for operators.
        headline = raw.split(" (")[0].rstrip(".") + "."
        return ErrorHelp(message=headline, tips=_TOO_LONG_TIPS)
    if "no audio track" in low:
        return ErrorHelp(
            message=(
                "This clip has no sound, so no ball strikes could be heard "
                "— that's how swings are found."
            ),
            tips=_SOUND_TIPS,
            checklist=True,
        )
    if "no ball strikes" in low and "no swing could be analyzed" not in low:
        return ErrorHelp(
            message=(
                "No ball strikes could be heard in this clip — that's how "
                "swings are found, so there's nothing to analyze yet."
            ),
            tips=_SOUND_TIPS,
            checklist=True,
        )
    if (
        "no swing could be analyzed" in low
        or "usable pose frames" in low
        or "fully in frame" in low
        or "no takeaway found" in low
        or "tracked frames" in low
    ):
        return ErrorHelp(
            message=(
                "The strikes were found, but the golfer couldn't be tracked "
                "well enough to measure a swing."
            ),
            tips=_BODY_TIPS,
            checklist=True,
        )
    if raw:
        return ErrorHelp(message=raw)
    return ErrorHelp(
        message="The analysis failed for an unknown reason — try uploading "
                "the clip again."
    )
