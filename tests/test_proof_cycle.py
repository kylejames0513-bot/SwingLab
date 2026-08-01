"""Proof Cycle v1: no verdict unless the matched measurement earns it."""

from __future__ import annotations

import math

import pytest

from swinglab.coaching import FLAG_CONSISTENCY, FLAG_SHOULDER_TILT, IssueCard, issue_cards
from swinglab.config import Config
from swinglab.metrics import ANGLE_DTL, ANGLE_FACE_ON, SwingMetrics, session_stats
from swinglab.proof_cycle import (
    ProofMeasurement,
    ProofRefilm,
    ProofSession,
    ProofTarget,
    SessionContext,
    compare_refilm,
)


def swing(**overrides) -> SwingMetrics:
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


def session(
    session_id: str,
    *,
    metrics: dict[str, object],
    user_id: str = "golfer-1",
    club: str | None = "Driver",
    hand: str | None = "Right",
    angle: str | None = ANGLE_FACE_ON,
    completed: bool = True,
    coaching_eligible: bool = True,
    warning: str | None = None,
) -> ProofSession:
    return ProofSession(
        session_id=session_id,
        user_id=user_id,
        club=club,
        hand=hand,
        angle=angle,
        metrics=metrics,
        completed=completed,
        coaching_eligible=coaching_eligible,
        warning=warning,
    )


def card(
    *,
    metric: str = "head_sway_backswing_sw",
    values: tuple[float | None, ...] = (0.50, 0.50, 0.50),
    worse_direction: str = "higher",
    session_label: str = "session mean",
) -> IssueCard:
    return IssueCard(
        flag="head-sway",
        metric=metric,
        display_name="Head sway (backswing)",
        unit="SW",
        per_swing=values,
        session_value=0.50,
        session_label=session_label,
        session_text="0.50 SW",
        benchmark_value=0.35,
        benchmark_text="flagged above 0.35 SW",
        worse_direction=worse_direction,
        severity="major",
        why="Measured fact one. Measured fact two.",
        fix="One measured fix.",
        drill_ids=("wall-turn",),
        drill_names=("Wall turn",),
    )


def target(
    *,
    metric: str = "head_sway_backswing_sw",
    values: tuple[float | None, ...] = (0.50, 0.50, 0.50),
    worse_direction: str = "higher",
    session_label: str = "session mean",
    angle: str = ANGLE_FACE_ON,
    **baseline_overrides,
) -> ProofTarget:
    baseline = session(
        "baseline",
        metrics={metric: values},
        angle=angle,
        **baseline_overrides,
    )
    return ProofTarget.from_issue_card(
        baseline,
        card(
            metric=metric,
            values=values,
            worse_direction=worse_direction,
            session_label=session_label,
        ),
    )


def accepted_refilm(
    proof_target: ProofTarget,
    values: tuple[float, ...],
    *,
    session_id: str = "accepted-refilm",
    **overrides,
) -> ProofRefilm:
    return ProofRefilm.from_session(
        proof_target,
        session(
            session_id,
            metrics={proof_target.metric: values},
            angle=proof_target.baseline_context.angle,
            **overrides,
        ),
    )


def test_target_snapshots_selected_shoulder_tilt_metric_not_brief_prose():
    rows = [
        swing(swing=index, shoulder_tilt_impact_deg=20.0, shoulder_tilt_delta_deg=-5.0)
        for index in range(1, 4)
    ]
    selected = next(
        item
        for item in issue_cards(rows, session_stats(rows), Config())
        if item.flag == FLAG_SHOULDER_TILT
    )
    baseline = ProofSession.from_swing_metrics(
        session_id="baseline",
        user_id="golfer-1",
        club="Driver",
        hand="Right",
        angle=ANGLE_FACE_ON,
        swings=rows,
    )

    proof_target = ProofTarget.from_issue_card(baseline, selected)

    assert selected.metric == "shoulder_tilt_delta_deg"
    assert proof_target.metric == selected.metric
    assert proof_target.aggregation == "mean"
    assert proof_target.baseline.value == -5.0
    assert proof_target.drill_ids == selected.drill_ids


