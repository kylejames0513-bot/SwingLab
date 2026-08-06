# CaddieInsight Expo Coaching Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one native iOS/Android CaddieInsight client that completes the coach-first analysis → Brief → practice → matched re-film loop while keeping all coaching, quota, identity, and entitlement truth on the existing backend.

**Architecture:** A strict TypeScript Expo SDK 57 app lives in `mobile/`. Expo Router composes the five destinations; TanStack Query owns in-memory server state; a small typed transport owns bearer auth, retries, error translation, and `history_epoch` invalidation. SecureStore holds the sole bearer secret and stable installation UUID. A tiny local Expo storage module places staged media in iOS Application Support with backup exclusion and Android `noBackupFilesDir`; an audited Expo `FileHandle` reader seeks with `offset` and calls `readBytes(chunkLength)` so only one bounded upload chunk enters JavaScript memory. Feature modules contain device/presentation behavior only.

**Tech Stack:** Node 22.13+, npm lockfile, Expo SDK 57 / React Native 0.86 / React 19.2, Expo Router, TypeScript strict mode, TanStack Query, `openapi-typescript` + `openapi-fetch`, Expo Camera/ImagePicker/FileSystem/SecureStore/Notifications/Linking/Network/Video/WebBrowser/Crypto, `@preeternal/react-native-file-hash` 2.0.8, Jest Expo, React Native Testing Library, Maestro.

## Global Constraints

- Use the stable Expo SDK 57 template: `npx create-expo-app@latest mobile --template default@sdk-57`. Do not use canary/beta packages.
- Use a development build, not Expo Go, for notifications and later native IAP.
- Never implement swing analysis, quota, Caddie Brief, Proof Cycle, or entitlement rules in TypeScript. Render server contracts and feature flags.
- Never load an entire selected video through `File.bytes()`, `bytesSync()`,
  `File.slice()`, `arrayBuffer()`, base64, or JavaScript `FormData`. Open one
  `FileHandle`, seek with its `offset`, read at most one server-bounded
  `readBytes(chunkLength)` buffer, and close the handle on every path.
- A privacy-export ZIP never enters JavaScript bytes/base64/`arrayBuffer`,
  `expo/fetch`, or Expo `File.downloadFileAsync`. An audited local native module
  rejects redirects before following them, sends the bearer only to the exact
  same-origin export path, and streams to a protected random partial file.
- The bearer token and pending PKCE verifier live only in SecureStore and never
  enter TanStack Query, logs, error reports, URLs, AsyncStorage, or snapshots.
- Personal reports and billing state are network-only. Only staged media,
  resumable offsets, the active drill, unsent practice evidence, and the bounded
  sanitized account/epoch-scoped telemetry queue/idempotency envelopes defined
  in Task 9 may persist in the app-private cache. All are cleared on sign-out,
  account deletion, or account/epoch/environment boundary change.
- Do not request location, contacts, tracking, Bluetooth, or photo-library
  permission at launch. Camera/microphone/media/notification prompts are just in
  time; microphone access is used only while recording a swing because impact
  audio is part of the existing analysis signal.
- Physical gear opens the configured Shopify HTTPS URL in a system browser;
  native screens never offer web checkout for digital Pro.
- Every screen must support Dynamic Type, VoiceOver/TalkBack, reduced motion,
  non-color status cues, and at least 44×44 pt on iOS / 48×48 dp on Android.
- Version 1 transfers video only while foregrounded. Backgrounding aborts the
  current bounded chunk, persists a paused state, and foregrounding reconciles
  the authoritative server offset before resume; store copy must not claim
  continued background transfer.

---

## Task 1: Scaffold and pin the Expo SDK 57 workspace

**Files:**

- Create: `mobile/package.json`
- Create: `mobile/package-lock.json`
- Create: `mobile/.nvmrc`
- Create: `mobile/tsconfig.json`
- Create: `mobile/eslint.config.js`
- Create: `mobile/jest.config.js`
- Create: `mobile/jest.setup.ts`
- Create: `mobile/app.config.ts`
- Create: `mobile/privacy/PrivacyInfo.xcprivacy`
- Create: `mobile/plugins/withPrivacyManifest.ts`
- Create: `mobile/plugins/withPhoneFormFactor.ts`
- Create: `mobile/eas.json`
- Create: `mobile/.gitignore`
- Create: `mobile/app/_layout.tsx`
- Create: `mobile/app/index.tsx`
- Create: `mobile/assets/icon.png`
- Create: `mobile/assets/adaptive-icon.png`
- Create: `mobile/assets/monochrome-icon.png`
- Create: `mobile/assets/splash-icon.png`
- Create: `mobile/assets/notification-icon.png`
- Create: `mobile/assets/README.md`
- Create: `mobile/src/config/env.ts`
- Create: `mobile/src/platform/privateNoBackupStorage.ts`
- Create: `mobile/src/platform/orientation.ts`
- Create: `mobile/modules/caddieinsight-storage/package.json`
- Create: `mobile/modules/caddieinsight-storage/expo-module.config.json`
- Create: `mobile/modules/caddieinsight-storage/index.ts`
- Create: `mobile/modules/caddieinsight-storage/src/CaddieInsightStorageModule.ts`
- Create: `mobile/modules/caddieinsight-storage/ios/CaddieInsightStorage.podspec`
- Create: `mobile/modules/caddieinsight-storage/ios/CaddieInsightStorageModule.swift`
- Create: `mobile/modules/caddieinsight-storage/android/build.gradle`
- Create: `mobile/modules/caddieinsight-storage/android/src/main/AndroidManifest.xml`
- Create: `mobile/modules/caddieinsight-storage/android/src/main/java/expo/modules/caddieinsightstorage/CaddieInsightStorageModule.kt`
- Create: `mobile/tests/platform/privateNoBackupStorage.test.ts`
- Create: `mobile/tests/platform/orientation.test.ts`
- Create: `mobile/tests/config/env.test.ts`
- Create: `mobile/tests/config/brandAssets.test.ts`
- Create: `mobile/tests/config/privacyManifest.test.ts`
- Create: `mobile/tests/config/formFactor.test.ts`
- Modify: repository `.gitignore`
- Modify: `README.md:897-901`

**Interfaces:**

- Produces npm scripts `start`, `android`, `ios`, `lint`, `typecheck`, `test`,
  `test:watch`, `expo:doctor`, `api:generate`, and `api:check`.
- Produces `AppEnvironment = { apiBaseUrl: URL; apiOrigin: string; environment:
  "development"|"staging"|"production"; buildProfile: string;
  easProjectId: string | null; environmentIdentity: string }`. The immutable
  `environmentIdentity` is derived at build time from the closed environment,
  canonical HTTPS API origin, build profile, and bundle/package identity; it
  contains no secret and cannot be overridden by downloaded/runtime state.
- Production bundle identifier/package name: `com.caddieinsight.app`.
- Native support floor is explicit through `expo-build-properties`: iOS
  deployment target `16.4` and Android `minSdkVersion: 24`. Do not lower or
  hard-code Expo's target/compile SDK defaults; signed release configuration must
  meet the store requirement re-read at build time.
- Version 1 sets `ios.supportsTablet=false`, so the generated iOS device family
  excludes native iPad UI but the iPhone app may still run on iPad in compatibility
  mode. It also does **not** opt the compatible iPhone app out of Apple-silicon
  Mac or Apple Vision Pro availability. Release requires an iPad compatibility-
  mode smoke plus separate Mac/Vision Pro App Store Connect opt-out/readback
  before distribution, submission, and publication, or the project must add those
  platforms' full QA, support, declarations, and assets. Android intends phone-only delivery,
  but omission of a form-factor declaration is not proof: a repository-owned
  config plugin emits `<supports-screens android:smallScreens="true"
  android:normalScreens="true" android:largeScreens="false"
  android:xlargeScreens="false" android:anyDensity="true">` without deprecated
  `resizeable` or invented width limits,
  while
  the signed AAB plus Google Play Device Catalog readback is authoritative. If
  any tablet, Chromebook, desktop, TV, Wear, Automotive, or XR form factor is
  targetable, release stops until Android large-screen layout/device QA, store
  declarations, and required/recommended screenshots are explicitly added (or a
  reviewed rebuild demonstrably excludes them).
- Development/staging identifiers append `.dev` and `.staging`; deep-link
  schemes are `caddieinsight-dev`, `caddieinsight-staging`, and
  `caddieinsight` for development, staging, and production respectively.
- Installed app identity is exactly `name: "CaddieInsight"` and
  `slug: "caddieinsight"`; every binary asset is repository-owned and referenced
  explicitly by `app.config.ts`, never inherited from the Expo sample.
- `PrivateNoBackupStorage` is a local Expo module available before any feature
  writes state; it returns separate pending-upload, small-state, and protected
  export-temporary directories only after backup exclusion and platform file
  protection are read back.
- `PrivateNoBackupStorage.protectAndVerify(uri) -> Promise<void>` rejects paths
  outside those roots; on iOS it reapplies and reads back backup exclusion plus
  `.complete` protection after every copy, temporary-file rename, or write, and
  on Android it verifies the canonical path remains beneath `noBackupFilesDir`.
- `OrientationController.enterCapture()` unlocks rotation and
  `.leaveCapture()` restores portrait-up; the root app calls `leaveCapture()`
  outside capture and capture cleanup calls it on every exit/error path.
