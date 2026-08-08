/**
 * The owned-surface HTTP client.
 *
 * Two rules the server enforces and this client must not fight:
 *
 * 1. A bearer credential reaches only the owned mobile surface. It cannot
 *    mint, list, or revoke device tokens — those require a cookie-authenticated
 *    same-origin browser session. `issueToken` therefore does not exist here.
 * 2. A malformed or invalid Authorization header fails with 401 and never
 *    falls back to a cookie. So a 401 always means "this credential is done",
 *    and the only correct response is to drop it and re-connect.
 */

import Constants from "expo-constants";

import { clearToken, readToken } from "../auth/token";
import type {
  CaddieBrief,
  GolferProfile,
  Me,
  MobileTokenRecord,
  SessionSummary,
  TodayView,
} from "./types";

/** Set via app.json → expo.extra.apiBaseUrl, or EXPO_PUBLIC_API_BASE_URL. */
export function apiBaseUrl(): string {
  const extra = Constants.expoConfig?.extra as
    | { apiBaseUrl?: string }
    | undefined;
  const configured =
    process.env.EXPO_PUBLIC_API_BASE_URL ?? extra?.apiBaseUrl ?? "";
  return configured.replace(/\/+$/, "");
}

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Raised after the stored credential has been cleared, so a caller that
 *  catches this can route straight to the connect screen without re-checking
 *  storage. */
export class UnauthorizedError extends ApiError {
  constructor(message = "This device is no longer connected.") {
    super(401, message);
    this.name = "UnauthorizedError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const base = apiBaseUrl();
  if (!base) {
    throw new ApiError(0, "No API base URL is configured for this build.");
  }

  const token = await readToken();
  if (!token) throw new UnauthorizedError("This device is not connected yet.");

  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  headers.set("Accept", "application/json");

  const response = await fetch(`${base}${path}`, { ...init, headers });

  if (response.status === 401) {
    // The token is revoked, expired, or issued under a superseded auth epoch.
    // None of those recover by retrying, so drop it here rather than leaving
    // a dead credential in the keychain to fail again on the next screen.
    await clearToken();
    throw new UnauthorizedError();
  }

  if (!response.ok) {
    // The server sends {"detail": "..."} for handled errors; fall back to the
    // status line rather than surfacing an empty message.
    let detail = `Request failed (${response.status}).`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(response.status, detail);
  }

  return (await response.json()) as T;
}

export const api = {
  me: () => request<Me>("/api/v1/me"),
  profile: () => request<GolferProfile>("/api/v1/profile"),
  today: () => request<TodayView>("/api/v1/today"),
  sessions: () => request<{ sessions: SessionSummary[] }>("/api/v1/sessions"),
  session: (jobId: string) =>
    request<SessionSummary>(`/api/v1/sessions/${encodeURIComponent(jobId)}`),
  brief: (jobId: string) =>
    request<CaddieBrief>(
      `/api/v1/sessions/${encodeURIComponent(jobId)}/brief`,
    ),
  /** Read-only. Issuing and revoking require the browser session. */
  devices: () => request<MobileTokenRecord[]>("/api/v1/mobile-tokens"),
};

/**
 * Private media needs the same bearer, so it cannot be handed to a plain
 * <Image source={{uri}}>. Callers pass these headers to expo-image/expo-video,
 * which must also have caching disabled — the URLs are short-lived and
 * owner-scoped, and a cached copy outlives the grant that authorised it.
 */
export async function mediaHeaders(): Promise<Record<string, string>> {
  const token = await readToken();
  if (!token) throw new UnauthorizedError("This device is not connected yet.");
  return { Authorization: `Bearer ${token}` };
}
