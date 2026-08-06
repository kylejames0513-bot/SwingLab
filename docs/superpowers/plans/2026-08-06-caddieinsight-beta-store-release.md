# CaddieInsight Mobile Beta and Store Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the verified backend, Expo client, and native entitlements into reproducible signed builds, prove reliability/privacy/billing on real devices, run a controlled 30–75 golfer retention beta, and prepare provider-appropriate approval-gated App Store/Google Play launches while retaining the PWA fallback path.

**Architecture:** Ordinary pull requests run deterministic, credential-free mobile CI. Signed EAS builds and store submissions run only through protected manual environments. Development/internal builds use distinct non-store identities; preview and production share the one store identity/app record/product set but bind to staging and production backends respectively. Every environment keeps isolated databases, credentials, and default-off server flags. Universal/app links are served by the existing FastAPI domain and bound to exact provider identities. Evidence is captured in PII-free checklists and machine-readable summaries; backend deploy, PWA state, Apple review/publication, and Google review/publication remain separate release facts.

**Tech Stack:** GitHub Actions, Node 22.13/npm, Expo SDK 57/EAS Build, Jest/RNTL, Maestro Android smoke, pytest/security workflows, TestFlight, Google Play internal/closed testing, App Store Server Notifications V2, Google RTDN/Pub/Sub, first-party KPIs, App Store/Play crash consoles.

## Global Constraints

- Implementing workflows/configuration does not authorize running a paid EAS
  build, enrolling developer accounts, creating store records/products, changing
  DNS/domain files, deploying Railway, submitting, publishing, or inviting
  customers. Each external mutation requires a separate explicit approval.
- Commit no Apple/Google/Expo credentials, provisioning profiles, keystores,
  service-account JSON, APNs/FCM keys, reviewer credentials, or provider receipts.
- Production uses `com.caddieinsight.app`; development/staging append `.dev` and
  `.staging`. Verify the production identifier is available before registering;
  stop rather than silently selecting another identity.
- Store products are monthly/annual digital Pro only. Physical gear remains
  Shopify browser checkout and no external digital purchase link appears.
- PWA and legacy `/upload` remain the product fallback through beta/public
  rollout, but are not infrastructure-independent: after any recovery-fenced
  state exists, recovery-store head+chain readback is a permanent backend startup
  dependency and its outage is a full-service fail-closed incident.
- Reliability/privacy/security gates are hard stops. Missing product targets
  causes iteration and another cohort, not an unsafe public launch.
- Before each store action, re-check current Apple, Google, Expo, privacy, target
  SDK, billing, and account-deletion rules against official documentation.

---

## Task 1: Add credential-free mobile CI and contract drift gates

**Files:**

- Create: `.github/workflows/mobile-ci.yml`
- Modify: `.github/workflows/security.yml:1-80`
- Create: `.github/dependabot.yml`
- Modify: `mobile/package.json`
- Modify: `mobile/.gitignore`
- Create: `mobile/scripts/assert-clean-bundle.mjs`
- Create: `tests/test_mobile_ci_contract.py`

**Interfaces:**

- Mobile CI runs for changes under `mobile/**`, `docs/api/openapi-v1.json`,
  `scripts/export_openapi.py`, backend API contracts, or its workflow.
- Required commands: `npm ci`, `npm run api:check`, `npm run expo:doctor`,
  `npm run lint`, `npm run typecheck`, `npm test -- --runInBand --ci`,
  `npx expo export --platform all --output-dir dist-ci`, bundle scan, and
  production-dependency audit.
- EAS build/submit commands are absent from pull-request CI.

- [ ] Add a failing Python repository test that parses the workflow and requires
  Node 22.13, npm cache keyed by `mobile/package-lock.json`, `working-directory:
  mobile`, every command above, least-privilege `contents: read`, and no secret/
  EAS build/submit use.
- [ ] Create `mobile-ci.yml` on Ubuntu with a concurrency group that cancels
  superseded branch runs. Upload only Jest/Expo diagnostic artifacts; never
  upload caches, local media, SecureStore data, `.env`, or generated credentials.
- [ ] Implement `assert-clean-bundle.mjs` to reject `ciat_`, private-key markers,
  provider secret env names, service-account JSON keys, localhost production
  URLs, email fixture domains outside allowlisted test chunks, and source-video
  extensions in `dist-ci`.
- [ ] Extend security CI to run `npm audit --omit=dev --audit-level=high` from
  `mobile/`, while keeping pinned pip-audit, Bandit, and Gitleaks. Dependabot
  groups Expo-compatible npm updates but never auto-merges them.
- [ ] Run the workflow command sequence locally and
  `python -m pytest tests/test_mobile_ci_contract.py -q`; expect all pass.
- [ ] Commit: `git commit -m "ci: verify Expo mobile client"`.

## Task 2: Finalize deterministic EAS build profiles and version identity

**Files:**

- Modify: `mobile/app.config.ts`
- Modify: `mobile/eas.json`
- Modify: `mobile/package.json`
- Create: `mobile/.easignore`
- Create: `mobile/contracts/openapi-v1.json`
- Create: `mobile/scripts/syncOpenApiSnapshot.mjs`
- Modify: `.gitignore`
- Create: `mobile/scripts/collectEasXcarchive.mjs`
- Create: `mobile/tools/ios-export-transforms.json`
- Create: `mobile/android-proguard-rules.pro`
- Create: `mobile/src/config/buildIdentity.ts`
- Create: `mobile/tests/config/buildIdentity.test.ts`
- Create: `mobile/tests/config/easIgnore.test.ts`
- Create: `mobile/tests/config/easArtifactPaths.test.ts`
- Create: `mobile/tests/config/easXcarchiveHook.test.ts`
- Create: `mobile/tests/config/androidReleaseMinify.test.ts`
- Create: `scripts/verify_mobile_release_ref.py`
- Create: `tests/test_mobile_release_ref.py`
- Create: `.github/workflows/mobile-build.yml`
- Create: `docs/runbooks/mobile-builds.md`

**Interfaces:**

- EAS build profiles are exactly `development`, `internal`, `preview`, and
  `production`; submit profiles are `preview` and `production`.
- `development` uses dev identifiers and a development client;
  `internal` uses staging identifiers/internal ad hoc distribution with native
  billing disabled; `preview` uses the production bundle/package identity with
  the staging backend for TestFlight/Play internal billing; `production` uses
  that same store identity with the production backend. There is one Apple/Play
  app record and one product set, never an improvised staging store listing.
- Version 1 also uses one EAS project ID for the store `preview`/`production`
  profiles. Because an Expo push token can persist across their in-place upgrade,
  staging push is a bounded prelaunch lane and Task 9's recovery-fenced cutoff,
  TTL wait, sender-token revocation, and physical upgrade proof are mandatory
  before production push or public availability. Local cache purging is not that
  server-side revocation.
- `preview` and `production` explicitly set EAS `distribution: "store"`.
  Their Android profile is explicitly `buildType: "app-bundle"` and must produce
  an AAB; their iOS profile has no development client/simulator/ad-hoc setting and
  must produce a non-development App Store IPA. Among non-development profiles,
  only the separately named `internal` profile may use
  `distribution: "internal"`/ad-hoc signing and an installable APK. A generated
  default may never silently turn preview into internal distribution.
- App version is SemVer in source; iOS build number/Android versionCode use EAS
  remote versioning with auto-increment. Every build records Git commit SHA, the
  exact EAS project UUID, and SHA-256 of the canonical tracked
  `docs/api/openapi-v1.json`; these public values are embedded in resolved build
  identity and sanitized provenance.
- Android preview/production release builds explicitly set
  `expo-build-properties.android.enableMinifyInReleaseBuilds=true`; their R8
  mapping is therefore mandatory and is uploaded/read back through the Sentry
  gate. The repository-owned extra rules keep only reviewed reflection/native-
  bridge requirements for Expo/React Native, Sentry, `expo-iap`, file hashing,
  and local modules—never a blanket `-keep` that silently disables shrinking.
  Development/internal profiles may remain unminified but cannot satisfy signed
  preview/production evidence.
- EAS CLI is pinned by `cli.version: "21.6.0"` and
  `cli.requireCommit: true` in `eas.json`. The protected
  workflow requires independent
  `platform: ios|android|all` and `profile:
  development|internal|preview|production` inputs and writes the returned build
  IDs/artifact URLs to a sanitized JSON artifact.
- Preview and production profiles set EAS
  `buildArtifactPaths: ["eas-artifacts/CaddieInsight.xcarchive.zip",
  "eas-artifacts/CaddieInsight-export-metadata.zip"]` in addition
  to the ordinary application archive. `eas-build-pre-install` calls
  `collectEasXcarchive.mjs mark`; on iOS it records a start marker, and
  `eas-build-on-success` calls `collectEasXcarchive.mjs collect` after Fastlane
  succeeds but before EAS uploads additional artifacts. The collector searches
  only Xcode archives newer than that marker, validates bundle/version/build,
  requires exactly one match, and uses macOS `ditto` to place the complete
  `.xcarchive` at the configured artifact path. In the protected export-metadata
  sidecar it also captures the effective generated `ios/Gymfile`, the one
  relevant post-marker ExportOptions/xcdistribution export record, `xcodebuild
  -version`, `fastlane --version`, hashes of the source evidence/archive/IPA,
  and the exact EAS build ID. Raw sidecar content remains protected; public
  evidence contains only a schema-validated redacted parse. Both hooks are
  explicit no-ops on Android/local non-EAS runs. The
  protected workflow downloads the application archive plus all additional
  artifacts by exact immutable EAS build ID with
  `eas build:download --build-id <id> --all-artifacts --json`; on iOS it fails
  unless the exact build supplies one IPA, one complete xcarchive, and one export-
  metadata sidecar. It hashes the IPA, AAB, xcarchive, sidecar, and sanitized EAS metadata without uploading the
  signed binaries to ordinary GitHub artifacts. Pre-review delivery uploads that
  exact EAS build once; store submission later selects/promotes the resulting
  processed provider artifact by its EAS provenance, never uploads an
  independently rebuilt/local or duplicate archive.
- Every EAS CLI invocation runs with `mobile/` as its working directory; workflow
  config, local commands, and tests must fail if invoked from the repository root.
- `mobile/contracts/openapi-v1.json` is a byte-for-byte tracked mirror of the
  canonical `docs/api/openapi-v1.json`, produced only by
  `syncOpenApiSnapshot.mjs`. CI fails on any mismatch; build identity hashes the
  mirror, and the EAS archive must contain those exact bytes. This is packaged
  contract evidence, not a second schema authority or a runtime API fallback.
- A production dispatch is accepted only from the protected `main` ref at the
  freshly fetched `origin/main` SHA. `verify_mobile_release_ref.py` records that
  reviewed 40-hex SHA, proves it is the checked-out commit with a clean build-
  input tree, reads the branch-protection required-check set, and requires every
  latest required check for that SHA to be completed/successful. Its sanitized
  attestation binds the source SHA, protected ref, check names/conclusions, and
  timestamp to the build dispatch. Preview may use an approved non-main ref but
  can never be promoted or re-described as production.

- [ ] Add failing config tests for unique bundle/package IDs, matching schemes,
  HTTPS API base URLs, no production debugger/dev menu, feature environment,
  build profile, embedded commit SHA/EAS project UUID/OpenAPI SHA-256, iOS deployment target 16.4, Android
  minSdkVersion 24, and target/compile SDK readback that meets the current store
  requirement without a lower manual override.
- [ ] Implement the deterministic OpenAPI mirror script, regenerate the mirror,
  and make `api:check` byte-compare it with `../docs/api/openapi-v1.json` before
  checking generated TypeScript. Assert the build-identity hash comes from the
  mirrored bytes and fail on a stale, reformatted, missing, or archive-omitted
  snapshot.
- [ ] Add a failing profile/resolved-prebuild test requiring Android preview and
  production minification, absence of a false override in generated Gradle
  properties, exact repository extra-rule injection, and no blanket keep rule.
  Build a disposable signed/minified fixture and require an R8 mapping with the
  same mapping identifier later read by Sentry; missing mapping or a minified
  camera/storage/IAP/integrity smoke failure blocks the profile.
- [ ] Add resolved-EAS tests requiring literal `distribution:"store"` for preview/
  production, Android `buildType:"app-bundle"`/AAB, and iOS App Store archive/
  non-development signing with `developmentClient` and simulator false/absent.
  Reject internal/ad-hoc preview credentials, APK output, simulator/development
  IPA, or any resolved artifact extension other than `.aab`/`.ipa`. Test
  `development` separately as a direct-install development client; among the
  remaining profiles, only the named `internal` profile may resolve
  `distribution:"internal"`, ad-hoc signing, or APK output.
- [ ] Configure exact identities: development is
  `com.caddieinsight.app.dev`/`caddieinsight-dev`; internal is
  `com.caddieinsight.app.staging`/`caddieinsight-staging`; preview is
  `com.caddieinsight.app`/`caddieinsight-staging` against staging; production is
  `com.caddieinsight.app`/`caddieinsight` against production. Preview includes
  staging app-link domains; production includes production domains only.
- [ ] Set exact `cli.version: "21.6.0"` and `cli.requireCommit: true` in
  `eas.json`; config tests must fail if either changes. Do not install EAS CLI as
  a project dependency. All documentation and workflows invoke
  `npx --yes eas-cli@21.6.0`, never an unpinned global `eas` binary.
- [ ] Add the two package lifecycle-hook entries and unit tests for iOS/Android,
  missing EAS environment, missing/multiple/newer-than-marker archives, wrong
  bundle/version/build, paths with spaces, failed `ditto`, and cleanup. The
  collector reads `EAS_BUILD_PLATFORM`/`EAS_BUILD_WORKINGDIR`, never prints
  provisioning data, never follows an archive outside the ephemeral Xcode
  archive root, and fails an iOS preview/production build rather than uploading
  ambiguous evidence. Require exactly one relevant export record newer than the
  marker and bind its effective Gymfile/options/tool versions by hash to the
  archive, IPA, and EAS build ID; a missing, stale, or ambiguous record fails iOS
  preview/production. Redact signing/provisioning secrets into a separate
  sanitized schema while retaining the raw sidecar only in protected evidence
  storage. Do not customize the signed application archive or submit from the
  copied xcarchive. Add the literal anchored
  `/mobile/eas-artifacts/` repository-ignore rule and a test that fails if any
  generated archive/evidence binary is tracked.
- [ ] Configure and contract-test the literal two-path preview/production
  `buildArtifactPaths` value for the collector outputs. In a protected disposable directory, download
  the successful build with pinned
  `npx --yes eas-cli@21.6.0 build:download --build-id <id> --all-artifacts --json`
  and fail unless exactly one application IPA, one complete xcarchive, and one
  export-metadata sidecar are attached to that build. Record their SHA-256 hashes, EAS build ID, commit,
  bundle/version/build, signing-certificate fingerprint, and artifact IDs/URLs;
  retain binaries only in protected EAS/evidence storage under the approved
  retention policy. Test missing/stale/wrong-build/multiple sidecars, incorrect
  archive or IPA hash binding, tool-version mismatch, raw-to-sanitized redaction,
  leaked provisioning data rejection, and the exact build-number-equality path.
- [ ] Keep credentials remote/protected. `.easignore` must exclude `.env*`, tests,
  docs/evidence, local build outputs, media, Python sessions, and exact `/android`
  and `/ios` generated-project roots while including runtime assets and the npm
  lockfile. Add a test that requires the two literal anchored `.easignore` rules,
  verifies both native roots with `git check-ignore`, and fails if either path is
  tracked. Before any signed build, inspect the local EAS archive and fail if it
  contains either native root.
- [ ] Create a `workflow_dispatch` build workflow with a required choice
  for both platform and profile, protected environment matching the profile,
  least privileges, concurrency lock, `defaults.run.working-directory: mobile`,
  and an explicit dry-run metadata job. Before resolving config or creating an
  archive, run `git diff --quiet -- mobile docs/api/openapi-v1.json` and
  `git diff --cached --quiet -- mobile docs/api/openapi-v1.json` from the
  repository root and reject any output from
  `git ls-files --others --exclude-standard -- mobile docs/api/openapi-v1.json`.
  This clean-source gate is additional to `cli.requireCommit`; no ignored or
  untracked build input may be represented by the embedded commit. It
  runs `npx --yes eas-cli@21.6.0 build --platform <platform> --profile <profile>
  --non-interactive --wait --json` only after environment approval, parses and
  uploads build ID/URL/platform/profile/version/commit JSON, and never submits.
- [ ] In the protected dry-run job, run
  `npx --yes eas-cli@21.6.0 build:inspect --platform <ios|android> --profile
  <preview|production> --stage archive --output <protected-empty-directory>`
  from `mobile/` for the selected platform/profile. Hash a canonical sorted
  manifest of every archived relative path and byte length, require the archive
  to contain the exact tracked `mobile/contracts/openapi-v1.json` mirror of
  `docs/api/openapi-v1.json` used by
  `buildIdentity.ts`, and require the embedded commit/OpenAPI/project tuple to
  match the clean checked-out revision. Delete the inspected archive after its
  sanitized manifest/hash is bound to the dispatch. Missing canonical OpenAPI,
  extra ignored/untracked build input, or archive/source hash drift blocks the
  cloud build.
- [ ] Give the protected workflow only `contents: read` and `checks: read` beyond
  its build needs. For `profile=production`, fetch `origin/main` and tags with no
  shallow ambiguity, require `GITHUB_REF=refs/heads/main`, `GITHUB_SHA` equal to
  `refs/remotes/origin/main`, then run the release-ref verifier against GitHub's
  branch-protection/commit-check readback. Test arbitrary branch/tag/SHA, stale
  local main, missing/queued/skipped/failed required check, duplicate check name,
  dirty tracked/staged/untracked build input, and valid protected-main success.
  Ref or check failure happens before EAS credentials/build dispatch are used.
