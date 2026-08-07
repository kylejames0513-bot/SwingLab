"""Versioned, transport-safe contracts shared by generated native clients."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


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
    display_name: str | None = None


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
    capture = "capture"
    processing = "processing"
    retry_exhausted = "retry_exhausted"
    unknown = "unknown"


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
    id: str
    status: str
    created_at: str
    source_name: str | None
    hand: Literal["left", "right"]
    angle: Literal["face-on", "dtl"]
    club: str | None
    level: str | None
    fast: bool
    swings_done: int
    swings_total: int
    queue_position: int | None
    report_url: str | None = None
    metrics_url: str | None = None
    comparison: ComparisonTarget | None = None
    failure: AnalysisFailure | None = None


class MobileTodayResponse(ContractModel):
    resource_version: Literal[1] = 1
    profile: Profile | None
    latest_session: MobileSessionResponse | None
    caddie_brief: dict[str, Any] | None
    practice_plan: list[dict[str, Any]]
    practice_checked_in: bool


class BriefResponse(ContractModel):
    resource_version: Literal[1] = 1
    caddie_brief: dict[str, Any]


class ComparableContextGroupResponse(ContractModel):
    resource_version: Literal[1] = 1
    sessions: list[MobileSessionResponse]


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
    resource_version: Literal[1] = 1
    target: ProofCycleTarget | None


class ProgressMetric(ContractModel):
    metric: MetricName
    current: float | None
    baseline: float | None
    trend: Literal["improving", "holding", "needs_attention", "unknown"]


class ProgressPayload(ContractModel):
    context: ComparisonTarget
    metrics: list[ProgressMetric]
    completed_sessions: int


class ProgressResponse(ContractModel):
    resource_version: Literal[1] = 1
    progress: ProgressPayload


class PracticeCheckin(ContractModel):
    session_id: str
    completed_at: float


class PracticeCheckinResponse(ContractModel):
    resource_version: Literal[1] = 1
    checkin: PracticeCheckin | None = None
    checkins: list[PracticeCheckin] | None = None


class Capabilities(ContractModel):
    native_auth: bool
    upload: bool
    push: bool
    proof_cycle: bool


class CapabilitiesResponse(ContractModel):
    resource_version: Literal[1] = 1
    capabilities: Capabilities


class NativeAuthStartRequest(ContractModel):
    email: str


class NativeAuthStartResponse(ContractModel):
    resource_version: Literal[1] = 1
    challenge_id: str
    expires_at: float


class NativeAuthExchangeRequest(ContractModel):
    challenge_id: str
    code: str


class NativeAuthExchangeSuccessResponse(ContractModel):
    resource_version: Literal[1] = 1
    status: Literal["authenticated"]
    access_token: str
    expires_at: float


class NativeAuthExchangePendingResponse(ContractModel):
    resource_version: Literal[1] = 1
    status: Literal["pending"]
    retry_after_seconds: int


class NativeSignOutResponse(ContractModel):
    resource_version: Literal[1] = 1
    signed_out: Literal[True] = True


class AnalysisRetryRequest(ContractModel):
    session_id: str


class AnalysisRetryResponse(ContractModel):
    resource_version: Literal[1] = 1
    session: MobileSessionResponse


class UploadCreateRequest(ContractModel):
    source_name: str
    hand: Literal["left", "right"] = "right"
    angle: Literal["face-on", "dtl"] = "face-on"
    club: str
    level: str | None = None


class UploadCreateResponse(ContractModel):
    resource_version: Literal[1] = 1
    session: MobileSessionResponse


class UploadStatusResponse(ContractModel):
    resource_version: Literal[1] = 1
    session: MobileSessionResponse


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


class MobileTokenIssueResponse(ContractModel):
    resource_version: Literal[1] = 1
    token: str
    device: MobileTokenMetadata


class MobileTokenRevokeResponse(ContractModel):
    resource_version: Literal[1] = 1
    revoked: Literal[True] = True


class NativeEventResponse(ContractModel):
    accepted: Literal[True] = True
