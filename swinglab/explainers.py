"""Plain-English metric explainers for beginners.

Every metric the report prints gets a 2-3 sentence "what it is / why it
matters / what good looks like" in beginner language, plus a one-line unit
gloss. The report's metric tables show these behind a tap-to-open
``<details>`` expander, and the /progress cards reuse the exact same
strings — one voice everywhere.

Threshold numbers inside the copy are rendered from the live ``coaching``
config section (:func:`build_explainers`), the same white-label rule as the
drills: retune config.yaml and the copy retunes with it. Benchmarks are
always framed as references, never day-one targets — a beginner moving
toward 3.0:1 is winning even while under it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import DEFAULTS

_DEG = "\N{DEGREE SIGN}"

SW_GLOSS = (
    "SW = shoulder-widths — so the number means the same however far the "
    "camera stands."
)
SECONDS_GLOSS = "Measured in seconds, straight from your video's frames."
RATIO_GLOSS = "A ratio: backswing time \N{DIVISION SIGN} downswing time."
DEG_GLOSS = "Degrees as the camera sees them, filmed face-on."


@dataclass(frozen=True)
class Explainer:
    metric: str      # SwingMetrics field name
    title: str       # plain-English name
    text: str        # 2-3 sentences: what it is / why it matters / what good looks like
    unit_gloss: str  # one line about the unit


def build_explainers(coach: dict) -> dict[str, Explainer]:
    """Explainer per metric, thresholds rendered from ``coach``
    (a ``coaching`` config section — pass ``cfg.coaching``)."""
    sway = f"{float(coach['sway_warn_sw']):.2f}"
    tempo_target = f"{float(coach['tempo_target']):.1f}"
    tempo_warn = f"{float(coach['tempo_warn_below']):.1f}"
    dip = f"{float(coach['head_dip_warn_sw']):.2f}"
    arm = f"{float(coach['lead_arm_warn_deg']):.0f}"
    tilt = f"{float(coach['shoulder_tilt_impact_min_deg']):.0f}"
    bal = f"{float(coach['finish_balance_warn_sw']):.2f}"

    entries = [
        Explainer(
            "strike_s",
            "Strike time",
            "When this swing's ball strike happened, in seconds into your "
            "clip. It's how the report tells your swings apart — nothing to "
            "train here.",
            SECONDS_GLOSS,
        ),
        Explainer(
            "backswing_s",
            "Backswing time",
            "How long the club took to go back, from the hands leaving "
            "address to the top of the swing. There's no single right "
            "number on its own — what matters is how it compares to the "
            "downswing, which is the tempo ratio.",
            SECONDS_GLOSS,
        ),
        Explainer(
            "downswing_s",
            "Downswing time",
            "How long the club took to come down, from the top to impact. "
            "Tour players sit near 0.25 s, but the ratio to your backswing "
            "matters far more than the raw time.",
            SECONDS_GLOSS,
        ),
        Explainer(
            "tempo_ratio",
            "Tempo",
            "Backswing time divided by downswing time. A low ratio usually "
            "means the club changed direction before the backswing finished, "
            f"which turns contact into a timing gamble. {tempo_target}:1 is "
            "the tour reference — moving toward it matters more than hitting "
            f"it, and staying at or above {tempo_warn}:1 keeps it off the "
            "flag list.",
            RATIO_GLOSS,
        ),
        Explainer(
            "head_sway_backswing_sw",
            "Head sway (backswing)",
            "How far your head drifts away from the target on the way back. "
            "Drift moves the bottom of your swing with it, so hitting the "
            "ball first then needs a perfectly timed slide home. The head "
            f"can rotate freely — it just shouldn't travel more than about "
            f"{sway} SW.",
            SW_GLOSS,
        ),
        Explainer(
            "head_sway_downswing_sw",
            "Head sway (downswing)",
            "How the head moves from the top down to impact — negative "
            "means back toward the target. A small move toward the target "
            "coming down is normal and isn't flagged on its own; it's shown "
            "as context for the backswing number.",
            SW_GLOSS,
        ),
        Explainer(
            "hip_slide_backswing_sw",
            "Hip slide (backswing)",
            "Sideways hip movement away from the target going back. A slide "
            "looks like a turn but loads nothing — the trail hip never "
            "coils, so the downswing starts with nothing to push from. "
            f"Turning inside about {sway} SW is the reference.",
            SW_GLOSS,
        ),
        Explainer(
            "hip_slide_downswing_sw",
            "Hip slide (downswing)",
            "Hip movement from the top to impact — negative means toward "
            "the target, which is exactly how a downswing starts. Context, "
            "not a flag.",
            SW_GLOSS,
        ),
        Explainer(
            "head_dip_sw",
            "Head dip",
            "How much your head drops between address and impact. A small "
            "squat is normal; a big drop lowers the whole swing's low "
            "point, so clean contact needs a last-instant rescue. Staying "
            f"under about {dip} SW is the reference.",
            SW_GLOSS,
        ),
        Explainer(
            "lead_arm_angle_deg",
            "Lead arm at impact",
            f"How straight your lead arm is at the strike — 180{_DEG} is "
            "perfectly straight as the camera sees it. A bent lead arm at "
            "impact shortens your reach at the exact moment that decides "
            f"contact. Staying above about {arm}{_DEG} is the reference.",
            DEG_GLOSS + f" 180{_DEG} = a perfectly straight arm.",
        ),
        Explainer(
            "shoulder_tilt_impact_deg",
            "Shoulder tilt at impact",
            "How tilted your shoulder line is at the strike — positive "
            "means the trail shoulder is lower, which is what a good impact "
            "looks like. Level or reversed shoulders usually mean the body "
            "stalled and the hands flipped at the ball. At least about "
            f"{tilt}{_DEG} of tilt is the reference.",
            DEG_GLOSS + " Positive = trail shoulder lower.",
        ),
        Explainer(
            "shoulder_tilt_delta_deg",
            "Tilt change (address \N{RIGHTWARDS ARROW} impact)",
            "How the shoulder tilt changed between address and impact. It "
            "should hold or grow — the trail shoulder working down through "
            "the ball — so a negative change (tilt shrinking) is the "
            "classic stand-up pattern.",
            DEG_GLOSS + " Zero or positive change is the reference.",
        ),
        Explainer(
            "finish_balance_sw",
            "Finish balance",
            "How much your feet moved while holding the finish. A quiet, "
            "held finish reads near zero and is the cheapest proof the "
            "whole swing stayed in balance; a step or stumble reads tenths. "
            f"At or under about {bal} SW is the reference.",
            SW_GLOSS,
        ),
    ]
    return {e.metric: e for e in entries}


# Rendered with the default thresholds — what most callers use directly.
# Callers with a retuned config should call build_explainers(cfg.coaching).
EXPLAINERS: dict[str, Explainer] = build_explainers(DEFAULTS["coaching"])
