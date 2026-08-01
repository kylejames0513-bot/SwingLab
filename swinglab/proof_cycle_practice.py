"""Structured practice context for the evidence-first Proof Cycle.

This module deliberately records *what a golfer says they practiced* and
whether a later upload was deliberately submitted as normal swings.  It never
uses either declaration to change a measurement verdict.  The Proof Cycle
sidecar remains the sole source for the matched mechanical comparison.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from .proof_cycle import ProofTarget
from .proof_cycle_artifact import (
    PersistedComparison,
    ProofCycleArtifact,
    proof_cycle_target_fingerprint,
)


PRACTICE_MINUTES = (10, 20, 45)
PRACTICE_OUTCOMES = ("completed", "still_working")
PracticeOutcome = Literal["completed", "still_working"]


@dataclass(frozen=True)
class ProofCyclePracticeAssignment:
    """One exact drill/target a golfer can log without trusting form fields."""

    baseline_session_id: str
    target_fingerprint: str
    target_name: str
    drill_id: str
    drill_name: str


@dataclass(frozen=True)
class ProofCyclePracticeEvidence:
    """One self-reported daily receipt for a verified target assignment."""

    user_id: str
    baseline_session_id: str
    target_fingerprint: str
    drill_id: str
    minutes: int
    outcome: PracticeOutcome
    completed_at: float
    completed_day: int


@dataclass(frozen=True)
class ProofCycleTransferCheck:
    """A server-validated declaration made before analysis starts."""

    session_id: str
    user_id: str
    baseline_session_id: str
    target_fingerprint: str
    drill_id: str
    club: str
    hand: str
    angle: str
    normal_swings: bool
    declared_at: float


@dataclass(frozen=True)
class PracticeTransferView:
    """Cautious, display-ready context beside a completed Proof Cycle card."""

    tone: Literal["positive", "neutral", "attention"]
    heading: str
    summary: str
    detail: str
    next_step: str
    practice_session_count: int
    practice_minutes: int
    normal_swing_declared: bool


def practice_assignment_from_target(
    target: ProofTarget | None,
) -> ProofCyclePracticeAssignment | None:
    """Derive one loggable drill from the immutable selected target.

    A target with no precise drill pair is intentionally not loggable.  The
    report can still show its mechanical comparison; product telemetry must
    not invent a prescription the briefing engine did not select.
    """

    if target is None:
        return None
    baseline_id = _text(target.baseline_context.session_id)
    drill_ids = tuple(_text(value) for value in target.drill_ids)
    drill_names = tuple(_text(value) for value in target.drill_names)
    if (
        not baseline_id
        or not drill_ids
        or len(drill_ids) != len(drill_names)
        or not drill_ids[0]
        or not drill_names[0]
    ):
        return None
    return ProofCyclePracticeAssignment(
        baseline_session_id=baseline_id,
        target_fingerprint=proof_cycle_target_fingerprint(target),
        target_name=_text(target.display_name),
        drill_id=drill_ids[0],
        drill_name=drill_names[0],
    )


def normalize_practice_minutes(value: object) -> int:
    """Accept only the deliberately bounded product durations."""

    if isinstance(value, bool):
        raise ValueError("Choose a valid practice duration.")
    try:
        minutes = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError("Choose a valid practice duration.") from None
    if minutes not in PRACTICE_MINUTES:
        raise ValueError("Choose a valid practice duration.")
    return minutes


def normalize_practice_outcome(value: object) -> PracticeOutcome:
    """Keep receipt outcomes structured and intentionally free of prose."""

    outcome = _text(value).casefold()
    if outcome not in PRACTICE_OUTCOMES:
        raise ValueError("Choose a valid practice outcome.")
    return outcome  # type: ignore[return-value]


def practice_transfer_view(
    artifact: ProofCycleArtifact | None,
    evidence: Iterable[ProofCyclePracticeEvidence],
    transfer_check: ProofCycleTransferCheck | None,
    *,
    user_id: object,
    refilm_session_id: object,
    club: object,
    hand: object,
    angle: object,
    baseline_created_at: object,
    refilm_created_at: object,
) -> PracticeTransferView | None:
    """Describe practice context without affecting the measurement result.

    This function is intentionally defensive even though its inputs normally
    come from authenticated SQLite rows.  A stale or cross-context declaration
    must not turn a later result into a claimed transfer check.
    """

    if (
        artifact is None
        or artifact.stage != "comparison"
        or artifact.target is None
        or artifact.comparison is None
    ):
        return None
    assignment = practice_assignment_from_target(artifact.target)
    if assignment is None:
        return None
    normalized_user = _text(user_id)
    normalized_refilm_id = _text(refilm_session_id)
    normalized_club = _text(club)
    normalized_hand = _hand(hand)
    normalized_angle = _angle(angle)
    baseline_time = _finite_time(baseline_created_at)
    refilm_time = _finite_time(refilm_created_at)
    if (
        not normalized_user
        or not normalized_refilm_id
        or not normalized_club
        or normalized_hand is None
        or normalized_angle is None
        or baseline_time is None
        or refilm_time is None
        or refilm_time <= baseline_time
    ):
        return None

    receipts = tuple(
        item
        for item in evidence
        if _evidence_matches_window(
            item,
            assignment,
            user_id=normalized_user,
            baseline_time=baseline_time,
            refilm_time=refilm_time,
        )
    )
    sessions = len(receipts)
    minutes = sum(item.minutes for item in receipts)
    normal_swing_declared = _transfer_check_matches(
        transfer_check,
        assignment,
        user_id=normalized_user,
        refilm_session_id=normalized_refilm_id,
        club=normalized_club,
        hand=normalized_hand,
        angle=normalized_angle,
    )
    comparison = artifact.comparison

    if not normal_swing_declared:
        return PracticeTransferView(
            tone="neutral",
            heading="Transfer check not recorded",
            summary=(
                "This upload was not declared as a normal-swing transfer "
                "check for the active practice target."
            ),
            detail=(
                "The matched measurement stands on its own. CaddieInsight "
                "will not connect any practice receipt to it."
            ),
            next_step=(
                "Before your next matched re-film, log the focused practice "
                "and declare normal swings at upload."
            ),
            practice_session_count=sessions,
            practice_minutes=minutes,
            normal_swing_declared=False,
        )

    if not sessions:
        return PracticeTransferView(
            tone="neutral",
            heading="Normal-swing transfer check recorded",
            summary=(
                "This upload was declared as normal swings, but no "
                "self-reported practice receipt was logged after the baseline."
            ),
            detail=(
                "The comparison is still useful, but CaddieInsight will not "
                "infer why the measurement changed."
            ),
            next_step="Log the focused practice before the next matched re-film.",
            practice_session_count=0,
            practice_minutes=0,
            normal_swing_declared=True,
        )

    receipt_summary = _receipt_summary(receipts, minutes)
    if comparison.verdict in ("not_comparable", "no_baseline"):
        return PracticeTransferView(
            tone="neutral",
            heading="Transfer evidence paused",
            summary=(
                f"{receipt_summary} before this normal-swing transfer check, "
                "but the upload did not earn a safe matched comparison."
            ),
            detail=(
                "No practice or transfer claim was made from an unmatched "
                "measurement."
            ),
            next_step="Use the same capture setup, then submit another normal-swing check.",
            practice_session_count=sessions,
            practice_minutes=minutes,
            normal_swing_declared=True,
        )
    if comparison.verdict == "inconclusive":
        return PracticeTransferView(
            tone="neutral",
            heading="Transfer evidence still measuring",
            summary=(
                f"{receipt_summary} before this normal-swing transfer check, "
                "but the matched result is not consistent enough yet."
            ),
            detail=(
                "Self-reported practice is context only; it does not turn a "
                "mixed measurement into an improvement claim."
            ),
            next_step="Keep the cue and capture setup stable for one more check.",
            practice_session_count=sessions,
            practice_minutes=minutes,
            normal_swing_declared=True,
        )
    if comparison.verdict == "needs_attention":
        return PracticeTransferView(
            tone="attention",
            heading="Transfer check needs a reset",
            summary=(
                f"{receipt_summary} before this normal-swing transfer check, "
                "but the matched measurement moved away from the target."
            ),
            detail=(
                "That does not erase the practice receipt or establish a "
                "cause. Return to the cue and collect another matched check."
            ),
            next_step="Practice the same target, then re-film normal swings again.",
            practice_session_count=sessions,
            practice_minutes=minutes,
            normal_swing_declared=True,
        )
    return _positive_transfer_view(comparison, receipt_summary, sessions, minutes)


def _positive_transfer_view(
    comparison: PersistedComparison,
    receipt_summary: str,
    sessions: int,
    minutes: int,
) -> PracticeTransferView:
    if comparison.verdict == "early_signal":
        result = "The matched measurement moved in the target direction once."
        next_step = "Keep the same cue and collect another normal-swing check."
    elif comparison.verdict == "holding":
        result = "The matched measurement is continuing to move in the target direction."
        next_step = "Keep the setup consistent for the next matched check-in."
    else:  # "improved" is the only remaining trusted positive verdict.
        result = "The repeated matched measurement now confirms the target pattern."
        next_step = "Keep the cue light and use the same setup for the next check-in."
    return PracticeTransferView(
        tone="positive",
        heading="Practice-to-re-film evidence",
        summary=f"{receipt_summary} before this normal-swing transfer check.",
        detail=(
            f"{result} That is consistent with transfer after logged practice, "
            "not proof that the drill caused the change."
        ),
        next_step=next_step,
        practice_session_count=sessions,
        practice_minutes=minutes,
        normal_swing_declared=True,
    )


def _evidence_matches_window(
    item: ProofCyclePracticeEvidence,
    assignment: ProofCyclePracticeAssignment,
    *,
    user_id: str,
    baseline_time: float,
    refilm_time: float,
) -> bool:
    completed_at = _finite_time(item.completed_at)
    return bool(
        completed_at is not None
        and baseline_time <= completed_at < refilm_time
        and _text(item.user_id) == user_id
        and _text(item.baseline_session_id) == assignment.baseline_session_id
        and _text(item.target_fingerprint) == assignment.target_fingerprint
        and _text(item.drill_id) == assignment.drill_id
        and item.minutes in PRACTICE_MINUTES
        and item.outcome in PRACTICE_OUTCOMES
    )


def _transfer_check_matches(
    check: ProofCycleTransferCheck | None,
    assignment: ProofCyclePracticeAssignment,
    *,
    user_id: str,
    refilm_session_id: str,
    club: str,
    hand: str,
    angle: str,
) -> bool:
    if check is None or not check.normal_swings:
        return False
    return (
        _text(check.session_id) == refilm_session_id
        and _text(check.user_id) == user_id
        and _text(check.baseline_session_id) == assignment.baseline_session_id
        and _text(check.target_fingerprint) == assignment.target_fingerprint
        and _text(check.drill_id) == assignment.drill_id
        and _text(check.club) == club
        and _hand(check.hand) == hand
        and _angle(check.angle) == angle
        and _finite_time(check.declared_at) is not None
    )


def _receipt_summary(
    receipts: tuple[ProofCyclePracticeEvidence, ...], minutes: int
) -> str:
    completed = sum(1 for item in receipts if item.outcome == "completed")
    total = len(receipts)
    noun = "receipt" if total == 1 else "receipts"
    verb = "was" if total == 1 else "were"
    if completed == total:
        return (
            f"{completed} self-reported completed practice {noun} "
            f"({minutes} minutes) {verb} logged"
        )
    if completed == 0:
        return (
            f"{total} self-reported practice {noun} marked still working "
            f"({minutes} minutes) {verb} logged"
        )
    still_working = total - completed
    completed_noun = "receipt" if completed == 1 else "receipts"
    still_working_noun = "receipt" if still_working == 1 else "receipts"
    return (
        f"{total} self-reported practice {noun} ({minutes} minutes) {verb} logged: "
        f"{completed} completed {completed_noun} and {still_working} "
        f"{still_working_noun} marked still working"
    )


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _hand(value: object) -> str | None:
    hand = _text(value).casefold()
    return hand if hand in ("left", "right") else None


def _angle(value: object) -> str | None:
    angle = _text(value).casefold()
    return angle if angle in ("face-on", "dtl") else None


def _finite_time(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    # Avoid importing math solely for an API-boundary predicate.
    if timestamp != timestamp or timestamp in (float("inf"), float("-inf")):
        return None
    return timestamp
