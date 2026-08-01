"""PR 3: self-reported practice context never changes Proof Cycle truth."""

from __future__ import annotations

from dataclasses import replace

import pytest

from swinglab.proof_cycle import ProofMeasurement, ProofRefilm, ProofTarget, SessionContext
from swinglab.proof_cycle_artifact import (
    PersistedComparison,
    ProofCycleArtifact,
    ProofCyclePolicy,
)
from swinglab.proof_cycle_practice import (
    ProofCyclePracticeEvidence,
    ProofCycleTransferCheck,
    normalize_practice_minutes,
    normalize_practice_outcome,
    practice_assignment_from_target,
    practice_transfer_view,
)
from swinglab.web.users import UserStore


def proof_target() -> ProofTarget:
    context = SessionContext(
        session_id="baseline",
        user_id="golfer-1",
        club="driver",
        hand="right",
        angle="face-on",
    )
    measurement = ProofMeasurement(
        metric="head_sway_backswing_sw",
        aggregation="mean",
        value=0.50,
        mean=0.50,
        std=0.0,
        readable_swings=3,
    )
    return ProofTarget(
        source_flag="head-sway",
        metric=measurement.metric,
        display_name="Head sway (backswing)",
        unit="SW",
        worse_direction="higher",
        aggregation="mean",
        benchmark_value=0.35,
        benchmark_text="flagged above 0.35 SW",
        drill_ids=("wall-turn",),
        drill_names=("Wall turn",),
        baseline_context=context,
        baseline=measurement,
        baseline_completed=True,
        baseline_coaching_eligible=True,
        baseline_warning=None,
    )


def comparison_artifact(verdict: str = "early_signal") -> ProofCycleArtifact:
    target = proof_target()
    refilm = ProofRefilm(
        context=SessionContext(
            session_id="refilm",
            user_id="golfer-1",
            club="driver",
            hand="right",
            angle="face-on",
        ),
        measurement=ProofMeasurement(
            metric=target.metric,
            aggregation="mean",
            value=0.40,
            mean=0.40,
            std=0.0,
            readable_swings=3,
        ),
        completed=True,
        coaching_eligible=True,
        warning=None,
    )
    return ProofCycleArtifact(
        source_session_id="refilm",
        source_metrics_sha256="a" * 64,
        stage="comparison",
        target=target,
        refilm=refilm,
        comparison=PersistedComparison(
            verdict=verdict,  # type: ignore[arg-type]
            hard_failures=(),
            notes=(),
            minimum_detectable_effect=0.03,
            maximum_refilm_spread=0.03,
            directional_change=0.10,
            accepted_refilm_count=1,
        ),
        policy=ProofCyclePolicy(
            noise_floor=0.03,
            minimum_readable_swings=3,
            minimum_refilms_for_improved=2,
            maximum_refilm_spread=None,
        ),
    )


def matching_evidence(target: ProofTarget, *, completed_at: float = 2.0):
    assignment = practice_assignment_from_target(target)
    assert assignment is not None
    return ProofCyclePracticeEvidence(
        user_id="golfer-1",
        baseline_session_id=assignment.baseline_session_id,
        target_fingerprint=assignment.target_fingerprint,
        drill_id=assignment.drill_id,
        minutes=20,
        outcome="completed",
        completed_at=completed_at,
        completed_day=0,
    )


def matching_transfer(target: ProofTarget, *, angle: str = "face-on"):
    assignment = practice_assignment_from_target(target)
    assert assignment is not None
    return ProofCycleTransferCheck(
        session_id="refilm",
        user_id="golfer-1",
        baseline_session_id=assignment.baseline_session_id,
        target_fingerprint=assignment.target_fingerprint,
        drill_id=assignment.drill_id,
        club="driver",
        hand="right",
        angle=angle,
        normal_swings=True,
        declared_at=3.1,
    )


def transfer_view(
    artifact: ProofCycleArtifact,
    evidence=(),
    transfer_check=None,
):
    return practice_transfer_view(
        artifact,
        evidence,
        transfer_check,
        user_id="golfer-1",
        refilm_session_id="refilm",
        club="driver",
        hand="right",
        angle="face-on",
        baseline_created_at=1.0,
        refilm_created_at=3.0,
    )


def test_practice_receipt_and_normal_swing_check_are_non_causal_context():
    artifact = comparison_artifact()
    assert artifact.target is not None

    view = transfer_view(
        artifact,
        [matching_evidence(artifact.target)],
        matching_transfer(artifact.target),
    )

    assert view is not None
    assert view.heading == "Practice-to-re-film evidence"
    assert view.practice_session_count == 1
    assert view.practice_minutes == 20
    assert "consistent with transfer after logged practice" in view.detail
    assert "not proof" in view.detail


