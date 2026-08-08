"""Pure native resource serializers and their read-only composition service."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

from ..api.auth import MobileAuthContext
from ..api.contracts import (
    BriefResponse,
    CapabilitiesResponse,
    MobileSessionResponse,
    MobileSessionsResponse,
    MobileTodayResponse,
    PracticeEvidenceReceipt,
    PracticeEvidenceRequest,
    Profile,
    ProfileResponse,
    ProfileUpdateRequest,
    ProgressResponse,
    ProofCycleTargetResponse,
)
from .credential_mutations import (
    CredentialMutationGuard,
    CredentialMutationRejected,
)
from .users import (
    HistoryEpochError,
    MobilePracticeEvidenceConflict,
    MobilePracticeReceiptConflict,
)
from ..clubs import CLUB_LABELS
from ..config import Config
from ..drills import gear_shop_url
from ..levels import LEVEL_LABELS
from ..metrics import ANGLES
from ..proof_cycle_artifact import (
    active_proof_cycle_target_for_context,
    proof_cycle_history_scan_limit,
    proof_cycle_view,
)
from ..proof_cycle_practice import practice_assignment_from_target
from .jobs import DONE, FAILED, PROCESSING, QUEUED, Job
from .users import User


_FEATURE_FLAGS = (
    "mobile_resources_enabled",
    "mobile_profile_writes_enabled",
    "mobile_practice_writes_enabled",
    "mobile_device_management_enabled",
    "mobile_resumable_upload_enabled",
    "mobile_privacy_enabled",
    "mobile_events_enabled",
    "mobile_push_enabled",
    "mobile_native_billing_enabled",
)
VIDEO_SUFFIXES = frozenset({".mov", ".mp4", ".m4v", ".avi", ".mkv"})
_NUMERIC_BOUNDS: dict[str, tuple[int, int]] = {
    "mobile_upload_chunk_mb": (1, 64),
    "mobile_active_uploads_per_user": (1, 10),
    "mobile_upload_ttl_seconds": (60, 7 * 24 * 60 * 60),
}


@dataclass(frozen=True)
class MobileResourceSettings:
    resources_enabled: bool
    profile_writes_enabled: bool
    practice_writes_enabled: bool
    device_management_enabled: bool
    resumable_upload_enabled: bool
    privacy_enabled: bool
    events_enabled: bool
    push_enabled: bool
    native_billing_enabled: bool
    upload_chunk_bytes: int
    active_uploads_per_user: int
    upload_ttl_seconds: int


class MobileResourceNotFound(LookupError):
    """An owned native resource is absent, stale, or belongs to another user."""


class MobileProfileUnauthorized(PermissionError):
    """The bearer credential is no longer admissible for a profile write."""


class MobileProfileHistoryConflict(RuntimeError):
    """The client expected a history epoch that is no longer current."""


class MobileProfileUnavailable(LookupError):
    """The account vanished or became unclaimable before the profile write."""


class MobilePracticeUnauthorized(PermissionError):
    """The bearer credential is no longer admissible for a practice write."""


class MobilePracticeHistoryConflict(RuntimeError):
    """The client expected a history epoch that is no longer current."""


class MobilePracticeUnavailable(LookupError):
    """No current owned Proof Cycle target matches the practice request."""


class MobilePracticeIdempotencyConflict(RuntimeError):
    """An Idempotency-Key was reused with a different practice body."""


class MobilePracticeDayConflict(RuntimeError):
    """A distinct practice receipt already exists for this target day."""


def validate_mobile_resource_settings(
    web: Mapping[str, object],
) -> MobileResourceSettings:
    """Validate every default-off Task 4 flag and bounded upload setting."""

    flags: dict[str, bool] = {}
    for name in _FEATURE_FLAGS:
        value = web.get(name, False)
        if type(value) is not bool:
            raise ValueError(f"web.{name} must be true or false.")
        flags[name] = value
    numbers: dict[str, int] = {}
    for name, (minimum, maximum) in _NUMERIC_BOUNDS.items():
        value = web.get(name)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(
                f"web.{name} must be an integer from {minimum} to {maximum}."
            )
        numbers[name] = value
    return MobileResourceSettings(
        resources_enabled=flags["mobile_resources_enabled"],
        profile_writes_enabled=flags["mobile_profile_writes_enabled"],
        practice_writes_enabled=flags["mobile_practice_writes_enabled"],
        device_management_enabled=flags["mobile_device_management_enabled"],
        resumable_upload_enabled=flags["mobile_resumable_upload_enabled"],
        privacy_enabled=flags["mobile_privacy_enabled"],
        events_enabled=flags["mobile_events_enabled"],
        push_enabled=flags["mobile_push_enabled"],
        native_billing_enabled=flags["mobile_native_billing_enabled"],
        upload_chunk_bytes=numbers["mobile_upload_chunk_mb"] * 1024 * 1024,
        active_uploads_per_user=numbers["mobile_active_uploads_per_user"],
        upload_ttl_seconds=numbers["mobile_upload_ttl_seconds"],
    )


def _public_store_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().rstrip("/")
    if (
        not candidate
        or len(candidate) > 2048
        or any(
            not character.isprintable() or character.isspace()
            for character in candidate
        )
    ):
        return None
    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or port == 0
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    return candidate


class MobileResourceService:
    """Read server-owned native policy without exposing configuration state."""

    def __init__(
        self,
        manager,
        users,
        cfg: Config,
        settings: MobileResourceSettings,
        *,
        brief_provider=None,
        proof_artifact_provider=None,
        active_target_provider=None,
        practice_plan_provider=None,
        clock=None,
    ):
        self._manager = manager
        self._users = users
        self._cfg = cfg
        self.settings = settings
        self._brief_provider = brief_provider or (lambda _job: None)
        self._proof_artifact_provider = proof_artifact_provider or (lambda _job: None)
        self._active_target_provider = active_target_provider or (
            lambda owner, boundary, before: active_proof_cycle_target_for_context(
                self._manager.list_comparable(
                    user_id=owner.id,
                    club=boundary["club"],
                    hand=boundary["hand"],
                    angle=boundary["angle"],
                    through=before,
                    limit=proof_cycle_history_scan_limit(self._cfg),
                ),
                self._cfg,
                user_id=owner.id,
                club=boundary["club"],
                hand=boundary["hand"],
                angle=boundary["angle"],
                before=before,
                baseline_job_for_id=self._manager.get,
            )
        )
        self._practice_plan_provider = practice_plan_provider or (
            lambda _brief, _profile: []
        )
        self._clock = clock or time.time

    def _current_owner(self, context: MobileAuthContext) -> User:
        current = self._users.get(context.user.id)
        if (
            current is None
            or current.auth_epoch != context.auth_epoch
            or current.history_epoch != context.user.history_epoch
        ):
            raise MobileResourceNotFound("Account history is no longer current.")
        return current

    def update_profile(
        self,
        context: MobileAuthContext,
        request: ProfileUpdateRequest,
        *,
        guard: CredentialMutationGuard,
        now: float | None = None,
    ) -> ProfileResponse:
        """Upsert the caller's profile under one credential-mutation lease."""

        try:
            lease = guard.admit(context)
        except CredentialMutationRejected as exc:
            raise MobileProfileUnauthorized(
                "Invalid mobile access token."
            ) from exc

        normalized = self._users._normalize_golfer_profile_values(
            display_name=request.display_name,
            experience_mode=request.experience_mode,
            handicap_range=request.handicap_range,
            primary_goal=request.primary_goal,
            practice_minutes=request.practice_minutes,
            sessions_per_week=request.sessions_per_week,
            handedness=request.handedness,
            camera_angle=request.camera_angle,
            preferred_club=request.preferred_club,
            reduced_motion=request.reduced_motion,
            marketing_email_opt_in=request.marketing_email_opt_in,
        )
        timestamp = self._clock() if now is None else float(now)
        try:
            with self._users._lock:
                try:
                    self._users._conn.execute("BEGIN IMMEDIATE")
                    lease.validate_locked(self._users, now=timestamp)
                    owner = self._users._conn.execute(
                        "SELECT 1 FROM users WHERE id = ?",
                        (context.user.id,),
                    ).fetchone()
                    if owner is None:
                        # Deleted identity must never surface as a history-epoch
                        # conflict from the epoch fence below.
                        raise CredentialMutationRejected(
                            "The authenticated mobile credential changed."
                        )
                    self._users._assert_history_epoch_locked(
                        context.user.id,
                        request.expected_history_epoch,
                    )
                    profile = self._users._upsert_golfer_profile_locked(
                        context.user.id,
                        normalized=normalized,
                        timestamp=timestamp,
                    )
                    self._users._conn.commit()
                except Exception:
                    if self._users._conn.in_transaction:
                        self._users._conn.rollback()
                    raise
        except CredentialMutationRejected as exc:
            raise MobileProfileUnauthorized(
                "Invalid mobile access token."
            ) from exc
        except HistoryEpochError as exc:
            raise MobileProfileHistoryConflict(str(exc)) from exc
        except ValueError as exc:
            message = str(exc)
            if message in {
                "Your account is no longer available.",
                "Verify your account before creating a golfer profile.",
            }:
                raise MobileProfileUnavailable(message) from exc
            raise
        finally:
            lease.release()
        return ProfileResponse(profile=serialize_profile(profile))

    def record_practice_evidence(
        self,
        context: MobileAuthContext,
        request: PracticeEvidenceRequest,
        *,
        idempotency_key: object,
        guard: CredentialMutationGuard,
        now: float | None = None,
    ) -> PracticeEvidenceReceipt:
        """Record one owned practice receipt under a credential-mutation lease."""

        try:
            lease = guard.admit(context)
        except CredentialMutationRejected as exc:
            raise MobilePracticeUnauthorized(
                "Invalid mobile access token."
            ) from exc

        timestamp = self._clock() if now is None else float(now)
        try:
            with self._users._lock:
                try:
                    self._users._conn.execute("BEGIN IMMEDIATE")
                    lease.validate_locked(self._users, now=timestamp)
                    owner_row = self._users._conn.execute(
                        "SELECT 1 FROM users WHERE id = ?",
                        (context.user.id,),
                    ).fetchone()
                    if owner_row is None:
                        raise CredentialMutationRejected(
                            "The authenticated mobile credential changed."
                        )
                    self._users._assert_history_epoch_locked(
                        context.user.id,
                        request.expected_history_epoch,
                    )
                    owner = context.user
                    baseline = self._manager.get(request.baseline_session_id)
                    boundary = (
                        _measurement_boundary(baseline)
                        if baseline is not None
                        else None
                    )
                    if (
                        baseline is None
                        or baseline.user_id != owner.id
                        or baseline.status != DONE
                        or not self._manager.coaching_eligible(baseline)
                        or boundary is None
                    ):
                        raise MobilePracticeUnavailable(
                            "No matching Proof Cycle practice target."
                        )
                    try:
                        active_target = self._active_target_provider(
                            owner, boundary, timestamp
                        )
                    except Exception as exc:
                        raise MobilePracticeUnavailable(
                            "No matching Proof Cycle practice target."
                        ) from exc
                    assignment = practice_assignment_from_target(active_target)
                    if (
                        assignment is None
                        or assignment.baseline_session_id
                        != request.baseline_session_id
                        or assignment.target_fingerprint
                        != request.target_fingerprint
                        or assignment.drill_id != request.drill_id
                    ):
                        raise MobilePracticeUnavailable(
                            "No matching Proof Cycle practice target."
                        )
                    receipt = self._users._record_mobile_practice_evidence_locked(
                        context.user.id,
                        baseline_session_id=request.baseline_session_id,
                        target_fingerprint=request.target_fingerprint,
                        drill_id=request.drill_id,
                        minutes=request.minutes,
                        outcome=request.outcome,
                        reps=request.reps,
                        feel=request.feel,
                        relative_strike=request.relative_strike,
                        start_line=request.start_line,
                        miss_pattern=request.miss_pattern,
                        expected_history_epoch=request.expected_history_epoch,
                        idempotency_key=idempotency_key,
                        now=timestamp,
                    )
                    self._users._conn.commit()
                except Exception:
                    if self._users._conn.in_transaction:
                        self._users._conn.rollback()
                    raise
        except CredentialMutationRejected as exc:
            raise MobilePracticeUnauthorized(
                "Invalid mobile access token."
            ) from exc
        except HistoryEpochError as exc:
            raise MobilePracticeHistoryConflict(str(exc)) from exc
        except MobilePracticeEvidenceConflict as exc:
            raise MobilePracticeIdempotencyConflict(str(exc)) from exc
        except MobilePracticeReceiptConflict as exc:
            raise MobilePracticeDayConflict(str(exc)) from exc
        finally:
            lease.release()
        return receipt

    def capabilities(self, context: MobileAuthContext) -> CapabilitiesResponse:
        with self._manager.history_delivery_guard():
            user = self._current_owner(context)
            used = max(0, int(self._manager.usage_this_month_snapshot(user.id)))
            limit = int(
                self._cfg.billing[
                    "pro_per_month" if user.is_pro else "free_per_month"
                ]
            )
            monthly_limit = None if limit <= 0 else limit
            remaining = (
                None if monthly_limit is None else max(0, monthly_limit - used)
            )
            max_upload_mb = int(self._cfg.web["max_upload_mb"])
            max_video_seconds = int(self._cfg.analysis["max_video_s"])
            if max_upload_mb <= 0 or max_video_seconds <= 0:
                raise RuntimeError("Native upload limits must be finite and positive.")
            return CapabilitiesResponse(
                capabilities={
                    "upload": {
                        "max_bytes": max_upload_mb * 1024 * 1024,
                        "max_video_seconds": max_video_seconds,
                        "chunk_bytes": self.settings.upload_chunk_bytes,
                        "active_limit": self.settings.active_uploads_per_user,
                        "allowed_suffixes": sorted(VIDEO_SUFFIXES),
                    },
                    "canonical": {
                        "hands": ["left", "right"],
                        "angles": list(ANGLES),
                        "clubs": list(CLUB_LABELS),
                        "analysis_states": [
                            "queued",
                            "processing",
                            "done",
                            "failed",
                        ],
                    },
                    "quota": {
                        "plan": "pro" if user.is_pro else "free",
                        "monthly_limit": monthly_limit,
                        "used": used,
                        "remaining": remaining,
                    },
                    "features": {
                        "native_auth": (
                            self._cfg.web["mobile_native_auth_enabled"] is True
                        ),
                        "resources": self.settings.resources_enabled,
                        "profile_writes": self.settings.profile_writes_enabled,
                        "practice_writes": self.settings.practice_writes_enabled,
                        "device_management": self.settings.device_management_enabled,
                        "resumable_upload": self.settings.resumable_upload_enabled,
                        "privacy": self.settings.privacy_enabled,
                        "events": self.settings.events_enabled,
                        "push": self.settings.push_enabled,
                        "native_billing": self.settings.native_billing_enabled,
                        "proof_cycle": self._cfg.proof_cycle.get("enabled") is True,
                    },
                    "physical_store_url": _public_store_url(gear_shop_url(self._cfg)),
                }
            )

    def sessions(self, context: MobileAuthContext) -> MobileSessionsResponse:
        with self._manager.history_delivery_guard():
            owner = self._current_owner(context)
            jobs = self._manager.list_recent(user_id=owner.id)
            return MobileSessionsResponse(
                sessions=[
                    serialize_mobile_session(
                        job,
                        owner,
                        queue_position=self._manager.queue_position(job),
                        coaching_eligible=(
                            self._manager.coaching_eligible(job)
                            if job.status == DONE
                            else None
                        ),
                    )
                    for job in jobs
                ]
            )

    def session(
        self, context: MobileAuthContext, session_id: str
    ) -> MobileSessionResponse:
        with self._manager.history_delivery_guard():
            owner = self._current_owner(context)
            job = self._manager.get(session_id)
            if job is None or job.user_id != owner.id:
                raise MobileResourceNotFound("Session not found.")
            return serialize_mobile_session(
                job,
                owner,
                queue_position=self._manager.queue_position(job),
                coaching_eligible=(
                    self._manager.coaching_eligible(job)
                    if job.status == DONE
                    else None
                ),
            )

    def brief(self, context: MobileAuthContext, session_id: str) -> BriefResponse:
        with self._manager.history_delivery_guard():
            owner = self._current_owner(context)
            job = self._manager.get(session_id)
            if job is None or job.user_id != owner.id:
                raise MobileResourceNotFound("Session not found.")
            boundary = _measurement_boundary(job)
            if boundary is None:
                raise MobileResourceNotFound("Session not found.")
            if job.status in {QUEUED, PROCESSING}:
                return _brief_unavailable(
                    boundary,
                    status="brief_not_ready",
                    message="Analysis is still in progress.",
                )
            if job.status != DONE:
                return _brief_unavailable(
                    boundary,
                    status="brief_not_ready",
                    message="This analysis did not produce a coaching brief.",
                )
            if not self._manager.coaching_eligible(job):
                return _brief_unavailable(
                    boundary,
                    status="refilm_required",
                    message="Capture a clearer matching swing video.",
                )
            brief = self._brief_provider(job)
            if brief is None or bool(getattr(brief, "refilm_required", False)):
                return _brief_unavailable(
                    boundary,
                    status=(
                        "refilm_required"
                        if bool(getattr(brief, "refilm_required", False))
                        else "brief_not_ready"
                    ),
                    message=(
                        "Capture a clearer matching swing video."
                        if brief is not None
                        else "A structured coaching brief is not available."
                    ),
                )
            artifact = (
                self._proof_artifact_provider(job)
                if self._cfg.proof_cycle.get("enabled") is True
                else None
            )
            return serialize_mobile_brief(
                brief,
                boundary,
                proof_cycle_target=self._proof_cycle_target(job, owner, artifact),
            )

    def _proof_cycle_target(
        self, job: Job, owner: User, artifact
    ) -> ProofCycleTargetResponse | None:
        if (
            artifact is None
            or getattr(artifact, "source_session_id", None) != job.id
            or getattr(artifact, "target", None) is None
        ):
            return None
        target = artifact.target
        assignment = practice_assignment_from_target(target)
        if assignment is None:
            return None
        context = getattr(target, "baseline_context", None)
        if (
            context is None
            or getattr(context, "user_id", None) != owner.id
            or getattr(context, "session_id", None)
            != assignment.baseline_session_id
        ):
            return None
        baseline = self._manager.get(assignment.baseline_session_id)
        boundary = _measurement_boundary(baseline) if baseline is not None else None
        if (
            baseline is None
            or baseline.user_id != owner.id
            or baseline.status != DONE
            or not self._manager.coaching_eligible(baseline)
            or boundary is None
            or boundary["club"] != getattr(context, "club", None)
            or boundary["hand"] != getattr(context, "hand", None)
            or boundary["angle"] != getattr(context, "angle", None)
        ):
            return None
        try:
            active_target = self._active_target_provider(
                owner, boundary, float(self._clock())
            )
        except Exception:
            return None
        active_assignment = practice_assignment_from_target(active_target)
        active_context = getattr(active_target, "baseline_context", None)
        if (
            active_assignment is None
            or active_context is None
            or getattr(active_context, "user_id", None) != owner.id
            or getattr(active_context, "club", None) != boundary["club"]
            or getattr(active_context, "hand", None) != boundary["hand"]
            or getattr(active_context, "angle", None) != boundary["angle"]
            or active_assignment.baseline_session_id
            != assignment.baseline_session_id
            or active_assignment.target_fingerprint != assignment.target_fingerprint
            or active_assignment.drill_id != assignment.drill_id
        ):
            return None
        return ProofCycleTargetResponse(
            baseline_session_id=assignment.baseline_session_id,
            target_fingerprint=assignment.target_fingerprint,
            drill_id=assignment.drill_id,
            **boundary,
        )

    def progress(self, context: MobileAuthContext) -> ProgressResponse:
        with self._manager.history_delivery_guard():
            owner = self._current_owner(context)
            grouped: dict[tuple[str, str, str], list[Job]] = {}
            for job in self._manager.list_recent(user_id=owner.id):
                boundary = _measurement_boundary(job)
                if boundary is None:
                    continue
                key = (boundary["club"], boundary["hand"], boundary["angle"])
                grouped.setdefault(key, []).append(job)
            groups = []
            for (club, hand, angle), jobs in grouped.items():
                artifact = None
                artifact_job = None
                if self._cfg.proof_cycle.get("enabled") is True:
                    for job in jobs:
                        if job.status != DONE:
                            continue
                        candidate = self._proof_artifact_provider(job)
                        if candidate is not None:
                            artifact = candidate
                            artifact_job = job
                            break
                target = (
                    self._proof_cycle_target(artifact_job, owner, artifact)
                    if artifact_job is not None
                    else None
                )
                labels = _progress_labels(artifact)
                groups.append(
                    {
                        "club": club,
                        "hand": hand,
                        "angle": angle,
                        "sessions": [
                            serialize_mobile_session(
                                job,
                                owner,
                                queue_position=self._manager.queue_position(job),
                                coaching_eligible=(
                                    self._manager.coaching_eligible(job)
                                    if job.status == DONE
                                    else None
                                ),
                            )
                            for job in jobs
                        ],
                        **labels,
                        "proof_cycle_target": target,
                    }
                )
            return ProgressResponse(groups=groups)

    def today(self, context: MobileAuthContext) -> MobileTodayResponse:
        with self._manager.history_delivery_guard():
            owner = self._current_owner(context)
            jobs = self._manager.list_recent(user_id=owner.id)
            profile = self._users.get_golfer_profile(owner.id)
            latest = jobs[0] if jobs else None
            cohort_day = None
            activated_at = self._manager.earliest_coaching_eligible_created_at(owner.id)
            if activated_at is not None:
                elapsed = max(0.0, float(self._clock()) - activated_at)
                cohort_day = min(365000, int(math.floor(elapsed / 86400.0)))

            raw_brief = None
            safe_brief = None
            practice_plan = []
            if latest is not None and latest.status == DONE:
                boundary = _measurement_boundary(latest)
                if boundary is not None and self._manager.coaching_eligible(latest):
                    raw_brief = self._brief_provider(latest)
                    if raw_brief is not None and not bool(
                        getattr(raw_brief, "refilm_required", False)
                    ):
                        artifact = (
                            self._proof_artifact_provider(latest)
                            if self._cfg.proof_cycle.get("enabled") is True
                            else None
                        )
                        safe_brief = serialize_mobile_brief(
                            raw_brief,
                            boundary,
                            proof_cycle_target=self._proof_cycle_target(
                                latest, owner, artifact
                            ),
                        )
                        practice_plan = serialize_practice_plan(
                            self._practice_plan_provider(raw_brief, profile)
                        )
                elif boundary is not None:
                    safe_brief = _brief_unavailable(
                        boundary,
                        status="refilm_required",
                        message="Capture a clearer matching swing video.",
                    )
            checked = {
                item.session_id
                for item in self._users.list_practice_checkins(owner.id, limit=20)
            }
            return MobileTodayResponse(
                profile=serialize_profile(profile),
                latest_session=(
                    serialize_mobile_session(
                        latest,
                        owner,
                        queue_position=self._manager.queue_position(latest),
                        coaching_eligible=(
                            self._manager.coaching_eligible(latest)
                            if latest.status == DONE
                            else None
                        ),
                    )
                    if latest is not None
                    else None
                ),
                caddie_brief=safe_brief,
                practice_plan=practice_plan,
                practice_checked_in=bool(latest is not None and latest.id in checked),
                cohort_day_since_first_analysis=cohort_day,
            )


