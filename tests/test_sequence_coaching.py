"""The kinematic sequence as coaching: one number, one flag, one priority.

:mod:`swinglab.sequence` measures the order body segments peak. This module
covers what happens after that — the reduction to a single signed number, the
flag it fires, and the rule that decides whether a sequencing fault or a
positional one gets the report's single priority slot.

The rule under test (``PRIORITY_RULE_SEQUENCE_FIRST``) is deliberately narrow:
a fired sequence flag goes to the front **of its severity band**, not to the
front of the report, and nothing anywhere claims the sequencing fault *caused*
the positional one. A single 2D camera cannot show that, so the ranking says
"look at this first" and stops there.

Replay safety is the other half. Rules 1 and 2 are immutable: a report stored
under either must render the same order forever, so a new flag must not
renumber them.
"""

from __future__ import annotations

import math

import pytest

from swinglab import coaching, sequence
from swinglab.coaching import (
    FLAG_HEAD_DIP,
    FLAG_SEQUENCE,
    PRIORITY_RULE_CLUB_AWARE,
    PRIORITY_RULE_LEGACY,
    PRIORITY_RULE_SEQUENCE_FIRST,
)
from swinglab.config import Config
from swinglab.metrics import ANGLE_DTL, SwingMetrics, compute_metrics, session_stats

from tests.test_sequence import FPS, FRAMES, _events, _tracked


def full_body(pelvis_peak: int, torso_peak: int, arm_peak: int):
    """test_sequence's frames carry only the segments the ordering needs.

    compute_metrics reads a whole body, so the head and feet are added here —
    parked still, since this module is testing the sequence field and a moving
    head would just add unrelated flags to the fixture.
    """
    import numpy as np

    from swinglab import pose as pose_module

    frames = []
    for frame in _tracked(pelvis_peak, torso_peak, arm_peak):
        enriched = dict(frame)
        enriched[pose_module.NOSE] = np.array([500.0, 300.0])
        enriched[pose_module.LEFT_EAR] = np.array([515.0, 300.0])
        enriched[pose_module.RIGHT_EAR] = np.array([485.0, 300.0])
        enriched[pose_module.LEFT_ANKLE] = np.array([540.0, 800.0])
        enriched[pose_module.RIGHT_ANKLE] = np.array([460.0, 800.0])
        frames.append(enriched)
    return frames

SHIPPED = "config.yaml"


def shipped_config() -> Config:
    """The deployed config — rule 3 is on there and off in the code default."""
    return Config.load(SHIPPED)


def swing(number: int = 1, **overrides) -> SwingMetrics:
    """A clean swing, with only the fields under test moved."""
    base = dict(
        swing=number,
        strike_s=1.0,
        backswing_s=0.9,
        downswing_s=0.3,
        tempo_ratio=3.0,
        head_sway_backswing_sw=0.1,
        head_sway_downswing_sw=0.1,
        hip_slide_backswing_sw=0.1,
        hip_slide_downswing_sw=0.1,
        target_direction=1,
    )
    base.update(overrides)
    return SwingMetrics(**base)


def flags_in_order(metrics: list[SwingMetrics], **kwargs) -> list[str]:
    cfg = shipped_config()
    cards = coaching.issue_cards(metrics, session_stats(metrics), cfg, **kwargs)
    return [card.flag for card in cards]


# -- the reduction to one number --------------------------------------------

def test_pelvis_leading_the_arm_is_a_positive_lead():
    """Sign is the whole finding: positive means the hips peaked first."""
    measured = sequence.analyze_sequence(
        _tracked(pelvis_peak=6, torso_peak=12, arm_peak=20),
        _events(),
        FPS,
        "right",
        "face-on",
    )
    lead_ms = sequence.pelvis_to_arm_lead_ms(measured, FPS)
    assert lead_ms is not None and lead_ms > 0


