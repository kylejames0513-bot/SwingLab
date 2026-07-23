"""Plain-English coaching notes generated from config thresholds."""

from __future__ import annotations

import math

from .config import Config
from .metrics import SwingMetrics

# Machine-readable flag keys, mirrored by product tags in the gear shop
# (a Shopify product tagged "swinglab:tempo" is recommended when an
# analysis raises FLAG_TEMPO — see swinglab.web.shop).
FLAG_SWAY = "sway"
FLAG_TEMPO = "tempo"
FLAG_HIP_SLIDE = "hip-slide"
FLAG_CONSISTENCY = "consistency"


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


def flag_keys(payload: dict, cfg: Config) -> list[str]:
    """The session's issues as flag keys, from a parsed metrics.json payload.

    Applies the same coaching thresholds as the prose notes above, but in a
    machine-readable form. Tolerates partial/legacy payloads (missing keys,
    NaN written as null) by skipping what it can't read.
    """
    coach = cfg.coaching
    swings = payload.get("swings") or []

    def metric(swing: dict, key: str) -> float | None:
        value = (swing.get("metrics") or {}).get(key)
        return float(value) if isinstance(value, (int, float)) else None

    def any_over(key: str, threshold: float) -> bool:
        return any(
            (v := metric(s, key)) is not None and v > threshold for s in swings
        )

    flags: list[str] = []
    if any_over("head_sway_backswing_sw", coach["sway_warn_sw"]):
        flags.append(FLAG_SWAY)
    if any(
        (v := metric(s, "tempo_ratio")) is not None and v < coach["tempo_warn_below"]
        for s in swings
    ):
        flags.append(FLAG_TEMPO)
    if any_over("hip_slide_backswing_sw", coach["sway_warn_sw"]):
        flags.append(FLAG_HIP_SLIDE)
    tempo_std = ((payload.get("session_stats") or {}).get("tempo_ratio") or {}).get(
        "std"
    )
    if (
        len(swings) >= 2
        and isinstance(tempo_std, (int, float))
        and tempo_std >= coach["tempo_std_praise"]
    ):
        flags.append(FLAG_CONSISTENCY)
    return flags


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
