"""Caddie Brief v1: one evidence-backed next action, never invented data."""

from __future__ import annotations

from swinglab.caddie_brief import (
    build_caddie_brief,
    build_caddie_brief_from_payload,
    metrics_from_payload,
    payload_has_unsupported_angle_data,
    payload_requires_refilm,
)
from swinglab.coaching import (
    FLAG_CONSISTENCY,
    FLAG_HEAD_DIP,
    FLAG_TEMPO,
    flag_keys,
    strength_cards,
)
from swinglab.config import Config
from swinglab.metrics import SwingMetrics, session_stats


def metric(**overrides) -> SwingMetrics:
    values = {
        "swing": 1,
        "strike_s": 3.0,
        "backswing_s": 0.75,
        "downswing_s": 0.25,
        "tempo_ratio": 3.0,
        "head_sway_backswing_sw": 0.10,
        "head_sway_downswing_sw": 0.05,
        "hip_slide_backswing_sw": 0.10,
        "hip_slide_downswing_sw": 0.05,
        "target_direction": 1,
        "head_dip_sw": 0.10,
        "lead_arm_angle_deg": 175.0,
        "shoulder_tilt_address_deg": 10.0,
        "shoulder_tilt_impact_deg": 20.0,
        "shoulder_tilt_delta_deg": 10.0,
        "finish_balance_sw": 0.10,
        "target_confident": True,
    }
    values.update(overrides)
    return SwingMetrics(**values)


def brief_for(*metrics, previous=None, trend=None):
    rows = list(metrics)
    return build_caddie_brief(
        rows,
        session_stats(rows),
        Config(),
        previous_flag_counts=previous,
        trend=trend,
    )


def test_brief_leads_with_one_issue_one_drill_and_one_real_strength():
    brief = brief_for(metric(tempo_ratio=2.0))
    assert brief is not None
    assert brief.focus_flag == FLAG_TEMPO
    assert brief.focus_name == "Tempo"
    assert brief.focus_value == "2.00:1"
    assert "flagged below 2.4:1" in brief.benchmark_text
    assert brief.strength and "Head sway" in brief.strength
    assert brief.drill.name
    assert brief.drill.dosage
    assert "Re-film" in brief.drill.success_metric
    assert not brief.clean


def test_protect_brief_records_the_exact_selected_strength_card():
    metrics = [metric()]
    brief = brief_for(*metrics)
    selected = strength_cards(metrics, Config())[0]
    assert brief.strength_key == selected.key
    assert brief.strength == selected.text


def test_improve_brief_does_not_replace_the_selected_issue_with_a_strength_key():
    brief = brief_for(metric(tempo_ratio=2.0))
    assert brief.focus_flag == FLAG_TEMPO
    assert brief.strength_key is None


def test_history_never_changes_the_report_priority():
    current = metric(tempo_ratio=2.0, head_dip_sw=0.50)
    brief = brief_for(
        current,
        previous={FLAG_TEMPO: 0, FLAG_HEAD_DIP: 12},
    )
    assert brief.focus_flag == FLAG_TEMPO
    assert brief.recurring_sessions == 1
    assert brief.remaining_issues == 1


def test_clean_session_gets_maintenance_not_a_fabricated_fault():
    brief = brief_for(metric())
    assert brief is not None
    assert brief.clean
    assert brief.focus_flag is None
    assert brief.focus_name == "Protect this baseline"
    assert "No measured issue crossed" in brief.why
    assert brief.drill.gear_tag == "swinglab:general"


def test_clean_dtl_session_uses_tempo_only_maintenance():
    brief = build_caddie_brief_from_payload(
        {
            "meta": {"angle": "dtl"},
            "swings": [{"metrics": {"tempo_ratio": 3.0}}],
        },
        Config(),
    )
    assert brief is not None and brief.clean
    assert brief.focus_name == "Protect your tempo baseline"
    assert brief.drill.id == "rhythm-baseline-refilm"
    assert "tempo ratio" in brief.drill.success_metric
    assert "head sway" not in brief.drill.success_metric
    assert "face-on" in brief.fix
    assert brief.strength_key == "tempo"
    assert brief.drill.id == "rhythm-baseline-refilm"


def test_dtl_payload_ignores_stale_face_on_metrics():
    brief = build_caddie_brief_from_payload(
        {
            "meta": {"angle": "dtl"},
            "swings": [
                {
                    "metrics": {
                        "tempo_ratio": 3.0,
                        "head_sway_backswing_sw": 0.8,
                        "hip_slide_backswing_sw": 0.8,
                    }
                }
            ],
        },
        Config(),
    )
    assert brief is not None and brief.clean
    assert brief.focus_flag is None
    assert brief.focus_name == "Protect your tempo baseline"
    assert "tempo and rhythm only" in brief.warning


