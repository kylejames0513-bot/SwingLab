# Cursor agent resume script: CaddieInsight Expo coaching client

Use this file as the operating prompt for the next coding agent working on the
native Expo app under `mobile/`.

## Frozen checkpoint

- Branch: `cursor/caddieinsight-mobile-gate-4b-909d`
- Main plan: `docs/superpowers/plans/2026-08-06-caddieinsight-expo-coaching-client.md`
- Tip SHA: `83dfebe64ef0ac68b08b52d90f05597d7f670577` (Tasks 1–5 partial)

Do not deploy, publish, change Shopify/Railway/store settings, or mutate any live
provider. Keep CaddieInsight customer-facing contracts authoritative on the
backend.

## Work completed

### Tasks 1–4

- Expo SDK 57 scaffold, typed OpenAPI transport/AuthStore/EnvironmentBoundary,
  email + review PKCE sign-in, coach tabs, Today, onboarding profile.

### Task 5 — Capture / preflight (partial, green)

- Capture source sheet (camera/import equal), guided camera screen (rear video +
  mic gate + overlay/countdown), library picker, review screen.
- `preflightMedia` against UploadCapabilities; bounded `readBoundedChunk` file
  adapter that refuses whole-file byte APIs.
- Tests: preflight + bounded reader. Native device prebuild/Maestro/500MiB heap
  proof deferred (no iOS/Android SDK here).

**Verified locally:** `npm test -- --runInBand` (66 pass), `npm run typecheck`,
`npm run lint`.

## Next task

**Task 6** — resumable upload + analysis state machine (`uploadMachine`,
`boundedFileReader` wired to real Expo FileHandle, session polling, UploadScreen).
Then Tasks 7–9 (Brief/Practice/re-film, push/More/privacy, cache/a11y/reliability).

Native storage Swift/Kotlin stubs still throw until Application Support /
`noBackupFilesDir` + protectAndVerify are implemented on device.
