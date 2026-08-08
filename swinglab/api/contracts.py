"""Versioned, transport-safe contracts shared by generated native clients."""

from __future__ import annotations

import unicodedata
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


RESOURCE_VERSION: Literal[1] = 1


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class APIError(ContractModel):
    resource_version: Literal[1] = 1
    code: str
    message: str
    retryable: bool = False
    reference_id: str | None = None


class Identity(ContractModel):
    id: str
    email: str
    email_verified: bool
    history_epoch: int
    shopify_customer_linked: bool
    shopify_account_state: str | None


class Profile(ContractModel):
    display_name: str | None
    experience_mode: str
    handicap_range: str | None
    primary_goal: str | None
    practice_minutes: int
    sessions_per_week: int
    handedness: Literal["left", "right"]
    camera_angle: Literal["face-on", "dtl"]
    preferred_club: str | None
    reduced_motion: bool
    marketing_email_opt_in: bool
    is_complete: bool
    updated_at: float


class IdentityResponse(ContractModel):
    resource_version: Literal[1] = 1
    identity: Identity
    profile: Profile | None


class ProfileResponse(ContractModel):
    resource_version: Literal[1] = 1
    profile: Profile | None


class ProfileUpdateRequest(ContractModel):
    """Closed native profile write body; legacy PUT /api/v1/profile stays manual."""

    display_name: str
    experience_mode: Literal["start", "improve", "compete"]
    handicap_range: (
        Literal[
            "new_to_golf",
            "30_plus",
            "20_to_29",
            "15_to_19",
            "10_to_14",
            "under_10",
            "prefer_not_to_say",
        ]
        | None
    )
    primary_goal: Literal[
        "consistency",
        "tempo",
        "weight_shift",
        "strike_quality",
        "balance",
        "confidence",
    ]
    practice_minutes: Literal[10, 20, 45]
    sessions_per_week: Literal[1, 2, 3]
    handedness: Literal["right", "left"]
    camera_angle: Literal["face-on", "dtl"]
    preferred_club: Literal[
        "driver", "fairway-wood", "hybrid", "iron", "wedge"
    ]
    reduced_motion: bool = Field(strict=True)
    marketing_email_opt_in: bool = Field(strict=True)
    expected_history_epoch: int = Field(ge=0, strict=True)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        if any(
            character in "\r\n"
            or unicodedata.category(character).startswith("C")
            or unicodedata.category(character) in {"Zl", "Zp"}
            for character in value
        ):
            raise ValueError("Your display name contains invalid characters.")
        normalized = " ".join(unicodedata.normalize("NFKC", value).split())
        if not normalized:
            raise ValueError("Enter the name you want CaddieInsight to use.")
        if len(normalized) > 50:
            raise ValueError(
                "Your display name must be 50 characters or fewer."
            )
        if any(
            character in "\r\n"
            or unicodedata.category(character).startswith("C")
            or unicodedata.category(character) in {"Zl", "Zp"}
            for character in normalized
        ):
            raise ValueError("Your display name contains invalid characters.")
        return normalized


class LegacySessionResponse(ContractModel):
    """Compatibility shape for existing PWA ``/api/v1/sessions`` routes."""

    resource_version: Literal[1] = 1
    id: str
    status: str
    created_at: str
    source_name: str | None
    hand: str
    angle: str
    club: str | None
    level: str | None
    fast: bool
    log: list[str]
    error: str | None
    report: str | None
    swings_done: int
    swings_total: int
    queue_position: int | None
    coaching_eligible: bool | None = None
    outcome: str | None = None
    report_url: str | None = None
    metrics_url: str | None = None


class LegacyTodayResponse(ContractModel):
    resource_version: Literal[1] = 1
    profile: Profile | None
    latest_session: LegacySessionResponse | None
    caddie_brief: dict[str, Any] | None
    practice_plan: list[dict[str, Any]]
    practice_checked_in: bool


class AnalysisFailureCode(str, Enum):
    video_too_long = "video_too_long"
    capture_no_strike = "capture_no_strike"
    capture_pose_unusable = "capture_pose_unusable"
    media_decode_failed = "media_decode_failed"
    analysis_runtime_unavailable = "analysis_runtime_unavailable"
    analysis_storage_unavailable = "analysis_storage_unavailable"
    analysis_internal_error = "analysis_internal_error"