- [ ] Add a protected iOS evidence job that downloads both artifacts by the
  returned build ID, expands the IPA read-only, and compares its Payload app to
  the xcarchive Products app. Require the same immutable EAS build ID/commit,
  bundle ID/version, Team/application identifier, privacy manifests, nested
  app/extension/framework/bundle inventory, and entitlements after only reviewed
  distribution substitutions. Build number must match unless the captured,
  hash-bound export sidecar explicitly proves its managed build-number
  substitution; observing a mismatch or an unbound ExportOptions file is never
  sufficient. That one
  transform is permitted by the closed `ios-export-transforms.json` policy, the
  archive→IPA values/relation are recorded in per-build evidence, and the IPA
  value becomes the submission/store-build identity. Verify each artifact's signatures independently;
  do not require equal CDHash, `_CodeSignature`, CodeResources, provisioning UUID,
  or raw whole-file hash after Xcode export. Compare Mach-O code/resources with a
  normalizer that excludes only code-signature blobs, permitted architecture
  thinning, symbol stripping, provisioning replacement, and packaging metadata
  plus the explicitly evidenced build-number substitution enumerated in
  `ios-export-transforms.json`; an executable/resource/manifest/SDK
  drift outside that closed allowlist fails. Persist the sanitized
  provenance/hash result for Task 5; do not retain either binary in public CI.
- [ ] Document local and CI commands, expected artifact (`.ipa`/`.aab`), EAS URL,
  commit/version/build capture, symbols/source maps, failed-build diagnosis, and
  cancellation. State that workflow dispatch itself is an external build action.
- [ ] Run config unit tests plus
  `npx --yes eas-cli@21.6.0 config --platform <ios|android> --profile
  <development|internal|preview|production>` for all eight combinations from
  `mobile/`, locally without credentials/building. Persist no output. Expect
  resolved non-secret config, exact identifier/scheme/backend/associated-domain
  matrix, preview’s production store identity with staging services, and
  production’s production services. Also assert preview/production resolve store
  distribution with AAB/App Store IPA outputs while internal resolves only its
  explicitly non-store artifacts. Credential-free tests parse the workflow and
  `eas.json` to require both clean-index commands, untracked-input rejection,
  `cli.requireCommit:true`, and the per-platform `build:inspect --stage archive`
  OpenAPI/archive-manifest gate.
- [ ] Commit: `git commit -m "build: define protected mobile build profiles"`.

## Task 3: Serve and verify universal/app-link associations

**Files:**

- Create: `swinglab/web/mobile_associations.py`
- Modify: `swinglab/web/app.py:1215-1280`
- Modify: `mobile/app.config.ts`
- Create: `scripts/render_mobile_associations.py`
- Create: `tests/test_mobile_associations.py`
- Create: `mobile/tests/platform/associationConfig.test.ts`
- Modify: `docs/environment.md`
- Modify: `docs/deployment.md`

**Interfaces:**

- `GET /.well-known/apple-app-site-association` returns Apple JSON with no
  redirect/content extension and exact app IDs/paths.
- `GET /.well-known/assetlinks.json` returns Android JSON with package and exact
  Play signing SHA-256 certificate fingerprint.
- Allowlisted private paths are `/app/auth/callback`, `/analysis/*`, and
  `/brief/*`; public help/landing remains ordinary HTTPS.
- `render_mobile_associations(team_id, android_sha256, environment) -> dict`
  validates identifiers and never invents them.

- [ ] Add failing route tests for correct content type, no cache ambiguity,
  no redirect, exact paths, invalid/missing provider IDs, no wildcard private
  path, and absence of credentials/user data.
- [ ] Implement association payloads from environment-specific public identity
  configuration. With native links disabled or identities missing, return 404
  rather than an invalid permissive document.
- [ ] Configure iOS associated domains and Android HTTPS intent filters for the
  exact production/staging hosts and paths; keep custom schemes as safe fallback.
- [ ] Add a renderer command that consumes explicit `--apple-team-id` and
  `--android-sha256` or environment values and prints canonical JSON for provider
  console verification. Test with deterministic fake IDs only.
- [ ] Add live readback steps: open both HTTPS endpoints, compare to App Store
  Team ID and Play App Signing fingerprint, install a store-signed build, and
  verify cold/warm links plus invalid/cross-account fallbacks.
- [ ] Run `python -m pytest tests/test_mobile_associations.py -q` and the mobile
  association tests; expect all pass with feature disabled by default.
- [ ] Commit: `git commit -m "feat: add verified mobile app links"`.

## Task 4: Prepare privacy, legal, review, and store-listing artifacts

**Files:**

- Create: `mobile/store/metadata/en-US/name.txt`
- Create: `mobile/store/metadata/en-US/subtitle.txt`
- Create: `mobile/store/metadata/en-US/description.txt`
- Create: `mobile/store/metadata/en-US/keywords.txt`
- Create: `mobile/store/metadata/en-US/promotional-text.txt`
- Create: `mobile/store/metadata/en-US/release-notes.txt`
- Create: `mobile/store/review-notes.md`
- Create: `mobile/store/privacy-data-inventory.md`
- Create: `mobile/store/apple-required-reason-api-inventory.md`
- Create: `mobile/store/apple-sdk-privacy-inventory.md`
- Create: `mobile/store/apple-privacy-checklist.md`
- Create: `mobile/store/apple-export-compliance.md`
- Create: `mobile/store/google-data-safety-checklist.md`
- Create: `mobile/store/content-rating-checklist.md`
- Create: `mobile/store/store-console-declarations.md`
- Create: `mobile/store/screenshots/README.md`
- Create: `mobile/store/screenshots/manifest.schema.json`
- Create: `mobile/store/assets/README.md`
- Create: `scripts/verify_mobile_store_assets.py`
- Modify: `mobile/app.config.ts`
- Modify: `mobile/privacy/PrivacyInfo.xcprivacy`
- Modify: `mobile/tests/config/privacyManifest.test.ts`
- Create: `swinglab/templates/web_privacy.html.j2`
- Create: `swinglab/templates/web_terms.html.j2`
- Create: `swinglab/templates/web_support.html.j2`
- Create: `swinglab/templates/web_account_deletion.html.j2`
- Modify: `swinglab/web/app.py:1268-1394`
- Create: `tests/test_mobile_store_artifacts.py`
- Create: `tests/test_mobile_store_assets.py`
- Create: `tests/test_mobile_public_policies.py`

**Interfaces:**

- Store copy describes guided swing capture, one Caddie Brief, practice, matched
  re-film, and progress without unsupported ball-flight/3D/diagnostic claims.
- Permission strings state the immediate purpose for camera, selected videos, and
  notifications. Version 1 declares no location, tracking, contacts, Bluetooth,
  advertising ID, or cross-app tracking.
- Privacy inventory covers account/profile, user video, derived coaching/report,
  practice evidence, purchases, device push identifier, reliability/product
  events, retention, export, history reset, account deletion, and providers.
- The app privacy manifest and App Store privacy answers are generated from that
  inventory. First-party required-reason inventory names every call site and
  validates C617.1 app-container metadata, E174.1 observable free-space checks,
  and CA92.1 app-only preferences; no value from those APIs is used for
  fingerprinting. SDK inventory covers React Native/Expo modules, Sentry,
  `expo-iap`/OpenIAP, file-hash, and the local storage/integrity modules, recording
  package/version, source/binary form, manifest path/hash, signature identity when
  applicable, required-reason categories, and declared collection.
- The Apple export-compliance inventory enumerates app code and every linked SDK/
  native module that uses or supplies encryption, the use (including transport
  and protected storage), applicable exemption/documentation determination,
  reviewer/date/source, and any App Store Connect compliance-document key. It
  does not assume that TLS or a third-party SDK is automatically exempt. Only
  after the reviewed determination may `ios.config.usesNonExemptEncryption` set
  `ITSAppUsesNonExemptEncryption=false`; otherwise it is true and the required
  documentation/declaration must be approved and linked before beta/review.
- Public HTTPS routes `/privacy`, `/terms`, `/support`, and `/account-deletion`
  render the four repository-owned templates without authentication. The
  account-deletion page describes both the in-app deletion path and a working
  support fallback; it does not claim deletion occurs merely by visiting. It
  states that account deletion does not cancel Apple/Google subscriptions and
  links to both providers’ subscription-management paths before deletion.
- Production store URLs are exactly `https://app.caddieinsight.com/privacy`,
  `https://app.caddieinsight.com/terms`,
  `https://app.caddieinsight.com/support`, and
  `https://app.caddieinsight.com/account-deletion`; staging substitutes its
  approved application origin without changing paths.
- Files under `mobile/store/metadata/en-US/` are the review-controlled source of
  truth. Task 7 manually copies them into App Store Connect and Play Console,
  reads every field back, and records content hashes; this plan does not rely on
  preview EAS Metadata automation.
- Binary branding is exactly the Task 1 asset set: `mobile/assets/icon.png`,
  `adaptive-icon.png`, `monochrome-icon.png`, `splash-icon.png`, and
  `notification-icon.png`, with installed name `CaddieInsight` and slug
  `caddieinsight`. `mobile/store/assets/README.md` records approved source,
  reviewer, export sizes, safe zones, colors, and content hashes.
- `store-console-declarations.md` is a versioned decision matrix, not a loose
  checklist. For each console it records the current section/field, whether it
  is always required or conditional, applicability and rationale, the exact
  reviewed answer (or a linked controlled worksheet), repository evidence,
  official source URL and review date, owner/reviewer, console path, and last
  read-back status/hash. At minimum it covers Apple primary language, primary/
  secondary category, content rights, age rating, copyright, app price,
  storefront availability, support/privacy/contact information, review access,
  and conditional EU trader/regional compliance; and Google app access, ads,
  target audience/content, content rating, Data Safety, privacy/account deletion,
  Health apps declaration, category/tags, store contact details, and conditional
  news/government/financial/VPN/sensitive-permission/advertising-ID declarations.
  A conditional section is never silently omitted: it has an evidence-backed
  `applicable` or `not_applicable` decision. Console-specific privacy, export-
  compliance, and rating worksheets remain linked source records rather than
  duplicated prose.
- Store-listing images are controlled release artifacts. The manifest schema
  binds every screenshot to platform, locale, ordered slot, exact EAS build ID,
  commit and UI-bundle/screenshot-surface config hash, backend fixture/environment, device/model/OS,
  dimensions, format/alpha, capture time, synthetic-data fixture, alt text,
  reviewer, and SHA-256. It binds non-screenshot graphics to their approved
  source/export hashes. Draft assets use the six feature slots Today, guided
  capture, Caddie Brief, Practice, matched progress, and More/Pro. At execution
  Version 1's verified `ios.supportsTablet=false` contract excludes native iPad
  UI but the iPhone app can still run on iPad in compatibility mode, and it does
  not exclude compatible-app distribution on Apple-silicon Mac or Apple Vision
  Pro. The iPad smoke plus provider-authoritative Mac/Vision opt-out/readbacks
  below are required for the narrow Apple scope. Android phone-only assets remain provisional until the
  exact signed AAB and Play Device Catalog readback show zero targetable tablet,
  Chromebook, desktop, TV, Wear, Automotive, or XR form factors. Any targeted
  non-phone form factor expands scope and must pass its current layout/device QA,
  declarations, and required/recommended screenshot set (or the app must be
  rebuilt and fully re-attested with a reviewed manifest policy that excludes
  it). At execution time the policy snapshot records the exact accepted Apple
  display sizes and
  requires 1–10 JPEG/PNG screenshots for the selected iPhone display class; the
  Google phone set uses six 1080x1920 portrait JPEG/24-bit PNG images (within the
  current 2–8 requirement), a 512x512 32-bit PNG-with-alpha listing icon no
  larger than 1024 KB, and a 1024x500 JPEG/24-bit PNG feature graphic with no
  alpha. Every Google image has reviewed alt text of at most 140 characters.
  Apple subscription review screenshots are separate, one controlled artifact
  for each monthly/annual product, and never masquerade as public-listing slots.
  The public More/Pro slot is captured from a deterministic ordinary Free
  passwordless capture account, framed to the real screen's state-invariant,
  nontransactional comparison region: truthful Free/Pro feature summary, Free-
  coaching continuity, and Terms/Privacy links. It makes no availability claim
  and contains no localized price, purchase CTA, sheet, or charge. The admission-
  specific control region is outside the frame. UI/snapshot tests require that
  exact captured region and its screenshot-surface config hash to be identical
  with ordinary purchase admission off and on; there is no screenshot-only UI
  mode. It is never captured from `review_demo_active` or purchase-test state.
  The verifier rejects `Temporary store-review access`, `store review`, test-
  purchase notices/instruments, sandbox labels, or any review-only badge/copy in
  every public-listing slot. Provider-specific review/demo fixtures may supply
  synthetic coaching data for feature slots only when no review-only state is
  visible; their Apple IAP review images remain in the separate review-artifact
  class.
  UI tests require no review/demo chrome on Today, Capture, Brief, Practice, or
  Progress while requiring the temporary-access disclosure in More/Pro and
  review/privacy account controls; this makes the five coaching captures from the
  seeded review fixture verifiably clean without hiding the disclosure from the
  reviewer.

- [ ] Add a failing artifact test for all required files, bounded names/subtitles/
  descriptions, working HTTPS support/privacy/account-deletion URLs, no price
  claims, no “fix your swing” promise, no location/tracking declaration, and
  explicit monthly/annual auto-renewal language, and no claim that video keeps
  transferring in the background.
- [ ] Extend the artifact test to decode every configured binary PNG, verify the
  exact dimensions/alpha/color constraints from Client Task 1, ensure all paths
  resolve, assert installed name/slug, and reject Expo template hashes or sample
  art. Record approved source/export provenance and content hashes before the
  first signed build.
- [ ] Add adversarial scope-isolation fixtures: `apple-public` and
  `apple-submission` must pass with every Google asset absent or corrupt;
  `google-submission` must pass with every Apple/IAP asset absent or corrupt.
  Each scope must still fail on its own missing, stale, malformed, misordered, or
  wrong-build asset. Parse the verifier dispatch so no provider scope can call or
  inherit `all`; this test prevents future cross-provider release coupling.
- [ ] Add a failing declaration-matrix test requiring every currently visible
  mandatory console section plus every named baseline section above, an exact
  reviewed answer or controlled-workbook link, conditional applicability with a
  non-placeholder rationale, official source/review date, repository evidence,
  owner/reviewer, and read-back state. Fail on blank/TBD answers, stale source
  reviews, contradictory pricing/privacy/rating answers, or an unclassified new
  console section captured by the execution-time inventory.
- [ ] Write original customer-facing metadata using the approved coach-first
  hierarchy. Do not present the Shopify shop as an app purpose or mention later
  GPS/community functionality as currently available.
- [ ] Write Apple privacy and Google Data Safety worksheets from actual code/data
  flows, not aspirational answers. Identify Expo push, Apple/Google billing,
  Railway processing, Shopify gear handoff, and optional diagnostics separately.
  Include Play Integrity’s disclosed request/app/license/device processing and
  the protected-review-only purpose; it is not advertising or cross-app tracking.
- [ ] Reconcile the data inventory into `PrivacyInfo.xcprivacy` collected-data/
  purpose/linkage/tracking declarations and the App Store privacy worksheet.
  Validate the plist and config-plugin output. Add a source/dependency scan that
  fails on a first-party required-reason call without an approved entry or a new
  SDK/native binary without inventory/manifest/signature review.
- [ ] Add export-compliance tests requiring the reviewed inventory, explicit
  boolean config, inventory hash, and a decision for every linked native SDK/
  module. Inspect disposable prebuild Info.plist output and fail if the key is
  missing, differs from the determination, or a required documentation key is
  absent; when classification is uncertain, stop for qualified compliance review
  rather than defaulting to `false`.
- [ ] Disclose the exact deleted-account Apple/Google credential rule: user
  binding is removed immediately; AES-GCM ciphertext is retained for provider
  lifecycle processing until the earlier of 30 days after a known terminal state
  or 90 days after deletion; only a non-reversible replay tombstone remains after.
- [ ] Add the four public policy/support routes and templates using the approved
  inventory. Test unauthenticated 200 responses, canonical links, noindex on the
  support/account-deletion forms where appropriate, and the absence of customer
  data, secrets, or unsupported deletion promises. Test the explicit Apple/
  Google auto-renewal warning and working provider management links.
- [ ] Add just-in-time purpose strings and verify the binary contains no unused
  permission descriptions. Camera records a swing; selected-video access imports
  a swing; notifications announce readiness/reminders generically.
