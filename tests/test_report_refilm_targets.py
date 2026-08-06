from __future__ import annotations

import pytest

from swinglab.caddie_brief import CaddieBrief
from swinglab.coaching import IssueCard, StrengthCard, issue_cards, strength_cards
from swinglab.config import Config
from swinglab.drills import Drill
from swinglab.metrics import SwingMetrics, session_stats
from swinglab.report_presenter import UnsupportedRefilmTarget, build_refilm_target
from swinglab.report_view import MeasurementUnit, TargetComparator, TargetWindow


def brief(*, focus: str | None, strength: str | None = None) -> CaddieBrief:
    return CaddieBrief(None, strength, focus, "Priority", None, None, "Why.", "Cue.", Drill("d", "Drill", "Aim", ("one",), "5", "do not parse me", "swinglab:test"), None, None, 0, 0, focus is None, False)


def card(flag: str, metric: str) -> IssueCard:
    return IssueCard(flag, metric, "Priority", "SW", (), None, "session", "", None, "", "higher", "warn", "Why.", "Cue.", (), ())


@pytest.mark.parametrize("flag, metric, comparator, coach_key, unit, successes", (
    ("tempo", "tempo_ratio", TargetComparator.COUNT_GTE, "tempo_warn_below", MeasurementUnit.RATIO, (4, 5)),
    ("consistency", "tempo_ratio", TargetComparator.LTE, "tempo_std_praise", MeasurementUnit.RATIO, None),
    ("sway", "head_sway_backswing_sw", TargetComparator.ALL_LTE, "sway_warn_sw", MeasurementUnit.SHOULDER_WIDTHS, None),
    ("hip-slide", "hip_slide_backswing_sw", TargetComparator.ALL_LTE, "sway_warn_sw", MeasurementUnit.SHOULDER_WIDTHS, None),
    ("head-dip", "head_dip_sw", TargetComparator.ALL_LTE, "head_dip_warn_sw", MeasurementUnit.SHOULDER_WIDTHS, None),
    ("arm-extension", "lead_arm_angle_deg", TargetComparator.COUNT_GTE, "lead_arm_warn_deg", MeasurementUnit.DEGREES, (4, 5)),
    ("shoulder-tilt", "shoulder_tilt_impact_deg", TargetComparator.ALL_GTE, "shoulder_tilt_impact_min_deg", MeasurementUnit.DEGREES, None),
    ("shoulder-tilt", "shoulder_tilt_delta_deg", TargetComparator.ALL_GTE, None, MeasurementUnit.DEGREES, None),
    ("balance", "finish_balance_sw", TargetComparator.ALL_LTE, "finish_balance_warn_sw", MeasurementUnit.SHOULDER_WIDTHS, None),
))
def test_issue_family_refilm_targets_are_explicit_and_configured(flag, metric, comparator, coach_key, unit, successes):
    cfg = Config()
    target = build_refilm_target(brief(focus=flag), (card(flag, metric),), (), cfg)
    expected = 0.0 if coach_key is None else cfg.coaching[coach_key]
    target_metric = "tempo_ratio_std" if flag == "consistency" else metric
    assert (target.metric_id, target.comparator, target.threshold, target.unit, target.window) == (target_metric, comparator, expected, unit, TargetWindow.SESSION)
    assert (target.required_successes, target.required_attempts) == successes if successes else (None, None)
    assert f"{expected:g}" in target.text


def test_target_text_and_structured_threshold_stay_in_sync_after_config_retune():
    cfg = Config()
    cfg.coaching["sway_warn_sw"] = 0.47
    target = build_refilm_target(brief(focus="sway"), (card("sway", "head_sway_backswing_sw"),), (), cfg)
    assert target.threshold == 0.47
    assert "0.47" in target.text


def test_protect_uses_targetable_selected_strength_mapping():
    target = build_refilm_target(
        brief(focus=None, strength="tempo"), (),
        (StrengthCard("tempo", "tempo_ratio", "Tempo", "steady"),), Config(),
    )
    assert target.metric_id == "tempo_ratio"
    assert target.comparator is TargetComparator.COUNT_GTE
    assert (target.required_successes, target.required_attempts) == (4, 5)


def swing(number: int, tempo: float) -> SwingMetrics:
    return SwingMetrics(
        swing=number, strike_s=3.0, backswing_s=0.75, downswing_s=0.25,
        tempo_ratio=tempo, head_sway_backswing_sw=0.1,
        head_sway_downswing_sw=0.05, hip_slide_backswing_sw=0.1,
        hip_slide_downswing_sw=0.05, target_direction=1, head_dip_sw=0.1,
        lead_arm_angle_deg=175.0, shoulder_tilt_address_deg=10.0,
        shoulder_tilt_impact_deg=20.0, shoulder_tilt_delta_deg=10.0,
        finish_balance_sw=0.1, target_confident=True,
    )


def test_real_consistency_issue_card_maps_tempo_ratio_to_standard_deviation_target():
    cfg = Config()
    rows = [swing(1, 2.6), swing(2, 3.4)]
    selected = next(card for card in issue_cards(rows, session_stats(rows), cfg) if card.flag == "consistency")
    assert selected.metric == "tempo_ratio"
    target = build_refilm_target(brief(focus="consistency"), (selected,), (), cfg)
    assert (target.metric_id, target.comparator, target.threshold) == (
        "tempo_ratio_std", TargetComparator.LTE, cfg.coaching["tempo_std_praise"],
    )


def test_real_consistency_strength_card_maps_tempo_ratio_to_standard_deviation_target():
    cfg = Config()
    rows = [swing(1, 2.9), swing(2, 3.1)]
    selected = next(card for card in strength_cards(rows, cfg, session_stats(rows)) if card.key == "consistency")
    assert selected.metric == "tempo_ratio"
    target = build_refilm_target(brief(focus=None, strength="consistency"), (), (selected,), cfg)
    assert (target.metric_id, target.comparator, target.threshold) == (
        "tempo_ratio_std", TargetComparator.LTE, cfg.coaching["tempo_std_praise"],
    )


@pytest.mark.parametrize("metric", ("stance_width_sw", "downswing_hand_speed_sw_s"))
def test_context_only_strength_cannot_receive_a_manufactured_target(metric):
    with pytest.raises(UnsupportedRefilmTarget):
        build_refilm_target(
            brief(focus=None, strength="context"), (),
            (StrengthCard("context", metric, "Context", "context only"),), Config(),
        )
