"""Private, durable Proof Cycle sidecars for completed web sessions.

The analysis pipeline and static report remain immutable outputs.  This module
adapts a completed web job's persisted ``metrics.json`` into the PR 1 evidence
engine, then writes a small, versioned sidecar beside the report.  It contains
only the selected target and compact measurement snapshots—not raw swings,
video paths, customer identifiers, or Shopify data.

Sidecars are intentionally fail-closed.  A missing sidecar means an older
session and is harmless; a corrupt or stale sidecar in an active matched chain
prevents a later result from claiming improvement.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Literal, Protocol

from .caddie_brief import (
    metrics_from_payload,
    payload_is_coaching_eligible,
    payload_requires_refilm,
    payload_structure_is_valid,
    quality_warning_from_payload,
    scope_metrics_for_angle,
)
from .coaching import (
    issue_cards,
    priority_rule_version,
    validate_priority_rule_version,
)
from .config import Config
from .metrics import (
    ANGLE_DTL,
    ANGLE_FACE_ON,
    NUMERIC_FIELDS,
    finite_float,
    session_stats,
)
from .proof_cycle import (
    ProofComparison,
    ProofMeasurement,
    ProofRefilm,
    ProofSession,
    ProofTarget,
    ProofVerdict,
    SessionContext,
    compare_refilm,
)


ARTIFACT_FILENAME = "proof-cycle.json"
ARTIFACT_FORMAT = "caddieinsight-proof-cycle"
ARTIFACT_VERSION = 1
_MAX_ARTIFACT_BYTES = 512 * 1024

ArtifactStage = Literal["baseline", "comparison", "unavailable"]


class JobLike(Protocol):
    """The small web-job surface this adapter needs (kept import-cycle free)."""

    id: str
    session_dir: Path
    status: str
    created_at: float
    hand: str
    angle: str
    club: str | None
    user_id: str | None
    report_rel: str | None


@dataclass(frozen=True)
class ProofCyclePolicy:
    """The explicit product policy that produced a persisted result."""

    noise_floor: float
    minimum_readable_swings: int
    minimum_refilms_for_improved: int
    maximum_refilm_spread: float | None


@dataclass(frozen=True)
class PersistedComparison:
    """The customer-safe scalar result of one PR 1 comparison."""

    verdict: ProofVerdict
    hard_failures: tuple[str, ...]
    notes: tuple[str, ...]
    minimum_detectable_effect: float | None
    maximum_refilm_spread: float | None
    directional_change: float | None
    accepted_refilm_count: int

    @classmethod
    def from_comparison(cls, comparison: ProofComparison) -> "PersistedComparison":
        return cls(
            verdict=comparison.verdict,
            hard_failures=comparison.confidence.hard_failures,
            notes=comparison.confidence.notes,
            minimum_detectable_effect=comparison.minimum_detectable_effect,
            maximum_refilm_spread=comparison.maximum_refilm_spread,
            directional_change=comparison.directional_change,
            accepted_refilm_count=comparison.accepted_refilm_count,
        )


@dataclass(frozen=True)
class ProofCycleArtifact:
    """One versioned sidecar written beside a completed result."""

    source_session_id: str
    source_metrics_sha256: str | None
    stage: ArtifactStage
    target: ProofTarget | None
    refilm: ProofRefilm | None
    comparison: PersistedComparison | None
    policy: ProofCyclePolicy | None
    reason: str | None = None


@dataclass(frozen=True)
class ProofCycleView:
    """Display-ready, deliberately non-causal customer copy for the web UI."""

    tone: Literal["baseline", "positive", "neutral", "attention"]
    heading: str
    target_name: str
    summary: str
    detail: str
    next_step: str
    accepted_refilm_count: int


@dataclass(frozen=True)
class _PreparedSession:
    session: ProofSession
    rows: tuple
    metrics_sha256: str


def proof_cycle_enabled(cfg: Config) -> bool:
    """Return a strict feature-flag value; malformed config stays off."""

    return cfg.proof_cycle.get("enabled") is True


def proof_cycle_history_scan_limit(cfg: Config) -> int:
    """Bound the broader DB scan needed before exact context filtering."""

    raw = cfg.proof_cycle.get("history_limit", 6)
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
        return 50
    return min(max(raw * 10, 20), 100)


def proof_cycle_artifact_path(job: JobLike) -> Path | None:
    """Resolve the private sidecar only under this job's declared report."""

    report = _report_path(job)
    if report is None:
        return None
    if getattr(job, "structured_report", False) is True:
        return Path(job.session_dir) / ARTIFACT_FILENAME
    return report.parent / ARTIFACT_FILENAME


def proof_session_from_job(job: JobLike, cfg: Config) -> ProofSession | None:
    """Adapt one completed, coachable job without trusting metrics metadata."""

    prepared, _ = _prepare_session(job, cfg)
    return prepared.session if prepared is not None else None


def proof_target_from_job(
    job: JobLike,
    cfg: Config,
    *,
    rule_version: int | None = None,
) -> ProofTarget | None:
    """Snapshot the exact structured IssueCard chosen by the current report."""

    prepared, _ = _prepare_session(job, cfg)
    if prepared is None:
        return None
    return _target_from_prepared(prepared, cfg, rule_version=rule_version)


def proof_cycle_target_fingerprint(target: ProofTarget) -> str:
    """Return the stable, owner-free identity of a persisted Proof target.

    The fingerprint is deliberately a public accessor instead of making web
    callers reach into this module's private JSON helper. It can link a
    first-party practice receipt to the exact baseline/metric/drill snapshot,
    but it is never a substitute for the authenticated job-owner check.
    """

    return _target_fingerprint(target)