class AnalysisFailure(ContractModel):
    code: AnalysisFailureCode
    retryable: bool
    message: str


class ComparisonTarget(ContractModel):
    session_id: str
    club: str | None
    hand: Literal["left", "right"]
    camera_angle: Literal["face-on", "dtl"]


class MobileSessionResponse(ContractModel):
    """Allowlisted native session detail; never carries raw job diagnostics."""

    resource_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=64)
    status: Literal["queued", "processing", "done", "failed"]
    created_at: str
    source_name: None = None
    hand: Literal["left", "right"] | None
    angle: Literal["face-on", "dtl"] | None
    club: Literal["driver", "fairway-wood", "hybrid", "iron", "wedge"] | None
    level: Literal["new", "improving", "experienced"] | None
    fast: bool
    swings_done: int = Field(ge=0)
    swings_total: int = Field(ge=0)
    queue_position: int | None = Field(default=None, ge=1)
    report_url: None = None
    metrics_url: None = None
    outcome: Literal["coaching_ready", "refilm_required"] | None = None
    failure_code: AnalysisFailureCode | None = None
    retryable: bool = False
    retry_expires_at: float | None = None
    remaining_retry_count: int = Field(default=0, ge=0)
    comparison: ComparisonTarget | None = None


class MobileSessionsResponse(ContractModel):
    resource_version: Literal[1] = 1
    sessions: list[MobileSessionResponse]


class PracticePlanOptionResponse(ContractModel):
    minutes: Literal[10, 20, 45]
    title: str = Field(min_length=1, max_length=160)
    detail: str = Field(min_length=1, max_length=500)
    selected: bool
    drill_name: str = Field(min_length=1, max_length=160)
    aim: str = Field(min_length=1, max_length=500)
    dosage: str = Field(min_length=1, max_length=320)
    pass_mark: str = Field(min_length=1, max_length=500)


class MobileTodayResponse(ContractModel):
    resource_version: Literal[1] = 1
    profile: Profile | None
    latest_session: MobileSessionResponse | None
    caddie_brief: BriefResponse | None
    practice_plan: list[PracticePlanOptionResponse]
    practice_checked_in: bool
    cohort_day_since_first_analysis: int | None = Field(default=None, ge=0, le=365000)


class LegacyBriefResponse(ContractModel):
    resource_version: Literal[1] = 1
    caddie_brief: dict[str, Any]


class ComparableContextGroupResponse(ContractModel):
    club: Club
    hand: Hand
    angle: CameraAngle
    sessions: list[MobileSessionResponse]
    outcome: Literal[
        "improved_and_holding", "early_signal", "inconclusive", "no_transfer_yet"
    ]
    decision: Literal["continue", "adjust", "stop", "coach_handoff"]
    outcome_label: Literal[
        "Improved and holding", "Early signal", "Inconclusive", "No transfer yet"
    ]
    decision_label: Literal["Continue", "Adjust", "Stop", "Coach handoff"]
    summary: str = Field(min_length=1, max_length=500)
    next_step: str = Field(min_length=1, max_length=500)
    proof_cycle_target: ProofCycleTargetResponse | None


MetricName = Literal[
    "backswing_s",
    "downswing_s",
    "tempo_ratio",
    "head_sway_backswing_sw",
    "head_sway_downswing_sw",
    "hip_slide_backswing_sw",
    "hip_slide_downswing_sw",
    "head_dip_sw",
    "lead_arm_angle_deg",
    "shoulder_tilt_impact_deg",
    "shoulder_tilt_delta_deg",
    "finish_balance_sw",
]


class ProofCycleContext(ContractModel):
    session_id: str
    club: str
    hand: Literal["left", "right"]
    angle: Literal["face-on", "dtl"]


class ProofCycleMeasurement(ContractModel):
    metric: MetricName
    aggregation: Literal["mean", "std", "worst"]
    value: float | None
    mean: float | None
    std: float | None
    readable_swings: int


class ProofCycleTarget(ContractModel):
    rule_version: Literal[1, 2]
    source_flag: str
    metric: MetricName
    display_name: str
    unit: str
    worse_direction: Literal["higher", "lower"]
    aggregation: Literal["mean", "std", "worst"]
    benchmark_value: float | None
    benchmark_text: str
    drill_ids: list[str]
    drill_names: list[str]
    baseline_context: ProofCycleContext
    baseline: ProofCycleMeasurement
    baseline_completed: bool
    baseline_coaching_eligible: bool
    baseline_warning: str | None