- `withPrivacyManifest` validates and copies the repository-owned
  `PrivacyInfo.xcprivacy` to the generated app target/root bundle under CNG. The
  manifest declares `NSPrivacyTracking=false`, no tracking domains, and the
  first-party required-reason uses actually exercised here:
  `NSPrivacyAccessedAPICategoryFileTimestamp/C617.1` for metadata inside the app
  container, `NSPrivacyAccessedAPICategoryDiskSpace/E174.1` for observable
  sufficient-space admission/deletion behavior, and
  `NSPrivacyAccessedAPICategoryUserDefaults/CA92.1` for app-only preferences.
  Timestamp/disk values stay on-device; third-party SDK declarations remain in
  their signed manifests and are reconciled from the archive, not copied blindly.

- [ ] From the repository root, run
  `npx create-expo-app@latest mobile --template default@sdk-57`; verify
  `mobile/package.json` resolves Expo 57 and Node’s minimum is 22.13. Do not
  create or keep a nested Git repository.
- [ ] Install runtime packages with
  `npx expo install expo-dev-client expo-build-properties expo-camera expo-image-picker expo-file-system expo-secure-store expo-notifications expo-linking expo-network expo-video expo-web-browser expo-device expo-application expo-constants expo-crypto expo-sharing expo-screen-orientation expo-haptics expo-audio expo-splash-screen` and
  `npm install @tanstack/react-query openapi-fetch @preeternal/react-native-file-hash@2.0.8` from `mobile/`.
- [ ] Install test/build dependencies with
  `npx expo install jest-expo jest @types/jest -- --save-dev` followed by
  `npm install --save-dev openapi-typescript @testing-library/react-native`.
- [ ] Add a failing `mobile/tests/config/env.test.ts` asserting production uses
  HTTPS, trailing slashes normalize once, missing production URL throws before
  rendering, and no secret-shaped environment variable is accepted.
- [ ] Implement `src/config/env.ts` from `EXPO_PUBLIC_API_BASE_URL`,
  `EXPO_PUBLIC_APP_ENV`, and the EAS project ID. These variables are public
  configuration only; never read provider credentials into the bundle.
- [ ] Replace the sample screen with a minimal root provider and deterministic
  loading/error shell. Configure `typedRoutes: true`, app-config orientation
  `default`, `ios.supportsTablet=false`, runtime portrait-up outside capture, the
  `expo-build-properties` support floors, the
  `@preeternal/react-native-file-hash` config plugin, microphone
  and camera purpose strings limited to guided swing recording, barcode scanning
  disabled, and no location/tracking permissions. These native modules confirm
  the development-build requirement.
- [ ] Add failing manifest/plugin tests for exact categories/reasons, valid plist,
  tracking false/empty domains, no undeclared collected-data claim, one generated
  root-bundle resource, and CNG idempotency. Inventory each first-party required-
  reason call site and assert C617.1/E174.1/CA92.1 use matches its approved scope;
  fail if a new category appears without a reviewed reason or if disk/timestamp
  information is serialized off-device.
- [ ] Add a failing generated-native-config test that reads back iPhone-only iOS
  `UIDeviceFamily` and `ios.supportsTablet=false` without claiming either is a
  Mac/Vision Pro availability control, plus the exact reviewed Android
  `<supports-screens>`/feature policy emitted by `withPhoneFormFactor`, and
  absence of TV/Wear/Automotive/XR features; fail if a template/dependency
  silently broadens the intended scope. Document that this source test is
  provisional and cannot replace signed-AAB/Play Device Catalog readback.
- [ ] Add failing orientation tests, then implement the runtime controller with
  `expo-screen-orientation`: ordinary routes lock portrait-up, capture unlocks
  portrait/landscape rotation, and blur/unmount/error cleanup restores portrait.
- [ ] Replace all sample branding. Add deterministic asset tests that decode the
  configured PNGs and require a 1024×1024 opaque app icon, 1024×1024 adaptive
  foreground in its safe zone, 432×432 single-color transparent monochrome icon,
  1024×1024 transparent splash mark, and 96×96 white-on-transparent Android
  notification glyph. Assert exact name/slug, every config reference, and that
  no hash matches the Expo template assets; document source/export rules.
- [ ] Implement/autolink the local storage module. iOS creates Application
  Support subdirectories with `FileProtectionType.complete`, then reads back
  both `FileAttributeKey.protectionKey == .complete` and
  `isExcludedFromBackupKey=true`; Android uses `context.noBackupFilesDir`. Add
  failing native/JS adapter tests for either unavailable/invalid protection or
  paths, then implement fail-closed directory validation. Start its audited
  functions-only scaffold with pinned
  `npx --yes create-expo-module@57.0.0 --local`, name/package/pod/Gradle project
  `CaddieInsightStorage`/`caddieinsight-storage`, and keep the generated package,
  podspec, Gradle file, manifest, module config, TypeScript bridge, and native
  source; remove sample view/event code and any nested example/Git repository.
  Inspect `npx expo prebuild --clean --no-install` output in a disposable copy.
- [ ] Run `npx expo-modules-autolinking resolve --platform apple` and the same
  with `--platform android`, plus `npx expo-modules-autolinking verify`; assert
  exactly one `CaddieInsightStorage` pod/Swift module and one
  `caddieinsight-storage` Gradle project/module class resolve from
  `mobile/modules`, with no duplicate/fallback package. Compile actual iOS and
  Android development and release configurations in disposable native projects
  (protected EAS macOS for iOS is acceptable) and fail if either configuration
  omits or cannot invoke the module.
- [ ] In the same disposable prebuild, assert Xcode's deployment target is exactly
  16.4 and every generated Android variant has `minSdkVersion` 24. Record the
  resolved target/compile SDK versions, fail if either is below current store
  policy or Expo SDK 57 compatibility, and do not commit generated projects.
- [ ] Add `.nvmrc` containing `22.13.0`, package `engines.node` as
  `>=22.13 <23`, strict/noUncheckedIndexedAccess TypeScript, and Jest Expo.
- [ ] Run `npm run expo:doctor`, `npm run lint`, `npm run typecheck`, and
  `npm test -- --runInBand`; expect all pass.
- [ ] Commit: `git add mobile .gitignore README.md && git commit -m "build: scaffold Expo mobile client"`.

## Task 2: Generate the API types and build the authenticated transport

**Files:**

- Create: `mobile/src/api/schema.generated.ts`
- Create: `mobile/src/api/client.ts`
- Create: `mobile/src/api/errors.ts`
- Create: `mobile/src/api/queryClient.ts`
- Create: `mobile/src/api/queryKeys.ts`
- Create: `mobile/src/config/appIdentity.ts`
- Create: `mobile/src/features/auth/authStore.ts`
- Create: `mobile/src/platform/secureStore.ts`
- Create: `mobile/src/platform/privateCache.ts`
- Create: `mobile/src/platform/environmentBoundary.ts`
- Create: `mobile/src/test/server.ts`
- Create: `mobile/tests/api/client.test.ts`
- Create: `mobile/tests/config/appIdentity.test.ts`
- Create: `mobile/tests/auth/authStore.test.ts`
- Create: `mobile/tests/reliability/environmentBoundary.test.ts`
- Modify: `mobile/package.json`

**Interfaces:**

- `AppIdentityHeaders` is the closed immutable tuple
  `X-CaddieInsight-Environment`, `X-CaddieInsight-Platform`,
  `X-CaddieInsight-App-Version`, `X-CaddieInsight-App-Build`, and
  `X-CaddieInsight-Application-Id`. `appIdentity.ts` derives it once from the
  embedded environment plus native `expo-application` values: environment;
  `ios|android`; marketing version; `CFBundleVersion`/Android versionCode as a
  canonical positive decimal string; and exact bundle/package ID. It validates
  all five against the build profile's allowlist before API construction. No
  downloaded state, route parameter, caller option, or ordinary header map may
  set or override any member.
- `apiRequest<Path, Method>(path, options) -> Promise<SuccessBody>` centrally adds
  bearer, `Accept: application/json`, every exact `AppIdentityHeaders` member, optional
  `Idempotency-Key`, a 30-second ordinary timeout, and one bounded retry only
  for idempotent requests/network failures. It does not synthesize a browser
  `Origin` header; native bearer routes are valid when `Origin` is absent.
- `CredentialStore.get/set/clear(): Promise<string | null | void>` stores the
  bearer under `ci.auth.token.v1` using SecureStore accessibility
  `WHEN_UNLOCKED_THIS_DEVICE_ONLY` on iOS.
- `AuthStore.bootstrap()`, `.completeExchange(token, {kind:"ordinary"} |
  {kind:"store_review", provider:"apple"|"google"})`, `.signOut()`, and
  `.handleUnauthorized()` own the credential lifecycle.
- `AuthStore.signOut()` atomically moves the bearer out of the active credential
  slot into one SecureStore-only pending-revocation record with a fresh 128-bit
  idempotency key, clears private local state, and retries the server sign-out
  endpoint until exact 204; that credential is never usable for app requests.
  The non-secret session kind/provider is stored beside the bearer in SecureStore,
  cleared by every credential/environment purge, and can select only UX/API
  branches such as review privacy re-authentication; it never grants a capability.
- `PrivateCache.clearAll()` deletes staged uploads, active drill, pending
  practice writes, the bounded telemetry queue/idempotency envelopes/local
  overflow counter, non-secret job references, and every partial/complete export
  temporary for the signed-in account.