def test_arms_peaking_first_is_a_negative_lead():
    measured = sequence.analyze_sequence(
        _tracked(pelvis_peak=20, torso_peak=12, arm_peak=6),
        _events(),
        FPS,
        "right",
        "face-on",
    )
    lead_ms = sequence.pelvis_to_arm_lead_ms(measured, FPS)
    assert lead_ms is not None and lead_ms < 0


def test_a_lead_the_frame_rate_cannot_resolve_is_not_a_number():
    """Below the separation floor there is no ordering, so there is no lead.

    Returning 0.0 here would be worse than returning nothing: zero is a
    perfectly good value that would sail through every downstream check.
    """
    measured = sequence.analyze_sequence(
        _tracked(pelvis_peak=12, torso_peak=13, arm_peak=13),
        _events(),
        FPS,
        "right",
        "face-on",
    )
    assert sequence.pelvis_to_arm_lead_ms(measured, FPS) is None


def test_an_unmeasured_sequence_has_no_lead():
    refused = sequence.analyze_sequence(
        _tracked(pelvis_peak=6, torso_peak=12, arm_peak=20),
        _events(),
        FPS,
        "right",
        "dtl",
    )
    assert not refused.measured
    assert sequence.pelvis_to_arm_lead_ms(refused, FPS) is None


# -- the number reaching SwingMetrics ---------------------------------------

def test_compute_metrics_carries_the_sequence_through():
    metrics = compute_metrics(
        1,
        full_body(pelvis_peak=20, torso_peak=12, arm_peak=6),
        _events(),
        FRAMES - 1,
        "right",
        cfg=Config(),
        fps=FPS,
    )
    assert metrics.sequence_pelvis_to_arm_ms < 0


def test_down_the_line_never_produces_a_sequence_number():
    """Segment angles live in the image plane, so DTL has nothing to measure."""
    metrics = compute_metrics(
        1,
        full_body(pelvis_peak=20, torso_peak=12, arm_peak=6),
        _events(),
        FRAMES - 1,
        "right",
        cfg=Config(),
        angle=ANGLE_DTL,
        fps=FPS,
    )
    assert math.isnan(metrics.sequence_pelvis_to_arm_ms)


# -- the flag ----------------------------------------------------------------

def test_arms_first_fires_the_sequence_flag():
    metrics = [swing(1, sequence_pelvis_to_arm_ms=-70.0)]
    assert FLAG_SEQUENCE in coaching.session_flags(
        metrics, session_stats(metrics), shipped_config()
    )


def test_hips_first_does_not_fire_it():
    metrics = [swing(1, sequence_pelvis_to_arm_ms=+70.0)]
    assert FLAG_SEQUENCE not in coaching.session_flags(
        metrics, session_stats(metrics), shipped_config()
    )


def test_an_unmeasured_sequence_never_fires_it():
    """NaN is the value a refusal arrives as, and a refusal must stay silent."""
    metrics = [swing(1, sequence_pelvis_to_arm_ms=float("nan"))]
    assert FLAG_SEQUENCE not in coaching.session_flags(
        metrics, session_stats(metrics), shipped_config()
    )


def test_the_payload_reader_agrees_with_the_in_memory_reader():
    """flag_keys and session_flags must not drift — they gate the same report."""
    payload = {
        "swings": [{"metrics": {"sequence_pelvis_to_arm_ms": -70.0}}],
        "meta": {"angle": "face-on"},
    }
    assert FLAG_SEQUENCE in coaching.flag_keys(payload, shipped_config())


# -- the priority slot -------------------------------------------------------

def casting_and_dipping() -> list[SwingMetrics]:
    """Every swing both out of order and dipping: two 'major' cards."""
    return [
        swing(n, sequence_pelvis_to_arm_ms=-70.0, head_dip_sw=0.40)
        for n in (1, 2, 3)
    ]