class ProofCycleTargetResponse(ContractModel):
    baseline_session_id: str = Field(min_length=1, max_length=64)
    target_fingerprint: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    drill_id: str = Field(min_length=1, max_length=128)
    club: Club
    hand: Hand
    angle: CameraAngle


class BriefPriorityResponse(ContractModel):
    key: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=160)
    value: str | None = Field(default=None, max_length=160)
    benchmark: str | None = Field(default=None, max_length=320)


class BriefEvidenceResponse(ContractModel):
    strength: str | None = Field(default=None, max_length=320)
    trend: str | None = Field(default=None, max_length=320)
    recurring_sessions: int = Field(ge=0, le=1000)
    remaining_issues: int = Field(ge=0, le=1000)


class PrescribedDrillResponse(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=160)
    aim: str = Field(min_length=1, max_length=500)
    dosage: str = Field(min_length=1, max_length=320)
    pass_mark: str = Field(min_length=1, max_length=500)


class MeasurementBoundaryResponse(ContractModel):
    club: Club
    hand: Hand
    angle: CameraAngle


class BriefResponse(ContractModel):
    resource_version: Literal[1] = 1
    status: Literal["coaching_ready", "brief_not_ready", "refilm_required"]
    priority: BriefPriorityResponse | None
    evidence: BriefEvidenceResponse | None
    confidence: Literal["high", "limited", "not_available"]
    hypothesis: str | None = Field(default=None, max_length=500)
    cue: str | None = Field(default=None, max_length=500)
    prescribed_drill: PrescribedDrillResponse | None
    measurement_boundary: MeasurementBoundaryResponse
    proof_cycle_target: ProofCycleTargetResponse | None
    message: str | None = Field(default=None, max_length=320)


class ProgressResponse(ContractModel):
    resource_version: Literal[1] = 1
    groups: list[ComparableContextGroupResponse]


class PracticeCheckin(ContractModel):
    session_id: str
    completed_at: float


class PracticeCheckinResponse(ContractModel):
    resource_version: Literal[1] = 1
    checkin: PracticeCheckin | None = None
    checkins: list[PracticeCheckin] | None = None


class PracticeEvidenceRequest(ContractModel):
    """Closed native practice-evidence body; legacy check-ins stay `{session_id}`."""

    baseline_session_id: str = Field(min_length=1, max_length=128)
    target_fingerprint: str = Field(min_length=64, max_length=64)
    drill_id: str = Field(min_length=1, max_length=128)
    minutes: Literal[10, 20, 45]
    outcome: Literal["completed", "still_working"]
    reps: int = Field(ge=1, le=300, strict=True)
    feel: Literal["easier", "same", "harder"] | None
    relative_strike: Literal["better", "same", "worse", "unknown"] | None
    start_line: Literal["left", "target", "right", "unknown"] | None
    miss_pattern: (
        Literal[
            "left",
            "right",
            "thin",
            "fat",
            "heel",
            "toe",
            "mixed",
            "none",
            "unknown",
        ]
        | None
    )
    expected_history_epoch: int = Field(ge=0, strict=True)


class PracticeEvidenceReceipt(ContractModel):
    resource_version: Literal[1] = 1
    receipt_id: str
    baseline_session_id: str
    target_fingerprint: str
    drill_id: str
    minutes: Literal[10, 20, 45]
    outcome: Literal["completed", "still_working"]
    reps: int
    feel: Literal["easier", "same", "harder"] | None
    relative_strike: Literal["better", "same", "worse", "unknown"] | None
    start_line: Literal["left", "target", "right", "unknown"] | None
    miss_pattern: (
        Literal[
            "left",
            "right",
            "thin",
            "fat",
            "heel",
            "toe",
            "mixed",
            "none",
            "unknown",
        ]
        | None
    )
    completed_at: float
    completed_day: int


Club = Literal["driver", "fairway-wood", "hybrid", "iron", "wedge"]
Hand = Literal["left", "right"]
CameraAngle = Literal["face-on", "dtl"]
AnalysisState = Literal[
    "queued",
    "processing",
    "done",
    "failed",
]


