"""Experience-level context — display framing only.

The level a golfer picked at upload ("New to golf" / "Improving" /
"Experienced") is stored on the job and in metrics.json's ``meta`` block,
shown as a chip on the report, and sets ONE framing line above the metrics
table. Like the club (swinglab.clubs), it is context for the reader, not an
input to the analysis: no threshold moves and no number changes because a
level was picked. The benchmarks stay the benchmarks — the framing just
tells a beginner the gap is normal and an experienced player where the
reference lines sit.
"""

from __future__ import annotations

# key (stored value) -> display label. Keys are what the upload form posts
# and what lands in the database and metrics.json.
LEVEL_LABELS: dict[str, str] = {
    "new": "New to golf",
    "improving": "Improving",
    "experienced": "Experienced",
}

# One honest framing line per level, rendered above the metrics table.
# "improving" gets none — the report's default voice already is that
# register. Never a threshold, never a score: framing only.
LEVEL_NOTES: dict[str, str] = {
    "new": (
        "You're new to this, so expect real gaps to the tour benchmarks — "
        "that's normal, not a problem. Watch the trend across sessions, "
        "not the gap on day one."
    ),
    "experienced": (
        "Benchmarks below are stated plainly: tour-average references with "
        "fixed flag thresholds. The deltas and per-swing spread are where "
        "the work usually is."
    ),
}


def level_label(key: str | None) -> str | None:
    """Display label for a stored level key, or None when unset/unknown."""
    if not key:
        return None
    return LEVEL_LABELS.get(key)


def level_note(key: str | None) -> str | None:
    """The framing line for a stored level key, or None when the default
    report voice is the right one (unset, unknown, or "improving")."""
    if not key:
        return None
    return LEVEL_NOTES.get(key)