def active_proof_cycle_target_for_context(
    prior_jobs: Iterable[JobLike],
    cfg: Config,
    *,
    user_id: object,
    club: object,
    hand: object,
    angle: object,
    before: object,
    baseline_job_for_id: Callable[[str], JobLike | None] | None = None,
) -> ProofTarget | None:
    """Return a verified active target for a prospective matched upload.

    A transfer declaration is made before its new video has metrics, so this
    adapter deliberately works from the persisted, validated history alone.
    It uses the exact owner/club/hand/angle context and fails closed on any
    unreadable active-sidecar history rather than attaching a declaration to a
    guessed target.
    """

    normalized_user = _text(user_id)
    normalized_club = _text(club)
    normalized_hand = _normalised_hand(hand)
    normalized_angle = _normalised_angle(angle)
    timestamp = finite_float(before)
    if (
        not normalized_user
        or not normalized_club
        or normalized_hand is None
        or normalized_angle is None
        or timestamp is None
    ):
        return None
    context = SessionContext(
        # This is intentionally synthetic: prospective target selection uses
        # capture context only and never serializes this placeholder.
        session_id="prospective-transfer-check",
        user_id=normalized_user,
        club=normalized_club,
        hand=normalized_hand,
        angle=normalized_angle,
    )
    target, _history, unsafe_history = _active_cycle_from_prior_jobs(
        prior_jobs,
        context,
        timestamp,
        cfg,
        baseline_job_for_id=baseline_job_for_id,
    )
    return None if unsafe_history else target


def build_proof_cycle_artifact(
    job: JobLike,
    prior_jobs: Iterable[JobLike],
    cfg: Config,
    *,
    baseline_job_for_id: Callable[[str], JobLike | None] | None = None,
) -> ProofCycleArtifact:
    """Build using the priority rule selected by the current exact-bool gate."""

    return _build_proof_cycle_artifact(
        job,
        prior_jobs,
        cfg,
        baseline_job_for_id=baseline_job_for_id,
        target_rule_version=None,
    )


def _build_proof_cycle_artifact(
    job: JobLike,
    prior_jobs: Iterable[JobLike],
    cfg: Config,
    *,
    baseline_job_for_id: Callable[[str], JobLike | None] | None = None,
    target_rule_version: int | None,
) -> ProofCycleArtifact:
    """Build a baseline or matched comparison without changing report output.

    ``prior_jobs`` comes from the job manager's same-owner, same-club query,
    but this function independently verifies every context field before using
    it.  That keeps the adapter safe in tests, restored databases, and future
    callers that do not use the current manager.
    """

    prepared, reason = _prepare_session(job, cfg)
    source_id = _job_id(job)
    if prepared is None:
        return ProofCycleArtifact(
            source_session_id=source_id,
            source_metrics_sha256=_metrics_sha256_for_job(job),
            stage="unavailable",
            target=None,
            refilm=None,
            comparison=None,
            policy=None,
            reason=reason,
        )

    target, history, unsafe_history = _active_cycle_from_prior_jobs(
        prior_jobs,
        prepared.session.context,
        _created_at(job),
        cfg,
        baseline_job_for_id=baseline_job_for_id,
    )
    if target is None:
        if unsafe_history:
            return _unavailable_artifact(
                job,
                prepared.metrics_sha256,
                "existing_cycle_unreadable",
            )
        target = _target_from_prepared(
            prepared, cfg, rule_version=target_rule_version
        )
        if target is None:
            return _unavailable_artifact(
                job,
                prepared.metrics_sha256,
                "no_selected_issue",
            )
        try:
            policy = _policy_for_metric(cfg, target.metric)
        except ValueError:
            return _unavailable_artifact(
                job,
                prepared.metrics_sha256,
                "policy_unavailable",
            )
        return ProofCycleArtifact(
            source_session_id=source_id,
            source_metrics_sha256=prepared.metrics_sha256,
            stage="baseline",
            target=target,
            refilm=None,
            comparison=None,
            policy=policy,
        )

    try:
        policy = _policy_for_metric(cfg, target.metric)
        # ``object()`` deliberately makes the PR 1 engine return inconclusive
        # when an earlier sidecar in this target chain is malformed or stale.
        # Never silently omit uncertainty and manufacture an improvement.
        comparison = compare_refilm(
            target,
            prepared.session,
            prior_refilms=(
                tuple(history) + ((object(),) if unsafe_history else ())
            ),
            noise_floor=policy.noise_floor,
            minimum_readable_swings=policy.minimum_readable_swings,
            minimum_refilms_for_improved=policy.minimum_refilms_for_improved,
            maximum_refilm_spread=policy.maximum_refilm_spread,
        )
    except ValueError:
        return _unavailable_artifact(
            job, prepared.metrics_sha256, "policy_unavailable"
        )

    accepted_refilm = (
        comparison.current if not comparison.confidence.hard_failures else None
    )
    return ProofCycleArtifact(
        source_session_id=source_id,
        source_metrics_sha256=prepared.metrics_sha256,
        stage="comparison",
        target=target,
        refilm=accepted_refilm,
        comparison=PersistedComparison.from_comparison(comparison),
        policy=policy,
    )


def write_proof_cycle_artifact(job: JobLike, artifact: ProofCycleArtifact) -> Path:
    """Atomically write a private sidecar without serializing unsafe values."""

    path = proof_cycle_artifact_path(job)
    if path is None or artifact.source_session_id != _job_id(job):
        raise ValueError("Proof Cycle sidecar has no safe completed-report path")
    current_digest = _metrics_sha256_for_job(job)
    if (
        artifact.source_metrics_sha256 is not None
        and artifact.source_metrics_sha256 != current_digest
    ):
        raise ValueError("metrics.json changed before the Proof Cycle sidecar wrote")

    payload = artifact_as_dict(artifact)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8") + b"\n"
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return path


def _artifact_file_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(info.st_mtime_ns),
        int(getattr(info, "st_birthtime_ns", info.st_ctime_ns)),
    )