def serialize_mobile_session(
    job: Job,
    owner: User,
    *,
    queue_position: int | None,
    coaching_eligible: bool | None,
) -> MobileSessionResponse:
    """Serialize only closed, owned job scalars; diagnostics never enter."""

    if job.user_id != owner.id:
        raise MobileResourceNotFound("Session not found.")
    status = job.status if job.status in {QUEUED, PROCESSING, DONE, FAILED} else FAILED
    club = job.club if job.club in CLUB_LABELS else None
    hand = job.hand if job.hand in {"left", "right"} else None
    angle = job.angle if job.angle in {"face-on", "dtl"} else None
    level = job.level if job.level in LEVEL_LABELS else None
    outcome = None
    if status == DONE:
        outcome = "coaching_ready" if coaching_eligible else "refilm_required"
    return MobileSessionResponse(
        id=job.id,
        status=status,
        created_at=datetime.fromtimestamp(job.created_at, timezone.utc).isoformat(),
        # Source labels are user supplied and may contain a local path. Native
        # v1 deliberately omits them instead of guessing a safe basename.
        source_name=None,
        hand=hand,
        angle=angle,
        club=club,
        level=level,
        fast=bool(job.fast),
        swings_done=max(0, int(job.swings_done)),
        swings_total=max(0, int(job.swings_total)),
        queue_position=(queue_position if status == QUEUED else None),
        report_url=None,
        metrics_url=None,
        outcome=outcome,
        # Task 4A has no durable typed failure classification. Raw worker
        # strings are private diagnostics and must not be guessed into one.
        failure_code=None,
        retryable=False,
        retry_expires_at=None,
        remaining_retry_count=0,
        comparison=None,
    )


