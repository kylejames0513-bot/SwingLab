# Cursor agent resume script: CaddieInsight Expo coaching client

Use this file as the operating prompt for the next coding agent working on the
native Expo app under `mobile/`.

## Frozen checkpoint

- Branch: `cursor/caddieinsight-mobile-gate-4b-909d`
- Main plan: `docs/superpowers/plans/2026-08-06-caddieinsight-expo-coaching-client.md`
- Tip SHA: `c37582635e3e8c01eeb45e79127f858e2ccab516` (update after each push)

Do not deploy, publish, change Shopify/Railway/store settings, or mutate any live
provider. Keep CaddieInsight customer-facing contracts authoritative on the
backend.

## Work completed

### Task 1 — Expo SDK 57 scaffold (this tip)

- Created `mobile/` from Expo SDK 57 template (`src/app` layout retained; plan
  paths that say `mobile/app/` map to `mobile/src/app/` here).
- Scripts: `start`, `android`, `ios`, `lint`, `typecheck`, `test`, `test:watch`,
  `expo:doctor`, `api:generate`, `api:check`. Node engines `>=22.13 <23`,
  `.nvmrc` `22.13.0`.
- `app.config.ts`: CaddieInsight / caddieinsight, env schemes + bundle IDs,
  `ios.supportsTablet=false`, `expo-build-properties` iOS 16.4 / Android min 24,
  camera/mic purpose strings, typedRoutes, privacy + phone form-factor plugins,
  repository-owned `assets/*`.
- `src/config/env.ts`, orientation controller, fail-closed
  `PrivateNoBackupStorage` + `modules/caddieinsight-storage` stub.
- Privacy manifest + plugins; Jest/ESLint/strict TS (`noUncheckedIndexedAccess`).
- Brand PNG placeholders + tests (PNG IHDR dimensions; not full pixel decode).
- Root `.gitignore` / README mobile note.

**Verified locally:** `npm test -- --runInBand` (23 pass), `npm run typecheck`,
`npm run lint`.

### Intentional stubs / deferred

- Native storage module: TS bridge + Swift/Kotlin stubs that throw until
  Application Support / `noBackupFilesDir` + protectAndVerify are implemented.
- Full asset alpha/opaque decode tests deferred (dimension + hash vs template).
- Native prebuild/compile and autolinking verify deferred (no macOS/Android SDK
  in this environment).
- `api:generate` / `api:check` scripts exist; generated schema is Task 2.

## Next task

Continue with **Task 2** of the Expo coaching client plan: OpenAPI types +
authenticated transport, AuthStore, SecureStore, environment boundary.
