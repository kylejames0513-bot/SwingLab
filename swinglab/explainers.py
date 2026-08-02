"""Two-register metric explainers: beginner voice plus a precise line.

Every metric the report prints gets a 2-3 sentence "what it is / why it
matters / what good looks like" in beginner language, plus a one-line unit
gloss, plus a one-line ``how`` — the precise measurement statement (what is
tracked, between which events, against which flag threshold) for readers
who want the method, not reassurance. The report's metric tables show all
of it behind a tap-to-open ``<details>`` expander, and the /progress cards
reuse the exact same strings — one voice everywhere.

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
SW_PER_SECOND_GLOSS = (
    "Shoulder-widths per second in the camera view — a personal comparison "
    "unit, not mph."
)


@dataclass(frozen=True)
class Explainer:
    metric: str      # SwingMetrics field name
    title: str       # plain-English name
    text: str        # 2-3 sentences: what it is / why it matters / what good looks like
    unit_gloss: str  # one line about the unit
    how: str = ""    # one precise line: what's measured, between which events,
                     # against which threshold — the experienced-player register


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
            how="Located from the impact transient in the clip's audio "
                "track — which is why sound has to be on.",
        ),
        Explainer(
            "backswing_s",
            "Backswing time",
            "How long the club took to go back, from the hands leaving "
            "address to the top of the swing. There's no single right "
            "number on its own — what matters is how it compares to the "
            "downswing, which is the tempo ratio.",
            SECONDS_GLOSS,
            how="Takeaway (tracked hands leaving address) to top (highest "
                "hand position before impact), timed at the analysis frame "
                "rate.",
        ),
        Explainer(
            "downswing_s",
            "Downswing time",
            "How long the club took to come down, from the top to impact. "
            "Tour players sit near 0.25 s, but the ratio to your backswing "
            "matters far more than the raw time.",
            SECONDS_GLOSS,
            how="Top of backswing to the audio-located impact, timed at the "
                "analysis frame rate.",
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
            how=f"backswing_s \N{DIVISION SIGN} downswing_s; flagged below "
                f"{tempo_warn}:1 against the {tempo_target}:1 reference.",
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
            how="Lateral head-center travel, address \N{RIGHTWARDS ARROW} "
                "top, from the 2D face-on pose track, normalized by shoulder "
                f"width; flagged beyond {sway} SW.",
        ),
        Explainer(
            "head_sway_downswing_sw",
            "Head sway (downswing)",
            "How the head moves from the top down to impact — negative "
            "means back toward the target. A small move toward the target "
            "coming down is normal and isn't flagged on its own; it's shown "
            "as context for the backswing number.",
            SW_GLOSS,
            how="Lateral head-center travel, top \N{RIGHTWARDS ARROW} "
                "impact, same normalization; sign is direction (negative = "
                "toward the target). Context only — no flag.",
        ),
        Explainer(
            "hip_slide_backswing_sw",
            "Hip slide (backswing)",
            "Sideways hip movement away from the target going back. A slide "
            "looks like a turn but loads nothing — the trail hip never "
            "coils, so the downswing starts with nothing to push from. "
            f"Turning inside about {sway} SW is the reference.",
            SW_GLOSS,
            how="Lateral mid-hip travel, address \N{RIGHTWARDS ARROW} top, "
                "from the 2D face-on pose track in shoulder widths; flagged "
                f"beyond {sway} SW.",
        ),
        Explainer(
            "hip_slide_downswing_sw",
            "Hip slide (downswing)",
            "Hip movement from the top to impact — negative means toward "
            "the target, which is exactly how a downswing starts. Context, "
            "not a flag.",
            SW_GLOSS,
            how="Lateral mid-hip travel, top \N{RIGHTWARDS ARROW} impact; "
                "negative = toward the target. Context only — no flag.",
        ),
        Explainer(
            "head_dip_sw",
            "Head dip",
            "How much your head drops between address and impact. A small "
            "squat is normal; a big drop lowers the whole swing's low "
            "point, so clean contact needs a last-instant rescue. Staying "
            f"under about {dip} SW is the reference.",
            SW_GLOSS,
            how="Vertical head-center drop, address \N{RIGHTWARDS ARROW} "
                f"impact, in shoulder widths; flagged beyond {dip} SW.",
        ),
        Explainer(
            "lead_arm_angle_deg",
            "Lead arm at impact",
            f"How straight your lead arm is at the strike — 180{_DEG} is "
            "perfectly straight as the camera sees it. A bent lead arm at "
            "impact shortens your reach at the exact moment that decides "
            f"contact. Staying above about {arm}{_DEG} is the reference.",
            DEG_GLOSS + f" 180{_DEG} = a perfectly straight arm.",
            how="Shoulder\N{EN DASH}elbow\N{EN DASH}wrist angle of the lead "
                "arm at the impact frame, as projected face-on; flagged "
                f"below {arm}{_DEG}.",
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
            how="Angle of the shoulder line against horizontal at the "
                f"impact frame; flagged below {tilt}{_DEG}.",
        ),
        Explainer(
            "shoulder_tilt_delta_deg",
            "Tilt change (address \N{RIGHTWARDS ARROW} impact)",
            "How the shoulder tilt changed between address and impact. It "
            "should hold or grow — the trail shoulder working down through "
            "the ball — so a negative change (tilt shrinking) is the "
            "classic stand-up pattern.",
            DEG_GLOSS + " Zero or positive change is the reference.",
            how="Impact tilt minus address tilt; flagged when negative "
                "(tilt shrinking through the ball).",
        ),
        Explainer(
            "finish_balance_sw",
            "Finish base stability",
            "How far the midpoint between your ankles drifted while you held "
            "the finish. A steady base is usually easier to repeat than a "
            "step or slide after impact, but this number is not total foot "
            f"movement. At or under about {bal} SW is the reference.",
            SW_GLOSS,
            how="Average ankle-midpoint drift while the finish is held, in "
                f"shoulder widths; flagged beyond {bal} SW. Equal and "
                "opposite foot motion can cancel, so the replay remains the "
                "visual check.",
        ),
        Explainer(
            "stance_width_sw",
            "Setup stance width",
            "How far apart your feet were at setup compared with your own "
            "shoulder width. This gives each follow-up video a repeatable "
            "setup baseline; narrow, shoulder-width, or wide is context, not "
            "a grade, because club choice, intended shot, and body shape all "
            "change the useful stance.",
            SW_GLOSS,
            how="Median horizontal ankle separation across the readable "
                "address frames, divided by address shoulder width. Context "
                "only — it never fires a coaching flag.",
        ),
        Explainer(
            "downswing_hand_speed_sw_s",
            "Downswing hand movement",
            "How quickly the midpoint between your wrists moved through the "
            "camera view from the top to the strike frame. Use it to compare "
            "your own swings filmed with the same club and camera setup; "
            "faster is not automatically better, and this is not clubhead "
            "speed, ball speed, or miles per hour.",
            SW_PER_SECOND_GLOSS,
            how="Average smoothed 2D wrist-midpoint path length from top to "
                "the audio-estimated impact frame, divided by elapsed time "
                "and address shoulder width. Context only.",
        ),
    ]
    return {e.metric: e for e in entries}


# Rendered with the default thresholds — what most callers use directly.
# Callers with a retuned config should call build_explainers(cfg.coaching).
EXPLAINERS: dict[str, Explainer] = build_explainers(DEFAULTS["coaching"])