def serialize_profile(profile) -> Profile | None:
    if profile is None:
        return None
    return Profile(
        display_name=profile.display_name,
        experience_mode=profile.experience_mode,
        handicap_range=profile.handicap_range,
        primary_goal=profile.primary_goal,
        practice_minutes=profile.practice_minutes,
        sessions_per_week=profile.sessions_per_week,
        handedness=profile.handedness,
        camera_angle=profile.camera_angle,
        preferred_club=profile.preferred_club,
        reduced_motion=profile.reduced_motion,
        marketing_email_opt_in=profile.marketing_email_opt_in,
        is_complete=profile.is_complete,
        updated_at=profile.updated_at,
    )


def serialize_practice_plan(items) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for item in list(items or ())[:3]:
        if not isinstance(item, Mapping) or item.get("minutes") not in {10, 20, 45}:
            continue
        try:
            output.append(
                {
                    "minutes": item["minutes"],
                    "title": _safe_text(item.get("title"), 160, required=True),
                    "detail": _safe_text(item.get("detail"), 500, required=True),
                    "selected": bool(item.get("selected") is True),
                    "drill_name": _safe_text(
                        item.get("drill_name"), 160, required=True
                    ),
                    "aim": _safe_text(item.get("aim"), 500, required=True),
                    "dosage": _safe_text(
                        item.get("dosage"), 320, required=True
                    ),
                    "pass_mark": _safe_text(
                        item.get("pass_mark"), 500, required=True
                    ),
                }
            )
        except MobileResourceNotFound:
            continue
    return output