def test_rule_three_gives_the_sequence_the_priority_slot():
    order = flags_in_order(casting_and_dipping())
    assert order[0] == FLAG_SEQUENCE
    assert FLAG_HEAD_DIP in order


def test_rule_three_is_what_the_shipped_config_selects():
    assert coaching.priority_rule_version(shipped_config()) == (
        PRIORITY_RULE_SEQUENCE_FIRST
    )


@pytest.mark.parametrize(
    "rule", [PRIORITY_RULE_LEGACY, PRIORITY_RULE_CLUB_AWARE]
)
def test_older_rules_still_rank_the_positional_fault_first(rule):
    """Replay safety: a stored rule-1/rule-2 report must not silently reorder.

    The sequence card may appear — the flag is measured under every rule — but
    it must not jump the queue, because that queue is what those reports were
    rendered from.
    """
    order = flags_in_order(casting_and_dipping(), rule_version=rule)
    assert order[0] == FLAG_HEAD_DIP


def test_the_legacy_flag_order_is_unchanged():
    """The rule-1 order of the pre-existing flags, pinned.

    Scope, honestly: the legacy rank is ``enumerate(specs)``, so *inserting* a
    key shifts every later rank uniformly and leaves relative order intact —
    this test cannot see an insertion, and pretending otherwise would be the
    kind of green that means nothing. It catches a reorder of existing
    entries. Where the new key was inserted is covered by
    ``test_older_rules_still_rank_the_positional_fault_first``, which does go
    red when FLAG_SEQUENCE is placed at the front of the dict.
    """
    metrics = [
        swing(n, head_dip_sw=0.40, finish_balance_sw=0.40, tempo_ratio=2.0)
        for n in (1, 2, 3)
    ]
    order = flags_in_order(metrics, rule_version=PRIORITY_RULE_LEGACY)
    assert order == ["tempo", "head-dip", "balance"]


def test_severity_still_outranks_the_sequence():
    """Rule 3 moves the sequence to the front of its band, not the report.

    One inverted swing out of two is a 'warn'; a head dip on both is 'major'.
    The more severe finding has to keep the slot, or "one priority" stops
    meaning "the biggest thing measured".
    """
    mixed = [
        swing(1, sequence_pelvis_to_arm_ms=-70.0, head_dip_sw=0.40),
        swing(2, sequence_pelvis_to_arm_ms=+90.0, head_dip_sw=0.40),
    ]
    cards = coaching.issue_cards(mixed, session_stats(mixed), shipped_config())
    by_flag = {card.flag: card.severity for card in cards}
    assert by_flag[FLAG_SEQUENCE] == "warn"
    assert by_flag[FLAG_HEAD_DIP] == "major"
    assert cards[0].flag == FLAG_HEAD_DIP


# -- what the golfer is actually handed --------------------------------------

def test_the_brief_prescribes_a_drill_with_a_falsifiable_pass_mark():
    from swinglab.caddie_brief import build_caddie_brief

    metrics = casting_and_dipping()
    brief = build_caddie_brief(metrics, session_stats(metrics), shipped_config())
    assert brief is not None
    assert brief.focus_flag == FLAG_SEQUENCE
    # The pass mark has to be checkable against a re-film, which means it has
    # to carry a number rather than an encouragement.
    assert any(char.isdigit() for char in brief.drill.success_metric)
    assert "re-film" in brief.drill.success_metric.lower()


def test_the_report_never_says_the_sequence_caused_the_other_fault():
    """The one claim the measurement cannot support, asserted against the copy.

    Ranking a cause above a symptom is a coaching judgement. Telling a golfer
    "this is why that happens" is a causal claim from a single 2D camera, and
    the difference between the two is the product's credibility.
    """
    prose = " ".join(
        [coaching.WHY_TEXT[FLAG_SEQUENCE], coaching.FIX_TEXT[FLAG_SEQUENCE]]
    ).lower()
    for forbidden in ("causes", "caused", "because of this", "which is why your"):
        assert forbidden not in prose