def test_target_preserves_consistency_as_a_standard_deviation_target():
    rows = [
        swing(swing=1, tempo_ratio=2.0),
        swing(swing=2, tempo_ratio=4.0),
        swing(swing=3, tempo_ratio=3.0),
    ]
    selected = next(
        item
        for item in issue_cards(rows, session_stats(rows), Config())
        if item.flag == FLAG_CONSISTENCY
    )
    baseline = ProofSession.from_swing_metrics(
        session_id="baseline",
        user_id="golfer-1",
        club="Driver",
        hand="Right",
        angle=ANGLE_FACE_ON,
        swings=rows,
    )

    proof_target = ProofTarget.from_issue_card(baseline, selected)

    assert proof_target.metric == "tempo_ratio"
    assert proof_target.aggregation == "std"
    assert proof_target.baseline.value == pytest.approx(math.sqrt(2 / 3))
    assert proof_target.baseline.value == pytest.approx(selected.session_value, abs=0.001)


def test_one_matched_refilm_is_an_early_signal_not_a_completed_claim():
    proof_target = target()
    refilm = session(
        "refilm-1", metrics={proof_target.metric: (0.40, 0.40, 0.40)}
    )

    result = compare_refilm(proof_target, refilm, noise_floor=0.05)

    assert result.verdict == "early_signal"
    assert result.directional_change == pytest.approx(0.10)
    assert result.minimum_detectable_effect == pytest.approx(0.05)
    assert result.accepted_refilm_count == 1


def test_two_confirming_refilms_improve_then_subsequent_refilm_holds():
    proof_target = target()
    first = accepted_refilm(proof_target, (0.42, 0.42, 0.42))
    second = session(
        "refilm-2", metrics={proof_target.metric: (0.40, 0.40, 0.40)}
    )
    improved = compare_refilm(
        proof_target,
        second,
        prior_refilms=(first,),
        noise_floor=0.05,
    )
    third = session(
        "refilm-3", metrics={proof_target.metric: (0.38, 0.38, 0.38)}
    )
    holding = compare_refilm(
        proof_target,
        third,
        prior_refilms=(first, improved.current),
        noise_floor=0.05,
    )

    assert improved.verdict == "improved"
    assert improved.accepted_refilm_count == 2
    assert holding.verdict == "holding"
    assert holding.accepted_refilm_count == 3


def test_configured_confirmation_count_stays_early_until_that_count_is_met():
    proof_target = target()
    first = accepted_refilm(proof_target, (0.42, 0.42, 0.42), session_id="one")
    second = session(
        "two", metrics={proof_target.metric: (0.40, 0.40, 0.40)}
    )
    third = session(
        "three", metrics={proof_target.metric: (0.38, 0.38, 0.38)}
    )

    two_refilms = compare_refilm(
        proof_target,
        second,
        prior_refilms=(first,),
        noise_floor=0.05,
        minimum_refilms_for_improved=3,
    )
    three_refilms = compare_refilm(
        proof_target,
        third,
        prior_refilms=(first, two_refilms.current),
        noise_floor=0.05,
        minimum_refilms_for_improved=3,
    )

    assert two_refilms.verdict == "early_signal"
    assert three_refilms.verdict == "improved"


def test_confirming_refilms_must_agree_with_each_other_not_just_baseline():
    proof_target = target()
    unusually_large_effect = accepted_refilm(
        proof_target, (0.00, 0.00, 0.00), session_id="large-effect"
    )
    modest_effect = session(
        "modest-effect", metrics={proof_target.metric: (0.44, 0.44, 0.44)}
    )

    result = compare_refilm(
        proof_target,
        modest_effect,
        prior_refilms=(unusually_large_effect,),
        noise_floor=0.05,
    )

    assert result.verdict == "inconclusive"
    assert "refilm_effect_is_not_consistent" in result.confidence.notes
    assert result.maximum_refilm_spread == pytest.approx(0.05)