def _read_artifact_bytes(path: Path) -> bytes:
    before = os.lstat(path)
    attributes = getattr(before, "st_file_attributes", 0)
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        or before.st_size > _MAX_ARTIFACT_BYTES
    ):
        raise ValueError("Proof Cycle sidecar is not a bounded regular file")
    expected = _artifact_file_identity(before)
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        if _artifact_file_identity(opened) != expected:
            raise ValueError("Proof Cycle sidecar changed before it was read")
        raw = handle.read(_MAX_ARTIFACT_BYTES + 1)
    if len(raw) > _MAX_ARTIFACT_BYTES:
        raise ValueError("Proof Cycle sidecar exceeds its read bound")
    after = os.lstat(path)
    if _artifact_file_identity(after) != expected or len(raw) != before.st_size:
        raise ValueError("Proof Cycle sidecar changed while it was read")
    return raw


def load_proof_cycle_artifact(job: JobLike) -> ProofCycleArtifact | None:
    """Load only a complete, current, schema-valid sidecar; otherwise None."""

    path = proof_cycle_artifact_path(job)
    if path is None:
        return None
    user_id = _text(getattr(job, "user_id", None))
    if not user_id:
        return None
    try:
        raw = _read_artifact_bytes(path)
        data = json.loads(raw.decode("utf-8"))
        artifact = _artifact_from_dict(data, user_id=user_id)
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
        return None
    if artifact.source_session_id != _job_id(job):
        return None
    if artifact.source_metrics_sha256 is not None:
        if artifact.source_metrics_sha256 != _metrics_sha256_for_job(job):
            return None
    if not _artifact_matches_source_job(artifact, job):
        return None
    return artifact


def verified_proof_cycle_artifact(
    job: JobLike,
    prior_jobs: Iterable[JobLike],
    cfg: Config,
    *,
    baseline_job_for_id: Callable[[str], JobLike | None] | None = None,
) -> ProofCycleArtifact | None:
    """Rebuild a result before rendering it, so a sidecar cannot self-attest.

    The worker writes a compact artifact for durability, but the result page
    never trusts its verdict alone.  It replays the same bounded, matched
    evidence calculation from the immutable metrics files and prior validated
    sidecars.  Any stale or structurally valid-but-edited output stays hidden
    instead of claiming improvement.
    """

    stored = load_proof_cycle_artifact(job)
    if stored is None or stored.target is None:
        return None
    rebuilt = _build_proof_cycle_artifact(
        job,
        prior_jobs,
        cfg,
        baseline_job_for_id=baseline_job_for_id,
        target_rule_version=stored.target.rule_version,
    )
    if artifact_as_dict(stored) != artifact_as_dict(rebuilt):
        return None
    return stored


def artifact_as_dict(artifact: ProofCycleArtifact) -> dict:
    """Return an explicit JSON-safe schema; never use dataclass ``asdict``."""

    target_data = _target_as_dict(artifact.target) if artifact.target else None
    return {
        "format": ARTIFACT_FORMAT,
        "version": ARTIFACT_VERSION,
        "source": {
            "session_id": artifact.source_session_id,
            "metrics_sha256": artifact.source_metrics_sha256,
        },
        "stage": artifact.stage,
        "reason": artifact.reason,
        "target": target_data,
        "target_fingerprint": (
            _target_fingerprint(artifact.target) if artifact.target else None
        ),
        "refilm": _refilm_as_dict(artifact.refilm) if artifact.refilm else None,
        "comparison": (
            _comparison_as_dict(artifact.comparison)
            if artifact.comparison
            else None
        ),
        "policy": _policy_as_dict(artifact.policy) if artifact.policy else None,
    }


def proof_cycle_view(artifact: ProofCycleArtifact | None) -> ProofCycleView | None:
    """Translate a trusted artifact to cautious copy, never raw reason codes."""

    if artifact is None or artifact.target is None:
        return None
    target_name = artifact.target.display_name
    if artifact.stage == "baseline":
        return ProofCycleView(
            tone="baseline",
            heading="Proof target set",
            target_name=target_name,
            summary=f"{target_name} is your matched baseline.",
            detail=(
                "This does not claim a change yet; it records the exact "
                "measurement the report chose to work on."
            ),
            next_step=(
                "Practice the cue above, then re-film with the same club, "
                "handedness, and camera angle."
            ),
            accepted_refilm_count=0,
        )

    comparison = artifact.comparison
    if comparison is None:
        return None
    count = comparison.accepted_refilm_count
    required_refilms = (
        artifact.policy.minimum_refilms_for_improved
        if artifact.policy is not None
        else 2
    )
    if comparison.verdict == "early_signal":
        remaining = max(1, required_refilms - count)
        refilm_instruction = (
            "Make one more matching re-film before calling it improved."
            if remaining == 1
            else (
                f"Make {remaining} more matching re-films before calling "
                "it improved."
            )
        )
        return ProofCycleView(
            tone="positive",
            heading="Early signal — keep testing",
            target_name=target_name,
            summary="One matched follow-up moved in the right direction.",
            detail=(
                "It is a measurement signal, not a claim that practice "
                "caused the change."
            ),
            next_step=refilm_instruction,
            accepted_refilm_count=count,
        )
    if comparison.verdict == "improved":
        return ProofCycleView(
            tone="positive",
            heading="Matched improvement confirmed",
            target_name=target_name,
            summary=(
                f"{required_refilms} matched follow-ups moved in the right "
                "direction."
            ),
            detail=(
                "CaddieInsight is confirming the measurement pattern, not "
                "assuming why it changed."
            ),
            next_step="Keep the setup consistent for the next check-in.",
            accepted_refilm_count=count,
        )
    if comparison.verdict == "holding":
        return ProofCycleView(
            tone="positive",
            heading="Matched progress is holding",
            target_name=target_name,
            summary="The matched follow-ups are still moving in the right direction.",
            detail="The original target stays fixed while the evidence accumulates.",
            next_step="Keep the same setup for your next matched check-in.",
            accepted_refilm_count=count,
        )
    if comparison.verdict == "needs_attention":
        return ProofCycleView(
            tone="attention",
            heading="Matched check needs a reset",
            target_name=target_name,
            summary="This matched clip moved away from the original target.",
            detail="One result does not rewrite your plan or erase the baseline.",
            next_step="Return to the cue above, then re-film before changing focus.",
            accepted_refilm_count=count,
        )
    if comparison.verdict == "not_comparable":
        return ProofCycleView(
            tone="neutral",
            heading="Comparison paused",
            target_name=target_name,
            summary="This clip could not be safely matched to the target.",
            detail=(
                "No improvement claim was made because the measurement or "
                "capture context did not match closely enough."
            ),
            next_step=(
                "Re-film with the same club, handedness, camera angle, and "
                "at least three readable swings."
            ),
            accepted_refilm_count=0,
        )
    if comparison.verdict == "no_baseline":
        return ProofCycleView(
            tone="attention",
            heading="Baseline needs a reset",
            target_name=target_name,
            summary="The original target is no longer strong enough to compare.",
            detail="CaddieInsight paused the result instead of guessing.",
            next_step="Capture a new clear baseline before using this check again.",
            accepted_refilm_count=0,
        )
    return ProofCycleView(
        tone="neutral",
        heading="Still measuring",
        target_name=target_name,
        summary="The matched clips do not yet form a consistent enough signal.",
        detail="No improvement claim was made from mixed or too-small movement.",
        next_step="Keep the setup the same and collect another matched re-film.",
        accepted_refilm_count=count,
    )