def _progress_labels(artifact) -> dict[str, str]:
    verdict = getattr(getattr(artifact, "comparison", None), "verdict", None)
    if verdict in {"improved", "holding"}:
        outcome, decision = "improved_and_holding", "continue"
    elif verdict == "early_signal":
        outcome, decision = "early_signal", "continue"
    elif verdict == "needs_attention":
        outcome, decision = "inconclusive", "stop"
    elif verdict == "no_baseline":
        outcome, decision = "inconclusive", "coach_handoff"
    elif verdict in {"inconclusive", "not_comparable"}:
        outcome, decision = "inconclusive", "adjust"
    else:
        outcome, decision = "no_transfer_yet", "continue"
    outcome_labels = {
        "improved_and_holding": "Improved and holding",
        "early_signal": "Early signal",
        "inconclusive": "Inconclusive",
        "no_transfer_yet": "No transfer yet",
    }
    decision_labels = {
        "continue": "Continue",
        "adjust": "Adjust",
        "stop": "Stop",
        "coach_handoff": "Coach handoff",
    }
    view = proof_cycle_view(artifact)
    return {
        "outcome": outcome,
        "decision": decision,
        "outcome_label": outcome_labels[outcome],
        "decision_label": decision_labels[decision],
        "summary": (
            _safe_text(getattr(view, "summary", None), 500)
            if view is not None
            else "No matched transfer result yet."
        )
        or "No matched transfer result yet.",
        "next_step": (
            _safe_text(getattr(view, "next_step", None), 500)
            if view is not None
            else "Continue with the current plan."
        )
        or "Continue with the current plan.",
    }