- `EnvironmentBoundary.bootstrap(appEnvironment) -> Promise<"ready">` is the
  first root-app operation and the sole gate to SecureStore/private-cache reads,
  API construction, deep-link handling, or private rendering. It compares the
  embedded immutable `environmentIdentity` and canonical API origin with a
  dedicated prior-install marker. Missing/mismatched markers first durably set a
  fail-closed purge journal, then remove every bearer, installation UUID, PKCE/
  step-up verifier, revocation/privacy/purchase/upload/practice/telemetry
  idempotency envelope, QueryClient value, protected staged-media file, every
  export temporary/partial, account cache, and every registered platform purge
  hook. It verifies absence and writes the new marker last. A crash,
  offline launch, deletion failure, downgrade, or profile switch resumes the
  purge and permits only a non-private recovery shell; no request or private
  route can run until the journal clears under the current identity.
- `AppError = {category, apiCode, status, retryable, retryAfterSeconds,
  referenceId, authenticate}` preserves the sanitized server `APIError.code`,
  HTTP status, `retryable`, reference ID, bounded parsed `Retry-After`, and
  `WWW-Authenticate` challenge. `category` is a small UI grouping; feature state
  machines branch on typed `apiCode`/status, never message text.

- [ ] Add the package scripts:
  `openapi-typescript ../docs/api/openapi-v1.json -o src/api/schema.generated.ts`
  and an `api:check` command that generates to a temporary file and byte-compares
  it with the tracked generated file.
- [ ] Generate the types and add a failing type fixture proving wrong profile
  fields, unrecognized states, or a missing `resource_version` do not compile.
- [ ] Add failing transport tests for bearer injection, no token logging,
  retryable GET, non-retried POST without idempotency, identical idempotency
  replay, omitted `Origin`, timeout translation, structured API errors, malformed JSON, and 401
  cache/credential clearing. Cover exact 401/`WWW-Authenticate`, 409, 429 with
  numeric and HTTP-date `Retry-After`, 507, `deletion_pending`,
  `history_reset_pending`, `source_unavailable_after_restore`, and unknown safe
  server codes; assert status/retryability/reference survive translation.
- [ ] Implement a small wrapper around `openapi-fetch`; do not expose its raw
  client outside `src/api/client.ts`. Normalize errors to:

  ```ts
  export type AppErrorCategory =
    | 'network' | 'auth' | 'authorization' | 'not_found' | 'conflict'
    | 'validation' | 'rate_limit' | 'capacity' | 'server' | 'unknown';

  export type AppError = {
    category: AppErrorCategory;
    apiCode: string | null;
    status: number | null;
    retryable: boolean;
    retryAfterSeconds: number | null;
    referenceId: string | null;
    authenticate: string | null;
  };
  ```

  Bound header values before storage/UI, ignore unsafe server messages for control
  flow, and map only allowlisted codes to customer copy.

- [ ] Implement a TanStack `QueryClient` with no persistence, bounded retry,
  `networkMode: 'online'` for private current truth, and query keys scoped by
  `history_epoch`.
- [ ] Implement the environment boundary before `AuthStore.bootstrap` and API
  client creation. Keep only the marker and purge journal readable before the
  gate; enumerate every SecureStore namespace and no-backup root centrally so a
  newly added durable operation fails tests until it joins the purge inventory.
  Treat an absent marker as untrusted even when other state exists, purge before
  adopting the current identity, and never migrate credentials or private data
  across canonical origins/environments.
- [ ] Add failing boundary tests for preview/staging → production, production →
  preview/downgrade, same environment with a changed canonical origin, missing/
  corrupt marker over old state, crash at every journal phase, offline launch,
  locked/failing SecureStore, undeletable media/export temporary, and restart.
  Seed every durable
  namespace and prove the current build emits no request, handles no private
  deep link, renders no account state, and reads no ordinary secret before a
  verified purge writes the new marker last; same-identity restart preserves
  valid state.
- [ ] Implement `privateCache.ts` only under the account-scoped state directory
  returned by the local backup-excluded storage module; use atomic temporary-
  file rename for JSON state, call `protectAndVerify` on the completed item, and
  never enable iOS file sharing/open-in-place.
- [ ] Run `npm run api:check && npm run typecheck && npm test -- --runInBand`; expect all pass.
- [ ] Commit: `git commit -m "feat: add typed mobile API transport"` with the
  generated contract, transport, stores, and tests staged intentionally.

## Task 3: Implement challenge-bound email sign-in and ownership invalidation

**Files:**

- Create: `mobile/app/(auth)/index.tsx`
- Create: `mobile/app/app/auth/callback.tsx`
- Create: `mobile/src/features/auth/api.ts`
- Create: `mobile/src/features/auth/pkce.ts`
- Create: `mobile/src/features/auth/EmailSignInScreen.tsx`
- Create: `mobile/src/features/auth/ReviewAccessScreen.tsx`
- Create: `mobile/src/features/auth/AuthCallbackScreen.tsx`
- Create: `mobile/src/features/auth/AuthBoundary.tsx`
- Create: `mobile/src/features/auth/signOut.ts`
- Modify: `mobile/app/_layout.tsx`
- Modify: `mobile/app/index.tsx`
- Create: `mobile/tests/auth/emailSignIn.test.tsx`
- Create: `mobile/tests/auth/reviewAccess.test.tsx`
- Create: `mobile/tests/auth/signOut.test.ts`
- Create: `mobile/tests/auth/authBoundary.test.tsx`

**Interfaces:**

- `createPKCE() -> Promise<{verifier: string; challenge: string}>` uses 32 random
  bytes and S256/base64url without padding.
- `getOrCreateInstallationId() -> Promise<uuid>` stores one random UUID only in
  SecureStore; it is never an analytics/device-advertising identifier.
- `startEmailSignIn(email, deviceLabel, installationId, challenge) ->
  NativeAuthStartResponse`.
- `NativeAuthExchangeResult` is the local discriminated alias imported only from
  generated `NativeAuthExchangeSuccessResponse |
  NativeAuthExchangePendingResponse`; no invented response name is permitted.
- `exchangeEmailSignIn(challengeId, emailCode, verifier, idempotencyKey) ->
  NativeAuthExchangeResult`; HTTP 202 contains no
  credential and preserves the same pending inputs/key for retry, while an exact
  terminal retry receives the same credential.
- `startReviewSignIn(account, deviceLabel, installationId, challenge) ->
  NativeAuthStartResponse` derives `provider` and exact app identity only from the
  immutable native build; the reviewer cannot select or override them.
- `exchangeReviewSignIn(challengeId, password, verifier, idempotencyKey) ->
  NativeAuthExchangeResult` follows the same
  atomic token/pending-202 contract. The reusable credential is held only in the
  controlled input and pending in-memory request; it is never written to
  SecureStore, a URL, telemetry, snapshots, or logs.
  A successful exchange records only `{kind:"store_review",provider}` with the
  bearer; ordinary email exchange records `{kind:"ordinary"}`.
- `AuthBoundary` renders auth, profile setup, or signed-in routes based only on
  SecureStore + `/api/v1/me`.

- [ ] Add failing component tests for normalized email, generic send response,
  resending countdown, malformed/expired callback, wrong installation, offline
  callback preservation, exchange success, same-device manual code entry, and
  no raw credential rendered or logged. Add a second-device link test proving a
  device without the matching pending challenge/verifier makes no exchange call,
  does not consume the original challenge, and offers only safe restart/handoff.
  Add a compile-time fixture that exhaustively narrows the exact generated 201
  success and 202 pending variants and fails if either generated model name or
  discriminator changes.
- [ ] Add failing review-access tests for the visible secondary entry on every
  submitted store build, platform-derived Apple/Google provider and exact build,
  generic start/failure copy, successful 201, exact 202/lost-response replay,
  process/background cancellation, and credential-field clearing. Prove the
  password/account never enters persistence, URLs, snapshots, telemetry, crash
  reports, or normal email sign-in; a stale/closed lane returns to the ordinary
  passwordless screen without revealing which field failed.
- [ ] Add a failing ownership test: bootstrap with history epoch 2, refetch `/me`
  at epoch 3, then assert all private queries and cached pending writes are
  removed before any new account data renders, except the separately bounded
  same-account privacy replay envelope described in Task 8; that envelope cannot
  render old coaching data or authorize a different mutation.
- [ ] Implement PKCE with `expo-crypto`; store the verifier and challenge ID in
  SecureStore with a 128-bit idempotency key and stable installation UUID under
  short-lived pending keys. Store neither email nor verifier in the URL. The
  universal link provides only challenge ID and email code.
- [ ] Add a clearly labeled **App review access** action on the auth screen and a
  reusable account/password form matching the provider review instructions. It
  calls the dedicated review start/exchange pair, uses the same protected pending
  PKCE/idempotency/token activation machinery, and fetches `/me` before routing.
  Keep account/password out of persisted pending state; if a 202 survives the
  in-memory password, show a neutral retry form requiring the same credential
  rather than caching it. This path never turns a normal password into native
  access unless the server's exact provider/build/window admission is active.
- [ ] Implement `/app/auth/callback` handling for cold/warm starts. Retry exchange
  with the same stored verifier/key through bounded `202 auth_pending` backoff
  until an explicit terminal response, persist the bearer atomically only from
  201, then delete pending keys, fetch `/me`, and route to onboarding or Today.
  A lost 201 response must recover the same token; 202 must render a neutral
  “Securing this device…” state with cancel-to-safe-restart, and an invalid/
  expired link renders a safe restart action.