- [ ] Define the screenshot manifest/schema, six ordered public-listing slots,
  two Apple subscription-review slots, Google listing-icon/feature-graphic
  exports, and a deterministic synthetic account/fixture. Screens contain no
  real golfer video, name/email, receipt/order/transaction, auth code, device/
  push token, notification, debug menu, staging label, or status-bar carrier.
  `verify_mobile_store_assets.py` decodes every file, validates current provider
  count/dimension/format/alpha/size rules, order/locale/alt text and SHA-256,
  scans OCR/metadata for PII/test/debug content, and rejects a screenshot whose
  build/commit/UI/screenshot-surface-config/fixture binding is stale. Before any
  fixture-backed coaching capture can be accepted, the workflow seals a sanitized
  fixture-capture attestation containing provider, synthetic generation/epoch,
  packaged template SHA-256, aggregate entity/media counts, build/UI/config/asset
  hashes, and capture time. Later terminal deletion of an Apple review generation
  is expected and does not invalidate already sealed pixel provenance: the
  verifier compares the attestation to the immutable packaged template and asset
  hashes rather than requiring that synthetic user to remain live. A changed
  template, build, UI, screenshot-surface config, capture, or attestation still
  invalidates the asset. Its bounded
  `--scope coaching` validates only the five named coaching slots;
  `--scope apple-public` adds Apple's ordinary-Free More/Pro slot and all six
  Apple public-listing screenshots; `--scope apple-submission` adds both Apple
  subscription-review artifacts; and `--scope google-submission` validates all
  six Google public-listing screenshots plus its icon and feature graphic.
  `--scope public` and `--scope all` are optional combined QA aggregates only,
  never submission gates. Apple submission requires only `apple-submission` and
  Google submission requires only `google-submission`; failure or drift in one
  scope cannot block the other. Purchase testing may stay closed for coaching,
  Apple-public, and Google-submission. Do not create final images before signed UI QA;
  placeholders cannot satisfy any scope.