def test_confirmation_stability_is_order_independent_without_timestamps():
    proof_target = target()
    first = accepted_refilm(
        proof_target, (0.44, 0.44, 0.44), session_id="first"
    )
    second = accepted_refilm(
        proof_target, (0.39, 0.39, 0.39), session_id="second"
    )
    third = session(
        "third", metrics={proof_target.metric: (0.34, 0.34, 0.34)}
    )

    result = compare_refilm(
        proof_target,
        third,
        prior_refilms=(second, first),
        noise_floor=0.05,
    )

    assert result.verdict == "inconclusive"
    assert "refilm_effect_is_not_consistent" in result.confidence.notes


@pytest.mark.parametrize(
    ("overrides", "expected_failure"),
    [
        ({"user_id": "golfer-2"}, "owner_mismatch"),
        ({"club": "7 iron"}, "club_mismatch"),
        ({"club": ""}, "session_club_missing"),
        ({"hand": "Left"}, "hand_mismatch"),
        ({"angle": ANGLE_DTL}, "angle_mismatch"),
    ],
)
def test_context_mismatch_never_claims_a_proof(
    overrides: dict[str, object], expected_failure: str
):
    proof_target = target()
    refilm = session(
        "refilm-1", metrics={proof_target.metric: (0.30, 0.30, 0.30)}, **overrides
    )

    result = compare_refilm(proof_target, refilm, noise_floor=0.05)

    assert result.verdict == "not_comparable"
    assert expected_failure in result.confidence.hard_failures
    assert result.accepted_refilm_count == 0


def test_baseline_cannot_be_reused_as_its_own_refilm():
    proof_target = target()
    reprocessed_baseline = session(
        "baseline", metrics={proof_target.metric: (0.30, 0.30, 0.30)}
    )

    result = compare_refilm(proof_target, reprocessed_baseline, noise_floor=0.05)

    assert result.verdict == "not_comparable"
    assert "session_is_baseline" in result.confidence.hard_failures


def test_prior_refilms_keep_context_and_quality_provenance():
    proof_target = target()
    foreign = accepted_refilm(
        proof_target,
        (0.40, 0.40, 0.40),
        session_id="foreign-refilm",
        user_id="golfer-2",
    )
    current = session(
        "current-refilm", metrics={proof_target.metric: (0.35, 0.35, 0.35)}
    )

    result = compare_refilm(
        proof_target,
        current,
        prior_refilms=(foreign,),
        noise_floor=0.05,
    )

    assert result.verdict == "inconclusive"
    assert "prior_refilm_owner_mismatch" in result.confidence.notes
    assert result.accepted_refilm_count == 1


def test_duplicate_or_quality_marked_history_never_counts_as_confirmation():
    proof_target = target()
    duplicate = accepted_refilm(
        proof_target,
        (0.40, 0.40, 0.40),
        session_id="duplicate-refilm",
    )
    warned = accepted_refilm(
        proof_target,
        (0.40, 0.40, 0.40),
        session_id="warned-refilm",
        warning="Tracking was unstable — numbers may be off.",
    )
    same_upload = session(
        "duplicate-refilm", metrics={proof_target.metric: (0.35, 0.35, 0.35)}
    )
    valid_current = session(
        "valid-current", metrics={proof_target.metric: (0.35, 0.35, 0.35)}
    )

    duplicate_result = compare_refilm(
        proof_target,
        same_upload,
        prior_refilms=(duplicate,),
        noise_floor=0.05,
    )
    warned_result = compare_refilm(
        proof_target,
        valid_current,
        prior_refilms=(warned,),
        noise_floor=0.05,
    )

    assert duplicate_result.verdict == "inconclusive"
    assert "duplicate_refilm_session_id" in duplicate_result.confidence.notes
    assert warned_result.verdict == "inconclusive"
    assert "prior_refilm_requires_refilm" in warned_result.confidence.notes


