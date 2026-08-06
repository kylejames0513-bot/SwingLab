"""Plain-English coaching notes generated from config thresholds."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from .config import Config
from .metrics import (
    ANGLE_DTL,
    ANGLE_FACE_ON,
    SwingMetrics,
    finite_float,
    session_stats,
)

# Camera-angle honesty copy. The DTL note goes on every down-the-line
# session; the mismatch note fires only when the address pose strongly
# disagrees with the angle the golfer picked (see
# metrics.apparent_camera_angle — conservative on purpose).
DTL_SESSION_NOTE = (
    "Filmed down the line — body-drift and angle numbers are measured "
    "face-on, so this report covers tempo and rhythm only. Film face-on for "
    "the full report."
)

_ANGLE_PHRASES = {ANGLE_FACE_ON: "face-on", ANGLE_DTL: "down the line"}

# Appended to a swing's coaching notes when pose.tracking_quality judges the
# track unreliable (heavy frame loss, or a core-landmark jump that means the
# detector locked onto someone/something else mid-swing). Honest, not scary:
# the numbers are still shown, flagged as possibly off.
TRACKING_UNSTABLE_NOTE = (
    "Tracking was unstable for this swing — numbers may be off; film with a "
    "clear view of your full body, nobody else in frame."
)


def angle_mismatch_note(chosen: str, apparent: str) -> str:
    """The low-confidence warning when the footage looks like the OTHER
    camera angle. Callers only pass a genuine disagreement."""
    return (
        f"Low confidence: this clip looks like it was filmed "
        f"{_ANGLE_PHRASES.get(apparent, apparent)}, but it was uploaded as "
        f"{_ANGLE_PHRASES.get(chosen, chosen)} — numbers may not mean what "
        "they say. Re-film, or re-upload with the right camera-angle setting."
    )


# Machine-readable flag keys, mirrored by product tags in the gear shop
# (a Shopify product tagged "swinglab:tempo" is recommended when an
# analysis raises FLAG_TEMPO — see swinglab.web.shop).
FLAG_SWAY = "sway"
FLAG_TEMPO = "tempo"
FLAG_HIP_SLIDE = "hip-slide"
FLAG_HEAD_DIP = "head-dip"
FLAG_ARM_EXTENSION = "arm-extension"
FLAG_SHOULDER_TILT = "shoulder-tilt"
FLAG_BALANCE = "balance"
FLAG_CONSISTENCY = "consistency"


PRIORITY_RULE_LEGACY = 1
PRIORITY_RULE_CLUB_AWARE = 2
SUPPORTED_PRIORITY_RULE_VERSIONS = frozenset(
    {PRIORITY_RULE_LEGACY, PRIORITY_RULE_CLUB_AWARE}
)

# Rule 2 changes only the stable order inside a severity band.  It does not
# alter thresholds, measurements, card copy, or the ordering of severity
# bands themselves.  Clubs absent from this map deliberately retain rule 1.
_CLUB_TIE_PRIORITIES: dict[str, tuple[str, ...]] = {
    "driver": (FLAG_BALANCE,),
    "fairway-wood": (FLAG_HEAD_DIP,),
    "hybrid": (FLAG_HEAD_DIP,),
    "iron": (FLAG_SWAY, FLAG_HIP_SLIDE),
}


def priority_rule_version(cfg: Config) -> int:
    """Select the immutable coaching priority rule with an exact bool gate."""

    return (
        PRIORITY_RULE_CLUB_AWARE
        if cfg.coaching.get("club_aware_enabled") is True
        else PRIORITY_RULE_LEGACY
    )


def validate_priority_rule_version(value: object) -> int:
    """Accept only priority rules this release can faithfully replay."""

    if type(value) is not int or value not in SUPPORTED_PRIORITY_RULE_VERSIONS:
        raise ValueError(f"Unsupported coaching priority rule: {value!r}")
    return value


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

    dip = m.head_dip_sw
    if not math.isnan(dip) and dip > coach["head_dip_warn_sw"]:
        notes.append(
            f"Head drops {dip:.2f} shoulder widths between address and impact "
            f"(flagged beyond {coach['head_dip_warn_sw']:.2f}). A small squat is "
            "normal — this much dip moves the swing's low point; feel the chest "
            "stay tall through the ball."
        )

    arm = m.lead_arm_angle_deg
    if not math.isnan(arm) and arm < coach["lead_arm_warn_deg"]:
        notes.append(
            f"Lead arm is bent to {arm:.0f}\N{DEGREE SIGN} at impact (flagged under "
            f"{coach['lead_arm_warn_deg']:.0f}\N{DEGREE SIGN}; 180\N{DEGREE SIGN} is straight as seen "
            "from the camera). Keep the lead arm long through the strike — width "
            "at impact is where contact lives."
        )

    tilt_i, tilt_d = m.shoulder_tilt_impact_deg, m.shoulder_tilt_delta_deg
    if not math.isnan(tilt_i) and tilt_i < coach["shoulder_tilt_impact_min_deg"]:
        notes.append(
            f"Shoulders are nearly level at impact ({tilt_i:.0f}\N{DEGREE SIGN}, measured "
            f"face-on, vs the {coach['shoulder_tilt_impact_min_deg']:.0f}\N{DEGREE SIGN} minimum). "
            "The trail shoulder should be clearly lower at the strike — keep the "
            "tilt rather than standing up out of it."
        )
    elif not math.isnan(tilt_d) and tilt_d < 0:
        notes.append(
            f"Shoulder tilt fell from {m.shoulder_tilt_address_deg:.0f}\N{DEGREE SIGN} at address "
            f"to {tilt_i:.0f}\N{DEGREE SIGN} at impact. The trail shoulder should work down "
            "through the ball, not level out."
        )

    bal = m.finish_balance_sw
    if not math.isnan(bal) and bal > coach["finish_balance_warn_sw"]:
        notes.append(
            f"Feet drift {bal:.2f} shoulder widths during the finish hold (flagged "
            f"beyond {coach['finish_balance_warn_sw']:.2f}). A held, quiet finish is "
            "the cheapest proof of a swing in balance — hold it until the ball lands."
        )

    if not notes:
        notes.append(
            "No measured coaching value crossed its configured threshold on "
            "this swing."
        )
    # The promised low-confidence line: when target-direction inference hit
    # its last-resort fallback, the toward/away signs are a guess. Only worth
    # saying when a lateral number was actually measured (down-the-line
    # sessions read NaN everywhere lateral, so the signs carry nothing).
    if not m.target_confident and not math.isnan(m.head_sway_backswing_sw):
        notes.append(
            "Low confidence: which direction the target is couldn't be read "
            "from this swing, so the toward/away signs on sway and slide are "
            "a best guess. If they look flipped, re-film with the full "
            "follow-through in frame."
        )
    return notes


@dataclass(frozen=True)
class StrengthCard:
    key: str
    metric: str
    display_name: str
    text: str


def strength_cards(
    all_metrics: list[SwingMetrics],
    cfg: Config,
    stats: dict[str, dict[str, float]] | None = None,
) -> list[StrengthCard]:
    """One short positive line per metric family that was measured AND came
    in inside its threshold this session — the mirror of the warn notes,
    same voice, same honest numbers. Returns [] when nothing qualifies:
    never fake praise.
    """
    coach = cfg.coaching
    if stats is None:
        stats = session_stats(all_metrics)
    cards: list[StrengthCard] = []

    def measured(attr: str) -> list[float]:
        return [
            v for m in all_metrics if not math.isnan(v := getattr(m, attr))
        ]

    sway = measured("head_sway_backswing_sw")
    if sway and max(sway) <= coach["sway_warn_sw"]:
        cards.append(StrengthCard(
            "sway", "head_sway_backswing_sw", "Head sway",
            f"Head sway peaks at {max(sway):.2f} shoulder widths going back — "
            f"inside the {coach['sway_warn_sw']:.2f} line. The turn is staying "
            "centered over the ball."
        ))

    tempo = measured("tempo_ratio")
    if tempo and min(tempo) >= coach["tempo_warn_below"]:
        cards.append(StrengthCard(
            "tempo", "tempo_ratio", "Tempo",
            f"Tempo holds at {min(tempo):.2f}:1 or better on every measured "
            "swing — at "
            f"or above the {coach['tempo_warn_below']:.1f}:1 line, moving "
            f"toward the {coach['tempo_target']:.1f}:1 reference. The "
            "backswing is getting time to finish."
        ))

    slide = measured("hip_slide_backswing_sw")
    if slide and max(slide) <= coach["sway_warn_sw"]:
        cards.append(StrengthCard(
            "hip-slide", "hip_slide_backswing_sw", "Hip slide",
            f"Hip slide stays at {max(slide):.2f} shoulder widths or less in "
            f"the backswing — inside the {coach['sway_warn_sw']:.2f} line. "
            "The hips are turning, not drifting."
        ))

    dip = measured("head_dip_sw")
    if dip and max(dip) <= coach["head_dip_warn_sw"]:
        cards.append(StrengthCard(
            "head-dip", "head_dip_sw", "Head dip",
            f"Head dip tops out at {max(dip):.2f} shoulder widths into impact "
            f"— inside the {coach['head_dip_warn_sw']:.2f} line. Height is "
            "holding through the strike."
        ))

    arm = measured("lead_arm_angle_deg")
    if arm and min(arm) >= coach["lead_arm_warn_deg"]:
        cards.append(StrengthCard(
            "arm-extension", "lead_arm_angle_deg", "Lead arm",
            f"Lead arm stays at {min(arm):.0f}\N{DEGREE SIGN} or straighter at "
            f"impact (180\N{DEGREE SIGN} is straight) — width through the "
            "ball is there."
        ))

    tilt_measured = measured("shoulder_tilt_impact_deg")
    tilt_fired = any(
        (
            not math.isnan(ti := m.shoulder_tilt_impact_deg)
            and ti < coach["shoulder_tilt_impact_min_deg"]
        )
        or (not math.isnan(td := m.shoulder_tilt_delta_deg) and td < 0)
        for m in all_metrics
    )
    if tilt_measured and not tilt_fired:
        cards.append(StrengthCard(
            "shoulder-tilt", "shoulder_tilt_impact_deg", "Shoulder tilt",
            f"Shoulder tilt holds at {min(tilt_measured):.0f}\N{DEGREE SIGN} "
            "or more at impact — the trail shoulder is working down through "
            "the ball."
        ))

    bal = measured("finish_balance_sw")
    if bal and max(bal) <= coach["finish_balance_warn_sw"]:
        cards.append(StrengthCard(
            "balance", "finish_balance_sw", "Finish balance",
            f"Finish drift stays at {max(bal):.2f} shoulder widths or less — "
            "the swing is ending somewhere the body can hold. Keep holding "
            "every finish."
        ))

    tempo_stats = stats.get("tempo_ratio")
    if (
        len(tempo) >= 2
        and tempo_stats is not None
        and tempo_stats["std"] < coach["tempo_std_praise"]
    ):
        cards.append(StrengthCard(
            "consistency", "tempo_ratio", "Swing-to-swing consistency",
            f"Tempo is consistent swing to swing (\N{PLUS-MINUS SIGN}"
            f"{tempo_stats['std']:.2f}) — same clock every time. That's an "
            "asset worth protecting."
        ))

    return cards


def praise_notes(
    all_metrics: list[SwingMetrics],
    cfg: Config,
    stats: dict[str, dict[str, float]] | None = None,
) -> list[str]:
    """The legacy praise text view of the typed strengths."""
    return [card.text for card in strength_cards(all_metrics, cfg, stats)]


def flag_keys(
    payload: dict, cfg: Config, *, angle: str | None = None
) -> list[str]:
    """The session's issues as flag keys, from a parsed metrics.json payload.

    Applies the same coaching thresholds as the prose notes above, but in a
    machine-readable form. Tolerates partial/legacy payloads (missing keys,
    NaN written as null) by skipping what it can't read.
    """
    coach = cfg.coaching
    swings = payload.get("swings")
    if not isinstance(swings, list):
        swings = []

    def metric(swing: dict, key: str) -> float | None:
        if not isinstance(swing, dict):
            return None
        metrics = swing.get("metrics") or {}
        if not isinstance(metrics, dict):
            return None
        return finite_float(metrics.get(key))

    def any_over(key: str, threshold: float) -> bool:
        return any(
            (v := metric(s, key)) is not None and v > threshold for s in swings
        )

    def any_under(key: str, threshold: float) -> bool:
        return any(
            (v := metric(s, key)) is not None and v < threshold for s in swings
        )

    flags: list[str] = []
    if any_over("head_sway_backswing_sw", coach["sway_warn_sw"]):
        flags.append(FLAG_SWAY)
    if any_under("tempo_ratio", coach["tempo_warn_below"]):
        flags.append(FLAG_TEMPO)
    if any_over("hip_slide_backswing_sw", coach["sway_warn_sw"]):
        flags.append(FLAG_HIP_SLIDE)
    if any_over("head_dip_sw", coach["head_dip_warn_sw"]):
        flags.append(FLAG_HEAD_DIP)
    if any_under("lead_arm_angle_deg", coach["lead_arm_warn_deg"]):
        flags.append(FLAG_ARM_EXTENSION)
    if any_under(
        "shoulder_tilt_impact_deg", coach["shoulder_tilt_impact_min_deg"]
    ) or any_under("shoulder_tilt_delta_deg", 0.0):
        flags.append(FLAG_SHOULDER_TILT)
    if any_over("finish_balance_sw", coach["finish_balance_warn_sw"]):
        flags.append(FLAG_BALANCE)
    tempo_values = [
        value
        for swing in swings
        if (value := metric(swing, "tempo_ratio")) is not None
    ]
    tempo_std = None
    if len(tempo_values) >= 2:
        try:
            candidate_std = statistics.pstdev(tempo_values)
        except (OverflowError, TypeError, ValueError):
            candidate_std = float("nan")
        if math.isfinite(candidate_std):
            tempo_std = round(candidate_std, 3)
    if (
        len(tempo_values) >= 2
        and tempo_std is not None
        and tempo_std >= coach["tempo_std_praise"]
    ):
        flags.append(FLAG_CONSISTENCY)
    meta = payload.get("meta") or {}
    resolved_angle = angle or (
        meta.get("angle") or meta.get("camera_angle")
        if isinstance(meta, dict)
        else None
    )
    if resolved_angle == ANGLE_DTL:
        flags = [
            flag
            for flag in flags
            if flag in (FLAG_TEMPO, FLAG_CONSISTENCY)
        ]
    return flags


def session_flags(
    all_metrics: list[SwingMetrics], stats: dict[str, dict[str, float]], cfg: Config
) -> list[str]:
    """The session's issues as flag keys, from in-memory SwingMetrics.

    Same thresholds and keys as :func:`flag_keys` (which reads a parsed
    metrics.json payload); this variant is what the report renderer uses to
    pick practice-plan drills (see swinglab.drills). NaN metrics never flag.
    """
    coach = cfg.coaching

    def any_over(attr: str, threshold: float) -> bool:
        return any(
            not math.isnan(v := getattr(m, attr)) and v > threshold
            for m in all_metrics
        )

    def any_under(attr: str, threshold: float) -> bool:
        return any(
            not math.isnan(v := getattr(m, attr)) and v < threshold
            for m in all_metrics
        )

    flags: list[str] = []
    if any_over("head_sway_backswing_sw", coach["sway_warn_sw"]):
        flags.append(FLAG_SWAY)
    if any_under("tempo_ratio", coach["tempo_warn_below"]):
        flags.append(FLAG_TEMPO)
    if any_over("hip_slide_backswing_sw", coach["sway_warn_sw"]):
        flags.append(FLAG_HIP_SLIDE)
    if any_over("head_dip_sw", coach["head_dip_warn_sw"]):
        flags.append(FLAG_HEAD_DIP)
    if any_under("lead_arm_angle_deg", coach["lead_arm_warn_deg"]):
        flags.append(FLAG_ARM_EXTENSION)
    if any_under(
        "shoulder_tilt_impact_deg", coach["shoulder_tilt_impact_min_deg"]
    ) or any_under("shoulder_tilt_delta_deg", 0.0):
        flags.append(FLAG_SHOULDER_TILT)
    if any_over("finish_balance_sw", coach["finish_balance_warn_sw"]):
        flags.append(FLAG_BALANCE)
    tempo_stats = stats.get("tempo_ratio")
    if (
        len(all_metrics) >= 2
        and tempo_stats is not None
        and tempo_stats["std"] >= coach["tempo_std_praise"]
    ):
        flags.append(FLAG_CONSISTENCY)
    return flags


# Why-it-matters / fix copy for the issue cards: exactly 2 sentences + 1
# sentence per flag, honest about what a single face-on camera can measure.
WHY_TEXT = {
    FLAG_SWAY: (
        "Lateral head drift going back moves the swing's centre, and the "
        "downswing has a quarter of a second to find its way home. Keeping the "
        "head inside the trail foot makes the turn repeatable instead of a "
        "recovery."
    ),
    FLAG_TEMPO: (
        "A backswing that never finishes forces the downswing to start from a "
        "moving platform, and everything after that is timing. The 3:1 ratio is "
        "not magic — it is simply the range where the swing has time to change "
        "direction."
    ),
    FLAG_HIP_SLIDE: (
        "Sliding the hips away from the target instead of turning them loads "
        "the trail side somewhere it cannot unload from. A turn stays over the "
        "trail hip; a slide has to be un-slid before impact."
    ),
    FLAG_HEAD_DIP: (
        "The head dropping between address and impact lowers the whole swing's "
        "centre, and the arc's low point drops with it. A small squat is "
        "normal; a dip this size means the strike depends on a late rescue."
    ),
    FLAG_ARM_EXTENSION: (
        "A lead arm this bent at impact shortens the swing's radius at the "
        "exact moment that decides contact. Width through the ball is what "
        "makes the strike repeatable; folding the arm trades it for a "
        "last-instant flip."
    ),
    FLAG_SHOULDER_TILT: (
        "At impact the trail shoulder should be clearly lower than the lead; "
        "shoulders that are level or reversed usually mean the body stopped and "
        "the hands are scooping. Measured face-on, that shows up as a flat "
        "shoulder line at the strike."
    ),
    FLAG_BALANCE: (
        "Feet moving during the finish hold mean the swing ended somewhere the "
        "body could not support. Balance at the finish is the cheapest summary "
        "of everything that happened before it."
    ),
    FLAG_CONSISTENCY: (
        "The swings in this session ran on noticeably different tempos, which "
        "makes every other number harder to repeat. The variance itself is the "
        "finding: same body, different clock."
    ),
}

FIX_TEXT = {
    FLAG_SWAY: (
        "Turn into the trail hip rather than drifting across it — the stick "
        "and mirror drills give the body a hard reference."
    ),
    FLAG_TEMPO: "Rehearse one count until it is boring — metronome or out loud.",
    FLAG_HIP_SLIDE: (
        "Give the hips something to turn against — the band and wall drills."
    ),
    FLAG_HEAD_DIP: (
        "Keep address height through the ball — the chair and head-window "
        "drills give an external reference."
    ),
    FLAG_ARM_EXTENSION: (
        "Reconnect the lead arm to the chest — the towel drill, then impact "
        "freezes."
    ),
    FLAG_SHOULDER_TILT: (
        "Rehearse the impact shape — the freeze drill with the trail shoulder "
        "working down and under."
    ),
    FLAG_BALANCE: (
        "Shrink the base and hold every finish for a three count — "
        "feet-together swings, then normal stance."
    ),
    FLAG_CONSISTENCY: (
        "Pick one count and make it the only one — rehearsal-and-ball pairs."
    ),
}


@dataclass(frozen=True)
class IssueCard:
    flag: str                     # flag id, e.g. "head-dip"
    metric: str                   # SwingMetrics field the card plots
    display_name: str
    unit: str                     # "SW" | "\N{DEGREE SIGN}" | ":1"
    per_swing: tuple[float | None, ...]   # one entry per swing, NaN -> None
    session_value: float | None
    session_label: str            # "session mean" | "std dev across swings"
    session_text: str             # preformatted, e.g. "0.41 SW" / "148\N{DEGREE SIGN}"
    benchmark_value: float | None  # threshold for the sparkline line (None = no line)
    benchmark_text: str
    worse_direction: str          # "higher" | "lower" (which side of the
                                  # benchmark is bad; drives sparkline accents)
    severity: str                 # "warn" | "major"
    why: str                      # exactly 2 sentences, honest
    fix: str                      # 1 sentence
    drill_ids: tuple[str, ...]
    drill_names: tuple[str, ...]


def issue_cards(
    all_metrics: list[SwingMetrics],
    stats: dict[str, dict[str, float]],
    cfg: Config,
    *,
    club: str | None = None,
    rule_version: int | None = None,
) -> list[IssueCard]:
    """One card per fired session flag (old and new alike), sorted 'major'
    first. Rule 1 preserves the legacy order; rule 2 may change only ties
    within the same severity for a supported, authoritative club."""
    # Function-local import: drills.py imports the FLAG_* constants from this
    # module, so a module-level import here would be circular.
    from . import drills

    selected_rule = (
        priority_rule_version(cfg)
        if rule_version is None
        else validate_priority_rule_version(rule_version)
    )
    coach = cfg.coaching
    deg = "\N{DEGREE SIGN}"
    library = drills.build_drills(cfg.coaching)

    def over(attr: str, thr: float):
        def rule(m: SwingMetrics) -> bool:
            v = getattr(m, attr)
            return not math.isnan(v) and v > thr

        return rule

    def under(attr: str, thr: float):
        def rule(m: SwingMetrics) -> bool:
            v = getattr(m, attr)
            return not math.isnan(v) and v < thr

        return rule

    def tilt_rule(m: SwingMetrics) -> bool:
        return under("shoulder_tilt_impact_deg", coach["shoulder_tilt_impact_min_deg"])(
            m
        ) or under("shoulder_tilt_delta_deg", 0.0)(m)

    # flag -> (metric, display_name, unit, session_text fmt, benchmark_value,
    #          benchmark_text, worse_direction, per-swing firing rule)
    sway_thr = float(coach["sway_warn_sw"])
    tempo_thr = float(coach["tempo_warn_below"])
    dip_thr = float(coach["head_dip_warn_sw"])
    arm_thr = float(coach["lead_arm_warn_deg"])
    tilt_thr = float(coach["shoulder_tilt_impact_min_deg"])
    bal_thr = float(coach["finish_balance_warn_sw"])
    specs = {
        FLAG_SWAY: (
            "head_sway_backswing_sw", "Head sway (backswing)", "SW",
            lambda v: f"{v:.2f} SW", sway_thr,
            f"flagged above {sway_thr:.2f} SW", "higher",
            over("head_sway_backswing_sw", sway_thr),
        ),
        FLAG_TEMPO: (
            "tempo_ratio", "Tempo", ":1",
            lambda v: f"{v:.2f}:1", tempo_thr,
            f"reference {float(coach['tempo_target']):.1f}:1 · "
            f"flagged below {tempo_thr:.1f}:1", "lower",
            under("tempo_ratio", tempo_thr),
        ),
        FLAG_HIP_SLIDE: (
            "hip_slide_backswing_sw", "Hip slide (backswing)", "SW",
            lambda v: f"{v:.2f} SW", sway_thr,
            f"flagged above {sway_thr:.2f} SW", "higher",
            over("hip_slide_backswing_sw", sway_thr),
        ),
        FLAG_HEAD_DIP: (
            "head_dip_sw", "Head dip", "SW",
            lambda v: f"{v:.2f} SW", dip_thr,
            f"flagged above {dip_thr:.2f} SW", "higher",
            over("head_dip_sw", dip_thr),
        ),
        FLAG_ARM_EXTENSION: (
            "lead_arm_angle_deg", "Lead-arm extension", deg,
            lambda v: f"{v:.0f}{deg}", arm_thr,
            f"180{deg} is straight · flagged below {arm_thr:.0f}{deg}", "lower",
            under("lead_arm_angle_deg", arm_thr),
        ),
        FLAG_SHOULDER_TILT: (
            "shoulder_tilt_impact_deg", "Shoulder tilt at impact", deg,
            lambda v: f"{v:.0f}{deg}", tilt_thr,
            f"flagged below {tilt_thr:.0f}{deg} or decreasing from address",
            "lower", tilt_rule,
        ),
        FLAG_BALANCE: (
            "finish_balance_sw", "Finish balance", "SW",
            lambda v: f"{v:.2f} SW", bal_thr,
            f"flagged above {bal_thr:.2f} SW", "higher",
            over("finish_balance_sw", bal_thr),
        ),
        FLAG_CONSISTENCY: (
            "tempo_ratio", "Tempo consistency", ":1",
            lambda v: f"\N{PLUS-MINUS SIGN}{v:.2f}", None,
            f"std dev flagged at or above {float(coach['tempo_std_praise']):.2f}",
            "higher", None,
        ),
    }

    cards: list[IssueCard] = []
    for flag in session_flags(all_metrics, stats, cfg):
        metric, name, unit, fmt, benchmark, bench_text, worse, rule = specs[flag]
        why = WHY_TEXT[flag]
        fix = FIX_TEXT[flag]
        if flag == FLAG_SHOULDER_TILT:
            impact_fired = any(
                under(
                    "shoulder_tilt_impact_deg",
                    coach["shoulder_tilt_impact_min_deg"],
                )(metric_row)
                for metric_row in all_metrics
            )
            if not impact_fired:
                metric = "shoulder_tilt_delta_deg"
                name = "Shoulder-tilt change"
                benchmark = 0.0
                bench_text = (
                    f"flagged below 0{deg} (tilt decreased from address)"
                )
                rule = under("shoulder_tilt_delta_deg", 0.0)
                why = (
                    "The measured shoulder tilt decreased from address instead "
                    "of building through the strike. That loss of angle can be "
                    "an early sign that the body is standing up through impact."
                )
                fix = (
                    "Rehearse increasing the tilt from address to impact — "
                    "the freeze drill makes that change visible."
                )

        per_swing = tuple(
            None if math.isnan(v := getattr(m, metric)) else float(v)
            for m in all_metrics
        )

        if flag == FLAG_CONSISTENCY:
            session_label = "std dev across swings"
            session_value = stats.get("tempo_ratio", {}).get("std")
            severity = (
                "major"
                if session_value is not None
                and session_value >= 2 * coach["tempo_std_praise"]
                else "warn"
            )
        else:
            session_label = "session mean"
            session_value = stats.get(metric, {}).get("mean")
            measured = sum(1 for v in per_swing if v is not None)
            flagged = sum(1 for m in all_metrics if rule(m))
            breaches = session_value is not None and (
                session_value > benchmark
                if worse == "higher"
                else session_value < benchmark
            )
            severity = (
                "major" if breaches or (measured >= 2 and flagged == measured)
                else "warn"
            )
            if not breaches:
                measured_values = [
                    value for value in per_swing if value is not None
                ]
                if measured_values:
                    session_label = "worst swing"
                    session_value = (
                        max(measured_values)
                        if worse == "higher"
                        else min(measured_values)
                    )
        session_text = fmt(session_value) if session_value is not None else "—"

        family_key = drills.family_for(flag)
        ds = library.get(family_key, []) if family_key else []

        cards.append(
            IssueCard(
                flag=flag,
                metric=metric,
                display_name=name,
                unit=unit,
                per_swing=per_swing,
                session_value=session_value,
                session_label=session_label,
                session_text=session_text,
                benchmark_value=benchmark,
                benchmark_text=bench_text,
                worse_direction=worse,
                severity=severity,
                why=why,
                fix=fix,
                drill_ids=tuple(d.id for d in ds),
                drill_names=tuple(d.name for d in ds),
            )
        )

    preferred = (
        _CLUB_TIE_PRIORITIES.get(club, ())
        if selected_rule == PRIORITY_RULE_CLUB_AWARE
        else ()
    )
    preferred_rank = {flag: rank for rank, flag in enumerate(preferred)}
    neutral_rank = len(preferred)
    legacy_rank = {flag: rank for rank, flag in enumerate(specs)}
    cards.sort(
        key=lambda card: (
            0 if card.severity == "major" else 1,
            preferred_rank.get(card.flag, neutral_rank),
            legacy_rank[card.flag],
        )
    )
    return cards


def session_notes(
    all_metrics: list[SwingMetrics], stats: dict[str, dict[str, float]], cfg: Config
) -> list[str]:
    coach = cfg.coaching
    notes: list[str] = []
    tempo_stats = stats.get("tempo_ratio")
    measured_tempos = [
        metric.tempo_ratio
        for metric in all_metrics
        if not math.isnan(metric.tempo_ratio)
    ]
    if len(measured_tempos) >= 2 and tempo_stats is not None:
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