def test_mixed_practice_outcomes_do_not_misstate_completed_minutes():
    artifact = comparison_artifact()
    assert artifact.target is not None
    completed = matching_evidence(artifact.target, completed_at=2.0)
    still_working = replace(
        completed,
        minutes=45,
        outcome="still_working",
        completed_at=2.5,
    )

    view = transfer_view(
        artifact,
        [completed, still_working],
        matching_transfer(artifact.target),
    )

    assert view is not None
    assert (
        "2 self-reported practice receipts (65 minutes) were logged: "
        "1 completed receipt and 1 receipt marked still working"
    ) in view.summary
    assert "completed practice receipt (65 minutes)" not in view.summary


def test_late_or_wrong_target_receipts_cannot_attach_to_an_earlier_refilm():
    artifact = comparison_artifact()
    assert artifact.target is not None
    late = matching_evidence(artifact.target, completed_at=3.0)
    wrong_target = ProofCyclePracticeEvidence(
        user_id=late.user_id,
        baseline_session_id=late.baseline_session_id,
        target_fingerprint="b" * 64,
        drill_id=late.drill_id,
        minutes=late.minutes,
        outcome=late.outcome,
        completed_at=2.0,
        completed_day=0,
    )

    view = transfer_view(
        artifact,
        [late, wrong_target],
        matching_transfer(artifact.target),
    )

    assert view is not None
    assert view.heading == "Normal-swing transfer check recorded"
    assert view.practice_session_count == 0


def test_cross_context_transfer_declaration_fails_closed():
    artifact = comparison_artifact()
    assert artifact.target is not None

    view = transfer_view(
        artifact,
        [matching_evidence(artifact.target)],
        matching_transfer(artifact.target, angle="dtl"),
    )

    assert view is not None
    assert view.heading == "Transfer check not recorded"
    assert not view.normal_swing_declared


def test_structured_receipts_validate_and_replay_safely_in_sqlite(tmp_path):
    users = UserStore(tmp_path / "users.db")
    user = users.create("practice@example.com", "longenough")
    fields = {
        "baseline_session_id": "baseline",
        "target_fingerprint": "a" * 64,
        "drill_id": "wall-turn",
    }

    first = users.record_proof_cycle_practice_evidence(
        user.id, minutes=10, outcome="completed", now=100.0, **fields
    )
    updated = users.record_proof_cycle_practice_evidence(
        user.id, minutes=45, outcome="still_working", now=200.0, **fields
    )
    next_day = users.record_proof_cycle_practice_evidence(
        user.id, minutes=20, outcome="completed", now=86500.0, **fields
    )

    assert first.completed_day == updated.completed_day
    assert updated.minutes == 45 and updated.outcome == "still_working"
    assert next_day.completed_day != updated.completed_day
    assert len(users.list_proof_cycle_practice_evidence(user.id)) == 2
    with pytest.raises(ValueError, match="duration"):
        users.record_proof_cycle_practice_evidence(
            user.id, minutes=25, outcome="completed", **fields
        )
    with pytest.raises(ValueError, match="outcome"):
        users.record_proof_cycle_practice_evidence(
            user.id, minutes=20, outcome="narrative", **fields
        )

    check = users.record_proof_cycle_transfer_check(
        user.id,
        session_id="refilm",
        club="driver",
        hand="right",
        angle="face-on",
        normal_swings=True,
        now=90000.0,
        **fields,
    )
    replay = users.record_proof_cycle_transfer_check(
        user.id,
        session_id="refilm",
        club="driver",
        hand="right",
        angle="face-on",
        normal_swings=True,
        now=90001.0,
        **fields,
    )
    assert replay == check
    with pytest.raises(ValueError, match="different transfer check"):
        users.record_proof_cycle_transfer_check(
            user.id,
            session_id="refilm",
            club="driver",
            hand="left",
            angle="face-on",
            normal_swings=True,
            **fields,
        )

    users.delete_user(user.id)
    assert users.list_proof_cycle_practice_evidence(user.id) == []
    assert users.get_proof_cycle_transfer_check(user.id, "refilm") is None


@pytest.mark.parametrize("value", (True, 25, "", "10.5"))
def test_structured_practice_duration_is_closed(value):
    with pytest.raises(ValueError):
        normalize_practice_minutes(value)


@pytest.mark.parametrize("value", ("completed", "still_working"))
def test_structured_practice_outcomes_are_bounded(value):
    assert normalize_practice_outcome(value) == value