def test_unproven_or_nonfinite_history_cannot_manufacture_improvement():
    proof_target = target()
    bare_measurement = ProofMeasurement(
        metric=proof_target.metric,
        aggregation=proof_target.aggregation,
        value=0.40,
        mean=0.40,
        std=0.0,
        readable_swings=3,
    )
    nonfinite = ProofRefilm(
        context=SessionContext(
            session_id="nonfinite-refilm",
            user_id="golfer-1",
            club="Driver",
            hand="Right",
            angle=ANGLE_FACE_ON,
        ),
        measurement=ProofMeasurement(
            metric=proof_target.metric,
            aggregation=proof_target.aggregation,
            value=math.nan,
            mean=math.nan,
            std=math.nan,
            readable_swings=3,
        ),
        completed=True,
        coaching_eligible=True,
        warning=None,
    )
    current = session(
        "current-refilm", metrics={proof_target.metric: (0.35, 0.35, 0.35)}
    )

    bare_result = compare_refilm(
        proof_target,
        current,
        prior_refilms=(bare_measurement,),  # type: ignore[arg-type]
        noise_floor=0.05,
    )
    nonfinite_result = compare_refilm(
        proof_target,
        current,
        prior_refilms=(nonfinite,),
        noise_floor=0.05,
    )

    assert bare_result.verdict == "inconclusive"
    assert "prior_refilm_provenance_missing" in bare_result.confidence.notes
    assert nonfinite_result.verdict == "inconclusive"
    assert (
        "prior_refilm_target_metric_unreadable" in nonfinite_result.confidence.notes
    )


def test_issue_card_evidence_must_belong_to_the_supplied_baseline_session():
    baseline = session(
        "baseline", metrics={"head_sway_backswing_sw": (0.20, 0.20, 0.20)}
    )
    other_session_card = card(values=(0.50, 0.50, 0.50))

    with pytest.raises(ValueError, match="does not match"):
        ProofTarget.from_issue_card(baseline, other_session_card)


def test_dtl_permits_a_tempo_target_but_not_a_face_on_mechanics_target():
    tempo_target = target(
        metric="tempo_ratio",
        values=(2.0, 2.0, 2.0),
        worse_direction="lower",
        angle=ANGLE_DTL,
    )
    tempo_refilm = session(
        "tempo-refilm",
        metrics={"tempo_ratio": (2.4, 2.4, 2.4)},
        angle=ANGLE_DTL,
    )
    mechanical_target = target(angle=ANGLE_DTL)
    mechanical_refilm = session(
        "mechanical-refilm",
        metrics={mechanical_target.metric: (0.30, 0.30, 0.30)},
        angle=ANGLE_DTL,
    )

    tempo_result = compare_refilm(tempo_target, tempo_refilm, noise_floor=0.10)
    mechanical_result = compare_refilm(
        mechanical_target, mechanical_refilm, noise_floor=0.05
    )

    assert tempo_result.verdict == "early_signal"
    assert mechanical_result.verdict == "no_baseline"
    assert (
        "baseline_target_metric_not_supported_by_angle"
        in mechanical_result.confidence.hard_failures
    )


def test_tracking_warning_and_unreadable_rows_require_a_new_refilm():
    proof_target = target()
    warning = "Tracking was unstable for this swing — numbers may be off."
    warned = session(
        "warned",
        metrics={proof_target.metric: (0.30, 0.30, 0.30)},
        warning=warning,
    )
    incomplete = session(
        "incomplete",
        metrics={proof_target.metric: (float("nan"), float("inf"), 0.30)},
    )

    warning_result = compare_refilm(proof_target, warned, noise_floor=0.05)
    incomplete_result = compare_refilm(proof_target, incomplete, noise_floor=0.05)

    assert warning_result.verdict == "not_comparable"
    assert "session_requires_refilm" in warning_result.confidence.hard_failures
    assert incomplete_result.verdict == "not_comparable"
    assert "insufficient_readable_swings" in incomplete_result.confidence.hard_failures


def test_malformed_warning_metadata_is_rejected_instead_of_treated_as_clean():
    with pytest.raises(TypeError, match="warning"):
        session(
            "malformed-warning",
            metrics={"head_sway_backswing_sw": (0.30, 0.30, 0.30)},
            warning={"tracking": "unstable"},  # type: ignore[arg-type]
        )


