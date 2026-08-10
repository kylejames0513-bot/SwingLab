"""The Swing Pattern describes a swing without ever diagnosing it.

These tests pin the three rules the module exists to keep: it reuses the
coaching thresholds rather than inventing a second opinion, an unreadable
axis is absent rather than guessed, and no line it writes claims a cause
or an outcome the video cannot measure.
"""

from __future__ import annotations

from swinglab.config import Config
from swinglab.metrics import SwingMetrics
from swinglab.swing_pattern import (
    AXIS_FINISH,
    AXIS_HEAD,
    AXIS_LOWER_BODY,
    AXIS_SEQUENCE,
    AXIS_TEMPO,
    build_swing_pattern,
)


NAN = float("nan")


def metrics(**overrides) -> SwingMetrics:
    """One swing with everything readable and quiet, before overrides."""

    base = dict(
        swing=1,
        strike_s=1.0,
        backswing_s=0.9,
        downswing_s=0.3,
        tempo_ratio=3.0,
        head_sway_backswing_sw=0.10,
        head_sway_downswing_sw=0.05,
        hip_slide_backswing_sw=0.10,
        hip_slide_downswing_sw=0.05,
        target_direction=1,
        head_dip_sw=0.05,
        lead_arm_angle_deg=170.0,
        shoulder_tilt_impact_deg=12.0,
        shoulder_tilt_delta_deg=4.0,
        finish_balance_sw=0.05,
        sequence_pelvis_to_arm_ms=40.0,
    )
    base.update(overrides)
    return SwingMetrics(**base)


def axes_by_key(pattern) -> dict:
    return {axis.key: axis for axis in pattern.axes}


def test_a_centered_body_led_swing_is_named_from_its_measurements():
    cfg = Config()
    pattern = build_swing_pattern([metrics(), metrics(swing=2)], cfg)

    assert pattern is not None
    assert pattern.name == "Centered, body-led swing"
    assert pattern.measured_swings == 2
    axes = axes_by_key(pattern)
    assert axes[AXIS_SEQUENCE].position == "body_led"
    assert axes[AXIS_LOWER_BODY].position == "centered"
    assert axes[AXIS_HEAD].position == "steady"
    assert axes[AXIS_TEMPO].position == "measured"
    assert axes[AXIS_FINISH].position == "held"


def test_an_arm_led_lateral_swing_reads_the_other_way():
    cfg = Config()
    swing = metrics(
        sequence_pelvis_to_arm_ms=-45.0,
        hip_slide_backswing_sw=0.44,
        head_sway_backswing_sw=0.40,
    )
    pattern = build_swing_pattern([swing], cfg)

    assert pattern is not None
    assert pattern.name == "Lateral, arm-led swing"
    axes = axes_by_key(pattern)
    assert axes[AXIS_SEQUENCE].position == "arm_led"
    assert axes[AXIS_LOWER_BODY].position == "lateral"
    assert axes[AXIS_HEAD].position == "mobile"


def test_the_sequence_dead_band_refuses_to_pick_a_leader():
    """A gap smaller than the separation constant is not a swing style.

    swinglab.sequence already refuses to resolve peaks closer than 1.5
    frame periods; this band keeps the ones it *does* resolve from being
    over-read as character when they are a few milliseconds apart.
    """
    cfg = Config()
    pattern = build_swing_pattern([metrics(sequence_pelvis_to_arm_ms=3.0)], cfg)

    axes = axes_by_key(pattern)
    assert axes[AXIS_SEQUENCE].position == "simultaneous"
    assert "neither is leading" in axes[AXIS_SEQUENCE].detail


def test_the_pattern_never_calls_a_swing_quiet_on_a_flagged_number():
    """The upper band edge IS the coaching threshold.

    A measurement at or past its warn line must land in the loud band, or
    the pattern would contradict the warn note printed on the same report.
    """
    cfg = Config()
    coach = cfg.coaching
    at_the_line = metrics(
        hip_slide_backswing_sw=coach["sway_warn_sw"],
        head_sway_backswing_sw=coach["sway_warn_sw"],
        head_dip_sw=coach["head_dip_warn_sw"],
        finish_balance_sw=coach["finish_balance_warn_sw"],
        tempo_ratio=coach["tempo_warn_below"] - 0.1,
    )
    axes = axes_by_key(build_swing_pattern([at_the_line], cfg))

    assert axes[AXIS_LOWER_BODY].position == "lateral"
    assert axes[AXIS_HEAD].position == "mobile"
    assert axes[AXIS_FINISH].position == "unsettled"
    assert axes[AXIS_TEMPO].position == "quick"


