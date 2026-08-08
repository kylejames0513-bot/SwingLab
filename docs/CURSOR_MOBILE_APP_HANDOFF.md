# Cursor agent resume script: CaddieInsight Expo coaching client

Use this file as the operating prompt for the next coding agent working on the
native Expo app under `mobile/`.

## Frozen checkpoint

- Branch: `cursor/caddieinsight-mobile-gate-4b-909d`
- Main plan: `docs/superpowers/plans/2026-08-06-caddieinsight-expo-coaching-client.md`
- Tip SHA: update after push

Do not deploy, publish, change Shopify/Railway/store settings, or mutate any live
provider.

## Work completed (client code complete for local finish)

### Tasks 1–9 (Expo Plan 2) — in-repo vertical

1. Scaffold (SDK 57)
2. Typed API transport + AuthStore + EnvironmentBoundary
3. Email / review PKCE sign-in + AuthBoundary routes
4. Coach shell, design tokens, Today, onboarding
5. Capture source / guided camera / import / preflight / review
6. Resumable upload machine + `hashAndUpload` + UploadScreen + analysis status
7. Brief, Practice (10/20/45), Progress, matched re-film params into capture
8. Push registration helpers, deep links, More (profile/pro/devices/privacy/gear),
   privacy step-up + export/reset/delete pending ops, export downloader JS facade
9. Foreground reconcile, a11y minima, Maestro smoke YAMLs, contract tests

**Verified:** `npm test -- --runInBand` (82 pass), `npm run typecheck`,
`npm run lint`, `npm run api:check`.

## What you finish locally (device / store)

These cannot be completed in this Linux cloud agent:

- Real `caddieinsight-storage` Application Support / `noBackupFilesDir` +
  `protectAndVerify` + native `ExportDownloader` stream (Swift/Kotlin stubs remain)
- `npx expo prebuild` / `run:ios` / `run:android` on a phone
- Maestro e2e against a live/dev API (`mobile/e2e/*.yaml` are scaffolds)
- EAS build, App Store / Play submission, Mac/Vision / tablet catalog readbacks
- Plan 3 native IAP / entitlements UI; Plan 4 beta store release checklist

## Next if extending further

- Implement native ExportDownloader + storage module on device
- Wire store-review privacy step-up password UI end-to-end
- Fill UploadScreen resume-from-paused path after AppState
- Point `EXPO_PUBLIC_*` at your staging API and run Maestro coaching-loop
