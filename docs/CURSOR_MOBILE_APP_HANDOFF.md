# Cursor agent resume script: CaddieInsight Expo coaching client

Use this file as the operating prompt for the next coding agent working on the
native Expo app under `mobile/`.

## Frozen checkpoint

- Branch: `cursor/caddieinsight-mobile-gate-4b-909d`
- Main plan: `docs/superpowers/plans/2026-08-06-caddieinsight-expo-coaching-client.md`
- Tip SHA: update after push

Do not deploy, publish, change Shopify/Railway/store settings, or mutate any live
provider. Keep CaddieInsight customer-facing contracts authoritative on the
backend.

## Work completed

### Tasks 1–5
Expo scaffold, typed transport/auth, email/review sign-in, coach shell/Today,
capture source + guided camera + preflight + bounded chunk reader.

### Task 6 — Upload machine core (partial)
- `uploadTypes`, pure `uploadMachine` transitions + `reconcileUpload`
- `uploadApi` (reserve/status/chunk/complete/abort + mobile session GET)
- `uploadRepository`, `fileSha256Hex`, `appLifecycle` foreground policy
- `useAnalysisJob` + `analysis/[sessionId]` status screen
- Tests for legal/illegal transitions and repository persistence (no bearer)

**Still needed for Task 6:** full `hashAndUpload` generator loop, UploadScreen
wired from capture, chunk transport tests, AppState pause/resume integration
tests, Maestro upload-resume e2e, native FileHandle adapter.

**Verified:** `npm test -- --runInBand` (71 pass), `npm run typecheck`.

## Next task

Finish **Task 6** upload loop end-to-end, then Tasks 7–9 (Brief/Practice/re-film,
push/More/privacy, reliability/a11y gate). Native storage module still stubs.
