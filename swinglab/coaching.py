"""Plain-English coaching notes generated from config thresholds."""

from __future__ import annotations

import math

from .config import Config
from .metrics import SwingMetrics


def swing_notes(m: SwingMetrics, cfg: Config) -> list[str]:
    coach = cfg.coaching
    notes: list[str] = []

    sway = m.head_sway_backswing_sw
    if not math.isnan(sway) and sway > coach["sway_warn_sw"]:
        notes.append(
            f"Head sways {sway:.2f} shoulder widths away from the target going "
            f"back (flagged beyond {coach['sway_warn_sw']:.2f}). Feel like the "
            "head stays inside the trail foot at the top."
        )

    tempo = m.tempo_ratio
    if not math.isnan(tempo) and tempo < coach["tempo_warn_below"]:
        notes.append(
            f"Tempo ratio {tempo:.1f}:1 is quicker than the {coach['tempo_warn_below']:.1f} "
            f"threshold (tour average is about {coach['tempo_target']:.1f}:1 with a "
            "downswing near 0.25 s). Let the backswing finish before starting down."
        )

    slide = m.hip_slide_backswing_sw
    if not math.isnan(slide) and slide > coach["sway_warn_sw"]:
        notes.append(
            f"Hips slide {slide:.2f} shoulder widths away from the target in the "
            "backswing. Turn into the trail hip rather than sliding across it."
        )

    if not notes:
        notes.append(
            "No flags on this swing — tempo and lateral movement are inside the "
            "configured thresholds."
        )
    return notes


def session_notes(
    all_metrics: list[SwingMetrics], stats: dict[str, dict[str, float]], cfg: Config
) -> list[str]:
    coach = cfg.coaching
    notes: list[str] = []
    tempo_stats = stats.get("tempo_ratio")
    if len(all_metrics) >= 2 and tempo_stats is not None:
        if tempo_stats["std"] < coach["tempo_std_praise"]:
            notes.append(
                f"Tempo is impressively consistent across swings (std dev "
                f"{tempo_stats['std']:.2f}). Consistency like this is a real "
                "asset — low variance is itself a finding worth keeping."
            )
        else:
            notes.append(
                f"Tempo varies noticeably between swings (std dev "
                f"{tempo_stats['std']:.2f}). Pick one count and rehearse it."
            )
    return notes