- [ ] Keep a manual code field on the initiating device so an email opened
  elsewhere can still complete there. If a callback arrives without a matching
  local challenge ID and verifier, do not call exchange: show “This sign-in was
  started on another device,” direct the golfer back to the initiating device,
  and offer a fresh sign-in on this device. Never copy PKCE material between
  devices or consume the original challenge from the second device.
- [ ] Accept the backend’s grouped eight-digit code with spaces/hyphens, normalize
  digits locally, and send it only in the exchange body. Never scrape the URL,
  transfer the verifier, or place the code in app logs/telemetry/persistence.
- [ ] On 401, sign out locally before navigation; on explicit sign-out, if a
  staged upload exists offer only “Keep working” (cancel sign-out) or “Discard
  and sign out.” On confirmation, move the token/idempotency key atomically to
  pending revocation before clearing active state. Online, handle 202/retry and
  exact lost-204 replay; offline, show locally signed out and retry at foreground/
  bootstrap before allowing another sign-in. Never use the pending token for any
  other API or leave another account’s video on disk.
- [ ] Add sign-out tests for online 204, 202 drain, lost response, offline local
  privacy, app restart, another-account sign-in blocked until pending revoke is
  attempted online, SecureStore write failure, and no pending credential in logs,
  query/cache state, telemetry queue/envelopes/counter, snapshots, or ordinary
  transport headers.
- [ ] Run `npm test -- auth --runInBand && npm run typecheck`; expect all pass.
- [ ] Commit: `git commit -m "feat: add native email sign-in"`.

## Task 4: Build the coach-first shell, design system, onboarding, and Today

**Files:**

- Create: `mobile/app/(tabs)/_layout.tsx`
- Create: `mobile/app/(tabs)/today.tsx`
- Create: `mobile/app/(tabs)/practice.tsx`
- Create: `mobile/app/(tabs)/analyze.tsx`
- Create: `mobile/app/(tabs)/progress.tsx`
- Create: `mobile/app/(tabs)/more.tsx`
- Create: `mobile/app/onboarding.tsx`
- Create: `mobile/src/design/tokens.ts`
- Create: `mobile/src/design/theme.tsx`
- Create: `mobile/src/ui/{Screen,Text,Button,Card,AsyncState,StatusBadge}.tsx`
- Create: `mobile/src/features/profile/{api,ProfileForm}.tsx`
- Create: `mobile/src/features/today/{api,TodayScreen,NextActionCard}.tsx`
- Create: `mobile/tests/navigation/tabs.test.tsx`
- Create: `mobile/tests/profile/onboarding.test.tsx`
- Create: `mobile/tests/today/today.test.tsx`

**Interfaces:**

- Tabs are exactly `today`, `practice`, center `analyze`, `progress`, `more`.
- `TodayScreen` consumes `GET /api/v1/mobile/today`; it never calls the legacy
  Today/session serializers or derives a different next
  action from raw sessions.
- `ProfileForm` consumes generated `ProfileResponse` and sends only generated
  `ProfileUpdateRequest` to `PUT /api/v1/mobile/profile`; it never calls the
  manually parsed legacy profile mutation. Marketing opt-in is a separate,
  unchecked control, is false by default, and never gates profile completeness.
  It supplies `/me`'s current `history_epoch`; a typed 409 purges/refetches the
  account context before the user can retry and never blindly resubmits stale
  profile state.
- Semantic tokens mirror brand intent—not CSS implementation—from
  `swinglab/templates/web_layout.html.j2:22-170`.

- [ ] Add failing navigation tests for exact tab labels/order, Analyze center
  action, signed-out redirect, deep-linked session stack above tabs, and reduced
  motion. Avoid screenshot-only assertions.
- [ ] Add failing onboarding tests for required goal/display name/preferred club,
  exact server `is_complete` parity, server enum rendering, handedness/angle
  defaults, separate consent, validation error
  focus, large text, stale-epoch 409 purge/refetch, and deletion/revocation 401.
  Contract tests fail if the mobile profile operation has
  no generated request body, accepts an extra field, or drifts to the legacy URL.
- [ ] Add failing Today tests for first baseline, active practice, checked-in,
  matched re-film, processing, coaching-ready, re-film-required, empty, offline,
  and error states returned by fixtures.
- [ ] Implement semantic color/type/spacing/radius/shadow/motion tokens. Ensure
  contrast, high-text scaling, non-color status icons/text, and platform minimum
  targets. Do not copy the desktop layout wholesale.
- [ ] Implement a custom accessible tab bar button for Analyze. It opens source
  choice and remains available without erasing Today’s current priority.
- [ ] Implement onboarding and Today through generated contracts. Put one next
  action and current Brief/practice status above metrics/history links.
- [ ] Run `npm test -- navigation profile today --runInBand && npm run lint && npm run typecheck`; expect all pass.
- [ ] Commit: `git commit -m "feat: build coach-first mobile shell"`.

## Task 5: Add guided recording, camera-roll import, and bounded preflight

**Files:**

- Create: `mobile/app/capture/index.tsx`
- Create: `mobile/src/features/capture/types.ts`
- Create: `mobile/src/features/capture/CaptureSourceSheet.tsx`
- Create: `mobile/src/features/capture/GuidedCameraScreen.tsx`
- Create: `mobile/src/features/capture/CaptureOverlay.tsx`
- Create: `mobile/src/features/capture/ReviewVideoScreen.tsx`
- Create: `mobile/src/features/capture/mediaPicker.ts`
- Create: `mobile/src/features/capture/mediaPreflight.ts`
- Create: `mobile/src/platform/files.ts`
- Modify: `mobile/src/platform/orientation.ts`
- Create: `mobile/tests/capture/{permissions,preflight,guidedCapture}.test.tsx`
- Create: `mobile/e2e/capture-import.yaml`

**Interfaces:**

- `CapturedMedia = { uri; sizeBytes; durationSeconds; suffix; mimeType;
  source: "camera"|"library"; audioExpected: boolean }` always points to a durable,
  app-private, backup-excluded pending-upload copy.
- `preflightMedia(media, capabilities, context) -> Promise<PreflightResult>`
  validates readable URI, size, duration, suffix, hand, angle, club, and current
  comparison context.
- `stageMedia(sourceUri, accountId) -> Promise<File>` copies without base64 and
  assigns an opaque local filename, then calls `protectAndVerify` before making
  the file available to review/upload.
- `PrivateNoBackupStorage.pendingUploadsDirectory()` and `.stateDirectory()`
  return separate roots under iOS Application Support with
  `isExcludedFromBackupKey=true` and file protection `.complete`, or Android
  `context.noBackupFilesDir`; neither path is user-shared, cloud-backed, or
  purgeable cache.

- [ ] Add failing tests for camera or microphone denied/limited/unavailable, import denied/
  canceled, missing file, oversize, overlength, unsupported suffix, low-storage
  copy failure, missing/stripped impact audio, changed comparison context,
  backup-exclusion failure, and valid camera/import paths.
- [ ] Implement source choice with camera and import equal in availability.
  Request each permission only after selection; denial shows Open Settings and
  the alternative source.
- [ ] Implement rear `CameraView` with `mode="video"`, audio enabled, no barcode
  scanner, a three-swing setup, face-on/down-the-line boundary copy, body
  silhouette, horizon/distance cues, audible/haptic countdown, and server-bounded
  `maxDuration`/`maxFileSize`. Request microphone only after Record is chosen;
  if denied, block silent recording and offer Import. Do not imply real-time
  pose validation.
- [ ] Use ImagePicker video-only with no editing/transcoding promise. Launch the
  system picker without a broad library prompt where the platform supports it;
  request access only for a platform path that actually requires it. Preserve
  original audio and copy the selected asset into `PrivateNoBackupStorage` before
  review.
- [ ] Exercise the already-scaffolded local storage module with native tests:
  iOS verifies its Application Support backup-exclusion resource value and
  `.complete` file-protection attribute on directories and a probe file; Android
  verifies `noBackupFilesDir`. Fail closed if the durable directory, exclusion,
  or protection cannot be verified; never fall back silently to Documents/cache.
- [ ] Build review/discard/upload controls with `expo-video`; show context and
  exact server limit errors. Deleting/discarding removes the private copy.
- [ ] Add a 500 MiB synthetic-file memory test for the file adapter: open the
  file once, assign `FileHandle.offset`, and call `readBytes(chunkLength)` using
  the server chunk size; assert the largest JS buffer and physical-device heap
  delta stay within one chunk plus 10% overhead. Cover EOF/short reads, abort,
  read/hash/network failure, and require `close()` in `finally` on every path.
  Add a pinned-SDK source/API guard that fails if Expo SDK 57 no longer exposes
  the audited `File.open()`/`FileHandle.offset`/`readBytes` contract, and
  explicitly fail any media-path use of `File.slice`, `bytes`, or `bytesSync`.
  Run this as a development-build device test before merging Task 6.
