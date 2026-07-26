"""Translate pipeline errors into plain guidance for the web UI.

The pipeline's exception messages are written for the CLI, where config
keys and ``--strikes`` flags are the right level of detail. On the web
status page they read as jargon, so this layer maps the known failure
modes to plain-English guidance plus a pointer at the filming checklist.
The CLI keeps its detailed messages; the JSON API keeps the raw error too
(machine consumers may want it) — only the rendered page is translated.

Unknown errors pass through untouched: honest raw beats invented friendly.
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


def friendly_error(raw: str | None) -> ErrorHelp:
    """Plain guidance for a failed job's error text. Known pipeline
    failures get translated (no config keys, no CLI flags); anything
    unrecognized is returned as-is."""
    raw = (raw or "").strip()
    low = raw.lower()

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
