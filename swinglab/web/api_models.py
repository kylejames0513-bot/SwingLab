"""Declared response shapes for the ``/api/v1`` surface.

The twelve-odd JSON handlers in :mod:`swinglab.web.app` return hand-built
dicts through ``JSONResponse``. That works, and it documents nothing:
``docs/api/openapi-v1.json`` had accurate paths and no types, so
``mobile/src/api/types.ts`` was hand-written from reading the server source.
Two hand-maintained descriptions of one wire format drift, and nothing fails
when they do — the client just starts reading a field the server stopped
sending.

These models are the single declared description. They are attached to the
routes as ``response_model`` so the exported OpenAPI carries real types, and
they are checked against **actual responses** by
``tests/test_api_response_contract.py``.

Two deliberate choices:

**Nothing validates at runtime.** FastAPI skips response validation when a
handler returns a ``Response`` object, and these handlers must keep returning
``JSONResponse`` because that is where the ``Cache-Control: no-store`` header
is applied — the privacy guarantee that reports and identity never sit in a
cache. Rather than move that header to keep a validator happy, the contract is
enforced in tests against real responses. A schema a customer's request can
crash is a worse trade than a schema CI proves.

**Every model forbids extra keys.** That is the half that catches drift. A new
key added to a payload without being declared here fails the contract test
immediately, which is the exact failure that silently shipped before.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    """Base: unknown keys are a contract breach, not something to tolerate."""

    model_config = ConfigDict(extra="forbid")


# -- identity and profile ----------------------------------------------------

class GolferProfile(ApiModel):
    display_name: str | None = None
    experience_mode: str | None = None
    handicap_range: str | None = None
    primary_goal: str | None = None
    practice_minutes: int | None = None
    sessions_per_week: int | None = None
    handedness: str | None = None
    camera_angle: str | None = None
    preferred_club: str | None = None
    reduced_motion: bool | None = None
    marketing_email_opt_in: bool | None = None
    is_complete: bool | None = None
    updated_at: float | str | None = None


class Identity(ApiModel):
    id: str
    email: str
    email_verified: bool
    history_epoch: int
    shopify_customer_linked: bool
    shopify_account_state: str | None = None


class MeResponse(ApiModel):
    resource_version: int
    identity: Identity
    profile: GolferProfile | None = None


class ProfileResponse(ApiModel):
    resource_version: int
    profile: GolferProfile | None = None


# -- device tokens -----------------------------------------------------------

class MobileToken(ApiModel):
    """Management metadata only. The raw credential is never in this shape —
    it appears once, in MobileTokenIssueResponse, and never again."""

    selector: str
    label: str
    created_at: float | str | None = None
    last_used_at: float | str | None = None
    expires_at: float | str | None = None
    revoked_at: float | str | None = None
    active: bool


class MobileTokenListResponse(ApiModel):
    resource_version: int
    tokens: list[MobileToken]


class MobileTokenIssueResponse(ApiModel):
    resource_version: int
    token: str
    device: MobileToken


class MobileTokenRevokeResponse(ApiModel):
    resource_version: int
    revoked: bool


# -- sessions ----------------------------------------------------------------

class Session(ApiModel):
    """Job.as_dict() plus the four keys api_payload adds once an analysis is
    finished. Those four are optional because they are genuinely absent from
    a queued or failed session, not because their type is unclear."""

    resource_version: int
    id: str
    status: str
    created_at: str
    source_name: str | None = None
    hand: str
    angle: str
    club: str | None = None
    level: str | None = None
    fast: bool
    log: list[str]
    error: str | None = None
    report: str | None = None
    swings_done: int
    swings_total: int
    queue_position: int | None = None
    coaching_eligible: bool | None = None
    outcome: str | None = None
    report_url: str | None = None
    metrics_url: str | None = None


class SessionListResponse(ApiModel):
    resource_version: int
    sessions: list[Session]


# -- the Caddie Brief --------------------------------------------------------

class BriefFocus(ApiModel):
    key: str | None = None
    name: str | None = None
    value: str | None = None
    benchmark: str | None = None
    why: str | None = None
    cue: str | None = None


class BriefDrill(ApiModel):
    id: str
    name: str
    aim: str
    dosage: str
    pass_mark: str


class CaddieBrief(ApiModel):
    version: int
    focus: BriefFocus
    drill: BriefDrill | None = None
    trend: str | None = None
    warning: str | None = None
    refilm_required: bool
    recurring_sessions: int | None = None
    remaining_issues: int | None = None


class SessionBriefResponse(ApiModel):
    resource_version: int
    caddie_brief: CaddieBrief | None = None


# -- practice ----------------------------------------------------------------

class PracticeChoice(ApiModel):
    minutes: int
    title: str
    detail: str
    selected: bool
    drill_name: str
    aim: str
    dosage: str
    pass_mark: str


class PracticeCheckin(ApiModel):
    session_id: str
    completed_at: float | str | None = None


class PracticeCheckinListResponse(ApiModel):
    resource_version: int
    checkins: list[PracticeCheckin]


class PracticeCheckinResponse(ApiModel):
    resource_version: int
    checkin: PracticeCheckin


# -- today -------------------------------------------------------------------

class TodayResponse(ApiModel):
    resource_version: int
    profile: GolferProfile | None = None
    latest_session: Session | None = None
    caddie_brief: CaddieBrief | None = None
    practice_plan: list[PracticeChoice]
    practice_checked_in: bool


# -- telemetry ---------------------------------------------------------------

class EventAccepted(ApiModel):
    accepted: bool