- [ ] Rebuild the Android development client after the native-module dependency
  lock changes: from the repository root `Push-Location mobile`, run
  `npx expo prebuild --clean`, `npx expo run:android --device`, and the storage/
  audio/hash integration tests, then `Pop-Location`. Require `/android` and
  `/ios` in `mobile/.gitignore`; from the repository root confirm both with
  `git check-ignore mobile/android mobile/ios`. After device proof resolve the
  exact `mobile/android` and `mobile/ios` paths and verify each parent is the
  resolved `mobile/` root before deleting those generated directories. Assert
  `Test-Path mobile/android,mobile/ios` is false and
  `git ls-files mobile/android mobile/ios` is empty. Release Task 2 repeats the
  exclusions in `.easignore` so EAS always runs clean CNG. iOS native-device
  proof is approval-gated Release Task 7.
- [ ] Run `npm test -- capture --runInBand && npm run typecheck`; execute the
  Maestro import smoke on an emulator with a fixture under the server limit.
- [ ] Commit: `git commit -m "feat: add guided mobile swing capture"`.

## Task 6: Implement the resumable upload and analysis state machine

**Files:**

- Create: `mobile/src/features/analysis/uploadTypes.ts`
- Create: `mobile/src/features/analysis/uploadRepository.ts`
- Create: `mobile/src/features/analysis/uploadMachine.ts`
- Create: `mobile/src/features/analysis/uploadApi.ts`
- Create: `mobile/src/features/analysis/fileHash.ts`
- Create: `mobile/src/features/analysis/boundedFileReader.ts`
- Create: `mobile/src/platform/appLifecycle.ts`
- Create: `mobile/src/features/analysis/UploadScreen.tsx`
- Create: `mobile/src/features/analysis/useAnalysisJob.ts`
- Create: `mobile/app/analysis/[sessionId].tsx`
- Create: `mobile/tests/analysis/{boundedFileReader,fileHash,uploadRepository,uploadMachine,jobPolling}.test.tsx`
- Create: `mobile/e2e/upload-resume.yaml`

**Interfaces:**

- `PendingUpload` stores local URI, source metadata, full SHA-256, reservation
  ID, acknowledged offset, idempotency key, account ID, `history_epoch`, and
  state; it never stores the bearer. It also persists the exact generated API
  `comparison` union (`null`, matched triple, or explicit-new-context triple) so
  resume/replay cannot silently lose Proof Cycle intent. For analysis retry it
  stores the server-advertised `expected_retry_attempt`, opaque receipt ID when
  returned, and exactly one 128-bit idempotency key for that attempt. It
  persists a separate 128-bit upload-abort idempotency key before the first
  reservation DELETE and keeps it through lost-response/restart replay, and
  persists a separate retry-source-discard idempotency key before the first
  discard request and keeps it through lost-response/restart replay.
- `hashAndUpload(file, capabilities, signal) -> AsyncGenerator<UploadProgress>`
  uses native streaming `fileHash(uri, { algorithm: "SHA-256" })` for the full
  file and `BoundedFileReader`, which owns one Expo `FileHandle`, seeks through
  `offset`, and returns one bounded `readBytes(chunkLength)` buffer per server
  chunk. It closes the handle in `finally` for completion, abort, and failure.
- Full-file SHA-256 is lowercase hexadecimal. Each chunk digest is converted
  from Expo Crypto bytes to RFC 4648 base64 for `Upload-Checksum: sha256 <value>`.
- State union: `preparing | reserving | uploading | paused | verifying |
  abort_pending | queued | processing | retryable_failed | retrying |
  retry_source_discard_pending | refilm_required | done | failed | expired |
  discarded`. `abort_pending` and `retry_source_discard_pending` are durable
  operation states, not UI-only flags; `discarded` is entered during an active
  account/epoch only after the corresponding authoritative replay-safe 204.
- `useAnalysisJob(sessionId)` polls only
  `GET /api/v1/mobile/sessions/{session_id}` and session history loads only
  `GET /api/v1/mobile/sessions`. Both decode the generated
  `MobileSessionResponse`; native code must never target legacy
  `/api/v1/sessions`, `/api/v1/sessions/{session_id}`, or `/api/v1/today`, whose
  byte-compatible browser responses can contain raw log/error fields.
- `reconcileUpload(local, remote) -> PendingUpload` treats the server offset as
  authoritative and never moves backward without re-reading local bytes.
- `ForegroundUploadPolicy` listens to React Native `AppState`: `inactive` or
  `background` aborts the in-flight chunk and durably transitions to `paused`;
  `active` performs `GET` reconciliation before any next chunk.

- [ ] Add failing pure-state tests for every legal transition and rejection of
  illegal transitions. Include offline, timeout, app restart, expired
  reservation, digest mismatch, server offset ahead/behind, cancel, duplicate
  completion, 401, 409 history reset, retryable failure/expiry/exhaustion,
  idempotent analysis retry, attempt-1 terminal failure → attempt-2 key rotation,
  lost response/restart/concurrent tap, low storage, keyed upload abort/lost 204/
  app restart/conflicting replay, server/local explicit
  discard, retry-source-discard pending/lost 204/restart, account/epoch mismatch,
  sign-out/reset/deletion boundary purge, and missing local source. Prove a
  pending key is never replayed under a different account/epoch and terminal
  `discarded` never precedes authoritative 204.
- [ ] Add failing transport tests proving each chunk contains exact offset,
  length, and SHA-256 header; full completion is idempotent; no POST/PATCH retry
  occurs without the same key/offset/comparison body; matched/new-context/null
  encode exactly as generated; queued/processing polling and history use the
  exact mobile GET/detail routes; no native request targets any legacy session/
  Today route; and a 500 MiB source is never read whole. Spy on the SDK boundary
  to require only `open`/`offset`/bounded `readBytes`/`close`, reject `slice`,
  `bytes`, and `bytesSync`, cover short-read loops, and prove the handle closes
  after digest, fetch, abort, or reconciliation failure.
- [ ] Add failing lifecycle tests proving background/inactive aborts the current
  request, acknowledges no unconfirmed bytes, persists `paused`, and starts no
  transfer while backgrounded. Foreground must fetch the authoritative server
  offset before resuming, including when the server accepted the canceled chunk.
- [ ] Implement a cache-backed repository using atomic JSON replacement. On
  launch, list pending records, verify account/epoch and file existence, query
  the server offset, then offer Resume or Discard.
- [ ] On reservation Discard, atomically persist `abort_pending` plus its
  operation-specific abort key before `DELETE /api/v1/uploads/{upload_id}`. Retry
  that exact key through timeout/restart until authoritative 204; do not treat GET
  404 alone as success and do not enter `discarded` or delete the local source/
  record before 204. A
  409 completed race reconciles the resulting session and retains source under
  the analysis terminal/retry rules instead of issuing another abort.
- [ ] Compute the full digest with the native streaming file-hash module so the
  video never enters the JS heap. Open a `FileHandle` once; for each server-
  bounded chunk set its `offset`, fill at most one chunk with bounded
  `readBytes(remaining)` calls (handling short reads/EOF), compute that exact
  `Uint8Array`'s Expo Crypto SHA-256, and send the same bounded bytes through
  `expo/fetch`. Close the handle in `finally`, update the acknowledged offset
  only after the server response, and release the buffer before reading another.
  Network loss pauses safely. Apply `ForegroundUploadPolicy`; do not register a
  background task or claim continued native transfer in version 1.
- [ ] After complete returns a job, retain the protected staged video and mark
  the upload record `queued`; invalidate Today/sessions/capabilities and navigate
  to owned analysis status. Delete both only after an owned terminal analysis
  response (`done`, `refilm_required`, permanent/exhausted/expired `failed`) or
  explicit discard. A server-declared retryable failure retains the source and
  one stable idempotency key for the current server-assigned retry attempt through
  `retry_expires_at`; call `POST /api/v1/mobile/sessions/{id}/retry` with that exact
  attempt/key and reconcile the same job. Keep the key across lost response and
  restart. Generate/persist a new key only after the server acknowledges the prior
  attempt’s terminal retryable result and advertises the next attempt number. Low
  storage may prompt Retry now or Discard, but never silently delete a still-
  retryable source. Discard first calls the idempotent
  `DELETE /api/v1/mobile/sessions/{id}/retry-source`, atomically persisting
  `retry_source_discard_pending` and its key before the first request; replay the
  same key after timeout/restart and enter `discarded`/delete the local source
  only after its 204 replay-safe confirmation.
  If restore reports `source_unavailable_after_restore`, reconcile the completed
  reservation and offer one idempotent re-upload from the retained source.
- [ ] On launch, replay either pending discard state only after the stored
  account ID and `history_epoch` match the current authenticated context. A
  sign-out, history reset, or account deletion first uses its authoritative
  server operation to cancel/delete owned upload and retry-source state where
  applicable, then boundary-purges the local record/source; an already changed
  account/epoch performs local cryptographic purge with zero network replay and
  relies only on the server's authenticated reset/deletion or bounded upload TTL.
  This boundary purge is recorded as a privacy/account transition, never
  misreported as a provider 204/`discarded`, and no operation key crosses users.
- [ ] Poll queued/processing state through only
  `GET /api/v1/mobile/sessions/{session_id}` with bounded adaptive intervals and
  stop in terminal/background states. Announce progress accessibly, permit
  leaving, and distinguish server outage from bad video.
- [ ] Run `npm test -- analysis --runInBand && npm run typecheck`; run the Maestro
  resume flow by terminating after one acknowledged chunk and reopening. Expect
  one server job and no duplicate analysis.
- [ ] Commit: `git commit -m "feat: add resumable swing upload flow"`.

## Task 7: Implement Brief, Practice, matched re-film, and Progress

**Files:**