def _prepare_session(
    job: JobLike, cfg: Config
) -> tuple[_PreparedSession | None, str]:
    if getattr(job, "status", None) != "done":
        return None, "not_complete"
    session_id = _job_id(job)
    user_id = _text(getattr(job, "user_id", None))
    club = _text(getattr(job, "club", None))
    hand = _normalised_hand(getattr(job, "hand", None))
    angle = _normalised_angle(getattr(job, "angle", None))
    if not session_id:
        return None, "session_id_required"
    if not user_id:
        return None, "account_required"
    if not club:
        return None, "club_required"
    if hand is None:
        return None, "hand_required"
    if angle is None:
        return None, "angle_required"
    payload, metrics_sha256, reason = _metrics_payload_for_job(job)
    if payload is None or metrics_sha256 is None:
        return None, reason
    if payload_requires_refilm(payload, angle=angle):
        return None, "refilm_required"
    if not payload_is_coaching_eligible(
        payload, cfg, angle=angle, club=club
    ):
        return None, "coaching_unavailable"
    rows = tuple(scope_metrics_for_angle(metrics_from_payload(payload), angle))
    if not rows:
        return None, "metrics_unreadable"
    warning = quality_warning_from_payload(payload, angle)
    return (
        _PreparedSession(
            session=ProofSession.from_swing_metrics(
                session_id=session_id,
                user_id=user_id,
                club=club,
                hand=hand,
                angle=angle,
                swings=rows,
                completed=True,
                coaching_eligible=True,
                warning=warning,
            ),
            rows=rows,
            metrics_sha256=metrics_sha256,
        ),
        "ready",
    )


def _target_from_prepared(
    prepared: _PreparedSession,
    cfg: Config,
    *,
    rule_version: int | None = None,
) -> ProofTarget | None:
    try:
        selected_rule = (
            priority_rule_version(cfg)
            if rule_version is None
            else validate_priority_rule_version(rule_version)
        )
        cards = issue_cards(
            list(prepared.rows),
            session_stats(list(prepared.rows)),
            cfg,
            club=prepared.session.context.club,
            rule_version=selected_rule,
        )
        if not cards:
            return None
        target = ProofTarget.from_issue_card(
            prepared.session,
            cards[0],
            rule_version=selected_rule,
        )
        policy = _policy_for_metric(cfg, target.metric)
    except ValueError:
        return None
    if (
        target.baseline.readable_swings < policy.minimum_readable_swings
        or target.baseline.value is None
        or target.baseline.mean is None
        or target.baseline.std is None
    ):
        return None
    return target


