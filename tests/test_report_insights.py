"""Novice-facing report interpretation without new coaching claims."""

from __future__ import annotations

from swinglab.config import Config
from swinglab.metrics import ANGLE_DTL, ANGLE_FACE_ON
from swinglab.report_insights import build_swing_breakdown
from swinglab.report_presenter import measurement_detail
from swinglab.metrics import session_stats
from tests.test_metrics_depth import make_metrics


def complete_metric(number: int = 1, **overrides):
    values = {
        "stance_width_sw": 0.95,
        "downswing_hand_speed_sw_s": 4.8,
    }
    values.update(overrides)
    return make_metrics(number, **values)


def test_face_on_breakdown_explains_six_paid_analysis_areas():
    cards = build_swing_breakdown(
        [complete_metric()],
        Config(),
        angle=ANGLE_FACE_ON,
        selected_club="Driver",
    )
    assert [card.title for card in cards] == [
        "Swing rhythm",
        "Setup and stance",
        "Body control",
        "Impact body shape",
        "Downswing hand movement",
        "Finish base stability",
    ]
    assert {card.evidence for card in cards} == {
        "Timing estimate",
        "Observed in this view",
        "Estimated impact frame",
        "Personal comparison only",
    }
    stance = next(card for card in cards if card.key == "stance")
    assert "about shoulder-width apart" in stance.summary
    assert "Driver" in stance.summary
    assert stance.status == "Setup baseline"


def test_context_measurements_never_become_good_bad_scores():
    cards = build_swing_breakdown(
        [
            complete_metric(
                stance_width_sw=2.5,
                downswing_hand_speed_sw_s=99.0,
            )
        ],
        Config(),
        angle=ANGLE_FACE_ON,
    )
    stance = next(card for card in cards if card.key == "stance")
    speed = next(card for card in cards if card.key == "hand-speed")
    assert stance.status == "Setup baseline" and stance.tone == "context"
    assert speed.status == "Movement baseline" and speed.tone == "context"
    assert "Faster is not automatically better" in speed.why


def test_breakdown_calls_attention_to_any_swing_that_crossed_a_line():
    cards = build_swing_breakdown(
        [
            complete_metric(1),
            complete_metric(
                2,
                tempo_ratio=2.0,
                head_sway_backswing_sw=0.5,
                lead_arm_angle_deg=130.0,
                finish_balance_sw=0.3,
            ),
        ],
        Config(),
        angle=ANGLE_FACE_ON,
    )
    by_key = {card.key: card for card in cards}
    for key in ("rhythm", "body-control", "impact", "finish"):
        assert by_key[key].status == "Needs attention"
        assert by_key[key].tone == "warning"
        assert "At least one" in by_key[key].summary


def test_uncertain_target_direction_never_becomes_toward_or_away_claim():
    cards = build_swing_breakdown(
        [complete_metric(target_confident=False)],
        Config(),
        angle=ANGLE_FACE_ON,
    )
    body = next(card for card in cards if card.key == "body-control")
    assert body.status == "Direction uncertain"
    assert body.tone == "context"
    assert "sideways" in body.summary
    assert "away from the target" not in body.summary
    assert "toward the target" not in body.summary
    assert "instead of guessing" in body.limit


def test_finish_card_names_ankle_midpoint_limit_instead_of_total_foot_motion():
    card = next(
        card
        for card in build_swing_breakdown(
            [complete_metric()], Config(), angle=ANGLE_FACE_ON
        )
        if card.key == "finish"
    )
    assert "midpoint of your stance" in card.summary
    assert "ankle-midpoint drift" in card.limit
    assert "not every movement" in card.limit


def test_hand_movement_card_rejects_launch_monitor_language():
    card = next(
        card
        for card in build_swing_breakdown(
            [complete_metric()], Config(), angle=ANGLE_FACE_ON
        )
        if card.key == "hand-speed"
    )
    assert "not clubhead speed or ball speed" in card.limit
    assert "never mph" in card.limit
    assert "same club, view, framing" in card.limit


def test_dtl_breakdown_remains_rhythm_only():
    cards = build_swing_breakdown(
        [complete_metric()], Config(), angle=ANGLE_DTL
    )
    assert [card.key for card in cards] == ["rhythm"]


def test_unreadable_context_is_named_instead_of_guessed():
    cards = build_swing_breakdown(
        [make_metrics()], Config(), angle=ANGLE_FACE_ON
    )
    by_key = {card.key: card for card in cards}
    assert by_key["stance"].status == "Not measured"
    assert by_key["hand-speed"].status == "Not measured"
    assert "instead of guessing" in by_key["stance"].limit


def test_guided_measurement_keeps_legacy_stance_fact_without_reusing_legacy_taxonomy():
    metrics = [complete_metric(stance_width_sw=0.95)]
    legacy = next(card for card in build_swing_breakdown(metrics, Config(), angle=ANGLE_FACE_ON) if card.key == "stance")
    guided = measurement_detail("stance_width_sw", metrics, session_stats(metrics), Config())
    assert guided is not None
    assert "0.95" in legacy.summary
    assert guided.numeric_value == 0.95