def _safe_text(value: object, maximum: int, *, required: bool = False) -> str | None:
    if not isinstance(value, str):
        if required:
            raise MobileResourceNotFound("Structured coaching is unavailable.")
        return None
    normalized = " ".join(
        "".join(character for character in value if character.isprintable()).split()
    )[:maximum]
    if not normalized:
        if required:
            raise MobileResourceNotFound("Structured coaching is unavailable.")
        return None
    return normalized


def _measurement_boundary(job: Job) -> dict[str, str] | None:
    if (
        job.club not in CLUB_LABELS
        or job.hand not in {"left", "right"}
        or job.angle not in {"face-on", "dtl"}
    ):
        return None
    return {"club": job.club, "hand": job.hand, "angle": job.angle}


def _brief_unavailable(
    boundary: dict[str, str],
    *,
    status: str,
    message: str,
) -> BriefResponse:
    return BriefResponse(
        status=status,
        priority=None,
        evidence=None,
        confidence="not_available",
        hypothesis=None,
        cue=None,
        prescribed_drill=None,
        measurement_boundary=boundary,
        proof_cycle_target=None,
        message=message,
    )


def serialize_mobile_brief(
    brief,
    boundary: dict[str, str],
    *,
    proof_cycle_target,
) -> BriefResponse:
    """Allowlist one existing Caddie Brief without diagnostic/report fields."""

    drill = getattr(brief, "drill", None)
    if drill is None:
        return _brief_unavailable(
            boundary,
            status="brief_not_ready",
            message="A structured coaching brief is not available.",
        )
    return BriefResponse(
        status="coaching_ready",
        priority={
            "key": _safe_text(getattr(brief, "focus_flag", None), 64),
            "name": _safe_text(getattr(brief, "focus_name", None), 160, required=True),
            "value": _safe_text(getattr(brief, "focus_value", None), 160),
            "benchmark": _safe_text(
                getattr(brief, "benchmark_text", None), 320
            ),
        },
        evidence={
            "strength": _safe_text(getattr(brief, "strength", None), 320),
            "trend": _safe_text(getattr(brief, "trend", None), 320),
            "recurring_sessions": max(
                0, min(1000, int(getattr(brief, "recurring_sessions", 0)))
            ),
            "remaining_issues": max(
                0, min(1000, int(getattr(brief, "remaining_issues", 0)))
            ),
        },
        confidence=(
            "limited" if bool(getattr(brief, "warning", None)) else "high"
        ),
        hypothesis=_safe_text(getattr(brief, "why", None), 500, required=True),
        cue=_safe_text(getattr(brief, "fix", None), 500, required=True),
        prescribed_drill={
            "id": _safe_text(getattr(drill, "id", None), 128, required=True),
            "name": _safe_text(getattr(drill, "name", None), 160, required=True),
            "aim": _safe_text(getattr(drill, "aim", None), 500, required=True),
            "dosage": _safe_text(
                getattr(drill, "dosage", None), 320, required=True
            ),
            "pass_mark": _safe_text(
                getattr(drill, "success_metric", None), 500, required=True
            ),
        },
        measurement_boundary=boundary,
        proof_cycle_target=proof_cycle_target,
        message=None,
    )