def _active_cycle_from_prior_jobs(
    prior_jobs: Iterable[JobLike],
    current: SessionContext,
    before: float,
    cfg: Config,
    *,
    baseline_job_for_id: Callable[[str], JobLike | None] | None = None,
) -> tuple[ProofTarget | None, tuple[ProofRefilm, ...], bool]:
    """Find one active exact-context target and its bounded safe history.

    A sidecar is a durable cache, not its own proof.  Before it can contribute
    a target or re-film measurement, the target is re-derived from its raw
    baseline and the re-film snapshot is re-derived from its raw source.  A
    caller may supply the exact baseline lookup so a long-running cycle stays
    verifiable even when the bounded recent-history scan no longer includes
    its original baseline.
    """

    candidates = [
        candidate
        for candidate in prior_jobs
        if _created_at(candidate) < before
        and _job_context_matches_context(candidate, current)
    ]
    candidates.sort(key=_created_at, reverse=True)
    candidates_by_id: dict[str, JobLike] = {}
    duplicate_ids: set[str] = set()
    for candidate in candidates:
        candidate_id = _job_id(candidate)
        if not candidate_id:
            continue
        if candidate_id in candidates_by_id:
            candidates_by_id.pop(candidate_id)
            duplicate_ids.add(candidate_id)
        elif candidate_id not in duplicate_ids:
            candidates_by_id[candidate_id] = candidate
    baseline_cache: dict[str, tuple[JobLike, ProofTarget] | None] = {}

    valid: list[tuple[JobLike, ProofCycleArtifact]] = []
    unreadable_created_at: list[float] = []
    for candidate in candidates:
        path = proof_cycle_artifact_path(candidate)
        if path is None or not path.is_file():
            # Sessions before this release did not define a Proof Cycle.  They
            # must not be retrofitted into a new comparison chain.
            continue
        artifact = load_proof_cycle_artifact(candidate)
        if artifact is None:
            unreadable_created_at.append(_created_at(candidate))
            continue
        if artifact.target is None:
            continue
        if not _artifact_sources_are_verified(
            artifact,
            candidate,
            cfg,
            candidates_by_id=candidates_by_id,
            duplicate_ids=duplicate_ids,
            baseline_job_for_id=baseline_job_for_id,
            baseline_cache=baseline_cache,
        ):
            unreadable_created_at.append(_created_at(candidate))
            continue
        if not _target_matches_context(artifact.target, current):
            unreadable_created_at.append(_created_at(candidate))
            continue
        valid.append((candidate, artifact))

    if not valid:
        return None, (), bool(unreadable_created_at)

    target = valid[0][1].target
    assert target is not None
    target_key = _target_fingerprint(target)
    baseline_created_at = _baseline_created_at(target, valid)
    # A corrupt sidecar before this active target was set belongs to an older
    # cycle.  A corrupt later one might be a missing confirmation, so it must
    # keep the next result inconclusive rather than being silently skipped.
    unsafe_history = any(
        created_at >= baseline_created_at for created_at in unreadable_created_at
    )
    history: list[ProofRefilm] = []
    history_limit = _history_limit(cfg)
    for candidate, artifact in valid:
        assert artifact.target is not None
        if _target_fingerprint(artifact.target) != target_key:
            # Parallel uploads can complete out of capture order.  An older
            # standalone baseline must not poison the newer active target; a
            # different target set after this baseline is still a conflict.
            if _created_at(candidate) >= baseline_created_at:
                unsafe_history = True
            continue
        if artifact.refilm is None:
            continue
        if not _refilm_matches_job(artifact.refilm, candidate):
            unsafe_history = True
            continue
        if len(history) < history_limit:
            history.append(artifact.refilm)
    return target, tuple(history), unsafe_history


def _artifact_sources_are_verified(
    artifact: ProofCycleArtifact,
    candidate: JobLike,
    cfg: Config,
    *,
    candidates_by_id: dict[str, JobLike],
    duplicate_ids: set[str],
    baseline_job_for_id: Callable[[str], JobLike | None] | None,
    baseline_cache: dict[str, tuple[JobLike, ProofTarget] | None],
) -> bool:
    """Re-derive the source facts a prior sidecar is allowed to carry.

    This intentionally stops short of recursively replaying every historical
    comparison: the only facts a later comparison consumes are the target and
    accepted re-film measurement. Both are tied here to their immutable
    metrics source, so a structurally valid edited sidecar cannot manufacture
    a target or history value detached from an authentic source session.
    """

    target = artifact.target
    if target is None:
        return False
    baseline_id = _text(target.baseline_context.session_id)
    if not baseline_id or baseline_id in duplicate_ids:
        return False
    cached = baseline_cache.get(baseline_id)
    if baseline_id not in baseline_cache:
        baseline_job = candidates_by_id.get(baseline_id)
        if baseline_job is None and baseline_job_for_id is not None:
            try:
                baseline_job = baseline_job_for_id(baseline_id)
            except Exception:
                baseline_job = None
        expected_target = (
            proof_target_from_job(
                baseline_job,
                cfg,
                rule_version=target.rule_version,
            )
            if baseline_job is not None and _job_id(baseline_job) == baseline_id
            else None
        )
        baseline_artifact = (
            load_proof_cycle_artifact(baseline_job)
            if baseline_job is not None
            else None
        )
        if (
            expected_target is None
            or baseline_artifact is None
            or baseline_artifact.stage != "baseline"
            or baseline_artifact.target != expected_target
        ):
            cached = None
        else:
            cached = (baseline_job, expected_target)
        baseline_cache[baseline_id] = cached
    if cached is None:
        return False
    baseline_job, expected_target = cached
    if target != expected_target:
        return False
    baseline_created_at = _created_at(baseline_job)
    candidate_created_at = _created_at(candidate)
    if baseline_created_at > candidate_created_at:
        return False
    if (
        baseline_created_at == candidate_created_at
        and _job_id(baseline_job) != _job_id(candidate)
    ):
        return False
    if artifact.refilm is None:
        return True
    prepared, _reason = _prepare_session(candidate, cfg)
    return (
        prepared is not None
        and artifact.refilm == ProofRefilm.from_session(target, prepared.session)
    )


def _baseline_created_at(
    target: ProofTarget, artifacts: Iterable[tuple[JobLike, ProofCycleArtifact]]
) -> float:
    """Find the target's own capture timestamp, or fail safely from the past."""

    baseline_id = target.baseline_context.session_id
    for job, _artifact in artifacts:
        if _job_id(job) == baseline_id:
            return _created_at(job)
    return float("-inf")


def _created_at(job: JobLike) -> float:
    try:
        return float(job.created_at)
    except (TypeError, ValueError):
        return float("-inf")


def _job_context_matches_context(job: JobLike, context: SessionContext) -> bool:
    return (
        _text(getattr(job, "user_id", None)) == context.user_id
        and _text(getattr(job, "club", None)) == context.club
        and _normalised_hand(getattr(job, "hand", None)) == context.hand
        and _normalised_angle(getattr(job, "angle", None)) == context.angle
    )


def _target_matches_context(target: ProofTarget, context: SessionContext) -> bool:
    baseline = target.baseline_context
    return (
        _text(baseline.user_id) == _text(context.user_id)
        and _text(baseline.club) == _text(context.club)
        and _normalised_hand(baseline.hand) == _normalised_hand(context.hand)
        and _normalised_angle(baseline.angle)
        == _normalised_angle(context.angle)
    )