- [ ] Review current official guidelines at execution time:
  [Apple App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/),
  [Google Play payments](https://support.google.com/googleplay/android-developer/answer/9858738),
  [Apple screenshot specifications](https://developer.apple.com/help/app-store-connect/manage-app-information/upload-app-previews-and-screenshots),
  [Google preview-asset requirements](https://support.google.com/googleplay/android-developer/answer/9866151),
  [Android screen-support manifest](https://developer.android.com/guide/topics/manifest/supports-screens-element),
  [Google Play Device Catalog](https://support.google.com/googleplay/android-developer/answer/7353455),
  [Google target-audience declarations](https://support.google.com/googleplay/android-developer/answer/9867159),
  [Google Health apps declaration](https://support.google.com/googleplay/android-developer/answer/14738291),
  [Apple app information](https://developer.apple.com/help/app-store-connect/reference/app-information/app-information),
  [Apple pricing/availability](https://developer.apple.com/help/app-store-connect/reference/pricing-and-availability/app-pricing-and-availability/),
  and [Expo SDK 57](https://docs.expo.dev/versions/v57.0.0/). Record review date,
  required/conditional console sections, exact image rules, and any changed
  requirement in the controlled checklists/matrix.
- [ ] Run `python -m pytest tests/test_mobile_store_artifacts.py tests/test_mobile_store_assets.py tests/test_mobile_public_policies.py -q`; expect all pass.
- [ ] Commit: `git commit -m "docs: prepare mobile store disclosures"`.

## Task 5: Create the real-device, billing, security, and recovery evidence harness

**Files:**

- Create: `docs/qa/mobile-device-matrix.md`
- Create: `docs/qa/mobile-billing-matrix.md`
- Create: `docs/qa/mobile-recovery-matrix.md`
- Create: `docs/qa/mobile-security-checklist.md`
- Create: `mobile/evidence/schema.json`
- Create: `mobile/evidence/console-readback.schema.json`
- Create: `mobile/evidence/README.md`
- Create: `scripts/validate_mobile_evidence.py`
- Create: `scripts/capture_mobile_console_evidence.py`
- Create: `scripts/verify_mobile_console_evidence.py`
- Create: `scripts/manage_mobile_evidence_keys.py`
- Create: `scripts/verify_ios_privacy_archive.py`
- Create: `scripts/verify_android_16k_alignment.py`
- Create: `scripts/fetch_mobile_release_tools.py`
- Create: `scripts/verify_sentry_mobile_release.py`
- Create: `scripts/verify_mobile_api_deployment.py`
- Create: `scripts/create_mobile_api_compatibility_attestation.py`
- Create: `mobile/evidence/mobile-api-compatibility.schema.json`
- Create: `mobile/evidence/mobile-api-behavior-v1.json`
- Create: `scripts/run_mobile_api_compatibility.py`
- Create: `mobile/tools/release-tools.lock.json`
- Create: `tests/test_mobile_evidence.py`
- Create: `tests/test_mobile_console_evidence.py`
- Create: `tests/test_ios_privacy_archive.py`
- Create: `tests/test_android_16k_alignment.py`
- Create: `tests/test_mobile_release_tools.py`
- Create: `tests/test_sentry_mobile_release.py`
- Create: `tests/test_mobile_api_deployment.py`
- Create: `tests/test_mobile_api_compatibility.py`
- Create: `tests/test_mobile_api_compatibility_runner.py`
- Create: `docs/qa/mobile-ios-privacy-archive.md`
- Create: `docs/qa/mobile-android-16k.md`
- Create: `.github/workflows/mobile-android-smoke.yml`
- Modify: `mobile/package.json`
- Modify: `mobile/package-lock.json`
- Modify: `mobile/app.config.ts`
- Modify: `mobile/eas.json`
- Create: `mobile/metro.config.js`
- Create: `mobile/src/platform/crashReporting.ts`
- Create: `mobile/tests/privacy/crashReporting.test.ts`
- Modify: `.github/workflows/mobile-build.yml`
- Modify: `docs/runbooks/mobile-builds.md`
- Modify: `pyproject.toml`

**Interfaces:**

- Evidence records environment, platform/OS/device class, app/build/commit,
  backend commit/health, scenario ID, result, timestamp, sanitized attachment
  path, and tester role. It forbids names, emails, video, metrics, receipts,
  provider tokens, device IDs, and credentials.
- `verify_mobile_api_deployment.py --base-url <https-origin> --snapshot
  docs/api/openapi-v1.json --build-identity <sanitized-build-json> --json` follows
  no redirect, fetches `/healthz` plus `/openapi.json`, canonicalizes the live
  schema with the backend's sorted compact JSON contract, and requires one exact
  SHA-256 across local snapshot, live schema, live health, and the store build's
  embedded build identity. It also requires live baked source commit, configured
  environment, canonical origin, and EAS project UUID to equal that immutable
  build's attestation, plus the build's exact application ID to appear in health's
  closed active-environment list at the expected policy revision. Output is
  hashes/identities/status only; a
  network error, runtime-generated drift, mutable-env commit claim, or mismatch is
  a hard failure, never a compatibility guess.
- Exact client/backend commit equality is the default. If separately dispatched
  platform builds or a necessary backend hotfix produces different commits, the
  verifier accepts only `--compatibility-attestation <signed-json>`. Its closed
  payload binds kind/schema, exact client and backend 40-hex commits, the one
  equal OpenAPI SHA-256, release version, canonical behavior-suite manifest/hash,
  protected required-check names/run IDs/conclusions for both commits, creation/
  expiry (at most 30 days), approver role, and release-evidence signing-key ID.
  `create_mobile_api_compatibility_attestation.py` reads the private Ed25519 key
  only from `MOBILE_EVIDENCE_SIGNING_KEY_FD` and may sign only after fresh clean-
  checkout backend tests, deterministic OpenAPI export, client `api:check`/
  typecheck, and the versioned mobile behavior suite all pass against that exact
  pair. Verification uses the packaged active public registry and rejects stale/
  revoked keys, changed hashes/checks, expiry, unknown fields, or a third commit.
  This allows compatible provider-independent builds/hotfixes without treating
  equal schemas alone as behavioral compatibility.
- `mobile-api-behavior-v1.json` is the closed, versioned cross-commit suite for
  every client-used route and semantic branch: auth 201/202 replay, identity/
  ownership/error envelopes, profile/capabilities, upload create/chunk/status/
  complete/retry/discard, analysis/Brief/practice/progress, device/push, privacy
  step-up/export/reset/delete, telemetry, and billing config/intent/provider
  results. `run_mobile_api_compatibility.py --client-commit A --backend-commit B
  --work-root <empty-protected-dir> --output <result-json>` verifies trusted refs,
  creates isolated read-only clean worktrees, starts only backend-B with a fresh
  fixture database/providers off, runs client-A's generated transport and the
  manifest's exact requests/assertions against it, and emits canonical bounded
  results plus manifest/test/source hashes. It cleans both worktrees/processes on
  success or interruption. The signer consumes only that successful result JSON;
  separate backend tests and client typecheck are prerequisites, not substitutes
  for this cross-commit execution.
- A console-readback evidence envelope is exactly one object
  `{payload:{...closed unsigned fields...},signature:"<base64url>"}`. Extra keys
  and padded/noncanonical base64url are rejected. Only `payload` uses the closed
  schema and canonical JSON
  encoding (UTF-8, NFC strings, lexicographically sorted object keys, no floats,
  no insignificant whitespace). It binds kind
  `credential_login_verified|credential_old_absent|app_access_clear`, provider,
  deployment environment, canonical backend origin/commit, exact application ID/
  version/build and processed store artifact ID/hash,
  console field revision/readback SHA-256, opaque clean-login/delisting evidence
  ID, UTC capture time, independent approver ID, and signing-key ID. The wrapper's
  top-level `signature` is detached base64url Ed25519 over only those canonical
  payload bytes. It contains no
  reviewer account/password, email, bearer, provider token, receipt, or console
  session data.
- `capture_mobile_console_evidence.py` reads a bounded redacted console readback
  from stdin and the private signing key only from an inherited descriptor named
  by `MOBILE_EVIDENCE_SIGNING_KEY_FD`; key bytes are rejected from argv, ordinary
  environment values, repository files, logs, and output. It validates all exact
  artifact/app bindings, computes the readback digest, signs, writes mode-0600 to
  an explicit protected output path, and immediately self-verifies.
  Its exact CLI is `python scripts/capture_mobile_console_evidence.py --kind
  <closed-kind> --provider apple|google --environment staging|production
  --backend-origin <https-origin> --backend-commit <sha> --application-id <id> --app-version
  <version> --app-build <build> --artifact-id <opaque-id> --artifact-sha256
  <64hex> --console-field-revision <opaque-revision> --evidence-id <opaque-id>
  --approver-id <non-PII-role-id> --captured-at <utc> --output
  <protected-absolute-json> --readback-stdin`; it rejects unknown/duplicate flags
  and obtains no semantic binding from the readback body itself.
  `verify_mobile_console_evidence.py` and the review-access CLI load only packaged
  `swinglab/entitlements/release_evidence_keys.json`, require a production-active
  key and capture time within 30 minutes with at most two minutes clock skew, and
  fail closed on unknown/retired keys, bad signatures, wrong kind/provider/
  environment/backend/build,
  replay conflict, or malformed/PII-bearing input.
  Verification uses `python scripts/verify_mobile_console_evidence.py --envelope
  <protected-absolute-json> --expected-kind <closed-kind> --provider apple|google
  --environment staging|production --backend-origin <https-origin>
  --backend-commit <sha> --application-id <id> --app-version <version> --app-build <build> --artifact-id
  <opaque-id> --artifact-sha256 <64hex> --json` and emits sanitized hashes/status
  only. Review-access receives the exact wrapper through
  `Get-Content -Raw -LiteralPath <protected-json> | swinglab review-access ...
  --console-evidence-stdin`; each operation uses a fresh envelope/evidence ID.
- Under separate credential approval,
  `manage_mobile_evidence_keys.py generate --private-out <protected-absolute-path>
  --public-out <temporary-public-json>` creates a mode-0600 Ed25519 private key
  outside the repository plus its non-secret public record. Rotation commits a
  second public key as `pending`, builds/tests the verifier, changes it to `active`
  while the old key remains active, signs and consumes a staging proof, waits past
  the 30-minute freshness bound with zero nonterminal operations using the old key,
  then marks the old public key `retired` and rebuilds. Only public key ID/bytes/
  activation/retirement times are committed and packaged; production review CLI
  mutations stay impossible while the registry has no active production key.
- Task 5 implements/tests only the signer, verifier, key-management tooling, and
  empty-registry fail-closed behavior; it does not generate a real key or modify
  `swinglab/entitlements/release_evidence_keys.json`. Task 7 alone owns the
  separately approved transition from the Entitlements plan's intentionally empty
  registry to a production-active public trust root.
- Required matrix entries are: iPhone SE (3rd generation) iOS 16.4 simulator and
  Android API 24 360×640/2-GiB emulator for minimum install/layout only; a
  physical iPhone SE (2nd/3rd generation) actually running the declared minimum
  iOS 16.4 and a physical Android device actually running API 24 for minimum-
  supported hardware paths; physical iPhone 15 or newer on latest stable iOS;
  a current iPad simulator plus one physical current iPad running the iPhone app
  in compatibility mode for install, auth, capture/import fallback, upload,
  coaching, privacy and billing-layout smoke (without representing native iPad UI
  or requiring iPad listing screenshots unless App Store Connect classifies the
  build as iPad-targeted);
  physical
  Pixel 8a or newer on the latest stable Android; and physical Samsung Galaxy
  A14/A15 class hardware for constrained storage/performance. Record exact model,
  OS build, free storage, and whether evidence is simulator or physical. This
  phone matrix is sufficient only after exact signed-AAB Device Catalog readback
  proves zero targetable Android non-phone form factors; otherwise add
  representative physical/emulated layout, interaction, performance, and store-
  screenshot evidence for every targeted form factor before beta/review.
- Android Maestro smoke is manual/scheduled against fixture data; camera,
  real billing, email links, provider push, background transitions, and iOS
  remain real-device evidence.
- `CrashReporter.init(buildIdentity)` wraps `@sentry/react-native@8.22.0` and is
  disabled when DSN or server capability is absent. Release/dist/platform are
  exact; automatic session tracking is on; tracing and replay are off;
  `sendDefaultPii=false`; request/user/breadcrumb/path/media attachments are
  stripped before send. `beforeSend` deletes the entire user/request/path surface,
  including `user.ip_address`, `user.ipAddress`, and any `{{auto}}` value; it never
  substitutes an installation/account/device identifier. The Sentry organization/
  project readback must also show `scrubIPAddresses=true`, server-side data
  scrubbing and default scrubbers enabled, reviewed sensitive/safe-field lists,
  no unreviewed advanced PII rule, and native crash-report storage disabled or
  bounded by the approved inventory. Sentry Release Health supplies one crash-free-session
  definition split by platform and immutable build.
- The Sentry Expo/Metro/Xcode/Gradle integrations upload the exact embedded
  JavaScript debug-ID source maps, iOS dSYMs, and Android ProGuard/R8 mapping
  during each protected preview/production EAS build. Runtime and upload use the
  same immutable release/dist derived from bundle/package, app version, native
  build number/versionCode, platform, and commit; sanitized build evidence binds
  those values to the EAS build ID. `SENTRY_AUTH_TOKEN` exists only in the
  approved EAS/CI environment, never as `EXPO_PUBLIC_*`; org/project and DSN are
  environment-bound configuration. `SENTRY_ALLOW_FAILURE=false`, so missing
  production config or any symbol/source-map upload failure fails the signed
  build. Credential-free ordinary CI validates resolved config without uploading.
  A protected post-build verifier polls Sentry for the exact release/dist and
  requires every expected JS debug ID, Mach-O UUID, and Android mapping identifier
  before that EAS build is eligible for device QA; it never prints the token or
  downloads customer events.
- `verify_ios_privacy_archive.py --archive <xcarchive> --ipa <ipa>
  --eas-build-metadata <json> --export-metadata-sidecar <zip>
  --export-transform-policy mobile/tools/ios-export-transforms.json --inventory
  mobile/store/apple-sdk-privacy-inventory.md --aggregate-report <path>` inspects
  the exact signed archive selected for submission. Its sanitized JSON evidence
  records build/commit, root and embedded `PrivacyInfo.xcprivacy` paths plus
  SHA-256 hashes, every embedded framework/xcframework/bundle, expected versus
  observed SDK code-signing identities, and Xcode aggregate privacy-report
  reconciliation against the repository inventory and App Store answers. A
  missing/malformed manifest, unreviewed binary, signature mismatch/failure, or
  declaration/report mismatch is a non-waivable submission failure. It also
  independently repeats Task 2's normalized export-provenance comparison. It
  verifies the protected sidecar hash/bindings, exactly one export record, tool
  versions, redacted parse, and closed transform policy, and fails unless the
  archive, IPA, sidecar, and EAS metadata belong to the exact EAS build ID that
  submission references. A build-number mismatch without sidecar-proven managed
  substitution fails.
- The same verifier requires `--export-compliance-inventory
  mobile/store/apple-export-compliance.md`, records its hash and the signed
  `ITSAppUsesNonExemptEncryption` value, and fails if the archive/IPA value is
  missing, inconsistent, or the evidence says required documentation is not
  approved/linked. It verifies the declared artifact; it never decides the legal
  classification itself.
- `mobile/tools/release-tools.lock.json` is the only release-tool authority. It
  records non-placeholder bundletool version, official immutable HTTPS release
  URL, SHA-256, JAR entrypoint/version probe, and the approved Android SDK build-
  tools package revision plus host-specific `zipalign` SHA-256. The fetch script
  allows only the reviewed Google/bundletool release and Android repository
  hosts, downloads into a content-addressed cache, verifies bytes before rename/
  execution, and never resolves `latest` or accepts a caller checksum.
- `verify_android_16k_alignment.py --aab <signed-aab> --tool-lock
  mobile/tools/release-tools.lock.json --tool-cache <path> --sdk-root <path>`
  expands the exact release artifact and records every native
  library. It requires bundle config `PAGE_ALIGNMENT_16K`, every 64-bit ELF
  `LOAD` segment alignment of at least `2**14`, and
  `zipalign -v -c -P 16 4` success for generated APKs. Any incompatible `.so`
  from Expo/React Native, Sentry, `expo-iap`, file-hash, or a local module is a
  hard stop; absence of native libraries is reported explicitly rather than
  assumed. Before inspection it compares bundletool/zipalign bytes, probed
  version, SDK `source.properties`, and lock revision; evidence records all
  versions/digests and fails closed on any mismatch or unpinned executable.

- [ ] Add failing validator tests for every required scenario, duplicate/missing
  records, unsupported builds, stale backend health, PII/token patterns, and hard
  gate failure. A red scenario cannot be waived by the script.
- [ ] Add signer/verifier/package tests for deterministic canonical bytes,
  stdin/descriptor-only inputs, 0600 output, exact app/artifact/kind binding,
  self-verification, invalid signature, unknown/pending/retired key, 30-minute
  freshness and clock skew, PII/secret patterns, replay/conflict, and public-key
  rotation overlap/retirement. Build the wheel and production Docker image and
  prove the CLI loads the exact committed public registry while private bytes are
  absent from Git, image layers, process argv/environment dumps, and logs.
- [ ] Install `@sentry/react-native@8.22.0`, configure its Expo/Metro integration,
  and add privacy tests proving absent config makes zero calls and sanitized
  fixture crashes contain no email, account ID, bearer, URL/query, local path,
  report/metric content, video attachment, screenshot, view hierarchy, tracing,
  or replay. Only platform/OS/device class, release, dist, and stack are allowed.
- [ ] Add failing build-integration tests for a single release/dist value shared
  by runtime and the Expo/Metro/Xcode/Gradle upload steps, exact version/build/
  platform/commit binding, missing production org/project/DSN/token, wrong EAS
  environment, source-map debug-ID drift, dSYM UUID drift, Android mapping-ID
  drift, and any upload failure. Configure protected preview/production builds
  with `SENTRY_ALLOW_FAILURE=false`; ordinary credential-free CI must prove it
  neither needs nor reads an upload token.
- [ ] Implement `verify_sentry_mobile_release.py` and mocked tests for bounded
  polling/readback of the exact release/dist, expected JS debug IDs, all app/
  embedded Mach-O UUIDs, Android mapping identifier, EAS build ID, and commit.
  Reject missing/extra/wrong-build artifacts and sanitize responses; the script
  accepts its API token only from protected process input and never prints or
  persists it.
- [ ] Add a release-health evidence schema with sessions, crashed sessions,
  crash-free percentage, platform, release/dist, observation window, and provider
  query timestamp. Require at least 200 sessions per platform before the numeric
  beta threshold can pass; smaller samples remain “insufficient,” never green.
- [ ] Add deliberately incomplete/mismatched signed-archive fixtures and tests
  proving the iOS verifier rejects a missing app-root manifest, malformed plist,
  missing embedded SDK manifest, unknown binary, invalid/unexpected SDK
  signature, required-reason mismatch, stale inventory hash, and Xcode aggregate
  report/App Store-answer mismatch, wrong EAS build ID, or IPA/xcarchive code/
  resource/privacy/SDK drift outside the closed export-transform allowlist. Add
  positive fixtures for legitimate re-signing, provisioning replacement,
  architecture thinning, symbol stripping, and packaging changes; prove none are
  mistaken for source drift. Download the application and all artifacts by the exact EAS
  build ID, export Xcode’s aggregate privacy report from that `.xcarchive`, run
  `codesign --verify --deep --strict`, run the verifier against both artifacts,
  and retain only its PII-free evidence plus the reviewed report.
- [ ] Add incompatible ELF/ZIP fixtures and tests proving the Android verifier
  rejects any 4 KiB-aligned 64-bit native library, bad APK ZIP alignment, missing
  `PAGE_ALIGNMENT_16K`, tool/version drift, or an artifact other than the signed
  release AAB. On a 16 KiB page-size emulator or physical device, require
  `adb shell getconf PAGE_SIZE` to return `16384`, disable compatibility mode,
  then install/launch and exercise camera/import, private storage, 500 MiB hash/
  upload, Play Integrity, IAP, background, and termination recovery. Record the
  current Play pre-launch/policy readback; do not substitute a normal 4 KiB
  device run.
- [ ] Add lock/fetch tests for placeholder/missing version, mutable or non-
  allowlisted URL, wrong bundletool/zipalign bytes, version-probe mismatch,
  unsupported host, partial download, cache poisoning, redirect to an unapproved
  host, and exact verified cache reuse. Populate the lock from reviewed official
  release artifacts during implementation, commit the exact version/URL/digests,
  and require code review for every update; the release verifier never downloads
  tools implicitly.
- [ ] Enumerate capture/import codecs, portrait/landscape, denied/limited
  permission, low storage, 500 MiB boundary, Wi-Fi/cellular/airplane transitions,
  background/termination, foreground upload reconciliation/resume, queue, push,
  same-device email link, second-device non-consumption plus safe restart/manual-
  code handoff, full coaching loop, gear checkout handoff, export/reset/delete, and
  accessibility at 200% text/screen reader/reduced motion.
- [ ] Define four non-waivable native-export artifact scenarios: exact processed
  iOS preview, Android preview, iOS production, and Android production store-
  installed builds. Each must exercise direct same-origin 200, same/cross-origin
  redirect rejection before Authorization, content-type/length/ZIP/path/result
  validation, an exact 1,100,000,000-byte export with bounded JavaScript heap on physical
  hardware, per-operation cancellation/drain with two concurrent downloads, and
  sign-out/environment/history-reset/account-delete purge races with no late file
  publication. Evidence binds the processed provider artifact ID/hash, EAS build
  ID, backend commit, native-module binary/hash, device/OS, and scenario result;
  a development/ad-hoc/rebuilt binary cannot satisfy it.
- [ ] Enumerate analysis failure recovery for every closed code: retryable failure
  with retained local/server source, lost retry response, app termination/restart,
  server-assigned attempt-2 key rotation, success, expiry, exhaustion, permanent/
  re-film classification, and retry-source discard. Prove server discard reaches
  replay-safe 204 before the app deletes its local source and capacity releases
  once; no scenario creates a duplicate job or quota use.
- [ ] Run camera, microphone, original-video import, private-storage protection,
  push receipt/deep link, background/foreground, forced termination/upload
  resume, email callback, and monthly/annual billing on both minimum-supported
  physical devices as well as current iOS/Android hardware. Simulators/emulators
  can prove layout/install only. If a real device cannot run a declared minimum
  OS/provider path, raise the supported floor and regenerate metadata/builds;
  never claim an untested minimum.
- [ ] Enumerate Apple and Google monthly/annual purchase, pending/confirming,
  restore, renewal, cancel with paid-through, grace/hold, expiration, refund,
  revoke, existing Stripe/Shopify/lifetime, overlap, duplicate pressure, provider
  outage, and account-binding conflict.
- [ ] Add cross-account security cases for every owned route/artifact/deep link,
  malformed bearer plus valid cookie, old auth/history epochs, copied auth/
  step-up link, revoked device, upload ID, push token, and provider purchase.
- [ ] Add a protected `workflow_dispatch` Android emulator smoke with pinned
  action SHAs, a local fixture backend, Maestro install/run, sanitized test
  artifacts, and no store credentials. Keep it non-required until flake rate is
  measured; unit/contract CI remains the merge gate.
- [ ] Add failing unit/contract tests for `verify_mobile_api_deployment.py` covering
  redirects, network/JSON/schema errors, local/live/health/build SHA drift, wrong
  baked commit without a valid compatibility attestation, environment, origin,
  application ID, or EAS project, and a valid
  exact-bound fixture. Run those tests with every evidence/signer/release-tool
  test in this task.
- [ ] Add compatibility-attestation schema/signer/verifier tests for exact-commit
  default, valid differing-commit pair, missing behavior suite, equal schema with
  failing behavior, dirty checkout, wrong/stale/expired/revoked signature, check-
  run drift, third-commit replay, and key-descriptor/argv/environment leakage.
  No operator “compatible” boolean or unsigned exception is accepted.
- [ ] Add runner tests for dirty/untrusted refs, A/B reversal, missing/extra/stale
  manifest scenario, schema-equal semantic drift, backend startup/cleanup failure,
  provider-I/O attempt, cross-worktree path escape, partial result, canonical hash
  stability, and a valid isolated A-client/B-backend pass. Assert the signer
  rejects hand-authored or non-runner result JSON.
- [ ] Run validator tests and one dry evidence file containing only fixture
  scenarios. Expect the validator to report real-device/billing evidence missing,
  not success. Run `python -m pytest tests/test_mobile_evidence.py
  tests/test_mobile_console_evidence.py tests/test_mobile_api_deployment.py
  tests/test_mobile_api_compatibility.py
  tests/test_mobile_api_compatibility_runner.py -q`;
  expect all pass.
- [ ] Commit: `git commit -m "test: define mobile release evidence"`.

## Task 6: Prepare staging backend activation and observability runbooks

**Files:**

- Create: `docs/runbooks/mobile-backend-rollout.md`
- Create: `docs/runbooks/mobile-incident-response.md`
- Modify: `docs/deployment.md`
- Modify: `docs/operations/backup-recovery.md`
- Create: `.github/workflows/mobile-backend-deploy.yml`
- Create: `scripts/verify_railway_mobile_deployment.py`
- Create: `tests/test_railway_mobile_deployment.py`
- Modify: `tests/test_ops.py`

**Interfaces:**

- Activation order is exact: inventory every pre-cutover erasure/revocation path
  and state (legacy mobile tokens, browser history reset, account deletion, and
  Shopify customer-delete/customer-redact/shop-redact webhooks/CLI) → provision
  dedicated recovery-fence credentials/conditional-write/readback → stop the
  service and accept the cutover-baseline backup/chain under maintenance → code/
  DB with all new flags off → v2 entitlement reads → native auth → capabilities/read APIs → mobile
  profile writes → practice writes → device management → resumable uploads →
  privacy/export → mobile events. Then Apple lifecycle/billing config and Google
  lifecycle/billing config advance as independent provider branches. Each step has live readback and rollback. Ordinary provider purchase/
  claim admission remains off; only the separate staging billing-test lane may
  enable afterward for its allowlist, and general production admission waits for
  Task 9's post-approval publication gate. Native auth and device
  management cannot enable before the recovery-fence prerequisite passes.
  Push readiness/rollback may be inspected with its flag off, but push is omitted
  from this activation sequence; Task 7's exact processed-preview proof is the
  sole staging flag-on point.
- Observability includes one-replica CPU/memory/disk, SQLite busy/latency, queued/
  processing jobs, active/expired uploads, upload completion, duplicate attempts,
  push outbox/receipt failure, provider reconciliation, and first-party coaching
  funnel counts; no PII.
- Privacy and credential-revocation activation require the approved backup
  destination to support the backend’s synchronous conditional recovery-fence
  immutable-record put/readback and `HEAD` CAS/readback. Restore fetches the
  newest head and complete referenced record chain separately, then reconciles
  token revocations before erasures and worker startup.
- The protected backend workflow accepts exact `staging|production` environment
  and lowercase 40-hex `source_commit` inputs. After environment approval it
  verifies that commit's protected-main/release-ref checks, invokes Railway's
  connected-GitHub deploy mutation with that literal `commitSha` (never “latest,”
  `redeploy`, or a local upload), and records the returned deployment ID. Railway
  provides `RAILWAY_GIT_COMMIT_SHA` to the Dockerfile at build time; no runtime
  environment value may author image identity. Staging/production service/shared
  variable inventories must prove `CADDIEINSIGHT_SOURCE_COMMIT` absent; that name
  is reserved for local/protected Docker builds when the Railway argument is
  absent. The verifier polls only that
  deployment, requires provider readback commit SHA, immutable image digest,
  success/active state, and `/healthz` baked source SHA/OpenAPI SHA/environment/
  origin/EAS-project agreement, then emits sanitized evidence.
- Target selection is not caller-controlled: each protected GitHub environment
  owns the expected Railway project, service, and environment IDs plus a token
  scoped to exactly that target. Before every mutation the workflow queries and
  byte-compares all three IDs and requires GitHub autodeploy disabled for the
  linked service/environment. It rejects a dashboard “Deploy Latest Commit,”
  redeploy, `railway up`, another workflow, or any deployment ID it did not just
  create; the post-deploy verifier also requires that returned ID remain the
  latest active deployment. Other mutable deploy paths remain prohibited and
  approval-gated, and an unexpected deployment freezes activation/release until
  investigated and re-attested.

- [ ] Add tests that require each feature flag to appear in `/healthz`, default
  off, and have a documented enable/readback/disable procedure.
- [ ] Implement and parse-test the protected deployment workflow and Railway
  verifier. Cover wrong/unreachable SHA, unprotected ref, non-green checks,
  missing/conflicting Railway build SHA, `redeploy`/latest/local-upload use,
  a configured Railway `CADDIEINSIGHT_SOURCE_COMMIT` variable,
  wrong project/service/environment ID, caller-supplied target override, enabled
  GitHub autodeploy, unexpected newer/dashboard/redeploy/local-upload deployment,
  wrong deployment ID/commit/image digest, failed/superseded deployment, health
  drift, timeout, and valid exact-commit success. The workflow exposes no Railway
  token or raw provider response and performs no deployment before environment
  approval.
- [ ] Document a pre-change backup plus scratch restore, current commit/image,
  `/healthz`, one-replica count, `/data/sessions` mount, injected `PORT`, DB path,
  disk headroom, and PWA upload/report smoke before native activation.
- [ ] Inventory active/revoked legacy `mobile_api_tokens`, existing recovery
  checkpoints/journals, browser `history_reset_enabled`, account/history deletion
  state, and enabled Shopify privacy webhooks/CLI before deploying upgraded code.
  Temporarily force browser history reset and every dependent Shopify/privacy/
  credential mutation path off/held until the accepted baseline exists. Verify
  exact legacy token 201/200 success and documented fail-closed 503 outage
  behavior plus browser reset 303 success/202 pending UX after activation.
- [ ] Perform the approval-gated baseline maintenance with the Railway service/
  supervisor scaled to zero and prove no process holds the volume. Run the new
  image’s additive schema/baseline CLI against the exclusively mounted current
  volume with providers/workers/mail/dependent routes off; fix the lineage, create
  and verify a fresh immutable backup of current truth, publish/read back the
  complete baseline record and `HEAD`, scratch-restore it, and mark `accepted`.
  Stamp only later backups with that lineage. Keep every older bundle immutable
  as audit evidence but mark it non-service-restorable; never auto-delete it.
- [ ] Add a recovery-fence-ledger drill: publish/read back full canonical
  immutable synthetic
  `token_revoke`, `history_reset`, `account_delete`, `shopify_customer_erase`,
  and `shopify_shop_erase` records at the approved
  off-volume destination, CAS/read back `HEAD`, restore older fixture snapshots,
  fetch/validate the complete chain without list permission, and prove before
  startup that the old bearer/push binding is rejected and erased data is purged.
  Keep native revocation/privacy/deletion disabled if immutable put/readback,
  head CAS/readback, encryption, monitoring, or full-chain retrieval is absent.
- [ ] Prove a generation-0/pre-cutover backup taken before browser history reset,
  Shopify customer delete/redact, and shop redact can still pass read-only audit
  validation but is rejected by `restore-to-service`. Prove the accepted baseline
  and later matching-lineage backup restore only through the stopped-service
  command with newest full-chain reconciliation.
- [ ] In the same drill, prepare a service restore from a pre-password-reset
  snapshot and prove the restored working copy increments every auth epoch,
  clears password verifiers and all session/token/challenge/push credentials,
  rejects the old password/cookie/bearer, and permits only verified-email
  re-entry. Record the operator path for accounts without reachable verified
  email; never mutate the immutable source bundle.
- [ ] Exercise `restore-to-service` with the supervisor stopped: parent promotion
  lock, fresh rollback bundle, same-volume staged copy, migrations with all
  providers/workers off, credential reset, newest-chain/extension reconciliation,
  atomic old-tree/new-tree promotion, postverification, then supervisor start.
  Crash/restart at every journal phase and prove exact resume or retained rollback
  tree; never start a half-promoted database or mutate/delete source evidence.
- [ ] Define safe load thresholds from measured staging runs, not guesses. The
  controlled cohort stops growing when queue latency, disk, worker saturation,
  SQLite contention, or upload failure breaches the documented threshold.
- [ ] Document provider incidents: disable only the affected provider’s new-
  purchase/claim admission and client capability, keep free/existing access and
  the provider lifecycle verifier/reconciler running, preserve verified paid-
  through, drain/replay webhooks/acknowledgements, compare source events, and
  communicate without claiming a charge/refund state not confirmed by the
  provider. Missing lifecycle credentials after state exists is a fail-closed
  backend incident, never a reason to freeze or invent entitlement truth.
- [ ] Document recovery-store incidents separately: alert before restart, do not
  remove credentials/flags or bypass readback, restore endpoint/IAM/KMS access,
  validate immutable chain+head, then start the full backend and rerun PWA smoke.
  Feature rollback cannot safely remove this dependency once state exists.
- [ ] Document PWA fallback and rollback binary/server compatibility. Never roll
  back below a database reader that understands the added tables unless the
  compatibility test proves those additive tables are safely ignored.
- [ ] Run `python -m pytest tests/test_ops.py tests/test_railway_mobile_deployment.py
  tests/test_backups.py tests/test_foundation_contracts.py -q`; expect all pass.
- [ ] Commit: `git commit -m "docs: define mobile backend rollout"`.

## Task 7: Approval-gated provider setup and internal alpha

**External changes — stop for explicit approval before executing this task.**

**Files:**

- Update after readback: `mobile/store/review-notes.md`
- Update after console inventory/readback: `mobile/store/store-console-declarations.md`
- Create after signed UI QA: `mobile/store/screenshots/manifest.json`
- Create after signed UI QA: `mobile/store/screenshots/apple/en-US/iphone-*/*.png`
- Create after signed UI QA: `mobile/store/screenshots/apple/iap-review/*.png`
- Create after signed UI QA: `mobile/store/screenshots/google-play/en-US/phone/*.png`
- Create after signed UI QA: `mobile/store/assets/google-play-listing-icon.png`
- Create after signed UI QA: `mobile/store/assets/google-play-feature-graphic.png`
- Update after evidence: `mobile/evidence/*.json`
- Update after setup: `docs/runbooks/native-billing.md`
- Modify after explicit key approval: `swinglab/entitlements/release_evidence_keys.json`
- Modify after explicit key approval: `tests/test_review_access_cli.py`
- Modify after explicit key approval: `tests/test_review_fixture.py`
- Modify after explicit key approval: `tests/test_mobile_console_evidence.py`
- No credential file is added to Git.

**Interfaces:**

- Provider setup produces read-back identifiers and sanitized lifecycle
  evidence tied to one commit/build; credentials remain in provider/EAS/Railway
  protected storage.
- Internal distribution is TestFlight internal plus Google Play internal only;
  neither public review nor automatic publication is enabled.
- Apple provides one app-level Sandbox Server URL, so TestFlight staging owns it
  through alpha/beta. Production App Review uses the explicit Task 9 drain/
  reconcile/repoint/restore runbook; there is no claim of simultaneous Apple
  sandbox webhook delivery to both databases.

**Provider identities/products:**

- Apple bundle: `com.caddieinsight.app`; subscription group
  `CaddieInsight Pro`; products `com.caddieinsight.pro.monthly` and
  `com.caddieinsight.pro.annual`.
- Google package: `com.caddieinsight.app`; subscriptions
  `caddieinsight_pro_monthly` with base plan `monthly`, and
  `caddieinsight_pro_annual` with base plan `annual`.
- If any ID is unavailable or conflicts with an existing owned record, stop and
  return to design review; do not improvise a public identity.

- [ ] With approval, verify Apple Developer/Google Play developer identity,
  agreements, tax/banking, roles, and bundle/package ownership. Record status,
  not private account details.
- [ ] Read back the Google developer account type/creation date and whether
  production access is already granted. If Google’s current new-personal-account
  rule applies (currently a personal account created after 2023-11-13), record
  the current Console threshold and make Task 8's Play closed-test lane—not this
  internal alpha—the eligibility path. Internal-track users and the mixed 30–75
  cohort size are not assumed to satisfy that Android-specific gate; do not apply
  for production access in this task.
- [ ] Create/verify the EAS project, APNs/FCM credentials, Apple app record,
  Google app record, Play App Signing, exact association identities, and
  protected CI environments. Read back every identifier from the provider.
- [ ] Configure the same read-back EAS UUID as the client's embedded project and
  as each Railway deployment's non-secret `CADDIEINSIGHT_EXPO_PROJECT_ID`. Set
  staging `CADDIEINSIGHT_MOBILE_DEPLOYMENT_ENVIRONMENT=staging` and reserve exact
  `production` for the production service; never infer either from URL or caller
  headers. With push still off, staging health must read back environment, exact
  project UUID, send envelope 30 seconds, and cutoff skew 60 seconds. Any client/
  backend/provider mismatch blocks push setup.
- [ ] With separate release-evidence key approval, run
  `python scripts/manage_mobile_evidence_keys.py generate --private-out
  <protected-absolute-path> --public-out <temporary-public-json>`. Keep the mode-
  0600 private key only in the protected manual signer environment, review the
  public fingerprint, change the packaged trust registry from intentionally empty
  to that one production-active public key, and commit only the public record.
  Build the exact wheel/Docker image, scan Git/image layers/argv/env/log fixtures
  for private bytes, deploy that image to staging in the activation step below,
  and read back its public key ID/fingerprint. Open the private key only as an
  inherited `MOBILE_EVIDENCE_SIGNING_KEY_FD`; generate and verify a fresh signed
  staging fixture envelope before the first `review-access` evidence mutation.
  Missing key, signer/verifier mismatch, or failed readback blocks review setup;
  document the two-active-key rotation/recovery procedure before continuing.
- [ ] After committing the approved public record, run
  `python -m pytest tests/test_mobile_console_evidence.py
  tests/test_review_access_cli.py tests/test_review_fixture.py -q` against the
  exact built wheel/image. Replace Task 5's expected-empty assertion with explicit
  active-key ID/fingerprint/package readback while retaining malformed/unknown/
  private-byte leakage failures; no later task may silently activate another key.
- [ ] In that exact EAS project, use separate provider-mutation approval to
  enable **enhanced push security** and read back that unauthenticated Expo Push
  Service requests are rejected with `UNAUTHORIZED`. Create distinct staging and
  production access tokens under dedicated Expo robot users with the lowest
  current role that can send for the project (record any unavoidable account-
  scope blast radius), store them only as each Railway environment's protected
  `EXPO_ACCESS_TOKEN`, and read back secret presence/token-key HMAC plus robot
  role—never the token. At this point configure/read back credentials only: the
  processed store preview does not yet exist, so keep staging and production push
  flags off. Missing/unauthorized delivery credentials always block their flag
  and never trigger an unauthenticated fallback. The exact staging delivery/
  rotation proof occurs only after the preview artifact is store-installed below;
  Task 9 permanently closes this Version 1 staging sender lane before repeating
  the proof for production. While production is public, future preview builds are
  polling-only unless a separately reviewed isolated token namespace and handoff
  plan replaces this contract.
- [ ] In Play Console/Google Cloud, explicitly enable and link Play Integrity for
  `com.caddieinsight.app`, configure the backend service account only for token
  decode, record project number/package/signing-certificate/version bindings and
  quota readback. Runtime provider warmup waits for the exact Play-internal build
  below; challenge/decode waits for Task 9's enabled production-review lane. No
  client credential or verdict is logged or committed.
- [ ] Separately enable the Google Play Android Developer API and create distinct
  staging/production server identities for purchase status/acknowledgement. In
  Play Console grant only the exact app plus **View financial data** and **Manage
  orders and subscriptions** required for Billing APIs; read back that release,
  publishing, catalog/store, tester, user/permission, and admin rights are absent.
  Keep these protected credentials distinct from Play Integrity decode, Pub/Sub
  push OIDC, and the Google Play system RTDN publisher; inject each environment's
  value only as protected `GOOGLE_PLAY_DEVELOPER_CREDENTIALS_JSON`. Record only service-
  account subject HMAC/readiness/permission booleans, never its email or JSON.
- [ ] Create monthly/annual auto-renewing products with localized metadata and
  price selections approved by the user. Version 1's approved baseline is
  recurring-only: create no Apple introductory offer and no Google named offer/
  tag/pricing phase unless a separate commercial-configuration approval specifies
  exact duration, price, eligibility, countries, start/end, offer ID/tag, and
  fallback. Read back either explicit `none` plus the Google regular-base-plan
  identity, or every approved offer field; provider defaults/array order are not
  configuration. Configure the Apple sandbox-notification
  URL and one Google RTDN topic with the protected staging Pub/Sub push
  subscription. Bind/read back its exact subscription name, HTTPS endpoint,
  expected envelope name, OIDC audience/service account, publisher/subscriber
  IAM, retry/dead-letter policy, and PII-free alert. Record the intended distinct
  production Apple endpoint and Google subscription/OIDC/dead-letter configuration,
  but do not set the Apple production URL or create the production Pub/Sub
  subscription until Task 9 deploys and reads back the production backend.
  Require staging `testPurchase` acceptance only
  for an active staging lane plus an approved opened candidate whose authoritative
  external account ID matches, or later lifecycle updates for a staging-owned
  token fingerprint; quarantine every unknown/mismatched notification before a
  durable entitlement write. A tokenless Google `testNotification` returns 204
  only after verified OIDC/subscription/bounded decode and one durable idempotent
  delivery-test receipt, with no Developer API or entitlement-state write. A
  token-bearing non-owning notification additionally requires its authoritative
  lookup and durable quarantine receipt before 204. Transient verification/
  storage/provider failures must redeliver through the bounded dead-letter
  policy. Production test
  purchases remain quarantined by default. Provision the protected review-lane
  schema/coarse kill switch separately but leave all durable production provider
  access, credential, build, demo, and purchase-test records absent until Task 9
  has exact provider artifacts and distinct provider-scoped review credentials.
  Exercise user-HMAC, Apple signed-version,
  Google package/certificate/version, product, window, and production/review
  isolation with fixtures only. Provider-authoritative
  production endpoint/readback occurs in Task 9 with no real-user binding.
- [ ] Manually copy the reviewed `mobile/store/metadata/en-US/` fields and exact
  public privacy/terms/support/account-deletion URLs into both consoles. Read
  every field back, compare normalized content hashes to the repository source,
  and record provider draft state; do not publish metadata in this task.
- [ ] Inventory every currently visible Apple/Google declaration section and
  reconcile it with `store-console-declarations.md`. Under the same bounded
  provider-metadata approval, enter only reviewed draft answers for Apple app
  information/category/content rights/age rating/free price/availability/contact/
  review access and applicable compliance sections, and Google app access/ads/
  target audience/content rating/Data Safety/privacy/account deletion/Health apps/
  category/contact and applicable policy declarations. Read back the normalized
  value, applicability, completion/status, and content hash for every row. A new,
  blank, contradictory, or console-required section blocks alpha metadata
  sign-off; no answer is inferred from the app category or prior web behavior.
- [ ] Before provisioning server secrets, read back the exact Railway project/
  service/staging-environment IDs into
  the protected GitHub environment, disable GitHub autodeploy for that linked
  service, and verify the deploy token cannot address any other target. Record
  sanitized IDs/autodeploy state; do not proceed while an unapproved push,
  dashboard latest/redeploy, or local-upload path has produced the active deploy.
  Within the same explicit external-setup approval, provision secrets through
  Railway/provider protected configuration and configure the approved
  off-volume recovery-fence immutable-record/head publisher/readback without committing
  credentials. Under the separate staging-deployment approval, dispatch only
  `mobile-backend-deploy.yml` with the reviewed exact commit; require Railway
  deployment ID, provider commit SHA, image digest, and baked health identity
  readback from `verify_railway_mobile_deployment.py`. Deploy to staging with explicit server environment `staging`, the
  provider-read-back EAS project UUID, and flags off; verify those exact health
  values, migrations and the
  recovery-fence drill. Even for an expected-empty synthetic database, first run
  `swinglab entitlements-backfill --db /data/sessions/swinglab.db --dry-run`,
  review its PII-free counts/digests, and take/scratch-restore a pre-apply backup.
  Under the approved mutation run the same command with `--apply`. If any Shopify/
  legacy ambiguity exists, validate the protected decision digest and run
  `swinglab entitlements-reconcile-shopify --db /data/sessions/swinglab.db
  --dry-run --decisions <protected-json>` before its matching `--apply`; preflight/
  apply Stripe in bounded calls with
  `swinglab entitlements-reconcile-stripe --db /data/sessions/swinglab.db
  --dry-run --limit 50` followed by the same literal command using `--apply` in
  place of `--dry-run`. Repeat dry-runs, take/scratch-restore a post-apply
  backup, and require zero unexplained legacy/v2 mismatch, active Stripe
  quarantine, ambiguous grant, or unprocessed provider event. Enable/read back
  `billing.entitlement_v2_reads_enabled` before any
  native route or test grant; a nonempty/ambiguous surprise blocks staging rather
  than being discarded. Before native billing config or the staging lane can be
  enabled, run
  `swinglab billing-account-keys-backfill --sessions-dir /data/sessions
  --batch-size 200 --dry-run --json`. Iterate separately approved bounded apply
  calls with the same path/size, `--apply --json`, and each prior non-null
  `next_cursor` supplied as `--after <cursor>` until `complete=true`; take and
  scratch-restore a fresh backup, then rerun the exact dry-run command and require
  `remaining_missing_apple=0` and `remaining_missing_google=0`. New account
  creation must preserve that
  invariant during the run. Then activate/read back in order: v2 entitlement
  reads → native auth → mobile
  reads → mobile profile writes → practice → devices → uploads → privacy/export
  → events. After shared readiness, advance Apple lifecycle/native billing config
  and Google lifecycle/native billing config as two independent branches; a
  provider failure blocks only its preview/alpha path.
  Keep staging push configured but disabled; the exact processed-preview gate
  below is its only activation point.
  Keep both ordinary new-
  purchase/claim admission flags and the staging billing-test lane off; the exact
  preview build does not exist yet. Prove each rollback and that missing
  lifecycle credentials fails startup. Google lifecycle additionally requires
  protected Developer API credential subject/package readback and a successful
  bounded read-only voided-purchase readiness probe; the application adapter has
  no refund/cancel/catalog/release/publishing methods. Only after the
  applicable provider lifecycle verifier flag (and Google's expected Pub/Sub
  subscription name) is enabled/read back, send that provider's test notification and require
  durable idempotent delivery-test receipts, 204 acknowledgement, zero Developer
  API/token/event/credential/binding/grant writes, and bounded redelivery/dead-
  letter behavior for an injected receipt-storage failure.
- [ ] Before any preview dispatch, create/link the Sentry project and provision
  the staging-bound client DSN plus protected `SENTRY_AUTH_TOKEN`/org/project in
  the preview EAS environment. Read back presence/scope and privacy settings—not
  secret values—and require `SENTRY_ALLOW_FAILURE=false`; keep replay, tracing,
  screenshots, and PII disabled. Read back and retain sanitized evidence that
  `scrubIPAddresses=true`, server-side/default data scrubbers are enabled, and
  reviewed sensitive/safe/advanced-scrubber fields match the privacy inventory.
  Missing/wrong-environment config or scrubber drift blocks preview
  dispatch because exact build-time symbol/source-map upload is not recoverable
  from a later rebuild.
- [ ] Before preview dispatch, run a disposable clean CNG prebuild and require
  Expo autolinking to resolve the existing `caddieinsight-storage` pod/Gradle
  module. On protected macOS compile and execute its iOS redirect/stream/operation-
  cancellation tests; compile and execute the Android equivalents. Fail on a
  missing native symbol/module, followed redirect, unbounded bridge payload, or
  per-operation cancellation mismatch, and delete generated native directories
  only after resolved-parent safety verification. This source/native test gate is
  required but does not substitute the processed store-artifact proof below.
- [ ] Dispatch `development` builds and verify camera/import/auth/upload plus
  local notification permission/routing mocks on directly installable real-
  device builds; do not enable a sender lane or claim provider push. Then dispatch
  `preview` store builds. Authenticated Expo send/ticket/receipt/delivery remains
  exclusive to the exact processed-preview gate below.
  Capture EAS URLs, hashes, version/build, commit, build-time Sentry release/dist
  and upload result, plus no-secret scans.
- [ ] Run the signed iOS archive privacy/provenance verifier and static signed
  Android AAB/generated-APK/ELF 16 KiB verifier against those exact preview
  artifacts. Any archive/privacy/signature/alignment, Sentry build-time upload,
  or environment-binding failure blocks provider delivery; no direct install of
  the App Store IPA/Play AAB is claimed.
- [ ] Before the first preview upload, under separate Apple availability-mutation
  approval, deselect/read back both **Make this app available** for iPhone/iPad
  apps on Apple-silicon Mac and **Make this app available on Apple Vision Pro** in
  App Store Connect. Bind the app-level field revisions to sanitized evidence.
  `supportsTablet=false` is not this opt-out. If either control is unavailable or
  remains enabled, the Apple branch stops until compatible Mac/Vision Pro
  behavior, camera fallback, device QA, declarations, support, and required assets
  are added and attested.
- [ ] Upload those exact preview EAS build IDs—not local/rebuilt artifacts—to
  TestFlight internal and Google Play internal only. Do not submit for public
  review or enable automatic publication. Read back iOS provider artifact ID,
  version/build, processing and export-compliance status; resolve `Missing
  Compliance` only from the reviewed inventory/documentation under explicit
  provider-mutation approval. Read back Android provider artifact ID,
  versionCode, AAB hash, Play App Signing certificate, and Device Catalog
  targeting/exclusion counts by phone, tablet, Chromebook/desktop, TV, Wear,
  Automotive, and XR. Phone-only asset scope requires every non-phone targeted
  count to be zero; otherwise stop and expand QA/assets/declarations or produce
  and fully re-attest a reviewed excluding build.
- [ ] After Apple processing, re-read both compatible-app opt-outs and their field
  revisions. Any drift blocks only Apple before installation or tester access.
- [ ] Install the exact processed iPhone preview build on the matrix iPad in
  compatibility mode and run the defined install/auth/capture-or-import/upload/
  coaching/privacy/billing-layout smoke. Require no clipped controls, unsupported-
  device crash, accidental iPad UI claim, or provider request for iPad screenshots.
  If App Store Connect/build metadata treats the app as iPad-targeted, stop and
  add full native iPad QA/assets rather than relying on compatibility evidence.
- [ ] Install only those processed store-delivered preview builds on clean
  devices. From the exact Play-internal artifact, run only the app-module
  Standard Integrity provider warmup and read back package/versionCode/Play App
  Signing certificate/project/quota configuration bound to provider artifact ID
  and commit. Do not request or claim a challenge-bound decode: the production-
  review challenge lane is intentionally empty/disabled in this task and the
  `staging_test` path never borrows it. Module/warmup/config drift blocks the
  device matrix; the provider-authoritative challenge/decode proof occurs in
  Task 9's exact production review lane, and no raw token/verdict is logged or
  committed here.
- [ ] For each exact processed/store-installed preview platform build, run
  `python scripts/verify_mobile_api_deployment.py --base-url <staging-origin>
  --snapshot docs/api/openapi-v1.json --build-identity
  <sanitized-preview-build-json> --json`. Require local/live/health/build contract
  hashes, exact build/backend commit equality (or a separately approved valid
  signed compatibility attestation), server environment/origin/EAS project, ID-policy
  revision, and exact application-ID admission to pass. Re-run after every
  staging deploy or restart before more artifact QA; mismatch blocks that
  platform and forces a corrected deployment or new build, never an override.
- [ ] On each exact store-installed preview artifact, run the evidence harness's
  iOS/Android native-export scenario: direct same-origin download, same/cross-
  origin redirect rejection before Authorization, exact type/length/ZIP/path/
  operation result, exact maximum 1,100,000,000-byte ZIP with bounded JavaScript heap on
  physical hardware, two concurrent operations with one-operation cancellation,
  generation-wide drain, and sign-out/environment/history-reset/account-delete
  purge races. Bind evidence to provider artifact/hash, EAS ID, backend commit,
  native-module binary/hash, device and OS; any late file/callback or rebuilt/
  development binary blocks internal alpha.
- [ ] Still on those exact processed preview builds, enable/read back staging push
  under the bounded activation approval, register one synthetic staging device
  with the exact environment/project/app-identity tuple, and prove authenticated
  Expo send → durable ticket →
  authenticated receipt → generic delivery. Rotate to a new dedicated staging
  access token, repeat, revoke the old token, and prove the old token and an
  unauthenticated request both receive `UNAUTHORIZED`; read back new token-key
  HMAC/robot scope presence. Assert every serialized message has `ttl=900` and
  record the sanitized last-provider-accepted time/cutoff revision. Any failure
  disables/read backs staging push and leaves polling available; production push
  stays off.
- [ ] Before enabling the Google branch of the staging billing lane, use separate
  provider-mutation approval to add only the exact protected staging Google
  billing account to Play Console **Settings → License testing**. Read back its
  membership into protected evidence with the email redacted, verify the exact
  Play-internal artifact was downloaded from Play by that same account, and bind
  server admission to the matching synthetic user/build. Missing or mismatched
  Console membership blocks `purchase_allowed`; the first controlled sheet must
  visibly show Google’s test-payment instruments/test-purchase notice and is
  canceled/closed if it does not—never continue with a real payment method.
- [ ] Under the bounded staging-test approval, now bind and enable only the
  Entitlement Plan's staging billing lane for the exact processed preview
  provider artifact/version/build, predeclared synthetic user HMACs, and approved
  products; read back each per-user capability and prove nonallowlisted users see
  no sheet. No earlier source/EAS metadata may stand in for this store readback.
- [ ] On those exact store-installed preview builds, exercise Pro disclosure
  before monthly/annual sheets on both platforms—including recurring price,
  auto-renew/cancel language, Terms/Privacy links, 200% text, and screen reader.
  With baseline offer state `none`, prove no trial/intro copy appears and Android
  selects only the approved regular-base-plan sentinel. Only if a separately
  approved live offer exists, additionally prove iOS eligible intro conversion
  plus returning/ineligible/query-failure recurring-only copy, and Android exact
  named-offer identity/phase plus regular fallback. Unit fixtures still cover
  both configured and absent-offer branches. Then
  execute the full device/billing/security/recovery matrices including the 16 KiB
  Android device run. Every sandbox/test transaction reconciles through purchase/
  notification/restore and leaves the expected entitlement after cancel/refund/
  revoke. The first Google license-test purchase must prove authorized
  `subscriptionsv2.get` plus server acknowledgement with the dedicated Developer
  API identity; permission/read/ack failure closes the lane. Any disclosure or
  matrix failure blocks internal alpha.
- [ ] Run the protected Sentry artifact verifier for each exact preview EAS ID/
  release/dist and require the embedded JS debug IDs, iOS UUIDs, and Android R8
  mapping identifier. Send one PII-free synthetic crash per exact store-installed
  preview build. Query the stored synthetic event and user/tag surfaces to prove
  no IP, `{{auto}}`, user object, request URL/header, local path, or attachment was
  stored; verify symbolication/session assignment, then delete the test events.
  Preview build/readback or privacy failure blocks alpha.
- [ ] After signed store-installed preview UI QA, first rehearse the dedicated
  review-auth path in staging with two different
  provider-scoped synthetic users, account names, secrets, intent namespaces and
  fixture generations—never a mailbox or normal passwordless account. Execute
  the following exact commands against the processed preview artifacts. Each
  `<*-op>` is a distinct 128-bit operation ID and is reused only by its dry-run/
  apply pair; the protected provision payload is supplied byte-for-byte to both
  invocations through stdin and never placed in argv, an environment variable,
  or a file:

  - Apple: `swinglab review-access provision --sessions-dir /data/sessions
    --provider apple --app-version <staging-apple-version> --app-build
    <staging-apple-build> --opens-at <utc> --closes-at <utc> --operation-id
    <apple-provision-op> --dry-run --json --secret-stdin`, then the identical
    command with `--apply`; `swinglab review-access open --sessions-dir
    /data/sessions --provider apple --operation-id <apple-open-op> --dry-run
    --json`, then the identical command with `--apply`; `swinglab review-access
    seed-fixture --sessions-dir /data/sessions --provider apple --operation-id
    <apple-seed-op> --dry-run --json`, then the identical command with `--apply`;
    then read only with `swinglab review-access status-fixture --sessions-dir
    /data/sessions --provider apple --json` and `swinglab review-access status
    --sessions-dir /data/sessions --provider apple --json`.
  - Google: `swinglab review-access provision --sessions-dir /data/sessions
    --provider google --app-version <staging-google-version> --app-build
    <staging-google-build> --standing-app-access --operation-id
    <google-provision-op> --dry-run --json --secret-stdin`, then the identical
    command with `--apply`; `swinglab review-access open --sessions-dir
    /data/sessions --provider google --operation-id <google-open-op> --dry-run
    --json`, then the identical command with `--apply`; `swinglab review-access
    seed-fixture --sessions-dir /data/sessions --provider google --operation-id
    <google-seed-op> --dry-run --json`, then the identical command with `--apply`;
    then read only with `swinglab review-access status-fixture --sessions-dir
    /data/sessions --provider google --json` and `swinglab review-access status
    --sessions-dir /data/sessions --provider google --json`.

  Verify Apple’s bounded window and a staging-only
  Google standing record, visible review entry, generic failures, full Pro demo,
  review-password privacy step-up/export/reset/delete, and fresh generation after
  synthetic deletion. Keep both fixture generations open only through the asset
  capture in the next gate. Commit no account, password, user ID, email, or signed
  envelope. Task 9 provisions different production identities/secrets.
- [ ] Now capture the six ordered Apple/Google draft public screens using those
  seeded synthetic coaching rows for the five feature slots, but capture More/Pro
  from a separate ordinary Free passwordless capture account. Frame only the
  tested state-invariant comparison region: truthful Free/Pro summary, Free-
  coaching continuity, and Terms/Privacy, with no availability claim, price, CTA,
  review-demo label, test instrument, sheet, or charge.
  Capture Apple monthly/annual IAP review screens only through its isolated
  sandbox lane. Export Google listing graphics, populate the manifest, run
  `scripts/verify_mobile_store_assets.py --scope apple-submission` and
  `--scope google-submission` independently, binding each coaching capture to the
  matching sanitized `status-fixture` template hash/generation. Obtain independent
  review and separate metadata approval/upload/readback for each provider; one
  failed scope blocks only that provider's alpha/draft path. `--scope all` is an
  optional aggregate and cannot substitute either result. These
  preview bindings cannot satisfy Task 9. Then close/read back the staging purchase
  lane while lifecycle remains active, remove/read back the Google License Tester,
  and close/purge both staging review records. Close Apple with
  `swinglab review-access close --sessions-dir /data/sessions --provider apple --operation-id
  <apple-close-op> --dry-run --json` and the identical `--apply` command, then
  purge with `swinglab review-access purge --sessions-dir /data/sessions
  --provider apple --operation-id <apple-purge-op> --dry-run --json` and its
  identical `--apply` command. For Google, first clear/read back the staging Play
  App-access dependency and capture/sign one fresh ≤30-minute
  `app_access_clear` envelope/evidence ID. Supply the same envelope bytes to
  `swinglab review-access close --sessions-dir /data/sessions --provider google
  --permanent --operation-id <google-close-op> --dry-run --json
  --console-evidence-stdin` and the identical `--apply` command. After close
  readback, capture/sign a second fresh envelope and use it with
  `swinglab review-access purge --sessions-dir /data/sessions --provider google --permanent
  --operation-id <google-purge-op> --dry-run --json --console-evidence-stdin` and
  the identical `--apply` command. Operation IDs and envelopes are distinct across
  close/purge and providers; an envelope is reused only within its dry-run/apply
  pair. Both Google envelopes prove no staging Play App-access field depends on
  the record. Retain no email/credential/envelope in committed evidence.
- [ ] Do not open or claim the production review lane during internal alpha.
  Exercise the same admission/quarantine logic against isolated staging fixtures
  and preview builds, including monthly/annual, restore, renewal, refund/revoke,
  wrong account/build/product/window, and bounded purge. The exact production
  build and provider-authoritative production review-lane proof occur only in
  Task 9 after production backend activation.
- [ ] Update sanitized evidence and commit only non-secret results:
  `git commit -m "test: record mobile internal alpha evidence"`.

## Task 8: Approval-gated 30–75 golfer retention beta

**External customer invitations — stop for explicit approval before executing this task.**

**Files:**

- Create: `docs/qa/mobile-beta-report.md`
- Create: `docs/runbooks/mobile-beta-lane.md`
- Update: `mobile/evidence/*.json`
- Update: `mobile/store/metadata/en-US/release-notes.txt`
- No participant PII or free-form feedback is committed.

**Interfaces:**

- Activated golfer: authenticated profile plus first submitted analysis.
- The retention beta uses only one explicitly attested lane: the exact immutable
  `preview` build (production bundle/package identity, staging runtime config),
  TestFlight external/Play closed distribution, and the isolated staging backend/
  database with the same one-replica topology. Participants create fresh staging
  accounts through normal email auth; no production account, history, report,
  purchase, Shopify grant, video, or database row is copied/imported, and beta
  state is never promoted to production. Invites disclose that beta history and
  test subscriptions will not transfer to the public app. Because preview and
  production share the store identity, the production binary's immutable
  environment/origin marker must run before auth/private-state reads and purge
  all staging SecureStore operations, installation identity, media, cache, and
  query state before requiring fresh production sign-in; it never migrates local
  beta state across that boundary.
- Ordinary cohort accounts have native new-purchase admission off and can use
  Free coaching. Billing correctness is measured separately with a small
  predeclared staging billing-test HMAC allowlist: Apple TestFlight sandbox and
  Google license-tester purchases carrying authoritative `testPurchase` only.
  A new Google staging binding also requires the active lane and exact approved
  intent/external-account candidate; after closure only lifecycle updates for a
  staging-owned token fingerprint continue. Those verified grants stay in
  staging, use no production review namespace or real payment method, and are
  purged under the staging retention policy. A non-allowlisted account never
  opens a sheet; unknown/mismatched test input or Google input without
  `testPurchase` is quarantined before durable entitlement state and triggers a
  stop/investigation, not a charge claim.
- Hard reliability gates: ≥99.5% Sentry crash-free sessions on each platform
  with at least 200 sessions per platform and one immutable release/dist,
  ≥95% upload completion excluding explicit cancel, zero duplicate jobs/charges,
  zero confirmed cross-account exposure, full purchase lifecycle correctness in
  the designated synthetic staging billing lane,
  zero unresolved critical security/privacy/billing/data-loss findings, and safe
  one-replica measurements. Crash-free, native artifact, provider billing, and
  store-processing failures freeze only their platform branch; cross-account/
  data-loss/security or shared backend/capacity failures freeze both.
- Product signals: ≥50% of first-Brief viewers start practice, ≥30% of practice
  starters complete matched re-film within 14 days, and ≥25% perform a meaningful
  week-two action.

- [ ] Before any invite, select and freeze at least one entry in the provider-
  local map `{ios?:{eas_build_id,provider_artifact_id,artifact_sha256},android?:
  {eas_build_id,provider_artifact_id,artifact_sha256}}`; iOS and Android never
  share one EAS build ID and one may proceed while the other is absent/blocked.
  For each selected branch, download its application/artifacts, rerun only its
  signed iOS privacy/provenance or Android 16 KiB gates, read back staging URL/
  environment/build identity and its feature/provider flags, and run the exact
  API-deployment verifier. Local/live/health/build OpenAPI SHA plus exact baked/
  build commit equality (or current signed compatibility attestation), environment,
  origin, EAS project, and application-ID policy must agree. A new commit/build
  resets only that branch's attestation/window; never switch it to production.
- [ ] Read back that exact preview build's TestFlight export-compliance status and
  signed Info.plist value against the reviewed inventory. `Missing Compliance`,
  a mismatched boolean, or required-but-unlinked documentation blocks the iOS
  beta wave independently of Android.
- [ ] Create the beta-lane runbook with fresh-account/no-production-clone rules,
  synthetic representative staging volume for capacity measurements, participant
  disclosure, staging privacy/export/delete support, exact billing-test allowlist
  admission/readback, Apple sandbox and Google license/testPurchase handling,
  purge/incident procedures, and the explicit later production re-sign-in/no-data-
  migration/local-wipe boundary. The participant disclosure states that an app
  update to the public production binary deletes staged local video/operations
  and requires a fresh sign-in. Test that production credentials/URLs are absent
  from the resolved preview build and staging cannot mutate production
  entitlements.
- [ ] Gate beta readiness on the client environment-boundary matrix: preview →
  production update, production → preview/downgrade, missing marker with existing
  state, crash at every purge phase, offline launch, locked/unavailable
  SecureStore, undeletable media, and restart. Prove no staging bearer, pending
  privacy/purchase/upload envelope, video, cache, installation ID, link, or
  request reaches production and vice versa; private rendering/network remain
  closed until the current marker is written last.
- [ ] Maintain one deduplicated global participant ledger capped at 75, but invite
  within each provider branch independently in increments of 10, then 25, then
  only the remaining approved capacity up to 40. Before every branch wave—
  including its first—stop for a fresh explicit invitation approval tied to that
  provider artifact, exact new-recipient count, global remaining capacity, and
  provider-local plus shared gate report; Task 8/prior/other-provider approval is
  not reusable. Immediately before its send, and after any intervening staging
  deploy/restart, rerun the API verifier only for that frozen platform. A platform
  mismatch freezes/resets only that branch; a shared backend/security gate freezes
  both. Expand only after that branch's prior-wave gates/operations review pass.
  Do not buy traffic or double-count a golfer who tests both platforms.
- [ ] Use TestFlight external testing and Play closed testing only after provider
  beta-review approval. Keep PWA visible as fallback and publish support/recovery
  instructions plus the staging/no-migration/test-subscription disclosure with
  each invite. Keep purchase UI unavailable to ordinary cohort accounts.
- [ ] Before reopening the Google staging billing branch, separately approve and
  re-add only the predeclared protected Google billing accounts to Play Console
  **Settings → License testing**. Read back redacted membership, verify each exact
  closed-track artifact was acquired from Play by the same account, and require
  the first controlled sheet to display test instruments/test-purchase notice.
  Missing/mismatched membership or a real-payment sheet keeps the branch closed;
  never select or charge a real method.
- [ ] Under the bounded staging-test approval, reopen/read back the billing-test
  lane only for the same frozen store artifact/version/build, predeclared
  synthetic billing users, and approved products; it remains absent for every
  ordinary participant and closes automatically at the recorded matrix deadline.
- [ ] Run monthly/annual purchase, restore, renewal, cancel/paid-through, grace/
  hold, expiry, refund, revoke, duplicate-pressure, and provider-outage cases only
  through the predeclared staging billing-test accounts. Report this synthetic
  billing evidence separately from cohort retention; record zero real charges,
  and stop immediately if any provider payload lacks its authoritative sandbox/
  test marker or any grant reaches production. At matrix completion close/read
  back the lane while keeping existing staging lifecycle processing active.
  Remove the protected Google billing accounts from License testing and read back
  absence after all intents settle; no email enters the committed beta report.
- [ ] Document the later Apple one-Sandbox-URL handoff without executing it in
  beta: stop new Apple staging intents, drain/reconcile authoritative staging
  status, record a cutoff, repoint/read back for production review, keep staging
  reconciliation live, then restore/read back the staging URL and reconcile the
  gap after review. Google RTDN subscriptions remain separate and do not use this
  cutover. Beta go/no-go evidence must not assume dual Apple sandbox delivery.
- [ ] Measure the gate from Sentry Release Health’s common session definition,
  split by platform/release/dist; use TestFlight/App Store Connect and Play
  Android vitals as separate corroborating diagnostics. Measure upload/job
  reliability from backend state and coaching funnel from the allowlisted event
  ledger. Do not merge missing/low-denominator data into a pass.
- [ ] Review weekly: device failures, queue/disk/SQLite headroom, auth/upload/push,
  entitlement lifecycle, support themes, accessibility, and the three retention
  signals. Capture only aggregate counts/percentages in the committed report.
- [ ] If the Google new-personal-account production gate applies, report the
  eligible Android closed-test subset and continuous opt-in window separately.
  Do not count iOS testers, interrupted opt-ins, internal-track users, or the
  cohort ceiling itself as proof; a requirement shortfall extends testing and
  blocks the Play production-access application/submission, not the Apple lane.
  Under the current rule, keep at least 12 eligible Android testers continuously
  opted in for at least 14 days and track only opt-in start/continuity plus
  aggregate engagement in protected evidence. Re-read the Console requirement;
  once it is satisfied, stop for explicit approval before submitting the
  production-access application, answer from measured beta evidence, and record
  Google's approval/readback before Task 9 Android submission.
- [ ] Any hard-gate miss freezes invites, disables the smallest affected flag or
  withdraws the affected build, preserves paid-through access, and starts a
  focused fix/retest cohort. Product-signal misses cause product iteration, not
  automatic public release.
- [ ] At cohort completion, write a go/iterate/no-go report with denominator,
  observation window, platform split, known bias, reliability evidence, product
  signals, capacity headroom, unresolved risks, and recommendation.
- [ ] Commit sanitized evidence/report only:
  `git commit -m "docs: evaluate mobile retention beta"`.

## Task 9: Approval-gated review submission and provider-appropriate public launch

**Public review/publication — stop for explicit approval before every submission and publication action.**

**Files:**

- Update: `mobile/store/review-notes.md`
- Update: `mobile/store/metadata/en-US/release-notes.txt`
- Update: `mobile/store/store-console-declarations.md`
- Replace with exact-production captures: `mobile/store/screenshots/manifest.json`
- Replace with exact-production captures: `mobile/store/screenshots/apple/en-US/iphone-*/*.png`
- Replace with exact-production captures: `mobile/store/screenshots/apple/iap-review/*.png`
- Replace with exact-production captures: `mobile/store/screenshots/google-play/en-US/phone/*.png`
- Update if source changed: `mobile/store/assets/google-play-listing-icon.png`
- Update if source changed: `mobile/store/assets/google-play-feature-graphic.png`
- Create: `docs/releases/mobile-v1-production-cutover.md`
- Create: `docs/releases/mobile-v1-release-record.md`
- Update: `docs/runbooks/mobile-backend-rollout.md`
- Update: `docs/runbooks/mobile-incident-response.md`

**Interfaces:**

- Submission uses one immutable signed build per platform with manual release;
  approval and public availability are recorded as distinct states.
- The release record names, per platform, exact app commit, version/build, EAS ID,
  provider artifact ID/hash, and verifier run ID/time; it separately records the
  live backend commit, Railway deployment ID/image digest, health readback, and
  common OpenAPI/behavior-suite hashes. For each app/backend pair it records
  either `commit_relation:"exact"` or the compatibility-envelope ID/hash/expiry/
  signing-key ID. It also records provider-local rollout stage, observation
  window, approval/publication/admission states, and rollback decision.

- [ ] With separate production-activation approval, execute the production
  cutover runbook using production inventory only; never reuse a staging database,
  credential, event, review grant, restore, or health claim. Capture a production
  pre-change backup and scratch restore, then stop the service and complete/read
  back the exact recovery-fence baseline maintenance, immutable chain/HEAD CAS,
  and restore-to-service prerequisite from Task 6 before any native privacy or
  credential mutation can enable.
- [ ] Provision the shared protected production recovery credentials and
  monitoring once. Read back the exact Railway project/service/production-
  environment IDs into the protected production GitHub environment, require
  GitHub autodeploy disabled, verify the deploy token cannot address staging or
  another service, and require the latest active deployment to be workflow-owned.
  Then create two independent provider branches: Apple provisions
  only its verifier/lifecycle credential and webhook; Google provisions only its
  verifier/lifecycle credential plus OIDC webhook/RTDN endpoint. A provider's
  absent or failed credential blocks only that branch. For Google, load the
  dedicated production Android Publisher credential, read
  back its approved subject/package plus only View-financial/Manage-orders roles,
  prove release/catalog/tester/admin roles absent, and complete the bounded read-
  only provider probe; Integrity-decode and push identities cannot substitute.
  Under separate production-deployment approval, dispatch only the protected
  exact-commit backend workflow and require matching Railway deployment ID,
  provider commit SHA, immutable image digest, and baked health identity before
  cutover. Deploy the exact schema/code with explicit server environment `production`, the
  same provider-read-back EAS project UUID, and every new flag off. Read both exact
  values from health, smoke the PWA, and prove migrations/capacity/health before
  any read cutover.
- [ ] Verify the runtime database is exactly `/data/sessions/swinglab.db`, then
  execute Entitlement Plan Task 2's production-data gate with literal commands.
  Start with `swinglab entitlements-backfill --db /data/sessions/swinglab.db
  --dry-run`. For every Shopify/legacy Season/Founders ambiguity, validate the
  protected uncommitted decision file and captured digest, then run
  `swinglab entitlements-reconcile-shopify --db /data/sessions/swinglab.db
  --dry-run --decisions <protected-json>`. Preflight Stripe with
  `swinglab entitlements-reconcile-stripe --db /data/sessions/swinglab.db
  --dry-run --limit 50`. Take/scratch-restore a fresh pre-apply backup, then under
  the approved mutation run
  `swinglab entitlements-backfill --db /data/sessions/swinglab.db --apply`, the
  exact Shopify command with `--apply`, and bounded
  `swinglab entitlements-reconcile-stripe --db /data/sessions/swinglab.db --apply
  --limit 50` calls until no eligible candidate remains or one explicit blocker
  stops cutover. Repeat all dry-runs for idempotent/zero-remaining counts, take and
  scratch-restore a post-apply backup, then require dual-write observation,
  shadow comparison, and zero active Stripe quarantine, unexplained mismatch,
  ambiguous grant, or unprocessed provider event. Read back existing
  web members—including the owner-controlled test account—through both legacy
  and v2 paths before enabling v2 reads. Any unresolved mapping blocks native
  billing/review; never invent or drop access.
- [ ] With ordinary/review purchase admission still off, run the exact production
  `swinglab billing-account-keys-backfill --sessions-dir /data/sessions
  --batch-size 200 --dry-run --json` and record only its PII-free counts. Iterate
  approved bounded calls with the same path/size, `--apply --json`, and each prior
  non-null `next_cursor` passed as `--after <cursor>` until `complete=true`; take/
  read back and scratch-restore a fresh backup containing the new rows, then rerun
  the exact dry-run command and require `remaining_missing_apple=0` and
  `remaining_missing_google=0` for every eligible live account. Concurrent new
  accounts must receive both keys in their create transaction; any conflict,
  deletion race, nonzero remainder, or restore mismatch blocks native billing
  config, the production review lane, and ordinary purchase admission.
- [ ] After the shared zero-mismatch and zero-missing-key readbacks, activate with
  readback and rollback at each shared step: v2 entitlement reads → native auth →
  mobile reads → mobile profile writes → practice → devices → uploads → privacy/
  export → events. Then advance Apple lifecycle plus Apple native-billing config
  only in the Apple branch, and Google lifecycle plus Google native-billing
  config only in the Google branch. Neither provider is a prerequisite for the
  other's flag/readback sequence. Keep production push off
  until the exact processed store builds pass the authenticated delivery/rotation
  probe below. Keep both providers'
  ordinary new-purchase/claim admission off
  through cutover, while existing access/restore/lifecycle processing remains on.
- [ ] Only after the production Apple lifecycle webhook/verifier is enabled and
  read back—with ordinary Apple purchase/claim admission still off—capture the
  current App Store Connect **Production Server URL**/notification version, then
  use separate provider-mutation approval to set the exact production HTTPS URL
  with Version 2. Read back the exact URL/version and request Apple’s production
  test notification. Require verified delivery, one durable idempotent `TEST`
  receipt before 2xx, duplicate replay, zero event/credential/binding/grant/
  watermark writes, and non-2xx retry behavior for injected verification/storage
  failure. Before any native-lifecycle watermark, failure restores the captured
  prior URL/version (or clears the newly set URL), reads back that exact state,
  and rolls back the Apple lifecycle flag; after a watermark, retain a compatible
  lifecycle endpoint and fix forward. The Sandbox Server URL remains unchanged
  until the later isolated review handoff.
- [ ] Only after the production Google lifecycle endpoint/reconciler and exact
  expected subscription name are enabled/read back—with ordinary Google
  purchase/claim admission still off—use separate provider-mutation approval to
  create the distinct production Pub/Sub push subscription on the exact shared
  RTDN topic with authenticated push configuration. The new subscription begins
  receiving only messages published after creation. Bind/read back its distinct
  subscription name, production HTTPS
  push URL, OIDC audience/service account, Google Play publisher/subscriber IAM,
  acknowledgement/retry limits, protected dead-letter topic/subscription, and
  PII-free alert. Send provider test notifications through the shared topic and
  prove both staging and production subscriptions receive them, durably record
  idempotent delivery-test receipts without a Play Developer API call, return
  204, and create no token,
  credential, provider event, binding, or entitlement grant. Invalid auth/body
  and injected transient Google/storage failures exercise bounded redelivery/
  dead-letter handling, not false acknowledgement. Failure deletes the newly
  created production subscription, reads back `NOT_FOUND` for its exact name,
  and rolls back the pre-watermark Google lifecycle flag before any production
  build; authenticated test/quarantine receipts do not set that watermark and no
  other provider/read cutover is hidden.
- [ ] In each provider branch independently, validate its read-back production
  lifecycle endpoint with Apple's production test path or Google's tokenless test
  notification, and exercise only that provider's isolated review namespace with
  signed fixtures until any later Apple Sandbox URL handoff. Verify real-account
  continuity only through one explicitly owner-controlled test account and
  ordinary read-only/PWA smoke; do not clone staging, mutate customer data, or
  make a real charge. Bind the resulting backend commit/health/flag readback to
  the upcoming production build; staging evidence cannot satisfy this gate.
- [ ] Do not create a reviewer through normal production passwordless auth and do
  not provision review access before that provider's processed build exists.
  Confirm once that the production-active release-evidence public key is packaged
  in the deployed wheel/image, the private key is absent, and the coarse review
  kill switch is on; then require a zero review-record count separately for each
  provider before its branch. Exact credentials, users, fixtures, builds, and
  console fields are created only after that provider's processing/readback.
- [ ] Before production build dispatch, under the protected build-environment
  approval, bind the production Sentry DSN/org/project and secret
  `SENTRY_AUTH_TOKEN` to the production EAS environment, with the exact runtime/
  upload release-dist derivation and `SENTRY_ALLOW_FAILURE=false`. Read back only
  presence/scope—not secret values—and prove production
  `scrubIPAddresses=true`, server-side/default data scrubbers and reviewed field/
  rule lists exactly match staging, and the resolved production config has
  no staging project/DSN. Missing configuration blocks dispatch; no later local
  source-map reconstruction may substitute for the exact build-time upload.
- [ ] After production activation approval, dispatch the exact `production` EAS
  profile separately for iOS and Android. Each platform branch independently
  passes the protected-main/release-ref attestation and required-check readback;
  a failure dispatches neither credentials nor build for that platform and does
  not stop the other. Download the resulting IPA or AAB and all additional artifacts
  by its exact immutable EAS build ID; require the iOS xcarchive/export-
  metadata sidecar and repository-locked Android tools. Read back the resolved
  production URL, scheme, app/associated-link domains, provider environment,
  bundle/package identity, version/build, and embedded environment marker, and
  fail on any staging URL, scheme, domain, credential identifier, or feature
  binding.
- [ ] In each platform branch, re-run its applicable signed checks: iOS archive→
  IPA provenance, privacy-manifest/aggregate report and SDK signatures; or static
  signed Android AAB/generated-APK/ELF 16 KiB verification. Preserve protected
  hashes/sidecar and sanitized evidence tied to commit, backend readback, and EAS
  IDs. These static gates do not claim the App Store IPA or Play AAB is directly
  installable and do not substitute a locally rebuilt/ad-hoc binary.
- [ ] With separate provider-specific pre-review distribution approval, upload
  only that branch's exact EAS build ID—not a local/rebuilt artifact—to App Store
  Connect/TestFlight internal or a non-public Google Play internal release. Read
  back iOS version/build/processing/export-compliance status or Android
  versionCode/AAB hash/Play App Signing certificate plus that provider artifact
  identifier. For Android, read the exact
  production AAB's Play Device Catalog counts/exclusions for every non-phone
  form factor; any targeted tablet/Chromebook/desktop/TV/Wear/Automotive/XR
  device invalidates phone-only assets and forces expanded QA/assets or a rebuilt,
  fully re-attested excluding artifact. Keep public review,
  production track, automatic release, and ordinary purchase admission off.
  Install only each processed store-delivered build on a clean device and bind
  its install evidence back to its EAS ID/provider readback. One provider's
  processing, catalog, or installation failure blocks only that branch.
- [ ] If a platform build commit differs from the live backend commit, stop that
  branch for separate compatibility-signing approval. Check out both exact clean
  commits, run the attestation tool's full schema/behavior/check suite, pass the
  external private key only by descriptor, and create one protected signed
  compatibility envelope for that exact pair/release. Recreate it after any
  backend or client commit/check-set change. An equal OpenAPI hash, operator note,
  other platform's envelope, or expired envelope is not sufficient.
- [ ] For each installed provider artifact, rerun the protected release-ref gate:
  fetch current `origin/main`; require both the artifact build SHA and live baked
  backend SHA remain reachable from it with each original branch-protection check
  set complete/successful. If they differ, require the runner-produced signed
  envelope to bind exactly that A/B pair, its closed reviewed diff/behavior
  manifest, and the common contract hash; do not apply a blanket no-diff rule that
  would make the compatibility path impossible. A change outside the attested
  pair/manifest or any third deployed commit invalidates it. Then run
  `verify_mobile_api_deployment.py` against production with that artifact's
  sanitized build identity. Exact local/live/health/build OpenAPI SHA, baked/build
  commit equality or the exact current signed compatibility attestation,
  environment, origin, EAS project, ID-policy revision, and application-
  ID admission must pass. Without a valid exact-equality or attested A/B path, a
  new reviewed build/deployment is required; mismatch blocks only that provider.
- [ ] From each exact processed store-installed production artifact, run the
  non-waivable native-export scenario: direct same-origin 200, same/cross-origin
  redirect rejection before Authorization, exact type/length/ZIP/destination/
  operation result, exact maximum 1,100,000,000-byte ZIP with bounded JavaScript heap on
  physical hardware, two concurrent operations with one-operation cancellation,
  generation-wide drain, and sign-out/environment/history-reset/account-delete
  purge races. Bind iOS/Android evidence to provider artifact/hash, EAS ID,
  backend commit, native-module binary/hash, device/OS, and require no late file
  or callback; preview/development/rebuilt evidence cannot substitute.
- [ ] Before a production device registers for push, permanently close the shared-
  identity Version 1 staging sender lane. First run `swinglab mobile-push-cutover
  status --sessions-dir /data/sessions --environment staging --expo-project-id
  <eas-project-uuid> --json`. Then run `swinglab mobile-push-cutover close
  --sessions-dir /data/sessions --environment staging --expo-project-id
  <eas-project-uuid> --operation-id <staging-push-close-op> --dry-run --json`,
  inspect the aggregate result, and repeat the identical command with `--apply`.
  Read back `closed`, zero new registration/enqueue/provider-I/O, and the exact
  cutoff revision; disable/read back staging `mobile_push_enabled`, restart its
  worker in polling-only mode, and wait for every guarded call/lease to drain.
  Re-run `status`, read its server-computed `provider_safe_after`, and wait until
  authoritative current UTC is at or after that value and `expiry_safe=true`
  (`max(last_provider_accepted_at, persisted provider_may_accept_until) +` the
  message `ttl=900` plus the fence-frozen 60-second clock-skew allowance, or the
  already-safe close time when no provider call started). Then
  run `swinglab mobile-push-cutover purge --sessions-dir /data/sessions
  --environment staging --expo-project-id <eas-project-uuid> --operation-id
  <staging-push-purge-op> --dry-run --json`
  and its identical `--apply` command. Require zero raw registrations/tokens,
  nonterminal outbox rows, tickets, receipts, or leases plus a scratch restore that
  cannot resurrect anything behind the newest recovery-head cutoff. Under the
  Expo-credential mutation approval revoke the dedicated staging access token,
  remove staging `EXPO_ACCESS_TOKEN`, and read back both the old token and an
  unauthenticated send as `UNAUTHORIZED`. Staging remains healthy and polling-
  only; no later Version 1 step may reopen its fence.
- [ ] For each platform branch being advanced, preserve one physical device with
  that exact processed preview build/token proof, then install only its exact
  processed production build in place. The other platform need not be ready.
  Before new-origin auth or request, verify the client dismisses presented
  notifications, cancels scheduled notifications, clears the last notification
  response/token cache, and completes the environment-boundary purge. Record only
  whether the newly fetched Expo token is byte-identical and its environment-
  scoped HMAC—never the raw token. Exercise a staging terminal job/reminder and
  direct registration attempt and prove the closed fence, absent sender credential,
  zero outbox/provider calls, and zero late notification through the TTL/skew
  observation boundary, whether the token stayed the same or changed.
- [ ] On each available exact production build, test production push independently.
  On the first such branch, enable/read back the shared production sender only
  under production activation approval and rotate its dedicated access token;
  later branches reuse the read-back sender without reopening staging. Register
  one synthetic device for that platform with the production environment/project/app-identity tuple,
  and prove authenticated Expo send → durable ticket → authenticated
  receipt → generic delivery. Rotate the dedicated production access token,
  repeat, revoke the old token, and prove both the old token and an unauthenticated
  request receive `UNAUTHORIZED`. Read back new token-key HMAC/robot scope
  presence, assert `ttl=900`, and prove only production can reach that upgraded
  device. A failure disables/read backs the shared production sender and leaves
  polling live on both platforms; push is not allowed to couple or block either
  store submission/publication, and there is no unauthenticated fallback.
- [ ] Run shared production backend/PWA smoke once. Then, in each provider branch,
  run native auth, environment-boundary local purge, camera/import/upload/Brief/
  practice, polling and push when enabled, existing-entitlement/restore, and
  review-integrity bridge availability without opening a purchase sheet. The
  Google branch additionally requires its exact AAB/APK static and physical 16
  KiB proof; Apple never waits on it. The Apple branch requires its exact signed
  archive/privacy/SDK evidence; Google never waits on it. Only a provider's own
  static-plus-store-delivery-attested EAS build may proceed toward submission.
- [ ] For each platform separately, require its production EAS job's build-time
  Sentry artifacts under the release/dist bound to EAS ID, commit, version/build,
  and production backend: JavaScript source maps plus iOS dSYMs for Apple, or
  JavaScript source maps plus Android ProGuard/R8 mapping for Google. Run the
  protected verifier for only that platform's expected debug IDs/UUIDs/mapping.
  From each exact store-installed build send one PII-free synthetic
  crash, query its stored event/user/tag surfaces and prove no IP, `{{auto}}`,
  user/request/path/attachment survived, then verify symbolicated app frames plus correct release/dist and Release
  Health assignment, then delete the synthetic events. Missing/mismatched
  artifacts, unsymbolicated frames, wrong environment/release, or absent health
  assignment blocks only that provider's submission/rollout; the other platform's
  missing artifact is irrelevant. Preview or after-the-fact locally
  generated symbols/evidence never substitute.
- [ ] Read back Google Play production-access status before provisioning review
  access and again in the final asset/declaration readback immediately before
  Android submission. If the current account-specific gate applies, require the
  Console to show the qualifying closed test and approved production access;
  elapsed time, total beta size, or a submitted application without approval is
  not evidence.
- [ ] In each provider branch independently, and only after that branch's exact
  processed artifact exists, provision its production review identity before its
  feature screenshot capture. For Apple, pipe
  its protected `{account,password}` JSON to
  `swinglab review-access provision --sessions-dir /data/sessions --provider apple
  --app-version <version> --app-build <build> --opens-at <utc> --closes-at <utc>
  --operation-id <uuid> --dry-run --json --secret-stdin`, inspect sanitized output,
  then repeat byte-for-byte with `--apply` replacing `--dry-run`; run provider-
  scoped `open`, `seed-fixture`, `status-fixture`, and `status` with fresh operation
  IDs and explicit dry-run/apply where mutating. For Google run the same provision
  pattern with a different secret/user and `--standing-app-access` instead of
  window flags, then `open`, `seed-fixture`, `status-fixture`, and `status`. Read
  back that provider's user/credential/build/fixture counts and matching recovery
  revision without exposing IDs. Never use normal passwordless auth, a shared
  user/secret/mailbox, or a provider credential from staging. Failure blocks only
  the affected provider branch.
- [ ] After each provider's provision/fixture operation, take/read back a fresh
  encrypted production backup, verify the generation-7 manifest and recovery-head
  binding, and scratch-restore it against that newest `review_access_revision`.
  Before entering that provider's console credentials, require provider-scoped `status`/
  `status-fixture` hash/count parity and clean review-credential login on the exact
  build from the restored copy. A prior backup that lacks an admitted credential
  hash/fixture is not service-restorable for standing review access; mismatch
  blocks that review branch rather than silently dropping standing App access.
- [ ] For each exact store-installed production build, recapture its six public-
  listing slots. Use the packaged deterministic coaching fixture for Today,
  guided capture, Caddie Brief, Practice, and matched progress; use a separate
  ordinary Free passwordless capture account for More/Pro and frame only the
  state-invariant comparison region: truthful Free/Pro summary, Free-coaching
  continuity, and Terms/Privacy, with the admission-specific control region out
  of frame and no availability claim, localized price, CTA, sheet, or charge.
  Prove the captured-region snapshot and screenshot-surface config hashes match
  with ordinary admission off and on; a mismatch blocks capture. Re-export Google
  listing graphics if their approved source/export hash changed. Bind production
  EAS IDs, commit/UI/config/account-mode hashes plus the exact sanitized
  `status-fixture` template hash/generation. Run
  `verify_mobile_store_assets.py --scope apple-public` for iOS and
  `--scope google-submission` for Android; OCR/metadata must reject every review/
  demo/test/sandbox label from public assets. A failure blocks only its provider.
  Get independent visual/copy/privacy approval per scope. Seal each sanitized
  fixture-capture attestation before that review generation can be purged. Keep
  only the two Apple IAP review artifacts incomplete.
- [ ] Re-run shared full CI/security, backend/PWA smoke, scratch restore,
  association links, and support/privacy/delete URLs once. Then apply provider-
  local non-waivable gates. Apple alone requires its signed real-device/billing
  matrix, screenshots/content rating/privacy answers, review access, exact build/
  commit provenance, archive privacy/SDK-signature report, export-compliance
  status, and Pro disclosure. Google alone requires its signed real-device/
  billing matrix, screenshots/content rating/Data Safety answers, review access,
  exact build/commit provenance, AAB/APK/ELF 16 KiB alignment plus 16 KiB-device
  proof, and Pro disclosure. A failure blocks only the applicable provider; a
  shared prerequisite failure blocks both.
- [ ] During each provider's exact-build pre-submission freeze, refresh only that
  provider's visible console declaration inventory and reconcile code/SDKs/
  permissions/data flows/build behavior/metadata to its rows in
  `store-console-declarations.md`. Obtain qualified review for any newly
  applicable legal/compliance answer. With separate provider-metadata approval,
  update/read back exact normalized answer/applicability/completion state/hash.
  A missing/stale/contradictory/new/unreadable Apple declaration blocks Apple;
  the equivalent Google declaration—including Health apps even when the reviewed
  answer is no health feature—blocks Google only.
- [ ] Under separate Apple or Google provider-metadata approval, enter only that
  provider's credential and instructions: Apple App Review fields or Google's
  different credential and precise Play **App access** fields. Never require both
  approvals/actions in one step. Read back that redacted field revision, then on its clean exact store build prove the
  visible review login, full `review_demo_active` Pro traversal, generic failure,
  review-password export/reset/delete, and fresh isolated fixture generation after
  synthetic deletion. Apple must match its current window/build. Google must
  re-login on the standing record with `purchase_allowed=false`, null context,
  no billing config/intent/CTA/sheet/restore claim/manage state. Keep Google App-
  access details continuously valid while the listing requires sign-in.
- [ ] Immediately before production Apple review testing, close only the Apple
  branch of the staging billing-test lane, wait for every opened/confirming Apple
  intent to settle or enter protected resolution, and run bounded App Store
  Server API history/status reconciliation until two passes share a stable cursor/
  snapshot. Record a sanitized staging cutoff while leaving its lifecycle
  verifier/reconciler enabled. With separate provider-mutation approval, edit the
  app's single Sandbox Server URL from staging to the production review endpoint,
  require V2, read back the exact URL/version, and send a provider test
  notification. Production accepts only predeclared review originals and
  quarantines every unrelated sandbox payload; it never forwards or writes the
  staging database. Failure closes review and restores/read-backs staging before
  retry.
- [ ] Run Apple review purchase testing independently. Enable it only with
  `swinglab review-access set-purchase-test --sessions-dir /data/sessions
  --provider apple --enabled --expires-at <utc> --operation-id <uuid> --dry-run
  --json`, inspect sanitized status, then repeat exactly with `--apply` replacing
  `--dry-run`; it remains bounded by the open Apple submission window. On the exact store build,
  prove AppTransaction marketing-version/app-version-ID plus separate build
  binding, monthly/annual disclosure/sandbox sheets, restore, renewal, cancel/
  paid-through, refund/revoke, outage/retry, wrong identity/build/product, expiry,
  isolated writes and reconciliation with zero real charges. Capture only the two
  Apple IAP review screenshots here. Extend expiry only through a new approved
  bounded operation if review remains open; expiry fails closed automatically.
- [ ] Run Google purchase testing as pre-submission developer QA only, using a
  separate controlled OS Play License Tester account; do not claim it is or will
  be the provider reviewer’s OS account. Under approval add/read back that tester,
  acquire the exact processed artifact from Play, and require test instruments/
  notice. Preview the Google purchase-test switch with the same literal `--dry-run`
  command shape and an expiry no more than two hours, inspect status, then repeat
  with `--apply`; prove Play Integrity, monthly/annual,
  restore/lifecycle, `subscriptionsv2.get`, acknowledgement, RTDN production
  isolated writes plus staging quarantine/replay, and zero real charges. Then run
  `set-purchase-test --disabled` first with `--dry-run` and then the byte-identical
  command using `--apply` plus the same fresh operation ID, wait for drain/reconcile/
  isolated purchase-state purge, read back demo/auth still active with null
  purchase context, remove the OS account from License Testing, and read back
  absence. All of this completes before Google submission; License Testing and a
  Google `production_review` sheet are never left for final Play review.
- [ ] Complete and gate provider assets independently. In the Apple branch, add
  its two IAP review artifacts to the six approved public slots, run
  `verify_mobile_store_assets.py --scope apple-submission`, obtain independent
  visual/copy/privacy approval, and under Apple metadata approval upload/read back
  exact public order/dimensions/locale/alt text/rendered thumbnails plus each
  product's screenshot/notes/source hash/attachment ID. In the Google branch, run
  `verify_mobile_store_assets.py --scope google-submission`, obtain its independent
  approval, and under Google metadata approval upload/read back the six public
  slots, icon, feature graphic, order/dimensions/locale/alt text/thumbnails and
  hashes. `--scope all` may produce a combined evidence report after both pass but
  is never required for either submission. Re-read only that provider's
  declarations and, for Google, production access. Keep Apple's bounded
  credential/demo (and purchase test only if still required) until Apple review
  is terminal; keep Google's purchase-disabled standing credential/demo and App-
  access instructions valid while its listing is active. Drift blocks only its
  provider.
- [ ] With separate **Apple submission** approval, rerun only Apple's protected-
  ref/check/API verifiers and Mac/Vision Pro opt-out readbacks, then select/attach
  the existing TestFlight-processed build by provider artifact ID, version/build,
  and EAS/hash provenance—never upload a rebuilt binary. For the first auto-
  renewable submission, add the `CaddieInsight Pro` group and both monthly/annual
  products with their required IAP review screenshot/notes to that exact app-
  version submission. Read back build/product/group links and independent app/
  monthly/annual accepted/rejected states; an approved app with an unattached or
  unapproved product is not billing approval. Record approval, not publication,
  and answer review questions only from verified Apple behavior.
- [ ] With separate **Google submission** approval, rerun only Google's protected-
  ref/check/API verifiers and production-access readback, then add/promote the
  already uploaded internal artifact/versionCode into the production review draft
  with manual release. Create no second AAB upload/versionCode. Read back exact
  artifact hash, versionCode, Play App Signing association, products/base plans/
  explicit offer state, and review-draft/manual-release state; fail on substitution.
  Record approval, not publication, and answer review questions only from verified
  Google behavior. Neither submission approval authorizes the other checkbox.
- [ ] When Apple review is terminal, first preview `set-purchase-test --disabled`
  with a fresh operation ID, inspect sanitized status, repeat with `--apply`, then
  drain/reconcile/purge only its isolated purchase state and prove no
  production grant/binding. Dry-run then apply distinct Apple `close` and `purge`
  operations to recovery-fence the review credential/demo/build, purge its
  synthetic generation/fixture, and read back zero rows. Under provider-metadata
  approval clear the Apple review account/password/instructions and read back the
  cleared field revision; do not leave a dead credential in App Store Connect.
  Restore the single Sandbox Server URL to staging, read back V2 URL state, send a
  staging test notification, and reconcile from the recorded cutoff. Complete all
  Apple cleanup before public publication. For Google, require purchase testing
  already disabled/purged and License Testing membership absent, but deliberately
  keep the purchase-disabled standing credential/demo/build plus Play App-access
  fields valid; terminal review is not authority to close or purge them.
- [ ] For each provider independently, require terminal app/release approval and
  active/read-back product state—Apple app plus both attached IAPs; Google current
  production access when applicable plus app/release and monthly/annual product/
  base-plan and either explicit no-offer/regular sentinel or separately approved
  offer state. Obtain one explicit provider-specific go-live
  approval for the exact two-step public-launch/admission cutover. Immediately
  before manual publication, rerun that artifact's protected-ref/required-check
  and production API-deployment verifiers; for Apple also re-read both compatible-
  app opt-outs. With ordinary
  purchase/claim admission still off, manually release version 1 once to only the
  preapproved Apple storefronts or Google countries and read back public store
  availability for the exact artifact. The first Google production release is
  not a 5/20/50% staged rollout, and the first Apple release does not use phased
  release. Either provider may remain held while the other launches.
- [ ] Only after that provider’s public availability readback and one more
  successful production API-deployment verification for that public artifact,
  enable/read back its ordinary purchase/claim admission, resolved client capability, exact
  product/base-plan/offer identity, and disclosure path using no-charge safe
  validation. On the exact public store-installed build, rerun the off/on snapshot
  parity assertion for the framed More/Pro comparison region and
  `verify_mobile_store_assets.py --scope apple-public` for Apple or
  `--scope google-submission` for Google; a changed region or screenshot-
  surface config disables admission and requires corrected assets plus the
  provider's current metadata-review path before relaunch. The expected appearance
  of price/CTA controls outside the captured frame does not stale that artifact.
  Never broaden sandbox/License Testing, copy a review grant, or
  expose the other provider. A failed readback immediately leaves/disables that
  provider’s admission while Free, existing access, restore and lifecycle remain
  live; the brief public Free-only interval is the intentional fail-safe state.
- [ ] Reserve provider percentage/phased rollout controls for a later update that
  already has a prior public version, and only after a fresh official-console
  readback plus separate approval and observation windows. Do not retroactively
  describe version 1’s single manual launch as staged/phased.
- [ ] At every step verify crashes, upload completion, queue/disk/SQLite,
  auth/push, purchase/restore/notification reconciliation, support, PWA health,
  and source-specific refunds/revocations. Stop rollout on any hard-gate miss.
- [ ] Version 1 rollback order is server safety first: disable/read back only the
  affected native feature/provider’s new-purchase/claim admission and client
  capability, then pause/remove new store availability where the provider permits;
  keep v2
  entitlement reads plus lifecycle verification, webhooks, acknowledgements,
  reconciliation, renewals, refunds/revocations, and paid-through expiry running.
  After the native-lifecycle watermark, never roll back to a binary/schema that
  lacks those paths. Version 1 has no prior public native build to restore: keep
  the compatible backend/PWA and lifecycle/restore paths, then fix forward with a
  new fully reviewed store version. Never claim an Apple/Google binary rollback
  that the first release cannot provide.
- [ ] Maintain Google standing review access while the listing requires sign-in.
  Before each later submission, `roll-build` adds the exact new build while the
  current public build remains admitted. Keep both only through the configured
  overlap; submit/publish the new build, then require its public-availability plus
  clean-login/observation readback before retiring/draining the old build. Never
  retire the only publicly available build merely because a new draft exists.
  Credential rotation is provider-local and two-phase: `prepare`
  with the new secret through `--secret-stdin`; before console activation, take/
  read back and scratch-restore an encrypted backup containing both credential
  hashes against the newest recovery revision and prove old/new clean logins;
  update Play App-access fields and
  read back/test; generate a fresh signed `credential_login_verified` envelope
  with `capture_mobile_console_evidence.py` and pipe it to `rotate-credential
  --phase activate --console-evidence-stdin`; remove/read back the old console
  credential; sign `credential_old_absent` and pipe it to `--phase retire`.
  Roll back only the prepared credential on failure and always retain one tested
  credential. Google permanent close/purge is permitted only after delisting or
  App-access sign-in clearance: capture/sign one fresh ≤30-minute
  `app_access_clear` envelope/evidence ID, pipe it through `close --permanent
  --dry-run`, inspect, then the same `--apply`; after close readback, capture a
  second fresh envelope/evidence ID and independently dry-run/apply
  `purge --permanent`. Evidence is never reused across operations. A mid-review
  Apple rotation uses the same signed phases inside its held window and affects no
  Google state. Keep only sanitized evidence digests/IDs; commit no credentials or
  signed envelopes.
  After every credential activation/retirement or standing fixture regeneration,
  take/read back a fresh encrypted backup and scratch-restore against the newest
  recovery revision; require status/fixture parity and clean current-build login
  before treating the old backup or credential as retired.
- [ ] Record six independent states with evidence and timestamps:
  implementation commit, GitHub `main`, Railway backend health, public PWA,
  Apple approval/public availability, and Google approval/public availability.
- [ ] Commit the final non-secret release record only after actual readback:
  `git commit -m "docs: record CaddieInsight mobile v1 release"`.

## Release plan completion gate

- [ ] Ordinary CI is green and reproducible with no credentials.
- [ ] Signed artifacts, provider product/webhook configuration, real-device
  matrices, alpha, and beta evidence are complete and tied to exact commits.
- [ ] Every reliability/privacy/security/capacity gate passes and the retention
  beta supports public release rather than merely producing downloads.
- [ ] The exact submitted iOS archive passes app/embedded-SDK privacy-manifest,
  required-reason, signature, aggregate-report, and App Store-answer
  reconciliation; the exact submitted Android artifact passes bundle/APK/ELF
  16 KiB alignment and a 16 KiB-device run. Both retain sanitized evidence.
- [ ] Exact-production screenshots/listing graphics and every required or
  applicable Apple/Google console declaration pass repository validation,
  independent review, provider upload/readback, and build/UI freshness gates.
- [ ] Exact-production source maps, dSYMs, and Android mappings are present in
  Sentry under the matching release/dist; one synthetic crash per platform was
  symbolicated/read back and then purged before submission.
- [ ] Apple and Google submission/publication were separately approved and live
  availability was read back from each public store.
- [ ] Final reporting distinguishes all six release states and retains an
  actionable PWA/backend/build rollback path.