def test_unusable_baseline_is_not_rebranded_as_an_inconclusive_improvement():
    proof_target = target(values=(0.50, 0.50), completed=False)
    refilm = session(
        "refilm-1", metrics={proof_target.metric: (0.30, 0.30, 0.30)}
    )

    result = compare_refilm(proof_target, refilm, noise_floor=0.05)

    assert result.verdict == "no_baseline"
    assert "baseline_not_complete" in result.confidence.hard_failures
    assert "baseline_insufficient_readable_swings" in result.confidence.hard_failures


def test_noise_and_conflicting_history_are_honestly_inconclusive():
    proof_target = target(values=(0.40, 0.50, 0.60))
    small_change = session(
        "small-change", metrics={proof_target.metric: (0.43, 0.43, 0.43)}
    )
    prior_regression = accepted_refilm(
        proof_target, (0.70, 0.70, 0.70), session_id="prior-regression"
    )
    later_improvement = session(
        "later-improvement", metrics={proof_target.metric: (0.30, 0.30, 0.30)}
    )

    small_result = compare_refilm(proof_target, small_change, noise_floor=0.10)
    conflicting_result = compare_refilm(
        proof_target,
        later_improvement,
        prior_refilms=(prior_regression,),
        noise_floor=0.10,
    )

    assert small_result.verdict == "inconclusive"
    assert "change_is_below_minimum_detectable_effect" in small_result.confidence.notes
    assert conflicting_result.verdict == "inconclusive"
    assert "refilm_direction_is_contradictory" in conflicting_result.confidence.notes


def test_latest_meaningful_regression_needs_attention_without_blame():
    proof_target = target()
    refilm = session(
        "refilm-1", metrics={proof_target.metric: (0.70, 0.70, 0.70)}
    )

    result = compare_refilm(proof_target, refilm, noise_floor=0.05)

    assert result.verdict == "needs_attention"
    assert "latest_refilm_moved_away_from_target" in result.confidence.notes


def test_zero_mde_still_requires_real_movement_before_an_early_signal():
    proof_target = target()
    unchanged = session(
        "unchanged", metrics={proof_target.metric: (0.50, 0.50, 0.50)}
    )

    result = compare_refilm(proof_target, unchanged, noise_floor=0.0)

    assert result.verdict == "inconclusive"
    assert "change_is_below_minimum_detectable_effect" in result.confidence.notes


def test_extreme_or_malformed_values_cannot_crash_into_a_false_verdict():
    proof_target = target()
    refilm = session(
        "legacy-refilm",
        metrics={proof_target.metric: (10**400, "0.30", math.nan)},
    )

    result = compare_refilm(proof_target, refilm, noise_floor=0.05)

    assert result.verdict == "not_comparable"
    assert "insufficient_readable_swings" in result.confidence.hard_failures


def test_policy_inputs_must_be_explicit_and_valid():
    proof_target = target()
    refilm = session(
        "refilm-1", metrics={proof_target.metric: (0.30, 0.30, 0.30)}
    )

    with pytest.raises(ValueError, match="noise_floor"):
        compare_refilm(proof_target, refilm, noise_floor=-0.1)
    with pytest.raises(ValueError, match="minimum_refilms_for_improved"):
        compare_refilm(
            proof_target,
            refilm,
            noise_floor=0.05,
            minimum_refilms_for_improved=1,
        )
    with pytest.raises(ValueError, match="minimum_refilms_for_improved"):
        compare_refilm(
            proof_target,
            refilm,
            noise_floor=0.05,
            minimum_refilms_for_improved=2.5,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="minimum_readable_swings"):
        compare_refilm(
            proof_target,
            refilm,
            noise_floor=0.05,
            minimum_readable_swings=2,
        )
    with pytest.raises(ValueError, match="maximum_refilm_spread"):
        compare_refilm(
            proof_target,
            refilm,
            noise_floor=0.05,
            maximum_refilm_spread=-0.01,
        )