def _target_matches_job(target: ProofTarget, job: JobLike) -> bool:
    """Check the persisted target against authoritative job-row context."""

    baseline = target.baseline_context
    return (
        _text(baseline.user_id) == _text(getattr(job, "user_id", None))
        and _text(baseline.club) == _text(getattr(job, "club", None))
        and _normalised_hand(baseline.hand)
        == _normalised_hand(getattr(job, "hand", None))
        and _normalised_angle(baseline.angle)
        == _normalised_angle(getattr(job, "angle", None))
    )


def _refilm_matches_job(refilm: ProofRefilm, job: JobLike) -> bool:
    return (
        refilm.context.session_id == _job_id(job)
        and _text(refilm.context.user_id) == _text(getattr(job, "user_id", None))
        and _text(refilm.context.club) == _text(getattr(job, "club", None))
        and _normalised_hand(refilm.context.hand)
        == _normalised_hand(getattr(job, "hand", None))
        and _normalised_angle(refilm.context.angle)
        == _normalised_angle(getattr(job, "angle", None))
    )


def _artifact_matches_source_job(artifact: ProofCycleArtifact, job: JobLike) -> bool:
    """Reject a locally copied/tampered sidecar before it reaches the UI."""

    if artifact.stage == "unavailable":
        return True
    target = artifact.target
    policy = artifact.policy
    if target is None or policy is None or not _target_matches_job(target, job):
        return False
    if artifact.stage == "baseline":
        return target.baseline_context.session_id == _job_id(job)

    comparison = artifact.comparison
    if comparison is None or target.baseline_context.session_id == _job_id(job):
        return False
    refilm = artifact.refilm
    if comparison.verdict in ("no_baseline", "not_comparable"):
        return refilm is None and comparison.accepted_refilm_count == 0
    if refilm is None or not _refilm_matches_job(refilm, job):
        return False
    if (
        refilm.measurement.metric != target.metric
        or refilm.measurement.aggregation != target.aggregation
        or comparison.accepted_refilm_count < 1
    ):
        return False
    if comparison.verdict == "early_signal":
        return comparison.accepted_refilm_count < policy.minimum_refilms_for_improved
    if comparison.verdict == "improved":
        return comparison.accepted_refilm_count == policy.minimum_refilms_for_improved
    if comparison.verdict == "holding":
        return comparison.accepted_refilm_count > policy.minimum_refilms_for_improved
    # ``inconclusive`` and ``needs_attention`` can happen at any non-zero
    # matched sample count.  The PR 1 engine has already preserved why.
    return comparison.verdict in ("inconclusive", "needs_attention")


def _unavailable_artifact(
    job: JobLike, metrics_sha256: str | None, reason: str
) -> ProofCycleArtifact:
    return ProofCycleArtifact(
        source_session_id=_job_id(job),
        source_metrics_sha256=metrics_sha256,
        stage="unavailable",
        target=None,
        refilm=None,
        comparison=None,
        policy=None,
        reason=reason,
    )


def _policy_for_metric(cfg: Config, metric: str) -> ProofCyclePolicy:
    settings = cfg.proof_cycle
    floors = settings.get("metric_noise_floors")
    if not isinstance(floors, dict):
        raise ValueError("Proof Cycle metric noise floors are missing")
    noise_floor = finite_float(floors.get(metric))
    if noise_floor is None or noise_floor < 0:
        raise ValueError("Proof Cycle metric noise floor is invalid")
    minimum_readable_swings = settings.get("minimum_readable_swings")
    minimum_refilms = settings.get("minimum_refilms_for_improved")
    if (
        not isinstance(minimum_readable_swings, int)
        or isinstance(minimum_readable_swings, bool)
        or minimum_readable_swings < 3
    ):
        raise ValueError("Proof Cycle minimum readable swings is invalid")
    if (
        not isinstance(minimum_refilms, int)
        or isinstance(minimum_refilms, bool)
        or minimum_refilms < 2
    ):
        raise ValueError("Proof Cycle confirmation count is invalid")
    maximum_refilm_spread = settings.get("maximum_refilm_spread")
    if maximum_refilm_spread is not None:
        maximum_refilm_spread = finite_float(maximum_refilm_spread)
        if maximum_refilm_spread is None or maximum_refilm_spread < 0:
            raise ValueError("Proof Cycle re-film spread is invalid")
    return ProofCyclePolicy(
        noise_floor=noise_floor,
        minimum_readable_swings=minimum_readable_swings,
        minimum_refilms_for_improved=minimum_refilms,
        maximum_refilm_spread=maximum_refilm_spread,
    )


def _history_limit(cfg: Config) -> int:
    value = cfg.proof_cycle.get("history_limit", 6)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        return 6
    return min(value, 100)


def _report_path(job: JobLike) -> Path | None:
    if getattr(job, "status", None) != "done":
        return None
    report_rel = getattr(job, "report_rel", None)
    if not isinstance(report_rel, str) or not report_rel:
        return None
    try:
        root = Path(job.session_dir).resolve()
        report = (root / report_rel).resolve()
    except (OSError, TypeError, ValueError):
        return None
    if not report.is_relative_to(root) or not report.is_file():
        return None
    return report


def _metrics_payload_for_job(
    job: JobLike,
) -> tuple[dict | None, str | None, str]:
    report = _report_path(job)
    if report is None:
        return None, None, "report_unavailable"
    metrics = report.parent / "metrics.json"
    try:
        raw = metrics.read_bytes()
    except OSError:
        return None, None, "metrics_unreadable"
    digest = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, digest, "metrics_unreadable"
    if not payload_structure_is_valid(payload):
        return None, digest, "metrics_unreadable"
    return payload, digest, "ready"


def _metrics_sha256_for_job(job: JobLike) -> str | None:
    report = _report_path(job)
    if report is None:
        return None
    try:
        return hashlib.sha256((report.parent / "metrics.json").read_bytes()).hexdigest()
    except OSError:
        return None