class UploadCapabilities(ContractModel):
    max_bytes: int = Field(ge=1)
    max_video_seconds: int = Field(ge=1)
    chunk_bytes: int = Field(ge=1)
    active_limit: int = Field(ge=1)
    allowed_suffixes: list[Literal[".avi", ".m4v", ".mkv", ".mov", ".mp4"]]


class CanonicalCapabilities(ContractModel):
    hands: list[Hand]
    angles: list[CameraAngle]
    clubs: list[Club]
    analysis_states: list[AnalysisState]


class QuotaCapabilities(ContractModel):
    plan: Literal["free", "pro"]
    monthly_limit: int | None = Field(default=None, ge=0)
    used: int = Field(ge=0)
    remaining: int | None = Field(default=None, ge=0)


class MobileFeatureCapabilities(ContractModel):
    native_auth: bool
    resources: bool
    profile_writes: bool
    practice_writes: bool
    device_management: bool
    resumable_upload: bool
    privacy: bool
    events: bool
    push: bool
    native_billing: bool
    proof_cycle: bool


class Capabilities(ContractModel):
    upload: UploadCapabilities
    canonical: CanonicalCapabilities
    quota: QuotaCapabilities
    features: MobileFeatureCapabilities
    physical_store_url: str | None = Field(default=None, max_length=2048)


class CapabilitiesResponse(ContractModel):
    resource_version: Literal[1] = 1
    capabilities: Capabilities


class NativeAuthStartRequest(ContractModel):
    email: str = Field(min_length=3, max_length=320)
    code_challenge: str = Field(
        min_length=43,
        max_length=43,
        pattern=r"^[A-Za-z0-9_-]{43}$",
    )
    installation_id: str = Field(
        min_length=36,
        max_length=36,
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}$"
        ),
    )
    device_label: str = Field(min_length=1, max_length=80)


class NativeAuthStartResponse(ContractModel):
    resource_version: Literal[1] = 1
    challenge_id: str
    expires_at: float


class NativeAuthExchangeRequest(ContractModel):
    challenge_id: str
    email_code: str = Field(min_length=8, max_length=15)
    code_verifier: str = Field(
        min_length=43,
        max_length=128,
        pattern=r"^[A-Za-z0-9._~-]{43,128}$",
    )


StepUpPurpose = Literal["data_export", "history_reset", "account_delete"]


class StepUpStartRequest(ContractModel):
    """Bearer-only step-up start body; installation binding is server-derived."""

    purpose: StepUpPurpose
    code_challenge: str = Field(
        min_length=43,
        max_length=43,
        pattern=r"^[A-Za-z0-9_-]{43}$",
    )


class StepUpStartResponse(ContractModel):
    resource_version: Literal[1] = 1
    challenge_id: str
    expires_at: float


class StepUpExchangeRequest(ContractModel):
    challenge_id: str = Field(min_length=1, max_length=64)
    email_code: str = Field(min_length=8, max_length=15)
    code_verifier: str = Field(
        min_length=43,
        max_length=128,
        pattern=r"^[A-Za-z0-9._~-]{43,128}$",
    )


class StepUpExchangeResponse(ContractModel):
    resource_version: Literal[1] = 1
    step_up_token: str
    purpose: StepUpPurpose
    expires_at: float


class NativeReviewAuthStartRequest(ContractModel):
    provider: Literal["apple", "google"]
    account: str = Field(min_length=1, max_length=160)
    code_challenge: str = Field(
        min_length=43,
        max_length=43,
        pattern=r"^[A-Za-z0-9_-]{43}$",
    )
    installation_id: str = Field(
        min_length=36,
        max_length=36,
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}$"
        ),
    )
    device_label: str = Field(min_length=1, max_length=80)


class NativeReviewAuthExchangeRequest(ContractModel):
    challenge_id: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)
    code_verifier: str = Field(
        min_length=43,
        max_length=128,
        pattern=r"^[A-Za-z0-9._~-]{43,128}$",
    )


class NativeAuthExchangeSuccessResponse(ContractModel):
    resource_version: Literal[1] = 1
    status: Literal["authenticated"]
    access_token: str
    expires_at: float