- Create: `mobile/app/brief/[sessionId].tsx`
- Create: `mobile/src/features/analysis/{BriefScreen,EvidenceSection}.tsx`
- Create: `mobile/src/features/practice/{api,PracticeScreen,PracticeTimer,PracticeEvidenceForm,pendingEvidence}.tsx`
- Create: `mobile/src/features/progress/{api,ProgressScreen,ContextGroup,TransferOutcome}.tsx`
- Modify: `mobile/src/features/analysis/uploadTypes.ts`
- Modify: `mobile/src/features/analysis/uploadMachine.ts`
- Modify: `mobile/src/features/capture/types.ts`
- Modify: `mobile/app/capture/index.tsx`
- Modify: `mobile/app/(tabs)/practice.tsx`
- Modify: `mobile/app/(tabs)/progress.tsx`
- Modify: `mobile/src/features/today/NextActionCard.tsx`
- Create: `mobile/tests/brief/brief.test.tsx`
- Create: `mobile/tests/practice/practice.test.tsx`
- Create: `mobile/tests/progress/progress.test.tsx`
- Create: `mobile/e2e/coaching-loop.yaml`

**Interfaces:**

- Brief renders one priority, evidence, confidence, hypothesis, prescribed drill,
  and measurable re-film target from only
  `GET /api/v1/mobile/sessions/{session_id}/brief` before optional safe structured
  details. It decodes generated `BriefResponse`; native code never calls legacy
  `/api/v1/sessions/{session_id}/brief`, renders report HTML, or accepts arbitrary
  report/artifact URLs. Version 1 has no downloadable evidence artifact path.
- Practice durations are exactly 10, 20, and 45 minutes of the same server
  experiment; offline completion queues the same idempotent evidence payload.
- Matched re-film passes the baseline club, hand, angle, and exact current
  `{baseline_session_id,target_fingerprint,drill_id}` into capture as
  `mode:"matched"`. A deliberate context change warns, then preserves the same
  triple with `mode:"new_context"`; neither path degrades to a null/ordinary
  upload. Those six launch values come only from the generated current
  `ProofCycleTargetResponse` returned by Progress or the owned Brief; the client
  never reconstructs them from labels, session cache, or report content.
- Progress uses only `GET /api/v1/progress` labels: improved and holding, early
  signal, inconclusive, or no transfer yet; followed by continue, adjust, stop,
  or coach handoff.

- [ ] Add failing Brief tests for the exact mobile route, coaching-ready,
  capture-only/re-film, missing, cross-account 404, stale epoch, in-progress,
  confidence label, evidence hierarchy, locked details, secret-shaped extra
  fields/HTML/artifact URL rejection, and large text/screen-reader order.
- [ ] Add failing practice tests for each duration, timer pause/resume, reduced
  motion, evidence field bounds, offline queue, identical replay, conflict,
  history-epoch deletion, and noneligible session.
- [ ] Add failing matched re-film tests for exact triple/context preservation,
  cross-account/stale-target 409, changed-context rejection before confirmation,
  generated Progress/Brief target source, absent/replaced/reset target,
  deliberate `new_context`, app termination/resume, and exactly one server
  transfer outcome for matched completion.
- [ ] Add failing Progress tests for empty, one context, multiple contexts,
  source labels, Pro capability lock, every approved transfer label/decision,
  and no client-side score invention.
- [ ] Implement the Brief hierarchy from the owned structured JSON response.
  Render only schema fields and never fetch/store report HTML, metrics, or
  downloadable artifacts offline.
- [ ] Implement the practice timer/checklist and cache only the active drill plus
  unsent evidence under `PrivateNoBackupStorage.stateDirectory()`. Mark cached
  content with last-updated time; never present it as current after reconnect
  until server sync succeeds.
- [ ] Implement Progress context groups and matched re-film entry. Preserve the
  exact `{baseline_session_id,target_fingerprint,drill_id}` through upload
  completion for both `matched` and explicitly confirmed `new_context`; clear it
  only after terminal completion, explicit discard/cancel, or authoritative
  target invalidation.
- [ ] Run `npm test -- brief practice progress --runInBand`; run the Maestro
  coaching loop against fixture API responses, then against a development
  backend with one real eligible sample. Expect the full loop to pass.
- [ ] Commit: `git commit -m "feat: complete native coaching loop"`.

## Task 8: Add push/deep links, More, gear handoff, and privacy controls

**Files:**

- Create: `mobile/src/platform/notifications.ts`
- Create: `mobile/src/platform/deepLinks.ts`
- Create: `mobile/src/features/more/MoreScreen.tsx`
- Create: `mobile/app/more/profile.tsx`
- Create: `mobile/app/more/pro.tsx`
- Create: `mobile/app/more/devices.tsx`
- Create: `mobile/app/more/privacy.tsx`
- Create: `mobile/src/features/more/{devices,privacy,gear}.ts`
- Create: `mobile/src/features/more/reviewPrivacyStepUp.ts`
- Create: `mobile/src/features/more/exportDownloader.ts`
- Create: `mobile/modules/caddieinsight-storage/src/ExportDownloader.types.ts`
- Modify: `mobile/modules/caddieinsight-storage/src/CaddieInsightStorageModule.ts`
- Modify: `mobile/modules/caddieinsight-storage/ios/CaddieInsightStorage.podspec`
- Modify: `mobile/modules/caddieinsight-storage/ios/CaddieInsightStorageModule.swift`
- Create: `mobile/modules/caddieinsight-storage/ios/Tests/ExportDownloaderTests.swift`
- Modify: `mobile/modules/caddieinsight-storage/android/build.gradle`
- Modify: `mobile/modules/caddieinsight-storage/android/src/main/java/expo/modules/caddieinsightstorage/CaddieInsightStorageModule.kt`
- Create: `mobile/modules/caddieinsight-storage/android/src/androidTest/java/expo/modules/caddieinsightstorage/ExportDownloaderInstrumentedTest.kt`
- Create: `mobile/src/features/more/pendingPrivacyOperation.ts`
- Modify: `mobile/app/(tabs)/more.tsx`
- Modify: `mobile/app/_layout.tsx`
- Modify: `mobile/app.config.ts`
- Create: `mobile/tests/platform/{notifications,deepLinks}.test.ts`
- Create: `mobile/tests/more/{devices,privacy,gear}.test.tsx`
- Create: `mobile/tests/more/reviewPrivacyStepUp.test.tsx`
- Create: `mobile/tests/more/exportDownloader.test.ts`

**Interfaces:**

- Notification registration occurs only after analysis submission or explicit
  opt-in. `getExpoPushTokenAsync({projectId})` always receives the exact embedded
  EAS project UUID, then `PUT /api/v1/devices/push` sends that same public
  `expo_project_id` and binds the token plus immutable app-identity headers to the
  current bearer selector. After opt-in the client repeats the idempotent PUT on
  cold start, foreground, token-listener change, and version/build change even if
  the token bytes are unchanged.
- Notification settings expose an opt-in 72-hour practice reminder. Analysis,
  re-film, reminder, and new-device security payloads remain generic and always
  refetch owned state.
- Deep links accept allowlisted routes only: auth callback, owned analysis/Brief,
  and public help. Private destinations always refetch before rendering.
- More links Profile, Pro summary (purchase UI is Plan 3), Gear, Devices,
  Privacy/export/history reset/account deletion, Support, Terms, and Privacy
  Policy. The Restore control remains absent until Plan 3 enables the billing
  capability and implements its behavior.
- `openGearStore(url)` accepts only the server-configured HTTPS Shopify host and
  uses `WebBrowser.openBrowserAsync`; it never injects bearer/cookies.
- `startPrivacyStepUp(purpose, codeChallenge)` sends the current bearer;
  `exchangePrivacyStepUp(challengeId, code, verifier, idempotencyKey)` sends only
  the challenge secrets/idempotency key and explicitly suppresses ambient auth.
  The resulting five-minute token stays in SecureStore only until one matching
  export/reset/delete request consumes it.
- When the persisted authenticated session kind is `store_review`, the privacy
  screen never starts the unreachable email-code flow. Instead
  `startReviewPrivacyStepUp(purpose, codeChallenge)` calls the dedicated bearer-
  only review start endpoint with central immutable app identity, then
  `exchangeReviewPrivacyStepUp(challengeId,password,verifier,idempotencyKey)` calls
  the no-ambient-auth exchange endpoint. The password exists only in the
  controlled input/in-memory request and is cleared on submit/background/error;
  PKCE/idempotency pending state contains no account/password. The returned
  five-minute single-use token feeds the same pending export/reset/delete
  operation machinery. Review deletion confirmation states that this synthetic
  generation is erased and a later store-review login may create a fresh demo;
  ordinary customer copy never says or performs that.
- `PendingPrivacyOperation` is written to SecureStore before the first protected
  POST/DELETE and contains account scope, purpose, one 128-bit idempotency key,
  and the exact generated non-secret semantic body (including the captured
  `expected_history_epoch` when applicable). It never stores bearer or step-up
  token. Server canonical replay excludes credentials, so after the first send
  the one-time step-up can be destroyed; unknown-journal replay prompts a fresh
  step-up and resends the same key/body.