def test_authoritative_dtl_angle_scopes_payload_without_meta():
    brief = build_caddie_brief_from_payload(
        {
            "swings": [
                {
                    "metrics": {
                        "tempo_ratio": 3.0,
                        "head_sway_backswing_sw": 0.8,
                    }
                }
            ]
        },
        Config(),
        angle="dtl",
    )
    assert brief is not None and brief.clean
    assert brief.focus_name == "Protect your tempo baseline"
    assert brief.focus_flag is None
    assert "tempo and rhythm only" in brief.warning


def test_dtl_raw_gate_catches_stale_face_on_session_stats():
    for stale_stats in (
        {"mean": 0.8, "std": 0.0},
        0.8,
        "stale",
    ):
        payload = {
            "meta": {"angle": "dtl"},
            "swings": [{"metrics": {"tempo_ratio": 3.0}}],
            "session_stats": {
                "tempo_ratio": {"mean": 3.0, "std": 0.0},
                "head_sway_backswing_sw": stale_stats,
            },
        }
        assert payload_has_unsupported_angle_data(payload)


def test_dtl_raw_gate_fails_closed_on_malformed_face_on_swing_value():
    for stale_value in (0.8, "0.8", {"mean": 0.8}, True):
        payload = {
            "meta": {"angle": "dtl"},
            "swings": [
                {
                    "metrics": {
                        "tempo_ratio": 3.0,
                        "head_sway_backswing_sw": stale_value,
                    }
                }
            ],
        }
        assert payload_has_unsupported_angle_data(payload)


def test_partial_face_on_clean_session_does_not_claim_full_baseline():
    brief = build_caddie_brief_from_payload(
        {"swings": [{"metrics": {"tempo_ratio": 3.0}}]},
        Config(),
    )
    assert brief is not None and brief.clean
    assert brief.focus_name == "Protect your tempo baseline"
    assert brief.drill.name == "Rhythm baseline re-film"
    assert "sway and slide" not in brief.drill.success_metric
    assert "complete body-motion baseline" in brief.fix
    assert brief.strength_key == "tempo"
    assert brief.drill.id == "rhythm-baseline-refilm"


def test_partial_baseline_without_tempo_uses_readability_maintenance_id():
    brief = build_caddie_brief_from_payload(
        {"swings": [{"metrics": {"head_sway_backswing_sw": 0.10}}]},
        Config(),
    )
    assert brief is not None and brief.clean
    assert brief.strength_key == "sway"
    assert brief.drill.id == "readability-baseline-refilm"


def test_payload_adapter_is_honest_about_missing_data():
    cfg = Config()
    for payload in (
        {},
        {"swings": [{"metrics": {"strike_s": 2.0}}]},
        {"swings": 1},
    ):
        brief = build_caddie_brief_from_payload(payload, cfg)
        assert brief is not None
        assert brief.refilm_required
        assert brief.focus_flag is None
        assert brief.drill is None


def test_payload_adapter_preserves_context_metrics_without_coaching_on_them():
    rows = metrics_from_payload(
        {
            "swings": [
                {
                    "metrics": {
                        "tempo_ratio": 3.0,
                        "stance_width_sw": 0.92,
                        "downswing_hand_speed_sw_s": 4.75,
                    }
                }
            ]
        }
    )
    assert len(rows) == 1
    assert rows[0].stance_width_sw == 0.92
    assert rows[0].downswing_hand_speed_sw_s == 4.75
    brief = brief_for(rows[0])
    assert brief.clean


def test_context_metrics_alone_cannot_fabricate_a_coaching_brief():
    brief = build_caddie_brief_from_payload(
        {
            "swings": [
                {
                    "metrics": {
                        "stance_width_sw": 0.92,
                        "downswing_hand_speed_sw_s": 4.75,
                    }
                }
            ]
        },
        Config(),
    )
    assert brief.refilm_required


def test_partial_dtl_payload_keeps_scope_warning_and_trend():
    brief = build_caddie_brief_from_payload(
        {
            "meta": {"angle": "dtl"},
            "swings": [{"metrics": {"tempo_ratio": 2.0}}],
        },
        Config(),
        trend="Tempo has moved 2.10:1 → 2.00:1 across 2 sessions",
    )
    assert brief is not None
    assert brief.focus_flag == FLAG_TEMPO
    assert "tempo and rhythm only" in brief.warning
    assert brief.trend.endswith("across 2 sessions")


def test_payload_adapter_surfaces_existing_quality_warning():
    warning = (
        "Tracking was unstable for this swing — numbers may be off; "
        "film with a clear view."
    )
    brief = build_caddie_brief_from_payload(
        {
            "swings": [
                {
                    "metrics": {"tempo_ratio": 2.0},
                    "notes": [warning],
                }
            ]
        },
        Config(),
    )
    assert brief is not None
    assert brief.warning == warning
    assert brief.refilm_required
    assert brief.focus_flag is None
    assert brief.drill is None