def _target_as_dict(target: ProofTarget) -> dict:
    return {
        "rule_version": target.rule_version,
        "source_flag": target.source_flag,
        "metric": target.metric,
        "display_name": target.display_name,
        "unit": target.unit,
        "worse_direction": target.worse_direction,
        "aggregation": target.aggregation,
        "benchmark_value": target.benchmark_value,
        "benchmark_text": target.benchmark_text,
        "drill_ids": list(target.drill_ids),
        "drill_names": list(target.drill_names),
        # Owner identity intentionally stays in the authenticated job row.
        "baseline_context": _context_as_dict(target.baseline_context),
        "baseline": _measurement_as_dict(target.baseline),
        "baseline_completed": target.baseline_completed,
        "baseline_coaching_eligible": target.baseline_coaching_eligible,
        "baseline_warning": target.baseline_warning,
    }


def _context_as_dict(context: SessionContext) -> dict:
    return {
        "session_id": context.session_id,
        "club": context.club,
        "hand": context.hand,
        "angle": context.angle,
    }


def _measurement_as_dict(measurement: ProofMeasurement) -> dict:
    return {
        "metric": measurement.metric,
        "aggregation": measurement.aggregation,
        "value": measurement.value,
        "mean": measurement.mean,
        "std": measurement.std,
        "readable_swings": measurement.readable_swings,
    }


def _refilm_as_dict(refilm: ProofRefilm) -> dict:
    return {
        "context": _context_as_dict(refilm.context),
        "measurement": _measurement_as_dict(refilm.measurement),
        "completed": refilm.completed,
        "coaching_eligible": refilm.coaching_eligible,
        "warning": refilm.warning,
    }


def _comparison_as_dict(comparison: PersistedComparison) -> dict:
    return {
        "verdict": comparison.verdict,
        "confidence": {
            "hard_failures": list(comparison.hard_failures),
            "notes": list(comparison.notes),
        },
        "minimum_detectable_effect": comparison.minimum_detectable_effect,
        "maximum_refilm_spread": comparison.maximum_refilm_spread,
        "directional_change": comparison.directional_change,
        "accepted_refilm_count": comparison.accepted_refilm_count,
    }


def _policy_as_dict(policy: ProofCyclePolicy) -> dict:
    return {
        "noise_floor": policy.noise_floor,
        "minimum_readable_swings": policy.minimum_readable_swings,
        "minimum_refilms_for_improved": policy.minimum_refilms_for_improved,
        "maximum_refilm_spread": policy.maximum_refilm_spread,
    }


