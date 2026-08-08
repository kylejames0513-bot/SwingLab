# Cursor agent resume script: CaddieInsight Expo coaching client

Use this file as the operating prompt for the next coding agent working on the
native Expo app under `mobile/`.

## Frozen checkpoint

- Branch: `cursor/caddieinsight-mobile-gate-4b-909d`
- Main plan: `docs/superpowers/plans/2026-08-06-caddieinsight-expo-coaching-client.md`
- Tip SHA: update after push (Tasks 2–4 landed)

Do not deploy, publish, change Shopify/Railway/store settings, or mutate any live
provider. Keep CaddieInsight customer-facing contracts authoritative on the
backend.

## Work completed

### Task 1 — Expo SDK 57 scaffold

- `mobile/` from Expo SDK 57 (`src/app` layout; plan `mobile/app/` → `mobile/src/app/`).
- Env, orientation, PrivateNoBackupStorage stub, privacy/form-factor plugins, brand assets.

### Task 2 — Typed API transport

- Hoisted OpenAPI `$defs` in `scripts/export_openapi.py` so `openapi-typescript` works.
- Generated `mobile/src/api/schema.generated.ts` + `api:generate` / `api:check`.
- App identity headers, `apiRequest` / `apiRequestWithStatus`, AppError translation,
  QueryClient, CredentialStore/SecureStore inventory, AuthStore (incl. 202 sign-out
  pending), PrivateCache, EnvironmentBoundary.

### Task 3 — Email / review sign-in

- PKCE S256, installation UUID, email start/exchange + review start/exchange API.
- EmailSignInScreen, ReviewAccessScreen, AuthCallbackScreen, AuthBoundary, signOut helper.
- Routes: `(auth)/`, `(auth)/review`, `app/auth/callback`.

### Task 4 — Coach-first shell

- Design tokens/theme + UI primitives.
- Tabs Today / Practice / Analyze / Progress / More; onboarding ProfileForm; Today screen
  via `GET /api/v1/mobile/today`; capture placeholder route.

**Verified locally:** `npm test -- --runInBand` (59 pass), `npm run typecheck`,
`npm run lint` (warnings only).

## Intentional stubs / deferred

- Native storage Swift/Kotlin still throw until Application Support / noBackup is real.
- Capture guided camera / resumable upload / Brief-Practice loop / push / privacy export
  (Expo Tasks 5–9) not done.
- Plans 3–4 (entitlements, store release) later; no deploy/submit.

## Next task

Continue with **Task 5** of the Expo coaching client plan: guided recording,
camera-roll import, and bounded preflight (then Task 6 resumable upload).
