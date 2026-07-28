"""Stable entrypoints for the CaddieInsight swing-analysis engine.

The implementation remains in :mod:`swinglab.pipeline` during the incremental
package migration. Existing imports keep working; new callers can depend on
this narrower boundary.
"""

from ..pipeline import (
    SessionResult,
    VideoTooLongError,
    ZeroStrikesError,
    analyze_video,
)

__all__ = [
    "SessionResult",
    "VideoTooLongError",
    "ZeroStrikesError",
    "analyze_video",
]
