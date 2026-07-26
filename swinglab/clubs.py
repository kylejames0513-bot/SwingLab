"""Club context — display labels only.

The club a session was filmed with is stored on the job and in
metrics.json's ``meta`` block and shown as a chip on the report, the
session list, and the progress dashboard. It is context for the reader,
not an input to the analysis: there are no per-club thresholds yet, and
no number changes because a club was picked.
"""

from __future__ import annotations

# key (stored value) -> display label. Keys are what the upload form posts
# and what lands in the database and metrics.json.
CLUB_LABELS: dict[str, str] = {
    "driver": "Driver",
    "fairway-wood": "Fairway wood",
    "hybrid": "Hybrid",
    "iron": "Iron",
    "wedge": "Wedge",
}


def club_label(key: str | None) -> str | None:
    """Display label for a stored club key, or None when unset/unknown."""
    if not key:
        return None
    return CLUB_LABELS.get(key)
