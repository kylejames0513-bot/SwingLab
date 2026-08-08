"""Curated practice drills matched to coaching flags.

Every report ends with a practice plan: for each flag the session raised
(see :mod:`swinglab.coaching`), one or more drills with an aim, a
step-by-step protocol, a dosage, and a measurable re-film target expressed
in the same numbers the report prints — so "fixed" means the next report
says so. A session with no flags gets the ``clean`` maintenance set.

The threshold numbers inside the drill text come from the ``coaching``
section of the config (:func:`build_drills`), so retuning
``coaching.sway_warn_sw`` and friends in config.yaml retunes the re-film
targets with no code edits — the same white-label rule as everything else.

Each drill carries the exact ``gear_tag`` the gear shop uses to match
training aids (see swinglab.web.shop), plus an optional ``gear_note`` naming
what kind of aid it is built around. Shoulder tilt intentionally uses its own
drill while sharing the arm-extension aid category.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .coaching import (
    FLAG_ARM_EXTENSION,
    FLAG_BALANCE,
    FLAG_CONSISTENCY,
    FLAG_HEAD_DIP,
    FLAG_HIP_SLIDE,
    FLAG_SHOULDER_TILT,
    FLAG_SWAY,
    FLAG_TEMPO,
)
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
    FLAG_HEAD_DIP: "Head dip",
    FLAG_ARM_EXTENSION: "Impact extension",
    FLAG_SHOULDER_TILT: "Shoulder-tilt change",
    FLAG_BALANCE: "Finish balance",
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


@dataclass(frozen=True)
class DrillPresentation:
    summary_steps: tuple[str, str, str]
    setup: str
    feel_cue: str
    equipment: str | None


class MissingDrillPresentation(LookupError):
    """A selected drill has no authored player-facing presentation."""


def build_drills(coach: dict) -> dict[str, list[Drill]]:
    """The drill library, with re-film targets rendered from ``coach``
    (a ``coaching`` config section — pass ``cfg.coaching``)."""
    sway = f"{float(coach['sway_warn_sw']):.2f}"
    tempo_warn = f"{float(coach['tempo_warn_below']):.1f}"
    tempo_target = f"{float(coach['tempo_target']):.1f}"
    tempo_std = f"{float(coach['tempo_std_praise']):.2f}"
    dip = f"{float(coach['head_dip_warn_sw']):.2f}"
    arm = f"{float(coach['lead_arm_warn_deg']):.0f}"
    tilt = f"{float(coach['shoulder_tilt_impact_min_deg']):.0f}"
    bal = f"{float(coach['finish_balance_warn_sw']):.2f}"

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
        FLAG_HEAD_DIP: [
            Drill(
                id="dip-chair-drill",
                name="Chair drill",
                aim="Hold address height through the swing instead of dropping into impact.",
                protocol=(
                    "Set a chair or bench so its back lightly touches your "
                    "glutes at address.",
                    "Swing to the top keeping that light contact — losing it "
                    "means the body is dropping or thrusting.",
                    "Swing down to a held impact position, still tall, contact "
                    "still light.",
                    "Hit balls at 80% with the chair a hand's width behind as "
                    "a reminder.",
                ),
                dosage="3 x 8 swings, 3x/week",
                success_metric=(
                    f"Re-film: head dip address-to-impact at or below {dip} "
                    f"shoulder widths on every swing."
                ),
                gear_tag="swinglab:head-dip",
                gear_note=(
                    "Any chair, bench or range basket at hip height works."
                ),
            ),
            Drill(
                id="dip-head-window",
                name="Head-window drill",
                aim="Give the head a fixed ceiling so a dip is felt the moment it starts.",
                protocol=(
                    "Address a teed ball with a doorframe edge, branch, or "
                    "partner-held alignment stick a finger's width above your "
                    "head.",
                    "Rehearse slow swings to impact speed — the head may "
                    "rotate and drift a touch, it never ducks away from the "
                    "reference.",
                    "If the legs collapse, restart from address and keep the "
                    "chest tall.",
                    "Check the gap every third swing and build toward full "
                    "speed.",
                ),
                dosage="2 x 10 rehearsals, daily",
                success_metric=(
                    f"Re-film: every swing's head dip at or below {dip} "
                    f"shoulder widths, with the session mean clearly inside it."
                ),
                gear_tag="swinglab:head-dip",
                gear_note=(
                    "A partner-held alignment stick makes the ceiling "
                    "objective; a doorframe works at home."
                ),
            ),
        ],
        FLAG_ARM_EXTENSION: [
            Drill(
                id="arm-towel-under-lead",
                name="Towel under the lead arm",
                aim=(
                    "Keep the lead arm connected and long through the strike "
                    "instead of folding into a chicken wing."
                ),
                protocol=(
                    "Trap a folded towel between your lead upper arm and your "
                    "chest at address.",
                    "Hit half swings keeping light pressure on the towel "
                    "through impact.",
                    "If the towel drops before the follow-through, the lead "
                    "arm broke away from the body.",
                    "Build to three-quarter swings only while the towel stays "
                    "put.",
                ),
                dosage="3 x 10 half swings, 3x/week",
                success_metric=(
                    f"Re-film five swings: lead-arm angle at impact at or "
                    f"above {arm}\N{DEGREE SIGN} on at least four of them."
                ),
                gear_tag="swinglab:arm-extension",
                gear_note=(
                    "Any golf towel works; a headcover does the same job."
                ),
            ),
            Drill(
                id="arm-impact-freeze",
                name="Impact freeze",
                aim=(
                    "Own the impact shape — long lead arm, trail shoulder "
                    "down — as a position, not an accident."
                ),
                protocol=(
                    "Swing at half speed and freeze at the impact position "
                    "for a full three seconds.",
                    "Check in a mirror or on camera: lead arm long, trail "
                    "shoulder clearly lower than the lead.",
                    "Rehearse five freezes, then hit one ball trying to swing "
                    "through that exact shape.",
                ),
                dosage="2 x 8 freezes, 3x/week",
                success_metric=(
                    f"Re-film: lead-arm angle at or above {arm}\N{DEGREE SIGN} "
                    f"and shoulder tilt at impact at or above "
                    f"{tilt}\N{DEGREE SIGN} on every swing."
                ),
                gear_tag="swinglab:arm-extension",
                gear_note=(
                    "Built around a full-length swing mirror; a phone on a "
                    "tripod works too."
                ),
            ),
        ],
        FLAG_SHOULDER_TILT: [
            Drill(
                id="shoulder-impact-freeze",
                name="Shoulder-tilt impact freeze",
                aim=(
                    "Keep the trail shoulder working down from address "
                    "through the strike."
                ),
                protocol=(
                    "Set up face-on to a mirror or phone and note the shoulder "
                    "line at address.",
                    "Swing at half speed and freeze at impact for three seconds.",
                    "Check that the trail shoulder is lower than at address, "
                    "then repeat the motion without standing up.",
                    "Rehearse five freezes, then hit one ball through the same "
                    "shape.",
                ),
                dosage="2 x 8 freezes, 3x/week",
                success_metric=(
                    f"Re-film face-on: shoulder tilt at impact at or above "
                    f"{tilt}\N{DEGREE SIGN}, with address-to-impact tilt change "
                    f"at or above 0\N{DEGREE SIGN} on every swing."
                ),
                gear_tag="swinglab:arm-extension",
                gear_note=(
                    "Built around a full-length swing mirror; a phone on a "
                    "tripod works too."
                ),
            ),
        ],
        FLAG_BALANCE: [
            Drill(
                id="balance-feet-together",
                name="Feet-together swings",
                aim=(
                    "Shrink the base so balance faults show up instantly and "
                    "the body learns to stay centered."
                ),
                protocol=(
                    "Set up with your feet touching and the ball on a tee.",
                    "Make smooth three-quarter swings — any lunge or slide "
                    "shows up as a stumble immediately.",
                    "Hold each finish for a full three count.",
                    "Widen the stance back to normal over the session, "
                    "keeping the same quiet finish.",
                ),
                dosage="3 x 8 swings, 2x/week",
                success_metric=(
                    f"Re-film: finish drift at or below {bal} shoulder widths "
                    f"on every swing."
                ),
                gear_tag="swinglab:balance",
            ),
            Drill(
                id="balance-hold-the-finish",
                name="Hold the finish",
                aim="Make the held finish the non-negotiable end of every swing.",
                protocol=(
                    "Swing at normal speed and hold the finish for a slow "
                    "three count.",
                    "Weight fully on the lead foot, trail toe down as a "
                    "kickstand only.",
                    "If you step or hop, the swing was out of balance before "
                    "the finish — take the next one at 80%.",
                ),
                dosage="Every ball for one range session, 2x/week",
                success_metric=(
                    f"Re-film: finish drift at or below {bal} shoulder widths "
                    f"— session mean and every individual swing."
                ),
                gear_tag="swinglab:balance",
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


def build_drill_presentations(
    coach: Mapping[str, object],
) -> dict[str, DrillPresentation]:
    """The authored three-stage view for drills that can lead a report."""
    return {
        "tempo-three-beat-count": DrillPresentation((
            "Set a steady beat and begin with half-speed swings.",
            "Take the club away on one and arrive at the top on three.",
            "Start down on the next beat and add speed only while the count holds.",
        ), "Ball teed low with room for three-quarter swings.",
           "Let the backswing finish before anything starts down.",
           "Swing metronome or spoken count"),
        "sway-stick-outside-trail-foot": DrillPresentation((
            "Place a leaning stick safely outside the trail foot, clear of the club path.",
            "Rehearse slow turns that stay clear of the stick while pressure loads inside the trail foot.",
            "Hit at 80 percent effort and stop if the body or club can contact the stick.",
        ), "Turf station with the stick outside and behind the swing arc.",
           "Turn into the trail hip — pressure inside, head quiet.", "Alignment stick"),
        "hip-slide-banded-turn": DrillPresentation((
            "Loop the band above the knees and begin with light outward tension.",
            "Turn to the top while both sides keep even tension.",
            "Hold for one beat, feel the trail glute loaded, then swing through.",
        ), "Stable shoes and a band that does not restrict circulation.",
           "Trail pocket turns back; it does not slide sideways.", "Hip resistance band"),
        "dip-chair-drill": DrillPresentation((
            "Set the chair for light glute contact at address.",
            "Rehearse to the top and a held impact while keeping the contact light.",
            "Move the chair one hand-width back and hit at 80 percent with the same height.",
        ), "Chair behind the hips and outside the club path.",
           "Keep the chest tall through the ball — same height you started.", "Chair or range basket"),
        "arm-towel-under-lead": DrillPresentation((
            "Trap a folded towel lightly under the lead upper arm.",
            "Make half swings and keep the towel through impact.",
            "Build to three-quarter swings only while the towel stays until follow-through.",
        ), "Half-swing station with a soft towel and clear club path.",
           "Lead arm stays connected and long through the strike.", "Golf towel or headcover"),
        "shoulder-impact-freeze": DrillPresentation((
            "Note the shoulder line at address in a face-on mirror or phone.",
            "Swing at half speed and freeze at impact for three seconds.",
            "Confirm the trail shoulder stayed lower, then blend five freezes into one ball.",
        ), "Face-on mirror or phone at hip height.",
           "Trail shoulder works down and under through impact.", "Mirror or phone tripod"),
        "balance-feet-together": DrillPresentation((
            "Tee the ball and begin with the feet touching.",
            "Make smooth three-quarter swings and hold each finish for three counts.",
            "Widen the stance gradually while preserving the same quiet finish.",
        ), "Level ground, teed ball, and reduced swing speed.",
           "Finish stacked and still enough to hold the pose.", "Tee"),
        "consistency-one-count": DrillPresentation((
            "Choose one count from the most repeatable swing.",
            "Hit ten wedges and ten mid-irons without changing that count.",
            "Step out and rehearse once whenever a swing feels rushed.",
        ), "Two clubs, one target, and one fixed beat.",
           "Every club runs on the same clock.", "Metronome optional"),
        "clean-baseline-refilm": DrillPresentation((
            "Recreate the same club, hand, camera angle, height, and framing.",
            "Make three swings with the same count and similar effort.",
            "Save the report as the next matched maintenance checkpoint.",
        ), "The same capture station used for the baseline.",
           "Protect the selected steady measurement — not a perfect-looking pose.", "Phone support"),
        "rhythm-baseline-refilm": DrillPresentation((
            "Recreate the same club and DTL camera setup.",
            "Make three swings with one count and similar effort.",
            "Re-film face-on next time when body-movement coaching is wanted.",
        ), "DTL phone at hip height with the full club motion visible.",
           "Repeat the rhythm this angle can measure honestly.", "Phone support or tripod"),
        "readability-baseline-refilm": DrillPresentation((
            "Set the phone face-on at hip height with the full body visible.",
            "Use bright even light and keep other people out of frame.",
            "Make three swings with the same club and camera position.",
        ), "Stable face-on phone support and uncluttered background.",
           "Make the motion readable before you judge it.", "Phone support or tripod"),
    }


def drill_presentation(drill: Drill, cfg: Config) -> DrillPresentation:
    """Return the authored presentation for a selected drill, never a fallback."""
    try:
        return build_drill_presentations(cfg.coaching)[drill.id]
    except KeyError as exc:
        raise MissingDrillPresentation(drill.id) from exc

# Fallbacks for flags that borrow another family's drills. Shoulder tilt now
# has its own evidence-matched plan; its selected drill carries the shared
# arm-extension gear tag separately.
ISSUE_FAMILY: dict[str, str] = {}


def family_for(flag: str) -> str | None:
    """DRILLS key for a flag: itself, a mapped family, or None (unknown)."""
    if flag in DRILLS:
        return flag
    return ISSUE_FAMILY.get(flag)


def practice_plan(
    flags: Iterable[str], cfg: Config | None = None
) -> list[dict]:
    """Ordered practice-plan blocks for the report.

    One block per fired flag's drill family (unknown flags are skipped;
    flags sharing a family — arm-extension and shoulder-tilt — render one
    block, first-seen order preserved), or the ``clean`` maintenance block
    when nothing fired. Each block is ``{"flag", "title", "drills"}``.
    """
    library = build_drills(cfg.coaching) if cfg is not None else DRILLS
    keys: list[str] = []
    for flag in flags:
        family = family_for(flag)
        if family is not None and family in library and family not in keys:
            keys.append(family)
    if not keys:
        keys = [CLEAN]
    return [
        {"flag": key, "title": PLAN_TITLES[key], "drills": library[key]}
        for key in keys
    ]


def gear_shop_url(cfg: Config) -> str | None:
    """The "Matched training aids" link target, or None when
    ``shop.store_url`` is unset/empty (the report then renders no link)."""
    store = str(cfg.shop.get("store_url") or "").strip().rstrip("/")
    return f"{store}{GEAR_COLLECTION_PATH}" if store else None