class NativeAuthExchangePendingResponse(ContractModel):
    resource_version: Literal[1] = 1
    exchange_id: str
    status: Literal["pending"]
    retry_after_seconds: int


class NativeSignOutResponse(ContractModel):
    resource_version: Literal[1] = 1
    signed_out: Literal[True] = True


class NativeSignOutPendingResponse(ContractModel):
    resource_version: Literal[1] = 1
    status: Literal["pending"]
    retry_after_seconds: int


class AnalysisRetryRequest(ContractModel):
    """Requeue one owned retryable failure at the exact next server attempt."""

    expected_retry_attempt: int = Field(ge=1, le=10, strict=True)


class AnalysisRetryResponse(ContractModel):
    resource_version: Literal[1] = 1
    session: MobileSessionResponse
    retry_attempt: int = Field(ge=1)
    retry_receipt_id: str = Field(min_length=1, max_length=64)


class UploadComparisonMatched(ContractModel):
    """The re-film matches the current owned Proof Cycle assignment exactly."""

    mode: Literal["matched"]
    baseline_session_id: str = Field(min_length=1, max_length=64)
    target_fingerprint: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    drill_id: str = Field(min_length=1, max_length=128)


class UploadComparisonNewContext(ContractModel):
    """A deliberate break: still names the assignment but changes context."""

    mode: Literal["new_context"]
    baseline_session_id: str = Field(min_length=1, max_length=64)
    target_fingerprint: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    drill_id: str = Field(min_length=1, max_length=128)


UploadComparison = Annotated[
    UploadComparisonMatched | UploadComparisonNewContext,
    Field(discriminator="mode"),
]


class UploadCreateRequest(ContractModel):
    """Closed native resumable-upload reservation body."""

    source_name: str = Field(min_length=1, max_length=255)
    file_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    file_bytes: int = Field(ge=1, strict=True)
    club: Club
    hand: Hand = "right"
    angle: CameraAngle = "face-on"
    level: Literal["new", "improving", "experienced"] | None = None
    comparison: UploadComparison | None = None
    expected_history_epoch: int = Field(ge=0, strict=True)


class UploadReservationResponse(ContractModel):
    """Durable acknowledged offset/status for one owned reservation."""

    resource_version: Literal[1] = 1
    upload_id: str = Field(min_length=1, max_length=64)
    status: Literal[
        "pending",
        "finalizing",
        "complete",
        "aborting",
        "aborted",
        "failed",
        "repair_required",
        "source_unavailable_after_restore",
    ]
    offset: int = Field(ge=0)
    file_bytes: int = Field(ge=1)
    chunk_bytes: int = Field(ge=1)
    expires_at: float


class UploadCompleteResponse(ContractModel):
    resource_version: Literal[1] = 1
    job: MobileSessionResponse
    replayed: bool


class PushRegistrationRequest(ContractModel):
    token: str
    platform: Literal["ios", "android"]


class PushRegistrationResponse(ContractModel):
    resource_version: Literal[1] = 1
    registered: Literal[True] = True


class NativeEventRequest(ContractModel):
    event: Literal[
        "landing_view",
        "account_verified",
        "upload_started",
        "upload_completed",
        "brief_viewed",
        "pro_clicked",
        "gear_match_clicked",
        "cart_started",
        "checkout_started",
        "paid_order",
        "fulfillment_updated",
        "repeat_analysis",
    ]
    session_id: str | None = None


class LegacySessionsResponse(ContractModel):
    resource_version: Literal[1] = 1
    sessions: list[LegacySessionResponse]


class MobileTokenMetadata(ContractModel):
    selector: str
    label: str
    created_at: float
    last_used_at: float | None
    expires_at: float
    revoked_at: float | None
    active: bool


class MobileTokenListResponse(ContractModel):
    resource_version: Literal[1] = 1
    tokens: list[MobileTokenMetadata]


class DeviceListResponse(ContractModel):
    resource_version: Literal[1] = 1
    devices: list[MobileTokenMetadata]


class MobileTokenIssueResponse(ContractModel):
    resource_version: Literal[1] = 1
    token: str
    device: MobileTokenMetadata


class MobileTokenRevokeResponse(ContractModel):
    resource_version: Literal[1] = 1
    revoked: Literal[True] = True


class NativeEventResponse(ContractModel):
    accepted: Literal[True] = True
