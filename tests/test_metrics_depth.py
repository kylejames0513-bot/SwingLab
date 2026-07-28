"""The four depth metrics (head dip, lead-arm angle, shoulder tilt, finish
balance), their flags/notes, the issue-card contract, and the new config keys.

All synthetic sequences are built from tests.conftest.make_landmarks with
per-frame landmark overrides — no ffmpeg, no mediapipe.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from swinglab import drills, pose
from swinglab.coaching import (
    FLAG_ARM_EXTENSION,
    FLAG_BALANCE,
    FLAG_HEAD_DIP,
    FLAG_SHOULDER_TILT,
    flag_keys,
    issue_cards,
    session_flags,
    session_notes,
    swing_notes,
)
from swinglab.config import DEFAULTS, Config
from swinglab.events import SwingEvents
from swinglab.metrics import (
    NUMERIC_FIELDS,
    SwingMetrics,
    compute_metrics,
    lead_trail_sides,
    session_stats,
)
from tests.conftest import make_landmarks


def events_for(tracked, shoulder_width=100.0, fps=30.0):
    return SwingEvents(
        address_idx=0,
        takeaway_idx=10,
        top_idx=40,
        impact_idx=54,
        takeaway_s=10 / fps,
        top_s=40 / fps,
        impact_s=54 / fps,
        finish_s=54 / fps + 0.55,
        shoulder_width_px=shoulder_width,
        hand_baseline=np.array([500.0, 600.0]),
    )


def shifted(lm: pose.Landmarks, dx: float = 0.0, dy: float = 0.0) -> pose.Landmarks:
    return {k: v + np.array([dx, dy]) for k, v in lm.items()}


def make_metrics(n: int = 1, **overrides) -> SwingMetrics:
    """A swing that fires nothing with default thresholds."""
    values = dict(
        swing=n,
        strike_s=3.0 * n,
        backswing_s=0.9,
        downswing_s=0.3,
        tempo_ratio=3.0,
        head_sway_backswing_sw=0.1,
        head_sway_downswing_sw=-0.1,
        hip_slide_backswing_sw=0.1,
        hip_slide_downswing_sw=-0.1,
        target_direction=1,
        head_dip_sw=0.1,
        lead_arm_angle_deg=170.0,
        shoulder_tilt_address_deg=8.0,
        shoulder_tilt_impact_deg=12.0,
        shoulder_tilt_delta_deg=4.0,
        finish_balance_sw=0.05,
    )
    values.update(overrides)
    return SwingMetrics(**values)


# ---------------------------------------------------------------- head_dip_sw


def test_head_dip_measures_sustained_drop():
    tracked = [make_landmarks() for _ in range(75)]
    for i in range(44, 55):  # mid-downswing through impact: everything +30 px down
        tracked[i] = shifted(make_landmarks(), dy=30.0)
    m = compute_metrics(1, tracked, events_for(tracked), 70, "right")
    assert m.head_dip_sw == pytest.approx(0.30, abs=0.005)


def test_head_dip_rising_head_clamps_to_zero():
    tracked = [make_landmarks() for _ in range(75)]
    for i in range(44, 55):
        tracked[i] = shifted(make_landmarks(), dy=-30.0)
    m = compute_metrics(1, tracked, events_for(tracked), 70, "right")
    assert m.head_dip_sw == 0.0


def test_head_dip_single_frame_spike_suppressed_by_median():
    tracked = [make_landmarks() for _ in range(75)]
    tracked[30] = shifted(make_landmarks(), dy=80.0)  # one-frame pose glitch
    m = compute_metrics(1, tracked, events_for(tracked), 70, "right")
    assert m.head_dip_sw < 0.1


def test_head_dip_nan_with_fewer_than_three_valid_frames():
    tracked = [make_landmarks() for _ in range(75)]
    for i in range(55):
        if i not in (0, 27):
            tracked[i] = None
    m = compute_metrics(1, tracked, events_for(tracked), 70, "right")
    assert math.isnan(m.head_dip_sw)


# -------------------------------------------------------- lead_arm_angle_deg


def arm_chain(lm, side_prefix, shoulder, elbow, wrist):
    s, e, w = side_prefix
    lm[s] = np.array(shoulder, dtype=np.float64)
    lm[e] = np.array(elbow, dtype=np.float64)
    lm[w] = np.array(wrist, dtype=np.float64)
    return lm


LEFT_CHAIN = (pose.LEFT_SHOULDER, pose.LEFT_ELBOW, pose.LEFT_WRIST)
RIGHT_CHAIN = (pose.RIGHT_SHOULDER, pose.RIGHT_ELBOW, pose.RIGHT_WRIST)


def test_lead_arm_straight_reads_180():
    tracked = [make_landmarks() for _ in range(75)]
    tracked[54] = arm_chain(
        make_landmarks(), LEFT_CHAIN, (550, 250), (550, 400), (550, 600)
    )
    m = compute_metrics(1, tracked, events_for(tracked), 70, "right")
    assert m.lead_arm_angle_deg == pytest.approx(180.0)


def test_lead_arm_right_angle_reads_90():
    tracked = [make_landmarks() for _ in range(75)]
    tracked[54] = arm_chain(
        make_landmarks(), LEFT_CHAIN, (550, 250), (550, 400), (700, 400)
    )
    m = compute_metrics(1, tracked, events_for(tracked), 70, "right")
    assert m.lead_arm_angle_deg == pytest.approx(90.0)


def test_lead_arm_handedness_uses_right_chain_for_lefty():
    tracked = [make_landmarks() for _ in range(75)]
    lm = arm_chain(make_landmarks(), LEFT_CHAIN, (550, 250), (550, 400), (550, 600))
    lm = arm_chain(lm, RIGHT_CHAIN, (450, 250), (450, 400), (300, 400))
    tracked[54] = lm
    ev = events_for(tracked)
    right = compute_metrics(1, tracked, ev, 70, "right")
    left = compute_metrics(1, tracked, ev, 70, "left")
    assert right.lead_arm_angle_deg == pytest.approx(180.0)  # left arm, straight
    assert left.lead_arm_angle_deg == pytest.approx(90.0)  # right arm, bent


def test_lead_arm_nan_when_impact_frame_missing():
    tracked = [make_landmarks() for _ in range(75)]
    tracked[54] = None
    m = compute_metrics(1, tracked, events_for(tracked), 70, "right")
    assert math.isnan(m.lead_arm_angle_deg)


def test_lead_arm_nan_on_degenerate_segment():
    tracked = [make_landmarks() for _ in range(75)]
    tracked[54] = arm_chain(
        make_landmarks(), LEFT_CHAIN, (550, 250), (550, 400), (550, 400)
    )  # wrist == elbow
    m = compute_metrics(1, tracked, events_for(tracked), 70, "right")
    assert math.isnan(m.lead_arm_angle_deg)


def test_lead_trail_sides_swaps_with_handedness():
    lead, trail = lead_trail_sides("right")
    assert lead == LEFT_CHAIN
    assert trail == RIGHT_CHAIN
    assert lead_trail_sides("left") == (RIGHT_CHAIN, LEFT_CHAIN)


# ----------------------------------------------------------- shoulder_tilt_*


def shoulders_tilted(angle_deg: float) -> pose.Landmarks:
    """Right-handed: lead = LEFT shoulder at (550, 250), trail = RIGHT at
    x 450; drop the trail shoulder so tilt reads +angle_deg."""
    lm = make_landmarks()
    lm[pose.RIGHT_SHOULDER] = np.array(
        [450.0, 250.0 + 100.0 * math.tan(math.radians(angle_deg))]
    )
    return lm


def test_shoulder_tilt_address_impact_and_delta():
    tracked = [make_landmarks() for _ in range(75)]
    tracked[0] = shoulders_tilted(8.0)
    tracked[54] = shoulders_tilted(25.0)
    m = compute_metrics(1, tracked, events_for(tracked), 70, "right")
    assert m.shoulder_tilt_address_deg == pytest.approx(8.0, abs=0.1)
    assert m.shoulder_tilt_impact_deg == pytest.approx(25.0, abs=0.1)
    assert m.shoulder_tilt_delta_deg == pytest.approx(17.0, abs=0.2)


def test_shoulder_tilt_sign_flips_with_handedness():
    tracked = [make_landmarks() for _ in range(75)]
    tracked[0] = shoulders_tilted(8.0)
    tracked[54] = shoulders_tilted(25.0)
    ev = events_for(tracked)
    left = compute_metrics(1, tracked, ev, 70, "left")
    assert left.shoulder_tilt_address_deg == pytest.approx(-8.0, abs=0.1)
    assert left.shoulder_tilt_impact_deg == pytest.approx(-25.0, abs=0.1)


def test_shoulder_tilt_nan_when_shoulders_stacked():
    tracked = [make_landmarks() for _ in range(75)]
    tracked[54] = make_landmarks(shoulder_span=10.0)  # |dx| = 10 < 0.2 * SW
    m = compute_metrics(1, tracked, events_for(tracked), 70, "right")
    assert math.isnan(m.shoulder_tilt_impact_deg)
    assert math.isnan(m.shoulder_tilt_delta_deg)  # delta needs both ends


def test_shoulder_tilt_delta_nan_when_address_missing():
    tracked = [make_landmarks() for _ in range(75)]
    tracked[0] = None
    m = compute_metrics(1, tracked, events_for(tracked), 70, "right")
    assert math.isnan(m.shoulder_tilt_address_deg)
    assert not math.isnan(m.shoulder_tilt_impact_deg)
    assert math.isnan(m.shoulder_tilt_delta_deg)


# --------------------------------------------------------- finish_balance_sw


def test_finish_balance_zero_when_still():
    tracked = [make_landmarks() for _ in range(75)]
    m = compute_metrics(1, tracked, events_for(tracked), 70, "right")
    assert m.finish_balance_sw == 0.0


def test_finish_balance_step_measured_exactly():
    tracked = [make_landmarks() for _ in range(75)]
    for i in (73, 74):
        lm = make_landmarks()
        lm[pose.LEFT_ANKLE] = lm[pose.LEFT_ANKLE] + np.array([20.0, 0.0])
        lm[pose.RIGHT_ANKLE] = lm[pose.RIGHT_ANKLE] + np.array([20.0, 0.0])
        tracked[i] = lm
    m = compute_metrics(1, tracked, events_for(tracked), 70, "right")
    # hold window clamps to frames 70..74; drift vs frame 70 is 0, 0, 20, 20 px
    # -> mean 10 px = 0.10 SW
    assert m.finish_balance_sw == pytest.approx(0.10, abs=0.001)


def test_finish_balance_reads_hold_frames_from_cfg():
    tracked = [make_landmarks() for _ in range(75)]
    for i in (73, 74):
        lm = make_landmarks()
        lm[pose.LEFT_ANKLE] = lm[pose.LEFT_ANKLE] + np.array([20.0, 0.0])
        lm[pose.RIGHT_ANKLE] = lm[pose.RIGHT_ANKLE] + np.array([20.0, 0.0])
        tracked[i] = lm
    cfg = Config()
    cfg.analysis["finish_hold_frames"] = 2  # hold ends before the step
    m = compute_metrics(1, tracked, events_for(tracked), 70, "right", cfg=cfg)
    assert m.finish_balance_sw == 0.0


def test_finish_balance_nan_with_fewer_than_three_hold_frames():
    tracked = [make_landmarks() for _ in range(75)]
    for i in range(71, 75):
        tracked[i] = None
    m = compute_metrics(1, tracked, events_for(tracked), 70, "right")
    assert math.isnan(m.finish_balance_sw)


def test_finish_balance_window_clamp_never_raises():
    tracked = [make_landmarks() for _ in range(75)]
    m = compute_metrics(1, tracked, events_for(tracked), 73, "right")
    assert math.isnan(m.finish_balance_sw)  # only 2 hold frames fit
    m = compute_metrics(1, tracked, events_for(tracked), 74, "right")
    assert math.isnan(m.finish_balance_sw)  # finish on the last frame


def test_fully_missing_window_all_new_metrics_nan():
    tracked: list = [None] * 75
    m = compute_metrics(1, tracked, events_for(tracked), 70, "right")
    for field_name in (
        "head_dip_sw",
        "lead_arm_angle_deg",
        "shoulder_tilt_address_deg",
        "shoulder_tilt_impact_deg",
        "shoulder_tilt_delta_deg",
        "finish_balance_sw",
    ):
        assert math.isnan(getattr(m, field_name))


# ------------------------------------------------------------ flags & notes


def all_flags_metrics() -> list[SwingMetrics]:
    return [
        make_metrics(
            1,
            tempo_ratio=2.0,
            head_sway_backswing_sw=0.5,
            hip_slide_backswing_sw=0.5,
            head_dip_sw=0.4,
            lead_arm_angle_deg=120.0,
            shoulder_tilt_impact_deg=2.0,
            shoulder_tilt_delta_deg=-6.0,
            finish_balance_sw=0.3,
        ),
        make_metrics(2, tempo_ratio=3.0),  # tempo std 0.5 fires consistency
    ]


def payload_for(ms: list[SwingMetrics], stats: dict) -> dict:
    """A metrics.json-shaped payload (NaN -> null), round-tripped through JSON."""

    def clean(d: dict) -> dict:
        return {
            k: (None if isinstance(v, float) and math.isnan(v) else v)
            for k, v in d.items()
        }

    payload = {
        "swings": [{"metrics": clean(m.as_dict())} for m in ms],
        "session_stats": stats,
    }
    return json.loads(json.dumps(payload))


def test_new_flags_fire_in_order_after_hip_slide_before_consistency():
    ms = all_flags_metrics()
    stats = session_stats(ms)
    assert session_flags(ms, stats, Config()) == [
        "sway",
        "tempo",
        "hip-slide",
        "head-dip",
        "arm-extension",
        "shoulder-tilt",
        "balance",
        "consistency",
    ]


def test_flag_keys_round_trip_matches_session_flags():
    ms = all_flags_metrics()
    stats = session_stats(ms)
    assert flag_keys(payload_for(ms, stats), Config()) == session_flags(
        ms, stats, Config()
    )


def test_each_new_flag_fires_alone():
    cfg = Config()
    for flag, overrides in [
        (FLAG_HEAD_DIP, {"head_dip_sw": 0.26}),
        (FLAG_ARM_EXTENSION, {"lead_arm_angle_deg": 149.0}),
        (FLAG_SHOULDER_TILT, {"shoulder_tilt_impact_deg": 4.9}),
        (FLAG_SHOULDER_TILT, {"shoulder_tilt_delta_deg": -0.1}),
        (FLAG_BALANCE, {"finish_balance_sw": 0.16}),
    ]:
        ms = [make_metrics(1, **overrides)]
        stats = session_stats(ms)
        assert session_flags(ms, stats, cfg) == [flag]
        assert flag_keys(payload_for(ms, stats), cfg) == [flag]


def test_values_at_threshold_do_not_fire():
    ms = [
        make_metrics(
            1,
            head_dip_sw=0.25,
            lead_arm_angle_deg=150.0,
            shoulder_tilt_impact_deg=5.0,
            shoulder_tilt_delta_deg=0.0,
            finish_balance_sw=0.15,
        )
    ]
    stats = session_stats(ms)
    assert session_flags(ms, stats, Config()) == []
    assert flag_keys(payload_for(ms, stats), Config()) == []


def test_nan_never_fires_new_flags():
    nan = float("nan")
    ms = [
        make_metrics(
            1,
            head_dip_sw=nan,
            lead_arm_angle_deg=nan,
            shoulder_tilt_impact_deg=nan,
            shoulder_tilt_delta_deg=nan,
            finish_balance_sw=nan,
        )
    ]
    stats = session_stats(ms)
    assert session_flags(ms, stats, Config()) == []
    assert flag_keys(payload_for(ms, stats), Config()) == []


def test_flag_keys_tolerates_legacy_payload_without_new_keys():
    legacy = {
        "swings": [
            {"metrics": {"tempo_ratio": 2.0, "head_sway_backswing_sw": 0.5}}
        ]
    }
    assert flag_keys(legacy, Config()) == ["sway", "tempo"]


def test_swing_notes_new_flags_present_and_tilt_note_single():
    m = make_metrics(
        1,
        head_dip_sw=0.4,
        lead_arm_angle_deg=120.0,
        shoulder_tilt_impact_deg=2.0,
        shoulder_tilt_delta_deg=-6.0,
        finish_balance_sw=0.3,
    )
    notes = swing_notes(m, Config())
    text = " ".join(notes)
    assert "Head drops 0.40 shoulder widths" in text
    assert "Lead arm is bent to 120\N{DEGREE SIGN} at impact" in text
    assert "as seen from the camera" in text
    assert "Shoulders are nearly level at impact (2\N{DEGREE SIGN}" in text
    assert "Feet drift 0.30 shoulder widths" in text
    # both tilt rules fire, but at most one shoulder-tilt note per swing
    assert _tilt_note_count(notes) == 1


def _tilt_note_count(notes: list[str]) -> int:
    return sum(
        n.startswith("Shoulders are nearly level")
        or n.startswith("Shoulder tilt fell")
        for n in notes
    )


def test_swing_notes_tilt_decrease_branch():
    m = make_metrics(
        1,
        shoulder_tilt_address_deg=12.0,
        shoulder_tilt_impact_deg=9.0,
        shoulder_tilt_delta_deg=-3.0,
    )
    notes = swing_notes(m, Config())
    assert any(
        "Shoulder tilt fell from 12\N{DEGREE SIGN} at address" in n for n in notes
    )
    assert _tilt_note_count(notes) == 1


def test_swing_notes_clean_fallback_unchanged():
    notes = swing_notes(make_metrics(1), Config())
    assert notes == [
        "No measured coaching value crossed its configured threshold on "
        "this swing."
    ]


def test_session_consistency_requires_two_measured_tempos():
    rows = [
        make_metrics(1, tempo_ratio=3.0),
        make_metrics(2, tempo_ratio=float("nan")),
    ]
    assert session_notes(rows, session_stats(rows), Config()) == []


# --------------------------------------------------------------- issue cards


def test_issue_card_fields_and_nan_becomes_none():
    ms = [make_metrics(1, head_dip_sw=0.4), make_metrics(2, head_dip_sw=float("nan"))]
    stats = session_stats(ms)
    cards = issue_cards(ms, stats, Config())
    assert [c.flag for c in cards] == ["head-dip"]
    c = cards[0]
    assert c.metric == "head_dip_sw"
    assert c.display_name == "Head dip"
    assert c.unit == "SW"
    assert c.per_swing == (0.4, None)
    assert c.session_value == pytest.approx(0.4)
    assert c.session_label == "session mean"
    assert c.session_text == "0.40 SW"
    assert c.benchmark_value == pytest.approx(0.25)
    assert c.benchmark_text == "flagged above 0.25 SW"
    assert c.worse_direction == "higher"
    assert c.severity == "major"  # session mean 0.4 breaches 0.25
    assert c.why and c.fix


def test_issue_cards_major_sorted_first_stable():
    # sway fires on one of two swings, mean under threshold -> warn;
    # tempo fires on both swings and the mean breaches -> major.
    ms = [
        make_metrics(1, head_sway_backswing_sw=0.5, tempo_ratio=2.0),
        make_metrics(2, head_sway_backswing_sw=0.1, tempo_ratio=2.0),
    ]
    stats = session_stats(ms)
    cards = issue_cards(ms, stats, Config())
    assert [c.flag for c in cards] == ["tempo", "sway"]
    assert [c.severity for c in cards] == ["major", "warn"]


def test_issue_cards_lower_direction_arm_card():
    ms = [make_metrics(1, lead_arm_angle_deg=140.0), make_metrics(2)]
    stats = session_stats(ms)
    (c,) = issue_cards(ms, stats, Config())
    assert c.flag == "arm-extension"
    assert c.unit == "\N{DEGREE SIGN}"
    assert c.worse_direction == "lower"
    assert c.session_label == "worst swing"
    assert c.session_text == "140\N{DEGREE SIGN}"
    assert c.benchmark_text == (
        "180\N{DEGREE SIGN} is straight · flagged below 150\N{DEGREE SIGN}"
    )
    assert c.severity == "warn"  # mean 155 above 150, one of two swings flagged


def test_delta_only_shoulder_card_uses_delta_evidence_before_sorting():
    ms = [
        make_metrics(
            1,
            head_sway_backswing_sw=0.50,
            shoulder_tilt_impact_deg=10.0,
            shoulder_tilt_delta_deg=-5.0,
        ),
        make_metrics(
            2,
            head_sway_backswing_sw=0.10,
            shoulder_tilt_impact_deg=10.0,
            shoulder_tilt_delta_deg=-3.0,
        ),
    ]
    cards = issue_cards(ms, session_stats(ms), Config())
    assert [card.flag for card in cards[:2]] == ["shoulder-tilt", "sway"]
    shoulder = cards[0]
    assert shoulder.metric == "shoulder_tilt_delta_deg"
    assert shoulder.display_name == "Shoulder-tilt change"
    assert shoulder.session_text == "-4\N{DEGREE SIGN}"
    assert shoulder.benchmark_text.startswith("flagged below 0\N{DEGREE SIGN}")
    assert shoulder.severity == "major"
    assert "decreased from address" in shoulder.why
    assert "flat" not in shoulder.why


def test_consistency_card_uses_std_and_no_benchmark_line():
    ms = [make_metrics(1, tempo_ratio=2.6), make_metrics(2, tempo_ratio=3.4)]
    stats = session_stats(ms)  # std 0.4 >= tempo_std_praise 0.3
    cards = issue_cards(ms, stats, Config())
    assert [c.flag for c in cards] == ["consistency"]
    c = cards[0]
    assert c.metric == "tempo_ratio"
    assert c.benchmark_value is None
    assert c.session_label == "std dev across swings"
    assert c.session_text == "\N{PLUS-MINUS SIGN}0.40"
    assert c.severity == "warn"  # 0.4 < 2 * 0.3


def test_consistency_card_major_at_double_praise_threshold():
    ms = [make_metrics(1, tempo_ratio=2.8), make_metrics(2, tempo_ratio=4.0)]
    stats = session_stats(ms)  # std 0.6 == 2 * 0.3
    (c,) = issue_cards(ms, stats, Config())
    assert c.flag == "consistency" and c.severity == "major"


def test_issue_cards_cover_all_eight_flags_with_library_drills():
    ms = all_flags_metrics()
    stats = session_stats(ms)
    cards = issue_cards(ms, stats, Config())
    assert {c.flag for c in cards} == {
        "sway",
        "tempo",
        "hip-slide",
        "head-dip",
        "arm-extension",
        "shoulder-tilt",
        "balance",
        "consistency",
    }
    all_ids = {d.id for ds in drills.DRILLS.values() for d in ds}
    for c in cards:
        assert len(c.drill_ids) == len(c.drill_names)
        for drill_id in c.drill_ids:
            assert drill_id in all_ids
    # the long-established families always resolve to drills
    by_flag = {c.flag: c for c in cards}
    assert by_flag["sway"].drill_ids and by_flag["tempo"].drill_ids


# ------------------------------------------------- session stats & config


def test_session_stats_covers_new_numeric_fields():
    for key in (
        "head_dip_sw",
        "lead_arm_angle_deg",
        "shoulder_tilt_impact_deg",
        "shoulder_tilt_delta_deg",
        "finish_balance_sw",
    ):
        assert key in NUMERIC_FIELDS
    assert "shoulder_tilt_address_deg" not in NUMERIC_FIELDS  # context only
    stats = session_stats([make_metrics(1), make_metrics(2)])
    assert stats["head_dip_sw"]["mean"] == pytest.approx(0.1)
    assert stats["finish_balance_sw"]["std"] == 0.0


def test_session_stats_still_skips_all_nan_fields():
    legacy = SwingMetrics(
        swing=1,
        strike_s=3.0,
        backswing_s=0.9,
        downswing_s=0.3,
        tempo_ratio=3.0,
        head_sway_backswing_sw=0.1,
        head_sway_downswing_sw=-0.1,
        hip_slide_backswing_sw=0.1,
        hip_slide_downswing_sw=-0.1,
        target_direction=1,
    )  # new fields default to NaN
    stats = session_stats([legacy])
    assert "head_dip_sw" not in stats
    assert "lead_arm_angle_deg" not in stats
    assert "tempo_ratio" in stats


def test_new_config_keys_in_defaults():
    coach = DEFAULTS["coaching"]
    assert coach["head_dip_warn_sw"] == 0.25
    assert coach["lead_arm_warn_deg"] == 150
    assert coach["shoulder_tilt_impact_min_deg"] == 5.0
    assert coach["finish_balance_warn_sw"] == 0.15
    assert DEFAULTS["analysis"]["finish_hold_frames"] == 6
    assert DEFAULTS["slowmo"]["annotated"] is True
    assert DEFAULTS["slowmo"]["trail_fade_s"] == 0.9


def test_new_config_keys_load_from_partial_yaml(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "coaching:\n  head_dip_warn_sw: 0.4\nslowmo:\n  annotated: false\n"
    )
    cfg = Config.load(path)
    assert cfg.coaching["head_dip_warn_sw"] == 0.4
    assert cfg.coaching["lead_arm_warn_deg"] == 150  # untouched default
    assert cfg.slowmo["annotated"] is False
    assert cfg.slowmo["trail_fade_s"] == 0.9
    assert cfg.analysis["finish_hold_frames"] == 6


def test_shipped_config_yaml_mirrors_defaults_for_new_keys():
    shipped = Path(__file__).resolve().parents[1] / "config.yaml"
    cfg = Config.load(shipped)
    for section, key in [
        ("coaching", "head_dip_warn_sw"),
        ("coaching", "lead_arm_warn_deg"),
        ("coaching", "shoulder_tilt_impact_min_deg"),
        ("coaching", "finish_balance_warn_sw"),
        ("analysis", "finish_hold_frames"),
        ("slowmo", "annotated"),
        ("slowmo", "trail_fade_s"),
    ]:
        assert cfg[section][key] == DEFAULTS[section][key]


def test_retuned_threshold_flows_through_flags():
    cfg = Config()
    cfg.coaching["head_dip_warn_sw"] = 0.45
    ms = [make_metrics(1, head_dip_sw=0.4)]
    assert session_flags(ms, session_stats(ms), cfg) == []
    cfg.coaching["head_dip_warn_sw"] = 0.25
    assert session_flags(ms, session_stats(ms), cfg) == [FLAG_HEAD_DIP]