def test_head_axis_takes_the_worse_of_sway_and_dip():
    """A big dip is never hidden behind a small sway."""
    cfg = Config()
    pattern = build_swing_pattern(
        [metrics(head_sway_backswing_sw=0.02, head_dip_sw=0.40)], cfg
    )

    axes = axes_by_key(pattern)
    assert axes[AXIS_HEAD].position == "mobile"
    assert "0.40 SW down" in axes[AXIS_HEAD].detail


def test_unreadable_axes_are_named_not_guessed():
    """NaN is the honest value everywhere else; it stays honest here."""
    cfg = Config()
    pattern = build_swing_pattern(
        [
            metrics(
                sequence_pelvis_to_arm_ms=NAN,
                finish_balance_sw=NAN,
            )
        ],
        cfg,
    )

    axes = axes_by_key(pattern)
    assert AXIS_SEQUENCE not in axes
    assert AXIS_FINISH not in axes
    assert "Downswing sequence" in pattern.unreadable
    assert "Finish" in pattern.unreadable
    # ...and the name falls back to what WAS measured.
    assert pattern.name == "Centered swing"


def test_a_down_the_line_session_gets_tempo_and_an_honest_note():
    """Face-on fields are NaN on a DTL clip; the pattern must not invent
    a movement portrait from the one number that survives."""
    cfg = Config()
    dtl = metrics(
        head_sway_backswing_sw=NAN,
        head_sway_downswing_sw=NAN,
        hip_slide_backswing_sw=NAN,
        hip_slide_downswing_sw=NAN,
        head_dip_sw=NAN,
        finish_balance_sw=NAN,
        sequence_pelvis_to_arm_ms=NAN,
    )
    pattern = build_swing_pattern([dtl], cfg, angle="dtl")

    axes = axes_by_key(pattern)
    assert set(axes) == {AXIS_TEMPO}
    assert "face-on" in pattern.note
    assert len(pattern.unreadable) == 4


def test_nothing_readable_renders_no_section_at_all():
    cfg = Config()
    blank = metrics(
        tempo_ratio=NAN,
        head_sway_backswing_sw=NAN,
        hip_slide_backswing_sw=NAN,
        head_dip_sw=NAN,
        finish_balance_sw=NAN,
        sequence_pelvis_to_arm_ms=NAN,
    )
    assert build_swing_pattern([blank], cfg) is None
    assert build_swing_pattern([], cfg) is None


def test_the_pattern_stays_inside_the_measurement_boundary():
    """No line may claim club, ball, 3D, causation, or an outcome.

    docs/product-interface.md is the contract: phone-video timing and 2D
    body movement from the selected camera, nothing else.
    """
    cfg = Config()
    variants = [
        [metrics()],
        [metrics(sequence_pelvis_to_arm_ms=-45.0, hip_slide_backswing_sw=0.44)],
        [metrics(tempo_ratio=4.0, finish_balance_sw=0.30)],
        [metrics(sequence_pelvis_to_arm_ms=3.0, head_dip_sw=0.30)],
    ]
    forbidden = (
        "club path", "clubface", "face angle", "attack angle", "dynamic loft",
        "launch", "spin", "carry", "strike location", "ball flight",
        "clubhead speed", "ball speed", "mph", "3d", "three-dimensional",
        "because", "causes", "caused", "will improve", "guarantee", "fix your",
    )
    for all_metrics in variants:
        pattern = build_swing_pattern(all_metrics, cfg)
        assert pattern is not None
        text = " ".join(
            [pattern.name, pattern.summary, pattern.note]
            + [f"{a.label} {a.display} {a.detail}" for a in pattern.axes]
        ).lower()
        for phrase in forbidden:
            assert phrase not in text, f"{phrase!r} in pattern copy: {text}"


def test_every_axis_detail_carries_its_number_and_its_line():
    """The pattern must be auditable against the measurements table."""
    cfg = Config()
    pattern = build_swing_pattern([metrics()], cfg)
    for axis in pattern.axes:
        assert any(ch.isdigit() for ch in axis.detail), axis.key
        assert axis.unit
        assert axis.label and axis.display


def test_as_dict_round_trips_for_metrics_json_consumers():
    cfg = Config()
    payload = build_swing_pattern([metrics()], cfg).as_dict()
    assert payload["name"]
    assert payload["measured_swings"] == 1
    assert {axis["key"] for axis in payload["axes"]} == {
        AXIS_SEQUENCE, AXIS_LOWER_BODY, AXIS_HEAD, AXIS_TEMPO, AXIS_FINISH
    }