- `ExportDownloader.downloadReadyExport(receipt, bearer, signal) -> File` accepts
  only a generated `ready` receipt with
  `max_download_bytes===MAX_PRIVACY_EXPORT_ZIP_BYTES===1_100_000_000` and integer
  `byte_size` in `1..1_100_000_000`. The handwritten constant is machine-checked
  against the tracked OpenAPI `const`/`maximum`; contract drift fails CI. It constructs
  exactly `PUBLIC_API_ORIGIN + /api/v1/privacy/exports/{owned_id}/download` with
  no query/userinfo/fragment or caller URL, then calls only local native
  `CaddieInsightStorage.downloadExport({operationId,url,destinationUri,bearer,
  expectedBytes,generation})`, where `operationId` is a fresh opaque 128-bit
  base64url value persisted only for the lifetime of this native call.
  It returns only sanitized
  `{operationId,destinationUri,status,finalUrl,contentType,contentLength,
  bytesWritten,zipSignatureValid}`
  after streaming with a fixed native buffer. Success requires direct status 200,
  byte-identical initial/final HTTPS origin/path, no redirect, exact
  `application/zip`, declared/content/file sizes all equal to receipt `byte_size`,
  returned destination equal to the protected random `.partial`, and a native
  bounded ZIP-signature check. No file byte, bearer, or header crosses the native
  bridge back to JavaScript.
- iOS uses an ephemeral `URLSession` delegate that rejects every redirect before
  following it and streams/moves only to the supplied protected partial; Android
  uses an audited client with both HTTP and HTTPS redirect following disabled and
  copies the response body through a fixed native buffer. Both enforce the exact
  initial origin/path before attaching Authorization. Both reject an
  `expectedBytes` outside `1..1_100_000_000` and local free space below
  `expectedBytes + 67_108_864` before creating the partial, attaching
  Authorization, or starting network I/O; they abort/delete on missing or
  conflicting content length/type/status, and register cancellable work by exact
  operation ID. JavaScript wires `AbortSignal` to
  `cancelAndDrain(operationId)`, awaits that operation's terminal callback, and
  removes the listener in `finally`; aborting one download cannot cancel or
  publish another. `cancelAndDrainAll(generation)` is reserved for account/
  environment/epoch purge and waits until every older-generation operation can no
  longer recreate a file.
- `destinationUri` is never write authority. Before attaching Authorization or
  opening a socket, each native implementation resolves the module-owned current-
  generation export-temporary root, re-verifies its no-backup/protection marker,
  requires the destination to be one direct random `.partial` child, and rejects
  traversal, separators, aliases, symlinks/reparse points, an existing leaf, or a
  canonical-parent mismatch. iOS holds the verified parent directory descriptor
  and creates mode-0600 with exclusive/no-follow semantics; Android uses the
  equivalent app-private `noBackupFilesDir` parent and exclusive no-follow
  descriptor creation. Both `fstat` a regular file and recheck the held
  descriptor/path identity before publication, so rename/symlink/TOCTOU races
  delete the partial and fail. No private ZIP byte may ever be written outside
  that native-owned root, even if JavaScript is compromised or races cleanup.
- After the first history-reset request, only a
  `PendingPrivacyReplayEnvelope = {accountId, purpose, originalBody,
  idempotencyKey, optionalReceiptId}` may survive an epoch advance. It is stored
  in a dedicated SecureStore namespace, contains no bearer, step-up token, media,
  report, session, practice, or new-epoch state, and can call only the same reset
  endpoint with the byte-identical body/key. It is deleted only after exact 204
  or an explicit terminal server response; it never enters QueryClient or feature
  rendering and cannot be reused for another account/purpose/body.

- [ ] Add failing notification tests for just-in-time prompt, denial, Expo token
  registration, rotation, sign-out removal, no metric/private text, foreground/
  background tap, practice reminder opt-in/off, generic security notice, stale
  credential, and cross-account job link.
- [ ] Add failing deep-link tests for cold/warm auth callbacks, owned job,
  unknown/expired/cross-account IDs, arbitrary external URL, and safe fallback.
- [ ] Add failing More tests for device list/current device, revoke other/self,
  profile, provider-aware Pro summary, Shopify host allowlist, export, step-up
  reset, active Apple/Google deletion warning and management links, persisted
  deletion retry after a lost 204, failure rollback, and sign-out cache cleanup.
- [ ] Add pending-operation tests for write-before-send failure, lost export 202,
  lost reset/deletion 204, restart, `/me` history-epoch advance, consumed step-up,
  unknown journal requiring fresh step-up, conflicting body, account mismatch,
  receipt capture, and SecureStore cleanup. Exact replay always uses the original
  body/key and cannot strand an accepted operation or reuse one key for another.
  Include the ordering: server commits reset/204 → response is lost → `/me`
  returns the new epoch → ordinary epoch purge clears all old coaching/media/
  upload/practice/telemetry state but preserves only the replay envelope → exact
  replay recovers 204 → envelope clears before new-epoch rendering proceeds.
- [ ] Add review privacy component/API tests for method selection from the stored
  session kind, exact generated start/exchange routes and app-identity headers,
  all three purposes, password-in-memory-only clearing, PKCE/idempotency replay,
  generic failure/rate limit, background/sign-out/credential-rotation races, and
  five-minute one-use consumption. Run export, reset, and deletion end to end;
  deletion must clear the old local generation before auth, explain synthetic
  regeneration, and allow a later standing-Google review login to fetch only the
  fresh fixture. An ordinary session must remain on email step-up and never see
  review-specific copy or send a review password.
- [ ] Add export-downloader unit/integration tests for exact origin/path and bearer
  header, mismatched literal maximum, size 0/1/1,100,000,000/1,100,000,001,
  insufficient/exact reserve free space, non-2xx, any backend redirect,
  wrong content type/length, progress overflow, returned-path mismatch, partial
  collision, operation/destination-result mismatch, abort/network failure,
  ZIP-signature failure, exact size, and final purge. Start two concurrent
  operation IDs, abort one, and prove its file/callback drains while the other
  alone completes at its own exact destination; then prove generation-wide
  cancellation drains both. Use local HTTP fixtures on both native platforms to prove a same-origin
  and cross-origin 3xx is not followed and never receives Authorization. Add a
  source/lint guard banning `File.downloadFileAsync`, `expo/fetch`, `fetch`,
  `arrayBuffer`, `bytes`, base64, and `FormData` from the export downloader.
  In both native suites add outside-root, `..`/encoded-separator, symlink parent/
  leaf, preexisting leaf, root replacement, rename-during-stream, and descriptor-
  identity-race cases; assert rejection occurs before Authorization/network/file
  bytes and leaves no outside-root or partial file.
- [ ] Extend the already scaffolded/autolinked `caddieinsight-storage` module—do
  not create a second partial native module. In a disposable CNG copy run
  `npx expo prebuild --clean --no-install`, require Expo autolinking to resolve
  its podspec and Gradle module/class. Add a CocoaPods `test_spec` that compiles
  and runs `ios/Tests/ExportDownloaderTests.swift`, and module Gradle instrumented-
  test wiring that compiles and runs `ExportDownloaderInstrumentedTest.kt` on an
  emulator/device. Those native suites own local HTTP fixtures, redirect-before-
  Authorization, bounded streaming, exact-length/ZIP rejection, concurrent
  operation isolation, cancel/drain races, partial cleanup, and late-callback
  assertions; a JS mock cannot satisfy a native branch. Compile/run the Android
  development client and native tests, then delete generated native directories only
  after the same resolved-parent safety check used in Task 5. Release Task 7 must
  compile and execute the iOS native tests before any store build, and both exact
  release artifacts repeat the redirect, heap, cancellation, and purge proof.
- [ ] Configure `expo-notifications` with explicit project ID and a generic
  Android icon/channel. Request permission after submission; handle failures as
  non-blocking because polling remains available.
- [ ] Register a notification cleanup extension with `EnvironmentBoundary`. Before
  a preview/production marker change can commit, call and await
  `Notifications.dismissAllNotificationsAsync()`,
  `cancelAllScheduledNotificationsAsync()`, and
  `clearLastNotificationResponseAsync()`, clear local token/notification-route
  state, and verify no presented or scheduled notification remains. Failure keeps
  the non-private recovery shell and retries; it never adopts the new marker or
  follows an old notification route early. This is device cleanup only and never
  claims to revoke the old environment's server registration.
- [ ] Add a physical-flow-compatible unit/integration fixture in which the exact
  Expo token remains byte-identical across preview → production and production
  → preview. Prove the boundary performs notification cleanup before marker
  adoption, emits no old-origin request, and after opt-in registers the unchanged
  token only with the new origin/project/app-identity tuple. Keep the reverse
  production → preview push lane disabled in Version 1; polling remains usable.
- [ ] Implement authenticated deep-link routing. Notification data supplies a
  route, never trusted content; the destination calls the owned API and maps 404
  to a safe explanation.
- [ ] Implement privacy step-up email links using the same PKCE helper but a
  purpose-specific pending verifier/challenge/idempotency key. Handle generic
  202, 429/`Retry-After`, grouped manual code, cold/warm callback, copied link on
  another device, selector/epoch invalidation, lost exchange response, and
  expiry without ever adding bearer/cookie to exchange. Delete pending material
  on terminal failure/sign-out; require destructive confirmation text and show
  exactly what is removed/retained before calling the API.
- [ ] Before account deletion, refetch entitlements and state plainly that
  deleting CaddieInsight does not cancel Apple App Store or Google Play auto-
  renewal. Show the applicable provider management link(s), require a separate
  acknowledgement, explain that a future subscription-only reclaim requires the
  same verified email plus new email verification/provider proof and will not
  restore coaching history, and still permit deletion. If provider state is
  unavailable, show both official management paths and the protected support path
  rather than implying cancellation or guaranteed restore.