def _target_fingerprint(target: ProofTarget) -> str:
    """A stable target identity that excludes the job-row owner identifier."""

    encoded = json.dumps(
        _target_as_dict(target),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _artifact_from_dict(data: object, *, user_id: str) -> ProofCycleArtifact:
    if not isinstance(data, dict):
        raise ValueError("artifact must be an object")
    if data.get("format") != ARTIFACT_FORMAT or data.get("version") != ARTIFACT_VERSION:
        raise ValueError("unsupported artifact version")
    source = _mapping(data.get("source"))
    source_session_id = _required_text(source.get("session_id"))
    source_digest = _optional_digest(source.get("metrics_sha256"))
    stage = data.get("stage")
    if stage not in ("baseline", "comparison", "unavailable"):
        raise ValueError("invalid artifact stage")
    reason = _optional_text(data.get("reason"))
    target_data = data.get("target")
    target = (
        _target_from_dict(_mapping(target_data), user_id=user_id)
        if target_data is not None
        else None
    )
    fingerprint = data.get("target_fingerprint")
    if target is None:
        if fingerprint is not None:
            raise ValueError("target fingerprint without target")
    elif not isinstance(fingerprint, str) or fingerprint != _target_fingerprint(target):
        raise ValueError("target fingerprint mismatch")
    refilm_data = data.get("refilm")
    refilm = (
        _refilm_from_dict(_mapping(refilm_data), user_id=user_id)
        if refilm_data is not None
        else None
    )
    comparison_data = data.get("comparison")
    comparison = (
        _comparison_from_dict(_mapping(comparison_data))
        if comparison_data is not None
        else None
    )
    policy_data = data.get("policy")
    policy = _policy_from_dict(_mapping(policy_data)) if policy_data is not None else None

    if stage == "baseline":
        if target is None or refilm is not None or comparison is not None or policy is None:
            raise ValueError("invalid baseline artifact")
    elif stage == "comparison":
        if target is None or comparison is None or policy is None:
            raise ValueError("invalid comparison artifact")
    elif target is not None or refilm is not None or comparison is not None or policy is not None:
        raise ValueError("invalid unavailable artifact")
    if stage == "unavailable" and not reason:
        raise ValueError("unavailable artifact needs a reason")
    return ProofCycleArtifact(
        source_session_id=source_session_id,
        source_metrics_sha256=source_digest,
        stage=stage,
        target=target,
        refilm=refilm,
        comparison=comparison,
        policy=policy,
        reason=reason,
    )


def _target_from_dict(data: dict, *, user_id: str) -> ProofTarget:
    rule_version = _positive_int(data.get("rule_version"), "rule_version")
    if rule_version not in (1, 2):
        raise ValueError("unsupported coaching priority rule")
    metric = _required_text(data.get("metric"))
    if metric not in NUMERIC_FIELDS:
        raise ValueError("unsupported target metric")
    aggregation = data.get("aggregation")
    if aggregation not in ("mean", "std", "worst"):
        raise ValueError("unsupported target aggregation")
    worse_direction = data.get("worse_direction")
    if worse_direction not in ("higher", "lower"):
        raise ValueError("unsupported target direction")
    baseline_context = _context_from_dict(
        _mapping(data.get("baseline_context")), user_id=user_id
    )
    baseline = _measurement_from_dict(_mapping(data.get("baseline")))
    if baseline.metric != metric or baseline.aggregation != aggregation:
        raise ValueError("baseline target mismatch")
    if _normalised_angle(baseline_context.angle) not in (ANGLE_FACE_ON, ANGLE_DTL):
        raise ValueError("unsupported target angle")
    return ProofTarget(
        source_flag=_required_text(data.get("source_flag")),
        metric=metric,
        display_name=_required_text(data.get("display_name")),
        unit=_required_text(data.get("unit")),
        worse_direction=worse_direction,
        aggregation=aggregation,
        benchmark_value=_optional_finite(data.get("benchmark_value")),
        benchmark_text=_required_text(data.get("benchmark_text")),
        drill_ids=_string_tuple(data.get("drill_ids")),
        drill_names=_string_tuple(data.get("drill_names")),
        baseline_context=baseline_context,
        baseline=baseline,
        baseline_completed=_strict_bool(data.get("baseline_completed")),
        baseline_coaching_eligible=_strict_bool(
            data.get("baseline_coaching_eligible")
        ),
        baseline_warning=_optional_text(data.get("baseline_warning")),
        rule_version=rule_version,
    )


def _context_from_dict(data: dict, *, user_id: str) -> SessionContext:
    club = _optional_text(data.get("club"))
    hand = _optional_text(data.get("hand"))
    angle = _optional_text(data.get("angle"))
    if not club or _normalised_hand(hand) is None or _normalised_angle(angle) is None:
        raise ValueError("invalid persisted comparison context")
    return SessionContext(
        session_id=_required_text(data.get("session_id")),
        user_id=user_id,
        club=club,
        hand=_normalised_hand(hand),
        angle=_normalised_angle(angle),
    )


def _measurement_from_dict(data: dict) -> ProofMeasurement:
    metric = _required_text(data.get("metric"))
    if metric not in NUMERIC_FIELDS:
        raise ValueError("unsupported persisted metric")
    aggregation = data.get("aggregation")
    if aggregation not in ("mean", "std", "worst"):
        raise ValueError("unsupported persisted aggregation")
    return ProofMeasurement(
        metric=metric,
        aggregation=aggregation,
        value=_optional_finite(data.get("value")),
        mean=_optional_finite(data.get("mean")),
        std=_optional_finite(data.get("std")),
        readable_swings=_nonnegative_int(data.get("readable_swings"), "readable_swings"),
    )


def _refilm_from_dict(data: dict, *, user_id: str) -> ProofRefilm:
    return ProofRefilm(
        context=_context_from_dict(_mapping(data.get("context")), user_id=user_id),
        measurement=_measurement_from_dict(_mapping(data.get("measurement"))),
        completed=_strict_bool(data.get("completed")),
        coaching_eligible=_strict_bool(data.get("coaching_eligible")),
        warning=_optional_text(data.get("warning")),
    )


def _comparison_from_dict(data: dict) -> PersistedComparison:
    verdict = data.get("verdict")
    if verdict not in (
        "no_baseline",
        "not_comparable",
        "inconclusive",
        "early_signal",
        "improved",
        "holding",
        "needs_attention",
    ):
        raise ValueError("unsupported comparison verdict")
    confidence = _mapping(data.get("confidence"))
    return PersistedComparison(
        verdict=verdict,
        hard_failures=_string_tuple(confidence.get("hard_failures")),
        notes=_string_tuple(confidence.get("notes")),
        minimum_detectable_effect=_optional_finite(
            data.get("minimum_detectable_effect")
        ),
        maximum_refilm_spread=_optional_finite(
            data.get("maximum_refilm_spread")
        ),
        directional_change=_optional_finite(data.get("directional_change")),
        accepted_refilm_count=_nonnegative_int(
            data.get("accepted_refilm_count"), "accepted_refilm_count"
        ),
    )


def _policy_from_dict(data: dict) -> ProofCyclePolicy:
    noise_floor = _optional_finite(data.get("noise_floor"))
    if noise_floor is None or noise_floor < 0:
        raise ValueError("invalid persisted noise floor")
    maximum_refilm_spread = _optional_finite(data.get("maximum_refilm_spread"))
    if maximum_refilm_spread is not None and maximum_refilm_spread < 0:
        raise ValueError("invalid persisted re-film spread")
    return ProofCyclePolicy(
        noise_floor=noise_floor,
        minimum_readable_swings=_positive_int(
            data.get("minimum_readable_swings"), "minimum_readable_swings", minimum=3
        ),
        minimum_refilms_for_improved=_positive_int(
            data.get("minimum_refilms_for_improved"),
            "minimum_refilms_for_improved",
            minimum=2,
        ),
        maximum_refilm_spread=maximum_refilm_spread,
    )


def _mapping(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def _required_text(value: object) -> str:
    text = _text(value)
    if not text:
        raise ValueError("expected non-empty text")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _required_text(value)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _normalised_hand(value: object) -> str | None:
    hand = _text(value).casefold()
    return hand if hand in ("left", "right") else None


def _normalised_angle(value: object) -> str | None:
    angle = _text(value).casefold()
    return angle if angle in (ANGLE_FACE_ON, ANGLE_DTL) else None


def _strict_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("expected boolean")
    return value


def _nonnegative_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _positive_int(value: object, name: str, *, minimum: int = 1) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{name} must be an integer at least {minimum}")
    return value


def _optional_finite(value: object) -> float | None:
    if value is None:
        return None
    converted = finite_float(value)
    if converted is None:
        raise ValueError("expected finite number or null")
    return converted


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("expected string list")
    return tuple(_required_text(item) for item in value)


def _optional_digest(value: object) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("invalid metrics digest")
    return value


def _job_id(job: JobLike) -> str:
    return _text(getattr(job, "id", None))