def test_refilm_warning_outranks_weaker_low_confidence_note():
    weak = "Low confidence: target direction could not be read."
    severe = (
        "Tracking was unstable for this swing — numbers may be off; "
        "film with a clear view."
    )
    brief = build_caddie_brief_from_payload(
        {
            "swings": [
                {
                    "metrics": {"tempo_ratio": 2.0},
                    "notes": [weak, severe],
                }
            ]
        },
        Config(),
    )
    assert brief is not None
    assert brief.warning == severe
    assert brief.refilm_required


def test_single_string_quality_warnings_still_require_refilm():
    warning = (
        "Tracking was unstable for this swing — numbers may be off; "
        "film with a clear view."
    )
    payloads = (
        {
            "session_notes": warning,
            "swings": [{"metrics": {"tempo_ratio": 3.0}}],
        },
        {
            "swings": [
                {
                    "metrics": {"tempo_ratio": 3.0},
                    "notes": warning,
                }
            ]
        },
    )
    for payload in payloads:
        assert payload_requires_refilm(payload)
        brief = build_caddie_brief_from_payload(payload, Config())
        assert brief is not None and brief.refilm_required


def test_payload_adapter_prioritizes_root_camera_warning_over_dtl_scope_note():
    warning = (
        "Low confidence: this clip looks like it was filmed face-on, but it "
        "was uploaded as down the line — numbers may not mean what they say."
    )
    brief = build_caddie_brief_from_payload(
        {
            "meta": {"camera_angle": "dtl"},
            "session_notes": [warning],
            "swings": [{"metrics": {"tempo_ratio": 2.0}}],
        },
        Config(),
    )
    assert brief is not None
    assert brief.warning == warning
    assert brief.refilm_required


def test_partial_tilt_payload_uses_the_metric_that_actually_fired():
    brief = build_caddie_brief_from_payload(
        {
            "swings": [
                {
                    "metrics": {
                        "swing": float("nan"),
                        "shoulder_tilt_delta_deg": -5.0,
                    },
                    "notes": "legacy non-list note",
                }
            ]
        },
        Config(),
    )
    assert brief is not None
    assert brief.focus_name == "Shoulder-tilt change"
    assert brief.focus_value == "-5°"
    assert brief.benchmark_text.startswith("flagged below 0°")
    assert brief.drill.name == "Shoulder-tilt impact freeze"
    assert "tilt change at or above 0°" in brief.drill.success_metric


def test_brief_names_the_triggering_swing_when_mean_is_inside_line():
    brief = brief_for(
        metric(swing=1, head_sway_backswing_sw=0.50),
        metric(swing=2, head_sway_backswing_sw=0.10),
    )
    assert brief.focus_name == "Head sway (backswing)"
    assert brief.focus_value == "Swing 1: 0.50 SW"
    assert "flagged above 0.35 SW" in brief.benchmark_text


def test_mixed_partial_tilt_payload_shows_the_metric_that_fired():
    brief = build_caddie_brief_from_payload(
        {
            "swings": [
                {"metrics": {"shoulder_tilt_impact_deg": 10.0}},
                {"metrics": {"shoulder_tilt_delta_deg": -5.0}},
            ]
        },
        Config(),
    )
    assert brief is not None
    assert brief.focus_name == "Shoulder-tilt change"
    assert brief.focus_value == "-5°"


def test_empty_legacy_rows_cannot_create_false_consistency_praise():
    brief = build_caddie_brief_from_payload(
        {
            "swings": [
                {"metrics": {"tempo_ratio": 2.0}},
                {"metrics": {}},
            ]
        },
        Config(),
    )
    assert brief is not None
    assert brief.focus_flag == FLAG_TEMPO
    assert brief.strength is None


def test_huge_persisted_numbers_are_ignored_without_crashing():
    huge = 10**400
    brief = build_caddie_brief_from_payload(
        {
            "swings": [
                {
                    "metrics": {
                        "swing": huge,
                        "tempo_ratio": huge,
                        "head_sway_backswing_sw": huge,
                    }
                }
            ]
        },
        Config(),
    )
    assert brief is not None
    assert brief.refilm_required
    assert brief.focus_flag is None


def test_extreme_finite_tempo_values_do_not_overflow_consistency():
    payload = {
        "swings": [
            {"metrics": {"tempo_ratio": 1e308}},
            {"metrics": {"tempo_ratio": 1.0}},
        ],
        "session_stats": {
            "tempo_ratio": {"mean": 1.0, "std": 0.0}
        },
    }
    brief = build_caddie_brief_from_payload(payload, Config())
    assert brief is not None
    assert brief.focus_flag in (FLAG_TEMPO, FLAG_CONSISTENCY)
    assert flag_keys(payload, Config()) == [
        FLAG_TEMPO,
        FLAG_CONSISTENCY,
    ]