- [ ] Persist the full history-reset `PendingPrivacyOperation` until terminal
  204. Render any 202 generically as `history_reset_pending`, honor its bounded
  server code/`Retry-After`, retry the captured epoch/body/key, and do not claim
  an export is the cause unless the response explicitly says so. Clear all old-
  epoch local state only after confirmation; a lost 204 must replay safely even
  when a later `/me` reports a new epoch.
- [ ] For account deletion, persist its full pending operation before the
  request. On the first authenticated 202 `deletion_pending`, immediately move
  only that bounded body/key/account-scope envelope to SecureStore, destroy the
  active bearer, QueryClient, media, and all other private cache, and render a
  non-private deletion-pending shell. On restart, replay the envelope without
  bearer or ambient cookie and honor `Retry-After`; 204 clears the final envelope.
  Test a long recovery-store/worker outage, restart, lost 204, and another-account
  sign-in attempt without rendering deleted-account state. The key is never
  logged or reused for another mutation.
- [ ] Persist the export pending operation before POST. On lost 202, replay it to
  recover the same receipt before requesting a new step-up; once captured, store
  the receipt ID and clear the submission key. Poll the
  owned ZIP from `/api/v1/privacy/exports/{export_id}/download` only after the
  status endpoint returns `ready`, before its one-hour ready-time expiry, into the
  account/environment/history-epoch-scoped protected export-temporary root. Use
  only `ExportDownloader` to stream natively to a random nonexisting `.partial`;
  call `protectAndVerify`, atomically rename, verify protection again, open the
  platform share sheet, and delete in
  `try/finally`. Purge partial/complete orphans before private startup and on each
  foreground, and include the root in environment/account/epoch changes,
  `PrivateCache.clearAll`, sign-out, history reset, and account-deletion purges.
  A deletion failure keeps the privacy shell fail-closed until startup/foreground
  purge verifies absence. Test termination/failure at write, protect, rename,
  share, and delete plus restart, foreground, account switch, epoch switch,
  sign-out, reset, and deletion; no ZIP may survive or enter backup/cache. Honor
  retry intervals and bounded failed/expired/size-limit codes and preflight free
  space. On current/minimum physical devices download a synthetic ZIP at the
  exact maximum ready `byte_size=1_100_000_000`, prove JavaScript heap stays within a
  small fixed control allowance rather than file size, interrupt it at multiple
  offsets, and verify every partial/orphan is removed on restart/foreground.
  Before environment/account/epoch changes, `PrivateCache.clearAll`, sign-out,
  reset, deletion, or startup orphan purge, increment the generation and call
  native `cancelAndDrainAll(previousGeneration)` and
  await it; only then delete the root. Register this as an `AuthStore`,
  `EnvironmentBoundary`, and `PrivateCache` teardown extension. Increment its
  generation before cancellation and recheck that generation natively immediately
  before publish/rename/share, so a late completion cannot recreate or reveal a
  file. Race completion against each purge and prove no native callback can
  recreate or rename an export afterward.
- [ ] Run `npm test -- platform more --runInBand && npm run lint && npm run typecheck`; expect all pass.
- [ ] Commit: `git commit -m "feat: add native device and privacy controls"`.

## Task 9: Harden cache, accessibility, reliability, and the client test gate

**Files:**

- Create: `mobile/src/platform/connectivity.ts`
- Create: `mobile/src/platform/telemetry.ts`
- Create: `mobile/src/ui/AppErrorBoundary.tsx`
- Create: `mobile/tests/accessibility/appAccessibility.test.tsx`
- Create: `mobile/tests/privacy/noSensitivePersistence.test.ts`
- Create: `mobile/tests/reliability/recoveryMatrix.test.ts`
- Create: `mobile/tests/reliability/mobileTelemetry.test.ts`
- Create: `mobile/e2e/auth-to-practice.yaml`
- Modify: `mobile/src/features/auth/AuthCallbackScreen.tsx`
- Modify: `mobile/src/features/analysis/uploadMachine.ts`
- Modify: `mobile/src/features/analysis/BriefScreen.tsx`
- Modify: `mobile/src/features/practice/PracticeScreen.tsx`
- Modify: `mobile/src/features/progress/ProgressScreen.tsx`
- Modify: `mobile/src/features/today/TodayScreen.tsx`
- Modify: all feature screens only where tests identify defects
- Create: `mobile/README.md`

**Interfaces:**

- `recordMobileEvent(event, {entity, occurredAt})` accepts the backend’s closed
  enum and generated discriminated `MobileEventEntity` only: `current_auth`,
  owned upload, owned session, or owned proof-cycle target. It computes one stable
  local semantic key from the event plus canonical typed entity fields, persists
  one random 128-bit idempotency key before send, and silently drops disallowed
  fields. `occurredAt` is untrusted local queue-order/TTL metadata only and is
  never serialized into the generated request body. It never derives or submits
  a server UTC day or client timestamp. The backend resolves
  ownership and independently enforces one semantic receipt, so re-render,
  restart, retry, reinstall/new key, clock skew, and offline boundary replay
  cannot create another accepted event for that action.
- A private account/epoch-scoped telemetry queue holds at most 100 entries for
  seven days and sends only while authenticated, foregrounded, and online. It
  stores no email, media path, report/metric content, provider receipt, or bearer;
  overflow/expiry drops the oldest diagnostic and increments only a local coarse
  counter. Telemetry never blocks coaching, and upload reliability remains
  derived exclusively from backend upload/job transitions.
- `purgeForHistoryEpoch(previous, next)` is called before any render when epochs
  differ and clears QueryClient plus all old-epoch account cache/pending files.
  Its sole exception is a validated same-account history-reset
  `PendingPrivacyReplayEnvelope`; it is moved through `retainReplayOnly`, which
  deletes every unrelated field/file and permits only byte-identical terminal
  replay. No feature screen renders until the purge and envelope validation end.
- Error boundary surfaces a non-sensitive reference ID and retry/support action;
  it never serializes component props, API bodies, or local paths.

- [ ] Add a static privacy test scanning SecureStore keys, cache JSON fixtures,
  telemetry fixtures, console calls, and error-boundary output for email, token
  prefix, provider receipt, metric fields, report content, and source filenames.
- [ ] Add accessibility tests with RNTL queries for labels/roles/state, focus on
  errors, 200% text, reduced motion, non-color status, logical screen-reader
  order, and minimum target styles on all primary actions.
- [ ] Add recovery-matrix tests for offline launch, server outage, 401, 404 owned
  resource, 409 epoch, upload expiry, missing/corrupt durable source, queue delay, push denial,
  and app termination. Each has one explicit user-safe recovery.
- [ ] Add one-event-under-rerender/restart/retry tests for every callpoint. Emit
  `auth_completed` only after a 201 exchange activates the bearer; upload start/
  resume/complete/fail/cancel/duplicate events from durable upload-machine
  transitions; `analysis_started` from the single queued job response;
  `brief_viewed` after the first successful owned Brief render; practice start/
  complete from its durable evidence identity; matched re-film start/complete
  from the preserved comparison triple; and `week_two_return` only after one of
  those successful server-owned coaching actions while Today’s server-owned
  cohort day is 8–14—never for a launch or passive tab view alone. Key each by
  its allowed typed entity: `current_auth`; reservation upload ID; analysis/Brief
  session ID; or baseline-session/target-fingerprint/drill proof-cycle target.
  Exercise offline queue/expiry/cap, account/epoch purge, device clock ±48 hours,
  UTC boundary, reinstall with a new local key, 401, 409, 429, and app termination
  without duplicate server acceptance; a distinct next-day owned action remains
  admissible. Byte-compare the generated POST body and prove `occurredAt`, local
  queue fields, and any server-day candidate are absent rather than rejected by
  the backend as extra fields.
- [ ] Implement connectivity banners and bounded backoff without treating offline
  as invalid media. Queue only the approved practice mutation; all billing and
  account decisions require a live response.
- [ ] Implement first-party mobile events with app version/platform/coarse
  network only. Keep Release Plan Task 5’s Sentry crash adapter default-off until
  its disclosure and protected configuration are approved; do not add an
  advertising ID.
- [ ] Run the complete local gate:
  `npm ci`, `npm run api:check`, `npm run expo:doctor`, `npm run lint`,
  `npm run typecheck`, and `npm test -- --runInBand`. Expected result: all pass
  with no unexpected console output.
- [ ] Run `npx expo export --platform all --output-dir dist-check` and inspect
  the bundle for secrets/source media. Remove the generated `dist-check` after
  inspection; it is not committed.
- [ ] Commit: `git commit -m "test: harden mobile coaching client"`.

## Client plan completion gate

- [ ] Verify Today → Analyze → upload → leave → result/Brief → practice → matched
  re-film → Progress on one iPhone and one Android device.
- [ ] Verify denied permissions, import fallback, airplane mode, foreground/
  background, forced termination, simulated missing/corrupt source, and push-
  denied polling.
- [ ] Verify `history_epoch`, sign-out, self-revocation, history reset, and account
  deletion clear private state before another account can render.
- [ ] Verify no digital web checkout is reachable and gear opens only the
  configured Shopify HTTPS host.
- [ ] Record the client commit and buildable state. Do not claim TestFlight,
  Play testing, store review, or public release; those require Plan 4.
