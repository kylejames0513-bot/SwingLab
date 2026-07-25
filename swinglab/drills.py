"""Curated practice drills matched to coaching flags.

Every report ends with a practice plan: for each flag the session raised
(see :mod:`swinglab.coaching`), two or three drills with an aim, a
step-by-step protocol, a dosage, and a measurable re-film target expressed
in the same numbers the report prints — so "fixed" means the next report
says so. A session with no flags gets the ``clean`` maintenance set.

The threshold numbers inside the drill text come from the ``coaching``
section of the config (:func:`build_drills`), so retuning
``coaching.sway_warn_sw`` and friends in config.yaml retunes the re-film
targets with no code edits — the same white-label rule as everything else.

Each drill carries a ``gear_tag`` (``swinglab:<flag>``), the same tag the
gear shop uses to match training aids to flags (see swinglab.web.shop), and
an optional ``gear_note`` naming what kind of aid the drill is built around.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .coaching import FLAG_CONSISTENCY, FLAG_HIP_SLIDE, FLAG_SWAY, FLAG_TEMPO
from .config import DEFAULTS, Config

# Drill-set key for a session that raised no flags.
CLEAN = "clean"

# The store path the report's "Matched training aids" link points at,
# appended to shop.store_url.
GEAR_COLLECTION_PATH = "/collections/swinglab-gear"

PLAN_TITLES = {
    FLAG_TEMPO: "Tempo",
    FLAG_SWAY: "Head sway",
    FLAG_HIP_SLIDE: "Hip slide",
    FLAG_CONSISTENCY: "Swing-to-swing consistency",
    CLEAN: "Maintenance — nothing flagged, keep it that way",
}


@dataclass(frozen=True)
class Drill:
    id: str
    name: str
    aim: str                    # one line: what the drill changes
    protocol: tuple[str, ...]   # 3-4 steps
    dosage: str                 # e.g. "3 x 10 swings, 3x/week"
    success_metric: str         # a measurable re-film target in report numbers
    gear_tag: str               # swinglab:<flag>, mirrors the shop's product tags
    gear_note: str = ""         # what kind of training aid the drill is built around


def build_drills(coach: dict) -> dict[str, list[Drill]]:
    """The drill library, with re-film targets rendered from ``coach``
    (a ``coaching`` config section — pass ``cfg.coaching``)."""
    sway = f"{float(coach['sway_warn_sw']):.2f}"
    tempo_warn = f"{float(coach['tempo_warn_below']):.1f}"
    tempo_target = f"{float(coach['tempo_target']):.1f}"
    tempo_std = f"{float(coach['tempo_std_praise']):.2f}"

    return {
        FLAG_TEMPO: [
            Drill(
                id="tempo-three-beat-count",
                name="Three-beat count",
                aim="Give the backswing time to finish before the downswing starts.",
                protocol=(
                    "Set a swing metronome to a steady beat, or count out loud.",
                    "Take the club away on one, reach the top on three.",
                    "Start down on the next beat — never before it.",
                    "Begin at half speed and build up while the count holds.",
                ),
                dosage="3 x 10 swings, 3x/week",
                success_metric=(
                    f"Re-film five swings: tempo ratio at or above {tempo_warn}:1 "
                    f"on at least four of them, trending toward the "
                    f"{tempo_target}:1 benchmark."
                ),
                gear_tag="swinglab:tempo",
                gear_note=(
                    "A clip-on swing metronome keeps the count honest; counting "
                    "out loud works without one."
                ),
            ),
            Drill(
                id="tempo-late-whoosh",
                name="Late whoosh",
                aim="Move the swing's speed to the bottom of the arc instead of the top.",
                protocol=(
                    "Swing a weighted tempo wand and listen for where the whoosh is.",
                    "Make the loudest whoosh happen past the ball position, never "
                    "at the top or early in the downswing.",
                    "Alternate one wand swing and one normal swing with the same feel.",
                ),
                dosage="3 x 8 swings, 2x/week",
                success_metric=(
                    f"Re-film: downswing near 0.25 s with a backswing long enough "
                    f"to keep every tempo ratio at or above {tempo_warn}:1."
                ),
                gear_tag="swinglab:tempo",
                gear_note=(
                    "Built for a weighted tempo wand; two irons held together "
                    "give a similar feel."
                ),
            ),
            Drill(
                id="tempo-pause-at-top",
                name="Pause at the top",
                aim="Separate going back from coming down so the transition stops rushing.",
                protocol=(
                    "Swing to the top and hold a full one-second pause.",
                    "Start down from the dead stop at about 70% effort.",
                    "Over ten swings, shrink the pause to a beat — not a stop.",
                ),
                dosage="2 x 10 swings, 3x/week",
                success_metric=(
                    f"Re-film: no swing under {tempo_warn}:1, with the session "
                    f"mean moving toward {tempo_target}:1."
                ),
                gear_tag="swinglab:tempo",
            ),
        ],
        FLAG_SWAY: [
            Drill(
                id="sway-stick-outside-trail-foot",
                name="Stick outside the trail foot",
                aim="Turn behind the ball instead of drifting off it.",
                protocol=(
                    "Push an alignment stick into the ground just outside your "
                    "trail foot, leaning slightly toward you.",
                    "Make slow backswings — brushing the stick means the body is "
                    "drifting, not turning.",
                    "Feel pressure load into the inside of the trail foot at the top.",
                    "Hit balls at 80% effort while staying clear of the stick.",
                ),
                dosage="3 x 10 swings, 3x/week",
                success_metric=(
                    f"Re-film: head sway address-to-top at or below {sway} "
                    f"shoulder widths on every swing."
                ),
                gear_tag="swinglab:sway",
                gear_note=(
                    "Needs one alignment stick pushed into turf; at home, a "
                    "doorframe or a chair back beside the trail hip works."
                ),
            ),
            Drill(
                id="sway-mirror-head-box",
                name="Mirror head box",
                aim="Give the eyes proof of a quiet head before the ball does.",
                protocol=(
                    "Face a full-length mirror and take your address.",
                    "Mark your head's reflected position with a piece of tape "
                    "on the glass.",
                    "Rehearse to the top: the head may rotate, but it stays "
                    "inside the tape.",
                ),
                dosage="2 x 10 rehearsals, daily",
                success_metric=(
                    f"Re-film: address-to-top head sway inside {sway} shoulder "
                    f"widths on three consecutive sessions."
                ),
                gear_tag="swinglab:sway",
                gear_note="Built around a full-length swing mirror.",
            ),
        ],
        FLAG_HIP_SLIDE: [
            Drill(
                id="hip-slide-banded-turn",
                name="Banded turn",
                aim="Convert lateral slide into rotation by giving the hips something to turn against.",
                protocol=(
                    "Loop a resistance band just above your knees and take your "
                    "address with light outward tension.",
                    "Turn to the top keeping the tension even — if the trail "
                    "side drifts away from the target, one side goes slack.",
                    "Hold the top for a beat, feel the trail glute loaded, then "
                    "swing through.",
                ),
                dosage="3 x 8 swings, 3x/week",
                success_metric=(
                    f"Re-film: hip slide address-to-top at or below {sway} "
                    f"shoulder widths."
                ),
                gear_tag="swinglab:hip-slide",
                gear_note="Built around a hip resistance band.",
            ),
            Drill(
                id="hip-slide-trail-pocket",
                name="Trail-pocket turn",
                aim="Feel the trail hip work back and around, not sideways.",
                protocol=(
                    "Set up with your trail hip about a hand's width from a wall "
                    "or chair back.",
                    "Turn to the top: the trail pocket works back toward the "
                    "wall, it never bumps it sideways.",
                    "Rehearse slowly five times, then hit a ball with the same feel.",
                ),
                dosage="2 x 10 rehearsals, daily",
                success_metric=(
                    f"Re-film: hip slide address-to-top under {sway} shoulder "
                    f"widths on every swing in the session."
                ),
                gear_tag="swinglab:hip-slide",
            ),
        ],
        FLAG_CONSISTENCY: [
            Drill(
                id="consistency-one-count",
                name="One count, every club",
                aim="Make one tempo the only tempo you own.",
                protocol=(
                    "Pick one count — or one metronome setting — that matches "
                    "your best swing.",
                    "Hit ten balls with a wedge to that count, then ten with a "
                    "mid-iron to the same count.",
                    "If a swing feels rushed, step out, rehearse once, step back in.",
                ),
                dosage="20 balls, 2x/week",
                success_metric=(
                    f"Re-film at least three swings: tempo standard deviation "
                    f"under {tempo_std} across the session."
                ),
                gear_tag="swinglab:consistency",
                gear_note="A clip-on swing metronome makes the count objective.",
            ),
            Drill(
                id="consistency-rehearsal-pairs",
                name="Rehearsal pairs",
                aim="Stop the tempo drifting as the bucket empties.",
                protocol=(
                    "Alternate one practice swing at your count with one ball "
                    "at the same count.",
                    "Never hit two balls back to back.",
                    "Only finish with three balls in a row once the count has "
                    "held for ten pairs.",
                ),
                dosage="15 pairs, 2x/week",
                success_metric=(
                    f"Re-film: tempo standard deviation below {tempo_std}, with "
                    f"every ratio at or above {tempo_warn}:1."
                ),
                gear_tag="swinglab:consistency",
            ),
        ],
        CLEAN: [
            Drill(
                id="clean-baseline-refilm",
                name="Baseline re-film",
                aim="A clean report is a baseline — keep it current so drift shows up early.",
                protocol=(
                    "Once a month, film the same session: same club, same "
                    "camera height, same angle.",
                    "Compare tempo, sway and slide against the last clean report.",
                    "If a number moves toward a threshold, run that flag's "
                    "drills for two weeks.",
                ),
                dosage="3 swings, monthly",
                success_metric=(
                    f"Stay clean: tempo at or above {tempo_warn}:1, head sway "
                    f"and hip slide at or below {sway} shoulder widths."
                ),
                gear_tag="swinglab:general",
            ),
            Drill(
                id="clean-mirror-checkpoints",
                name="Mirror checkpoints",
                aim="Keep the positions honest between filmed sessions.",
                protocol=(
                    "In front of a full-length mirror, hold address, top and "
                    "impact for three seconds each.",
                    "At each stop, check the head against where it sat at address.",
                    "Run it as the warm-up before every range session.",
                ),
                dosage="5 minutes before each session",
                success_metric=(
                    f"Next re-film: head sway address-to-top stays inside "
                    f"{sway} shoulder widths."
                ),
                gear_tag="swinglab:general",
                gear_note="Built around a full-length swing mirror.",
            ),
        ],
    }


# The library rendered with the default thresholds — what most callers and
# the tests use. Callers with a retuned config should go through
# practice_plan(), which rebuilds the text from the live thresholds.
DRILLS: dict[str, list[Drill]] = build_drills(DEFAULTS["coaching"])


def practice_plan(
    flags: Iterable[str], cfg: Config | None = None
) -> list[dict]:
    """Ordered practice-plan blocks for the report.

    One block per fired flag (unknown flags are skipped), or the ``clean``
    maintenance block when nothing fired. Each block is
    ``{"flag", "title", "drills"}``.
    """
    library = build_drills(cfg.coaching) if cfg is not None else DRILLS
    keys = [f for f in flags if f in library] or [CLEAN]
    return [
        {"flag": key, "title": PLAN_TITLES[key], "drills": library[key]}
        for key in keys
    ]


def gear_shop_url(cfg: Config) -> str | None:
    """The "Matched training aids" link target, or None when
    ``shop.store_url`` is unset/empty (the report then renders no link)."""
    store = str(cfg.shop.get("store_url") or "").strip().rstrip("/")
    return f"{store}{GEAR_COLLECTION_PATH}" if store else None
