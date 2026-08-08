/**
 * Response shapes for the owned mobile surface.
 *
 * These are HAND-WRITTEN, and that is a deliberate stopgap rather than a
 * preference. `docs/api/openapi-v1.json` is exported from the running app and
 * its paths and parameters are accurate, but the `/api/` handlers return bare
 * `JSONResponse` rather than declared response models, so not one operation
 * carries a 200 schema. Generating types from it today yields route names with
 * `unknown` bodies.
 *
 * They mirror the contract documented in `docs/mobile-api-tokens.md`. The risk
 * is the ordinary one for hand-written types: the server can change shape
 * without anything here failing to compile. `tests/test_openapi_export.py`
 * narrows that by asserting this file and the exported document agree on which
 * routes exist — it cannot check field-level drift.
 *
 * The fix is to give the `/api/` handlers Pydantic response models, then
 * replace this file with `openapi-typescript` output. Until then treat every
 * field here as a claim about the server, not a guarantee from it.
 */

/** Every owned resource is nullable-by-default: a field the server omits must
 *  not crash a screen. Prefer optional properties over required ones. */

export interface Identity {
  /** Bump this and the client must drop cached session/report/practice state.
   *  Advanced by "Delete swing history / Start over" in the browser, which
   *  deliberately does NOT revoke device tokens or advance auth_epoch. */
  history_epoch: number;
  email?: string;
  display_name?: string | null;
}

export interface Membership {
  is_pro: boolean;
  plan?: string | null;
  analyses_left?: number | null;
}

export interface Me {
  identity: Identity;
  membership?: Membership;
}

export interface GolferProfile {
  display_name?: string | null;
  experience_mode?: string | null;
  handicap_range?: string | null;
  primary_goal?: string | null;
  practice_minutes?: number | null;
  sessions_per_week?: number | null;
  handedness?: string | null;
  camera_angle?: string | null;
  preferred_club?: string | null;
  is_complete?: boolean;
}

/** Server-side job state. The client branches on this and never infers
 *  readiness from the presence of other fields. */
export type SessionState =
  | "queued"
  | "processing"
  | "done"
  | "failed"
  | "coaching_ready"
  | "refilm";

export interface SessionSummary {
  id: string;
  state: SessionState;
  created_at?: string | null;
  club?: string | null;
  angle?: string | null;
  /** Present only once a report exists. Absent is normal, not an error. */
  report_url?: string | null;
}

export interface SessionList {
  sessions: SessionSummary[];
}

export interface CaddieBrief {
  priority?: string | null;
  cue?: string | null;
  drill?: string | null;
  refilm_target?: string | null;
}

export interface TodayView {
  headline?: string | null;
  preferred_club?: string | null;
  practice_minutes?: number | null;
  membership?: Membership;
  recent?: SessionSummary[];
}

export interface PracticeCheckin {
  id?: string;
  created_at?: string | null;
  outcome?: string | null;
}

/** Device-token lifecycle metadata. The raw credential is returned exactly
 *  once, by POST, in a no-store response — it is never readable again. */
export interface MobileTokenRecord {
  selector: string;
  label?: string | null;
  created_at?: string | null;
  last_used_at?: string | null;
  expires_at?: string | null;
  revoked_at?: string | null;
  active: boolean;
}

export interface MobileTokenIssued extends MobileTokenRecord {
  /** Save straight to the keychain. Present only on the issuing response. */
  token: string;
}

/** The paths this client calls. Kept as a const map so the export test can
 *  compare it against the OpenAPI document without parsing TypeScript. */
export const API_ROUTES = {
  me: "/api/v1/me",
  profile: "/api/v1/profile",
  today: "/api/v1/today",
  sessions: "/api/v1/sessions",
  session: "/api/v1/sessions/{job_id}",
  brief: "/api/v1/sessions/{job_id}/brief",
  practiceCheckins: "/api/v1/practice-checkins",
  mobileTokens: "/api/v1/mobile-tokens",
  mobileToken: "/api/v1/mobile-tokens/{selector}",
  events: "/api/v1/events",
} as const;

export type ApiRoute = (typeof API_ROUTES)[keyof typeof API_ROUTES];
