# CaddieInsight Mobile Backend Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the secure, retry-safe backend foundation required by one native iOS/Android client while preserving current PWA, browser, CLI, Shopify, Railway, and analysis success contracts. The only legacy operational extensions are the recovery-store fail-closed results specified below: mobile-token issue/revoke can return 503, browser history reset can remain at a truthful 202 pending page, and verified Shopify privacy webhooks can return retriable 503 until their recovery record and local erasure complete.

**Architecture:** `swinglab.web.app.create_app` remains the composition root. New Pydantic contracts and additive routers live in `swinglab/api/`; mobile persistence/orchestration lives in focused `swinglab/web/` modules but reuses the existing `UserStore` and `JobManager` connections. Challenge consumption and device-token issuance share one `UserStore` transaction. Upload completion uses one per-upload lock plus a durable prepared/finalized journal so SQLite and same-volume file moves recover to one externally visible job after any crash. Provider work is kept out of analysis workers through a leased push outbox.

**Tech Stack:** Python 3.11+, FastAPI/Pydantic, SQLite WAL, local same-volume session artifacts, `httpx`, `pytest`, deterministic OpenAPI JSON, existing Railway root-container contract.

## Global Constraints

- Preserve all routes and key shapes asserted by `tests/test_foundation_contracts.py`; new endpoints are additive.
- Preserve strict bearer parsing: an invalid `Authorization` header never falls back to a cookie.
- Cookie auth is allowed only on additive read routes. Every new unsafe mobile
  mutation is bearer-only; auth/step-up exchanges and exact deletion replay use
  their own challenge/idempotency secrets and never fall back to a cookie.
- Preserve the `/upload` multipart route and its authentication/quota error ordering, including the OpenAPI `club` patch at `swinglab/web/app.py:4699`.
- Keep the single-replica `/data/sessions/swinglab.db` and local-artifact
  contract. Do not move app data to Redis/Postgres/object storage or add a second
  worker process. The sole exception is the small approval-gated monotonic
  recovery-fence record chain plus head at the existing off-volume backup
  destination, accessed
  synchronously with its own least-privilege credentials; media, database rows,
  and ordinary artifacts remain local.
- New mobile features default off in bare-code defaults and shipped configuration until their deployment gate is verified.
- No provider HTTP request runs inside `JobManager._run`; terminal state is durable before an outbox row is claimed.
- No email, raw media, report content, metric value, auth secret, or push token appears in logs or product events.
- Every persisted mobile state HMAC is a pair of a bounded key ID and 32-byte
  digest. Unique indexes and lookups include both fields; no table stores an
  unversioned HMAC that would make rotation or restore ambiguous.
- Existing `mobile_api_tokens.token_hash` is explicitly not a state HMAC: it
  remains the established unversioned SHA-256 verifier of a high-entropy random
  bearer secret so legacy and new authentication stay compatible. Recovery
  HMACs that stored verifier under `recovery-token-verifier`; raw bearer material
  is neither needed nor persisted.
- Every HMAC uses a fixed, versioned, domain-specific prefix. A digest created
  for one raw-input class or operation is never accepted by another domain.
- Run focused tests after every task and the full Python suite plus container smoke before integration.

---

## Task 1: Freeze the versioned API and generate deterministic client contracts

**Files:**

- Create: `swinglab/api/contracts.py`
- Create: `swinglab/api/errors.py`
- Create: `scripts/export_openapi.py`
- Create: `docs/api/openapi-v1.json`
- Create: `tests/test_mobile_openapi_contract.py`
- Create: `tests/test_mobile_api_errors.py`
- Modify: `swinglab/api/__init__.py:1-25`
- Modify: `swinglab/web/app.py:477-585,4076-4350,4699-4728`
- Modify: `swinglab/web/jobs.py:218-288`
- Modify: `swinglab/web/users.py:830-950`
- Modify: `swinglab/web/throttle.py:40-80`
- Modify: `tests/test_foundation_contracts.py:25-57,168-215`
- Modify: `.github/workflows/ci.yml:36-46`

**Interfaces:**

- Produces `swinglab.api.contracts.RESOURCE_VERSION: Literal[1]`.
- Produces Pydantic models with `extra="forbid"`: `APIError`,
  `IdentityResponse`, `ProfileResponse`, `ProfileUpdateRequest`,
  `LegacyTodayResponse`,
  `MobileTodayResponse`,
  `LegacySessionResponse`, `MobileSessionResponse`,
  `BriefResponse`, `ProofCycleTargetResponse`, `ComparableContextGroupResponse`,
  `ProgressResponse`, `PracticeCheckinResponse`, `CapabilitiesResponse`,
  `NativeAuthStartRequest/Response`, `NativeAuthExchangeRequest`,
  `NativeAuthExchangeSuccessResponse`, `NativeAuthExchangePendingResponse`,
  `NativeSignOutResponse`,
  `ComparisonTarget`, `AnalysisFailureCode`, `AnalysisFailure`,
  `AnalysisRetryRequest/Response`,
  `UploadCreateRequest/Response`, `UploadStatusResponse`,
  `PushRegistrationRequest/Response`, and `NativeEventRequest`.
- Produces `python scripts/export_openapi.py --output docs/api/openapi-v1.json`.
- Produces `install_mobile_error_handlers(app, mobile_route_names)` before any
  mobile route lands. It maps only named mobile routes’ `HTTPException`, request
  validation, throttling, and uncaught failures to `APIError`, preserving
  `WWW-Authenticate`/`Retry-After`; legacy routes retain `{"detail": ...}`.
- Consumes only a temporary sessions directory and `Config()`; exporting must
  not send mail, start Shopify sync, or create a persistent scheduler.
- Extends both app factories with keyword-only
  `start_background_workers: bool = True`. `False` suppresses Shopify sync,
  digest, expiry, push, and every future background worker while preserving the
  existing `start_shopify_sync_worker` compatibility keyword.
- Produces idempotent `JobManager.close()`, `UserStore.close()`, and
  `Throttle.close()` methods so contract generation releases every SQLite and
  thread-pool resource that `create_app` owns.
- Produces `JobManager(..., recover_interrupted: bool = True)` plus explicit
  `.recover_interrupted(blocked_user_ids=frozenset())`. Passing false performs
  schema setup but neither calls `_requeue_interrupted()` nor submits work; the
  default preserves existing direct callers until Task 6 changes app startup.

- [ ] Add a failing contract test that creates an app with a temporary sessions
  directory, canonicalizes `app.openapi()` with sorted keys and compact JSON,
  and compares it byte-for-byte with `docs/api/openapi-v1.json`. Expected first
  failure: `FileNotFoundError` for the snapshot.
- [ ] Add a failing lifecycle test that closes jobs, users, and throttle twice
  after app construction without leaking a worker thread or locked SQLite file.
  Expose the existing throttle instance as `app.state.throttle`, then implement
  the three idempotent close methods. `JobManager.close()` shuts down its pool
  before closing its connection.
- [ ] Split constructor-time `_requeue_interrupted()` behind the explicit
  recovery method. Prove `recover_interrupted=False` submits nothing and prove
  the default path preserves current restart behavior; the OpenAPI exporter and
  every `start_background_workers=False` app use the deferred path.
- [ ] Add a failing test that asserts the pre-existing route set remains a
  subset of the generated path set and that `/upload` still requires `club`.
  Run `python -m pytest tests/test_mobile_openapi_contract.py tests/test_foundation_contracts.py -q`; expect failure until the exporter exists.
- [ ] Add a synthetic named mobile route in tests and assert exact APIError
  contracts for 401/403/404/409/422/429/500, random reference ID on 5xx, no
  validation input/path leakage, and unchanged legacy errors. Implement and
  install the scoped mapper now so Tasks 3–7 cannot commit an unstructured new
  route even before Task 8 runs the whole real-route matrix.
- [ ] Implement `contracts.py` with explicit field names and `Literal` enums.
  Use this base and never accept arbitrary client metadata:

  ```python
  class ContractModel(BaseModel):
      model_config = ConfigDict(extra="forbid")

  class APIError(ContractModel):
      resource_version: Literal[1] = 1
      code: str
      message: str
      retryable: bool = False
      reference_id: str | None = None
  ```

- [ ] Add `scripts/export_openapi.py` with
  `export_openapi(output: Path) -> None`. Create its sessions path with
  `tempfile.TemporaryDirectory(prefix="swinglab-openapi-")`, then construct the
  app as `create_app(Config(), Path(temp_dir),
  start_background_workers=False)`. Remove only
  the volatile top-level `servers` entry if present, serialize with
  `json.dumps(schema, sort_keys=True, separators=(",", ":")) + "\n"`, and call
  `app.state.jobs.close()`, `app.state.users.close()`, and
  `app.state.throttle.close()` in `finally`.
- [ ] Annotate existing `/api/v1` routes with response models without changing
  their returned dictionaries. Where a current payload is richer than the
  first typed model, model the existing fields; do not delete the field.
- [ ] Keep pre-existing `/api/v1/sessions` routes on `LegacySessionResponse` with
  their exact current keys/values, including legacy log/error compatibility. Add
  a separate `serialize_mobile_session(job, owner) -> MobileSessionResponse` for
  additive native routes and upload/retry responses. It allowlists only owned
  public status/progress/result references, comparison, and the bounded failure/
  retry contract—never legacy `log`, raw `error`, traceback, command, local path,
  stderr, or exception text. Mobile feature code/tests may not import the legacy
  serializer/model.
- [ ] Keep pre-existing `/api/v1/today` on `LegacyTodayResponse` with exact values.
  Add `serialize_mobile_today(...) -> MobileTodayResponse`; any embedded latest
  session is a `MobileSessionResponse`, so raw legacy job log/error/path content
  cannot leak through the native dashboard.
- [ ] Validate real successful and error response bodies from every typed route
  with the corresponding contract model. Existing `JSONResponse` handlers do
  not gain runtime validation merely from a `response_model`; contract tests
  must call `model_validate(response.json())` and assert exact key preservation.
  New mobile routes use `APIError`; legacy `{"detail": ...}` errors stay intact.
- [ ] Deliberately replace the current all-API-route equality assertion in
  `test_account_passwordless_and_api_routes_are_stable` with an exact frozen
  legacy set that must remain a subset. Keep exact method and response-key
  assertions for every legacy route so additive mobile paths cannot weaken them.
- [ ] Generate the snapshot and rerun the two focused files. Expected result:
  all tests pass and the legacy path/key assertions remain unchanged.
- [ ] Run `python -m pytest tests/test_mobile_openapi_contract.py
  tests/test_mobile_api_errors.py tests/test_foundation_contracts.py -q`;
  expect all pass before the snapshot is committed.
- [ ] Add a CI command after pytest:
  `python scripts/export_openapi.py --output "$RUNNER_TEMP/openapi.check.json"`
  followed by a
  Python byte comparison with `docs/api/openapi-v1.json`. Do not rewrite the
  tracked snapshot in CI.
- [ ] Commit: `git add swinglab/api swinglab/web/app.py swinglab/web/jobs.py swinglab/web/users.py swinglab/web/throttle.py scripts/export_openapi.py docs/api tests/test_mobile_openapi_contract.py tests/test_mobile_api_errors.py tests/test_foundation_contracts.py .github/workflows/ci.yml && git commit -m "test: freeze mobile API contract"`.

## Task 2: Make bearer authentication selector-aware without breaking callers

**Files:**

- Create: `swinglab/api/auth.py`
- Modify: `swinglab/web/users.py:723-738,3864-4190`
- Modify: `swinglab/web/app.py:649-732,4101-4350`
- Modify: `tests/test_mobile_api_tokens.py:111-430`
- Create: `tests/test_mobile_auth_context.py`

**Interfaces:**

- Produces `MobileAPIPrincipal(user: User, selector: str, auth_epoch: int,
  installation_key: str | None)` beside `UserStore` in `swinglab.web.users`; API code
  imports it one way so `web.users` never imports `swinglab.api.auth`.
- Produces `UserStore.authenticate_mobile_api_principal(token: object, *, now: float | None = None) -> MobileAPIPrincipal | None`.
- Preserves `UserStore.authenticate_mobile_api_token(...) -> User | None` as a
  compatibility wrapper around the principal method.
- Produces `MobileAuthContext(user: User, via_bearer: bool, selector: str | None)`
  and `resolve_mobile_auth(request, users, require_account) -> MobileAuthContext`.
- Produces `require_mobile_bearer(...) -> MobileAuthContext`, which rejects
  cookie-only authentication and is required by every new authenticated POST,
  PUT, PATCH, or DELETE route—not only push/device-bound routes. It returns
  structured 401 `bearer_required` with zero side effects.
- New bearer-authenticated native routes do not require an `Origin` header and
  accept its normal absence from React Native. If an `Origin` is present it
  cannot replace or weaken bearer validation. Same-origin/CSRF enforcement stays
  on legacy browser cookie/form routes only; native routes never fall back from
  a missing/invalid bearer to an ambient valid cookie.

- [ ] Add failing tests for a valid principal selector, malformed/unknown token
  equivalence, expiry, revocation, `auth_epoch` invalidation, and sampled
  `last_used_at` updates. Expected failure: the principal method is absent.
- [ ] Add a failing route-level test proving `require_mobile_bearer` returns 401
  for cookie-only access and never accepts an invalid bearer alongside a valid
  cookie.
- [ ] Add a shared unsafe-route test fixture. Each later task registers every new
  mutation and proves cookie-only and invalid bearer plus valid cookie perform
  no write for missing, same-origin, and hostile `Origin` variants. Separately
  prove a valid bearer with no `Origin` succeeds, a hostile supplied `Origin`
  does not grant cookie authority or change bearer identity, and auth/step-up
  exchanges reject/ignore ambient cookies and authenticate only their challenge
  secrets. Preserve legacy browser routes and their existing
  `_same_origin_form_post` checks.
- [ ] Add the frozen dataclasses and refactor the existing authentication body
  once. The compatibility method must remain exactly:

  ```python
  def authenticate_mobile_api_token(self, token: object, *, now=None) -> User | None:
      principal = self.authenticate_mobile_api_principal(token, now=now)
      return principal.user if principal is not None else None
  ```

- [ ] Move reusable strict-header logic to `swinglab/api/auth.py`; retain the
  current customer-facing 401 text and `WWW-Authenticate: Bearer` header.
- [ ] Change the `app.py` closure to adapt `MobileAuthContext` back to its
  existing `(user, via_bearer)` tuple for old routes. Do not rewrite every
  existing handler in this task.
- [ ] Run `python -m pytest tests/test_mobile_api_tokens.py tests/test_mobile_auth_context.py tests/test_foundation_contracts.py -q`; expect all pass.
- [ ] Commit: `git add swinglab/api/auth.py swinglab/web/users.py swinglab/web/app.py tests && git commit -m "refactor: expose mobile auth context"`.

## Task 3: Add challenge-bound, one-time native email sign-in

**Files:**

- Create: `swinglab/web/mobile_schema.py`
- Create: `swinglab/web/mobile_auth.py`
- Create: `swinglab/web/review_auth.py`
- Create: `swinglab/web/credential_mutations.py`
- Create: `swinglab/web/recovery_fence_ledger.py`
- Modify: `swinglab/api/auth.py`
- Modify: `swinglab/backups/store.py`
- Modify: `swinglab/backups/cli.py`
- Modify: `swinglab/cli.py`
- Modify: `swinglab/web/users.py:222-435,831-1200,3763-4057,5600-5700,6800-6895`
- Modify: `swinglab/api/contracts.py`
- Create: `swinglab/api/mobile_routes.py`
- Modify: `swinglab/web/app.py:477-630,882-920,948-960,1768-1920,4076-4350`
- Modify: `swinglab/web/access_log.py`
- Modify: `swinglab/web/throttle.py:1-100`
- Modify: `swinglab/config.py:144-196`
- Modify: `config.yaml:147-196`
- Modify: `pyproject.toml:21-43`
- Modify: `swinglab/backups/core.py`
- Create: `swinglab/backups/restore_service.py`
- Modify: `docs/mobile-api-tokens.md`
- Modify: `docs/environment.md`
- Modify: `docs/deployment.md`
- Modify: `docs/operations/backup-recovery.md`
- Create: `tests/test_mobile_native_auth.py`
- Create: `tests/test_mobile_review_auth.py`
- Create: `tests/test_recovery_fence_ledger.py`
- Create: `tests/test_recovery_fence_remote_store.py`
- Create: `tests/test_mobile_rate_limits.py`
- Create: `tests/test_credential_mutation_guard.py`
- Modify: `tests/test_backups.py`

**Interfaces:**

- `POST /api/v1/auth/email/start` consumes
  `{"email": str, "code_challenge": base64url_sha256, "installation_id": uuid,
  "device_label": str}` and
  always returns HTTP 202 with `challenge_id` and `expires_at` in a
  non-enumerating response.
- `POST /api/v1/auth/email/exchange` consumes
  `{"challenge_id": str, "email_code": str, "code_verifier": str}` plus a
  required 128-bit `Idempotency-Key`. It returns HTTP 201 with the same raw
  `ciat_...` credential on an exact retry, or HTTP 202
  `{exchange_id, status:"pending", retry_after_seconds}` with no credential while
  a prior installation token's recovery fence is being published.
- `ReviewAuthAdmission` is a composition-root protocol whose default
  implementation always denies. Entitlements Task 5 supplies the production
  implementation and is the only component allowed to match a provider-specific
  synthetic user and exact platform/version/build. Apple also requires its
  bounded submission window; Google instead requires an active standing Play
  App-access record for a currently supported public/submitted build.
- `POST /api/v1/auth/review/start` consumes
  `{"provider":"apple"|"google", "account":str,
  "code_challenge":base64url_sha256, "installation_id":uuid,
  "device_label":str}` plus the immutable app identity headers. While no review
  lane is configured it returns 404 with zero writes. Otherwise it always returns
  the same HTTP 202 challenge shape for matching, unknown, wrong-provider, and
  wrong-build accounts, sends no email, and discloses no admission state.
- `AppIdentityHeaders` is the exact required native tuple
  `X-CaddieInsight-Environment`, `X-CaddieInsight-Platform`,
  `X-CaddieInsight-App-Version`, `X-CaddieInsight-App-Build`, and
  `X-CaddieInsight-Application-Id`. One shared parser rejects a missing,
  duplicated/comma-joined, whitespace-padded, malformed, unsupported, or
  environment/platform/application-ID/version/build-mismatched member before
  review admission or any side effect. The parsed immutable value is passed—not
  reconstructed—to review auth, billing config/intents, Apple review challenges,
  and Google Integrity binding; no JSON/query/caller header override is accepted.
- `mobile_deployment_environment` is one required server-owned closed value
  `development|staging|production` (safe shipped default `development`) with the
  exact non-secret override `CADDIEINSIGHT_MOBILE_DEPLOYMENT_ENVIRONMENT`. The
  parser, staging-only lane rejection, review/billing evidence validation, push
  fences/CLI, and health readback all use this value; they never infer environment
  from a caller header, URL/host, database path, Railway name, or build profile.
  Any header or operator `--environment` mismatch fails before state or provider
  I/O. Staging/production activation requires an explicit matching override and
  readback; the default cannot masquerade as either.
- `PUBLIC_BASE_URL` is the one server-owned canonical application origin. At
  staging/production startup it is required, normalized to one HTTPS origin with
  no userinfo/path/query/fragment, and exposed in `/healthz` as
  `mobile_public_origin`; request Host/Forwarded headers and native identity
  headers can never change it. The shipped closed application-ID policy revision
  1 is `{development:["com.caddieinsight.app.dev"],staging:
  ["com.caddieinsight.app.staging","com.caddieinsight.app"],production:
  ["com.caddieinsight.app"]}` for each supported native platform. The staging
  pair deliberately admits direct-install internal and store-identity preview
  builds; production admits no staging/development ID. Health exposes only the
  active environment's sorted list and revision. The shared parser rejects any
  ID outside it before state/provider I/O, and no runtime mutable variable,
  database row, URL, or caller header can widen the code-owned policy.
- `POST /api/v1/auth/review/exchange` consumes
  `{"challenge_id":str, "password":str, "code_verifier":str}` plus a required
  128-bit `Idempotency-Key`. `ReviewAuthAdmission` verifies a dedicated scrypt
  hash from the provider-scoped review-credential record—never
  `users.password_hash` or `UserStore.authenticate`—then the endpoint reuses the
  email exchange's inactive token/recovery-fence journal and 201/202 exact-replay
  contract. Failure is one generic rate-limited 401 class. The reusable password
  never becomes token key material, appears in a canonical request hash, or
  enters logs/persistence.
- A successful review exchange issues an otherwise ordinary installation-bound
  bearer with nullable `review_provider`, exact `review_build`, and
  short-lived `review_expires_at` scope. This is always a bearer-session expiry,
  not necessarily the credential/lane expiry: a Google reviewer can re-login
  while its standing App-access record remains active. `MobileAPIPrincipal` and
  `MobileAuthContext` carry that scope; each authenticated request rechecks the lane, and closure/expiry,
  provider/build mismatch, account deletion, or password/reset epoch rotation
  rejects and revokes it. Ordinary email/browser tokens keep null scope. Apple
  and Google review credentials must resolve to different stable users, so a
  bearer, intent, grant, or cleanup action can never cross providers.
- Review-scoped device-limit queries first exclude `review_expires_at <= now` and
  atomically mark those selectors revoked before counting the six live
  installations. A bounded startup/periodic purge deletes expired review
  challenges/exchange receipts and expired scoped tokens after cancellation/
  extension drain; expiry remains authoritative after crash or old-backup restore,
  so no cleanup outage can make an expired selector usable or able to block a new
  standing-Google login. Ordinary token retention/device limits are unchanged.
- `UserStore.begin_mobile_email_signin(...) -> MobileAuthChallenge` stores only
  normalized email, verifier challenge, versioned email-code HMAC pair,
  lifecycle metadata, and a bounded device label.
- `UserStore.prepare_mobile_email_signin_exchange(..., idempotency_key) ->
  MobileAuthExchangeJournal` consumes the challenge, converges the account,
  reads the resulting `auth_epoch`, inserts the replacement token as inactive,
  locally fences the prior installation selector, and persists only `prepared`
  in one `BEGIN IMMEDIATE` transaction. `MobileAuthService.resume_exchange(...)`
  publishes and reads back the recovery record outside every SQLite transaction;
  each later `prior_recovery_fenced -> replacement_active -> complete` advance is
  its own atomic transition after validating the previous phase/readback. The
  old token is revoked before the replacement becomes active, and only
  `replacement_active|complete` may expose the deterministic credential.
- Token secret bytes are deterministic only for this exchange:
  `SHA256(code_verifier || "." || challenge_id || ".caddieinsight-token-v1")`.
  The server stores only its normal token hash plus the public selector. A
  persisted selector/request hash lets the same verifier, code, installation,
  and idempotency key recover the identical credential after a lost response;
  any conflict fails closed.
- `POST /api/v1/auth/sign-out` requires the current bearer plus a 128-bit
  `Idempotency-Key` and returns 202 while registered cleanup hooks drain or 204
  when the current selector is revoked. Its durable
  `prepared -> recovery_fenced -> extensions_closed -> token_revoked -> complete`
  journal permits an exact replay with the now-revoked bearer/key before normal
  authentication; any other use remains generic 401. Entering `prepared`
  immediately rejects ordinary use of that selector. The operation cannot pass
  `recovery_fenced` or return 204 until the independent monotonic recovery head
  has accepted and read back its `token_revoke` record.
- `RecoveryFenceLedger` is an append-only, HMAC-chained sidecar outside ordinary
  point-in-time backup bundles. After its mandatory cutover baseline, each
  `token_revoke` record carries a
  sequence, cutoff time, and versioned domain-separated HMACs of both selector
  and stored token verifier; no raw bearer material is persisted. Startup and
  restore require the newest independently protected head and apply matching
  revocations before worker start or request acceptance. Its closed record-kind
  union also reserves `push_environment_cutoff` for Task 7 and
  `review_access_revision` for Entitlements Task 5. A push cutoff record carries
  only deployment environment, versioned Expo-project-ID HMAC, activation/cutoff
  revision, last-provider-started/accepted/provider-may-accept-until/closed times,
  persisted cutoff skew/provider-safe-after, and closed state—never a raw
  token or project ID. Restore applies it before reconciling registrations/outbox
  rows or starting a push worker/request, removes or rejects state at an older
  activation revision, and cannot reopen the fence. The
  provider-scoped record carries the monotonic lane revision, exact supported
  version/build rows, window/purchase-test state, and versioned HMACs of admitted
  opaque review credential IDs; it carries no account, password/hash, user ID, or
  bearer. Restore applies the newest accepted revision after additive migration
  and before review auth/billing starts, so an older backup cannot resurrect a
  retired credential/build, expired purchase-test cycle, or closed/purged review
  generation.
- `RecoveryFenceStoreSettings` uses dedicated protected credentials and a
  dedicated object prefix whose identity may get/conditionally put only `HEAD`
  and immutable content-addressed `records/<sequence>-<sha256>.json` objects. It
  has no list/delete or backup-prefix read/write permissions. The web process
  never receives backup credentials. The existing S3-compatible adapter provides
  transport, but backup and recovery-fence roles/config are not interchangeable.
- Remote acceptance first conditionally puts and reads back the full canonical
  immutable record (sequence, previous record key/hash, kind fields, key IDs,
  record hash, and chain HMAC), then compare-and-swaps and reads back `HEAD` from
  the expected prior ETag/sequence/hash to that exact record key. A head hash
  without its complete retrievable record is never accepted.
- The genesis `cutover_baseline` record contains no PII: it binds a random
  recovery-lineage ID, minimum service-restorable backup creation time, baseline
  backup ID/manifest SHA-256, schema generation, and baseline DB checkpoint.
  Every later service-restorable backup carries that lineage/baseline ID. Older
  bundles remain immutable audit evidence and may be scratch-validated, but the
  service-restore command rejects them before migration/startup so legacy token,
  browser-history, account, and Shopify erasures cannot be resurrected.
- Baseline initialization is a durable
  `lineage_prepared -> backup_verified -> record_published -> head_published ->
  scratch_verified -> accepted` journal. `lineage_prepared` fixes the random
  lineage before backup creation; `backup_verified` binds exact backup ID,
  creation time, generation, and manifest hash before any remote I/O. Dependent
  routes remain held until immutable record/HEAD readbacks and an exact scratch
  restore advance the same journal to `accepted`.
- `prepare_restored_auth_state(connection)` runs only on the disposable working
  copy selected for service restore. In one transaction it increments every
  restored user's `auth_epoch`, replaces every password hash with the existing
  empty/unconfigured sentinel, and deletes all mobile tokens, native/browser
  auth challenges, email/reset codes, OAuth/browser sessions, selector-bound
  push state, and every auth-exchange/sign-out/device-revoke journal and replay
  receipt. It records a local restore-credential-reset marker so ordinary crash
  startup recovery cannot mistake canceled credential work for resumable work.
  Re-entry requires verified email; an account without a reachable verified
  email remains locked for explicit operator recovery. The retained immutable
  backup bundle is unchanged.
- `UserStore.converge_verified_identity_locked(...)` is shared by browser and
  native verification inside their existing transaction. It preserves
  `verify_email_signin`, claims pending Pro for the normalized email, creates the
  missing golfer profile under the same conditions as browser login, and marks
  eligible Shopify sync pending. It returns convergence facts without making a
  provider request.
- The email contains both the universal link and a human-readable eight-digit
  code grouped as `NNNN-NNNN`. Exchange strips ASCII spaces/hyphens before the
  constant-time hash check; the generic start response and fallback page never
  disclose the code.
- Native auth reuses the existing trusted-proxy `client_ip(request)` plus a new
  HMAC-keyed sibling to the existing `Throttle`; legacy `allow/record` behavior
  stays unchanged. `KeyedThrottle.consume_many(entries, now=None) ->
  MultiRateLimitDecision` accepts bounded `(domain, raw_key, limit, window_s)`
  entries, prunes and counts every active key version for all entries under one
  lock and `BEGIN IMMEDIATE`, then inserts every debit or none. The single-entry
  `consume(...)` delegates to it. Parallel callers cannot pass the same last slot
  and a denied email/IP pair never partially consumes the other key. No raw key
  reaches SQLite or logs. Auth start, known-challenge exchange failure, step-up,
  and telemetry use this all-or-none multi-key primitive. Shipped bounds are
  20 starts/IP/15 minutes, 5 starts/email/15
  minutes, 20 failed exchanges/IP/15 minutes, 10 failed exchanges/email/15
  minutes, and at most 20/3 live challenges per IP/email. Active-challenge IPs
  are stored only as versioned HMACs. Limit decisions depend on counters, never
  account existence; starts otherwise keep the same generic 202, while any
  exhausted window returns the same 429 `rate_limited` and bounded
  `Retry-After`. HMAC-keyed rate rows purge after 24 hours in bounded batches.
- `CredentialMutationGuard.admit(auth_context) -> CredentialMutationLease`
  opens a selector-scoped lease only after the bearer is active and captures its
  `auth_epoch`. Every unsafe bearer mutation calls
  `lease.validate_locked(user_store)` in its final SQLite transaction, which
  rechecks the selector is active and the epoch unchanged before any state or
  filesystem publication can become externally visible.
- Sign-out, device revoke, same-installation rotation, password reset, and account
  deletion close admission for the affected selector(s), cancel cooperative
  requests, and wait for active leases without holding a SQLite, upload,
  maintenance, or recovery-store lock. A bounded drain timeout returns durable
  202 and startup/replay resumes it; 204 is impossible while an earlier unsafe
  request could still commit. Fixed ordering is owner mutation fence → credential
  admission/lease → maintenance file lock → per-object lock → UserStore lock/
  SQLite transaction. Sign-out and self-device-revoke use an atomic
  `validate_and_close_caller` transition that validates the caller, closes its
  selector, and converts/releases the caller lease before draining other work.
  Other-device revoke keeps the initiator lease and drains only the target
  selector. Deletion converts its authenticated lease into the owner fence before
  closing all owner selectors. These special transitions cannot wait on their
  own lease.

- [ ] Add failing persistence tests for a 10-minute expiry, 60-second resend
  throttle, five failed attempts, single use, wrong verifier, wrong code, and
  two installations starting against the same email. Add lost-response replay,
  conflicting idempotency, same-installation rotation, sixth-installation
  refusal, and expired replay cases. For rotation, add remote outage, 202 exact
  replay, and crashes before/after each journal phase; the old selector is
  rejected from `prepared`, the replacement is rejected until active, and 201
  occurs only after token-revoke publish/readback. Restore a pre-rotation backup
  with the newest head and prove the old bearer stays dead. Do not reuse the
  browser-session-keyed `email_codes` row.
- [ ] Add start/exchange abuse tests at every exact boundary for one IP/many
  emails, many IPs/one email, concurrent starts, live-challenge caps, resend
  suppression, unknown challenge, and valid-challenge wrong verifier/code.
  Failed valid exchanges debit both IP and normalized-email windows; an unknown
  challenge debits IP only. Assert account-present/absent requests have identical
  202/429 body/header/timing class, no email is sent beyond the cap, raw IP is
  absent from DB/logs, and trusted proxy parsing matches the browser routes.
- [ ] Add atomic keyed-throttle tests with parallel consumers at limit−1, exact
  limit, and limit+1; only the allowed count commits. Cover two-key all-or-none
  admission/denial, no partial debit on either limit or injected insert failure,
  old/current HMAC key lookup, domain mismatch, 24-hour bounded purge/restart,
  precise retry-after bounds, and unchanged legacy `Throttle` tests.
- [ ] Add credential-mutation barrier tests for a practice write, upload PATCH/
  completion, telemetry event, push registration, and privacy/export start held
  immediately before final commit while sign-out, self/device revoke, token
  rotation, password reset, or deletion closes admission. Release each barrier
  and prove the stale selector/epoch commits nothing; revocation stays 202 until
  drain, then reaches 204 and survives restart. Exercise the fixed lock order and
  prove concurrent deletion/revoke/upload cannot deadlock.
- [ ] Add explicit self-sign-out/self-revoke tests proving
  `validate_and_close_caller` does not self-wait, plus other-device tests proving
  the initiator remains admitted while only the target drains. Race two caller
  mutations at the close boundary and assert no commit after terminal revoke.
- [ ] Add failing review-auth tests for visible reusable access without email,
  generic start/401 behavior, wrong password/account/provider/build/window or
  unsupported standing-Google build,
  exact lost-response replay, rate limits, closure/expiry/revoke/reset/deletion,
  and ordinary-token null scope. Use two distinct synthetic users and prove an
  Apple credential cannot issue or use a Google-scoped bearer (and vice versa),
  while no real user/password can enter the path. Prove the synthetic user and
  review credential are rejected by browser password login, passwordless/email-
  code start/exchange, signup/convergence, password reset, and PWA cookie auth;
  only `/api/v1/auth/review/*` can verify the separate hash. Scan DB/backups/logs
  for raw account names, passwords, PKCE verifiers, idempotency keys, and bearer secrets.
- [ ] Test seven-plus sequential Google review installations across scoped-token
  expiry. Each expired selector is excluded/revoked before the six-device check,
  bounded startup/periodic cleanup resumes after crash, active leases drain, and
  the standing credential continues to issue one current-build bearer while every
  expired bearer remains generic 401 after restart/restore.
- [ ] Add one generated native app-identity contract and tests for every exact
  header on iOS/Android. Reject missing, duplicate/comma-joined, malformed,
  caller-overridden, wrong environment/platform/bundle/package, marketing-version,
  or native-build values across review start/exchange, billing config/intent,
  Apple review challenge, and Google Integrity. Prove the shared parsed tuple is
  the only downstream input and ordinary browser routes do not need these headers.
- [ ] Test `mobile_deployment_environment` default/closed-enum/override parsing,
  staging and production explicit readback, caller/header/host/CLI non-authority,
  and startup rejection when a staging-only lane or production review/evidence
  setting conflicts. `/healthz` exposes only the configured closed value.
- [ ] Add sign-out tests for current-selector-only revoke, required idempotency,
  extension hook success/pending/failure, crash at every journal phase, lost 204
  replay with the revoked bearer, conflicting key/request, and no effect on
  another device. Store only versioned token/idempotency HMACs and request hash.
  Add a pre-sign-out backup → sign-out → restore case proving the old bearer and
  any selector-bound push registration stay revoked.
- [ ] Resume every nonterminal sign-out journal during app startup before worker
  start or request acceptance, regardless of auth/push/privacy feature flags.
  Missing referenced HMAC key fails startup closed. Reconstruct the app after a
  crash at every phase and prove hooks finish, the selector is revoked, and exact
  replay returns 204 even when the client never retries.
- [ ] Resume every nonterminal auth-exchange journal in the same pre-request
  recovery pass. Never hold a SQLite transaction/connection lock across remote
  I/O. A recovery-fence outage keeps the exchange at 202 with both selectors
  safely unavailable; after readback, one transaction activates exactly one
  replacement and exact retry/lost-201 recovery returns its deterministic raw
  token. A conflicting request remains 409 and learns no journal detail.
- [ ] Implement `RecoveryFenceLedger.append_and_publish` with local fsync,
  immutable-record put/readback followed by conditional off-volume `HEAD`
  advancement/readback. Serialize every web/CLI writer with one process mutex
  plus fixed-byte cross-process `.recovery-fence.lock`; while held, fetch/validate
  `HEAD`, allocate exactly N+1, fsync, publish, and read back before release. A
  publish outage keeps sign-out at 202 and the selector locally fenced. Restore
  fetches `HEAD` and follows explicit previous-record keys to genesis without
  list permission, validating every canonical body/hash/HMAC before auth lookup,
  then removes matching token/sign-out/push rows. Stale, missing-record,
  divergent-CAS, invalid-chain, or missing-key input fails startup closed.
- [ ] Add two-process/thread parallel appends across distinct revoke/reset/delete
  kinds, CAS conflict, orphan immutable record, crash at every put/readback/head
  step, and immediate local-volume-loss tests. Exactly one sequence chain wins;
  the loser refetches/rebases its same logical event without overwriting or
  duplicating it, and every operation that returned 204 remains fully
  reconstructible from `HEAD` plus immutable record bodies alone.
- [ ] Add the already-vetted `boto3>=1.37.32,<2` transport to the production
  `web` extra and keep the `backup` extra for operator commands. Add a container
  import plus fake-S3 conditional put/readback smoke. Document the separate
  least-privilege recovery-fence IAM policy, prefix, enable/readback/disable,
  credential rotation, and denial of backup list/read/write/delete. Never fall
  back to full backup credentials.
- [ ] Add an approval-gated `recovery-fence-ledger initialize-baseline` command.
  Inventory every pre-cutover erasure path, deploy schema with dependent routes
  held, create/verify a fresh immutable backup of current live truth, generate a
  recovery-lineage ID, conditionally publish/read back the full baseline record
  and head, then stamp only subsequent manifests with that baseline/lineage.
  Test that a pre-cutover generation-0/older bundle can still pass read-only
  evidence verification but `restore-to-service` rejects it; the exact baseline
  and every later matching-lineage backup may proceed. Retention/quarantine of
  older bundles remains an explicit operator action, never an automatic delete.
- [ ] Crash and lose responses after every baseline phase. Exact retry must reuse
  the same lineage/backup/hash/record, resume without a second genesis, and reach
  accepted only after scratch proof. A conflicting remote genesis, backup hash,
  local journal request, or head fails closed and keeps token issue/revoke,
  browser reset, Shopify privacy erasure, and native privacy/auth routes held.
- [ ] Define the startup I/O gate exactly. A pristine/generation-0 database with
  no recovery-fence checkpoint/journal and every dependent flag off starts with
  zero provider calls. Any recovery-fence row/checkpoint, nonterminal revocation,
  enabled native-auth/device/privacy feature, existing
  `web.history_reset_enabled` browser surface, or enabled Shopify privacy
  webhooks requires the verified cutover baseline, dedicated credentials,
  immutable-record put/readback, head CAS/readback, and current-chain validation;
  absence/outage fails startup closed before workers or requests.
- [ ] Add failing API tests for generic 202 responses, absent SMTP behavior,
  Shopify-stub convergence via `verify_email_signin`, raw-token one-time return,
  and `Cache-Control: no-store`.
- [ ] Add parity tests for an existing verified account, first verification,
  pending Pro grant, missing/existing profile, Shopify cohort in/out, and lost-
  response replay. Extract the browser login’s verified-identity convergence
  into the lock-held UserStore helper and call it from native exchange before
  token commit. After commit, invoke the existing idempotent
  `queue_shopify_sync` for first success or replay while sync remains pending;
  never call Shopify synchronously from exchange.
- [ ] Add failing email tests for a random eight-digit code, grouped plaintext/
  HTML rendering, space/hyphen normalization on exchange, manual-code completion
  on the initiating device, and absence of code/verifier from subject, preview
  preheader, generic API response, fallback HTML, logs, and third-party requests.
- [ ] Add a failing privacy test that sends secret-like values in query strings
  and request JSON, then proves the access log contains neither email code,
  verifier, challenge, nor raw bearer token.
- [ ] Dump auth tables and a backup manifest after start/exchange/lost-201 replay,
  then enumerate every eight-digit code and ordinary SHA-256 construction used by
  legacy code. No guess may match a stored replay/request value, and no plain
  verifier-derived fingerprint may appear. Only configured versioned HMAC
  candidates can bind an exact replay; cover old/current key rotation and missing-
  key startup failure.
- [ ] Add an additive `mobile_auth_challenges` table with a random public ID,
  normalized email, `(code_hmac_key_id, code_hmac)` under the dedicated
  `email-code-verifier` domain, PKCE `S256` challenge, device label, created/
  expiry/consumed timestamps, attempts, installation-ID HMAC plus HMAC key ID,
  start-IP HMAC plus key ID, issued selector, versioned exchange-idempotency
  HMAC, canonical request hash, and a purpose fixed to `signin`. Index active
  rows by `(start_ip_hmac_key_id, start_ip_hmac, expires_at, consumed_at)` and
  count candidates across every active HMAC key version for the live-IP cap.
  Create strict required-column checks following the mobile-token migration
  pattern; never add a plaintext code/verifier column and never reuse the legacy
  plain-SHA-256 `_hash_code`. Verification computes candidates for all active key
  versions and compares in constant time; generation-1/rotation/offline-dictionary
  tests require both code HMAC columns and their referenced keys.
- [ ] Add a separate additive `mobile_review_auth_challenges` table with provider,
  exact app identity, nullable matched synthetic user key, expiry/attempts, PKCE
  challenge, and only versioned HMACs of account, start IP, password proof, and
  idempotency/replay inputs. It stores no raw account/password and shares neither
  email-code rows nor domains. Add nullable review-scope columns to
  `mobile_api_tokens`; existing rows remain valid with null values.
- [ ] Add the auth-exchange journal and inactive/active token state to the same
  additive schema. Persist only versioned idempotency/selector/token-verifier
  HMACs, bounded phase/timestamps, and deterministic credential recovery inputs
  already derivable from the challenge—not a raw bearer. Its canonical replay
  bytes contain only challenge/purpose identifiers plus versioned domain-separated
  HMACs of the supplied email code and PKCE verifier, the existing installation/
  idempotency HMACs, and non-secret fields; the stored request hash is over those
  sanitized bytes, never raw/ordinary SHA-256 of email, code, verifier, or bearer.
  Purge a completed journal only after its replay window closes.
- [ ] Add nullable `installation_key` and `installation_key_version` columns to
  `mobile_api_tokens`. Existing/browser-issued tokens remain null and valid;
  only native challenge exchange requires a non-null installation binding.
- [ ] Refactor `verify_email_signin` and `issue_mobile_api_token` into private
  lock-held helpers so the exchange uses one SQLite transaction. Preserve their
  public methods and current browser behavior.
- [ ] Generate one installation UUID on-device and store it only in SecureStore;
  persist its keyed HMAC in SQLite. A successful reauthentication revokes the
  prior selector for that `(user, installation)` only after the replacement is
  inserted. A sixth distinct active installation returns 409 `device_limit`
  without consuming the challenge; the golfer must revoke a listed device.
- [ ] Add protected `MOBILE_STATE_HMAC_KEYRING` as versioned JSON with one current
  key ID and one or more 32-byte base64 keys. A shared `VersionedHMAC` helper
  returns `(key_id, digest)` and queries by computing candidates for every active
  key. Define distinct fixed `caddieinsight.mobile.v1/<domain>\0` prefixes for
  installation ID, auth-start client IP, auth-start normalized-email rate key,
  auth-exchange client IP, auth-exchange normalized-email rate key, email-code
  verifier, auth-exchange code proof, auth-exchange PKCE-verifier proof, review-
  auth account, review-auth client IP, review-auth password proof, review-auth
  PKCE-verifier proof, review-auth idempotency, exchange
  idempotency, sign-out idempotency, device-revoke idempotency, practice
  idempotency, upload idempotency, upload-abort idempotency, analysis-retry idempotency, analysis-source-
  discard idempotency, export idempotency, history-reset
  idempotency, account-delete idempotency, event idempotency, recovery selector,
  recovery token verifier, erasure stable-user ID, erasure normalized email, and
  Shopify-erasure shop domain, Shopify-erasure customer ID, Shopify-erasure
  normalized email, and recovery-chain link. Tasks 6–8 extend the closed domain
  enum with separately
  named step-up code proof, step-up PKCE-verifier proof, review-step-up password
  proof, review-step-up PKCE-verifier proof, review-step-up idempotency, review-
  step-up start selector/account/client-IP, review-step-up exchange account/client-
  IP, push Expo-project ID, push-cutover operation ID, and telemetry rate keys;
  do not expose a generic `mutation`,
  `recovery`, `ip`, `email`, or `selector` domain.
  Add same-input cross-domain non-equality tests plus current/old-key lookup,
  rotation, restart, and missing-key tests for every persisted domain. Startup
  requires the keyring to cover every key ID referenced by any live table,
  journal, receipt, trigger dependency, local/remote recovery checkpoint, or the
  backup being restored, regardless of feature flags. Missing coverage fails
  before requests/workers even with all flags off. New rows use the current key;
  old keys remain configured until usage counts reach zero. Backups record only
  referenced key IDs, and scratch restore requires the matching protected
  keyring.
- [ ] Add a versioned `mobile_state` backup-manifest extension. Absence means
  pre-mobile generation 0; generations 1–7 add, respectively, native-auth,
  practice-detail, upload, privacy/deletion, push, telemetry, and production-
  review access tables/columns.
  `MOBILE_STATE_GENERATIONS` defines each cumulative required table/column set.
  A current backup must declare the highest supported generation; any partial
  generation fails validation. Restore validation keeps the extracted bundle
  read-only and byte-identical; after validation it makes a second disposable
  working copy, runs additive migrations there with every feature flag off, and
  never mutates the retained restore artifact/evidence. Test original hashes
  before/after, legacy generation 0, each complete generation, current, missing
  table/column, unknown future generation, and migrated scratch working copy.
- [ ] Apply `prepare_restored_auth_state` after immutable bundle validation and
  additive migration but before recovery-head reconciliation or app startup.
  Test pre-password-reset and pre-auth-epoch backup → reset/logout → restore:
  the old password, signed cookie, bearer, email code, OAuth/browser session,
  and push binding all fail; email re-verification can establish one fresh
  credential without changing account entitlement/history data.
- [ ] Back up every auth-exchange phase plus completed replay metadata, prepare
  each disposable copy for service restore, and prove all replacement/old tokens,
  challenges, exchange/sign-out/revoke journals, and receipts are canceled/
  purged. Service restore never resumes or exposes a deterministic pre-restore
  credential; only ordinary crash startup resumes nonterminal exchange work.
- [ ] Land generation 1 with this task: require `mobile_auth_challenges`, mobile
  and review-auth challenges, auth-exchange and sign-out journal/receipt tables,
  plus native installation-HMAC/key-ID, inactive/active-state, and nullable
  review-scope columns on `mobile_api_tokens`, plus
  recovery-fence checkpoints, baseline-init journal/accepted marker, and
  `mobile_rate_limit_events` with domain/key-ID/digest/time columns plus lookup/
  purge indexes. Record exact required columns,
  schema digests, row counts, phase/domain counts, and referenced HMAC key IDs;
  reject any partial table/column/index set. For ordinary crash-startup coverage,
  reconstruct one `prepared` exchange and prove both selectors remain unavailable
  until recovery completes the published rotation or safely remains pending;
  point-in-time service restore always purges that work as specified above.
- [ ] Define HMAC key usage as the union of every live version column and the key
  IDs in every retained backup manifest. An old key cannot leave protected
  configuration until live usage is zero and all backups naming it expire under
  retention or are deleted after a newer verified backup. Because an HMAC cannot
  be re-keyed without raw input, never claim an offline backup was re-keyed.
- [ ] Purge expired unused challenges and consumed/replay metadata 24 hours
  after expiry. Include start-IP key IDs in usage/backup audits; test current/old
  key live-cap counting and generation-1 rejection when either IP column/index is
  absent. The purge never removes an active device credential.
- [ ] Send an HTTPS universal link using `PUBLIC_BASE_URL` and the route
  `/app/auth/callback?challenge_id=...&code=...`; the public fallback page must
  contain only “Open CaddieInsight,” “enter the code from this email on the
  device where sign-in started,” and a code-expired recovery action. The email
  itself renders the grouped code beside the link. Mark the entire route/query
  as secret in `access_log.py` redaction. Return
  `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, a self-only CSP, and
  no third-party script/image/analytics request.
- [ ] Add `web.mobile_native_auth_enabled: false` to `DEFAULTS` and shipped
  `config.yaml`. When false, both endpoints return 404 and no table row/email is
  created. Enabling it is invalid until dedicated recovery-fence credentials,
  conditional write/readback, current-head verification, and a scratch recovery
  drill have passed; otherwise startup fails closed. Add the six bounded auth-
  abuse settings above to defaults/shipped config/environment docs with strict
  positive/minimum/maximum validation and readback tests. Tests enable auth
  explicitly.
- [ ] Keep review auth unavailable unless the injected `ReviewAuthAdmission`
  reports an active lane. Add independent bounded review-start/exchange IP and
  account rate settings to defaults/config/docs; no raw provider reviewer
  identity or credential appears in config or health output.
- [ ] Run `python -m pytest tests/test_mobile_native_auth.py tests/test_mobile_review_auth.py tests/test_passwordless.py tests/test_mobile_api_tokens.py tests/test_access_log_privacy.py tests/test_backups.py -q`; expect all pass.
- [ ] Regenerate `docs/api/openapi-v1.json`, rerun its drift test, and commit:
  `git commit -m "feat: add challenge-bound native sign-in"` with only the files
  from this task staged.

## Task 4: Publish server-owned capabilities and structured coaching resources

**Files:**

- Modify: `swinglab/api/contracts.py`
- Modify: `swinglab/api/mobile_routes.py`
- Modify: `swinglab/web/mobile_schema.py`
- Create: `swinglab/web/mobile_resources.py`
- Modify: `swinglab/web/app.py:833-850,1168-1208,4076-4350`
- Modify: `swinglab/web/users.py:354-422,622-810`
- Modify: `swinglab/web/credential_mutations.py`
- Modify: `swinglab/web/mobile_schema.py`
- Modify: `swinglab/backups/core.py`
- Modify: `swinglab/config.py:144-242`
- Modify: `config.yaml:147-293`
- Create: `tests/test_mobile_capabilities.py`
- Create: `tests/test_mobile_profile_api.py`
- Create: `tests/test_mobile_progress_api.py`
- Create: `tests/test_mobile_sessions_api.py`
- Create: `tests/test_mobile_brief_api.py`
- Create: `tests/test_mobile_practice_api.py`
- Modify: `tests/test_backups.py`
- Modify: `tests/test_shopify_privacy.py`
- Modify: `tests/test_foundation_contracts.py`

**Interfaces:**

- `ProfileUpdateRequest` is the generated mobile write contract with
  `extra="forbid"`: normalized `display_name` (1–50 characters after NFKC/
  whitespace/control validation), closed existing literals for
  `experience_mode`, `handicap_range`, `primary_goal`, `practice_minutes:
  10|20|45`, `sessions_per_week:1|2|3`, `handedness:"right"|"left"`,
  `camera_angle:"face-on"|"dtl"`, and approved `preferred_club`, plus strict
  booleans `reduced_motion` and `marketing_email_opt_in` and nonnegative
  `expected_history_epoch`. Marketing opt-in may be false and never contributes
  to `is_complete`; it is not inferred from profile completion or preselected.
  Display name, primary goal, and preferred club are all required so the returned
  `is_complete` exactly preserves the existing browser/server contract.
- Additive `PUT /api/v1/mobile/profile` requires bearer authentication and that
  typed body, returns typed `ProfileResponse` through the mobile `APIError`
  handler, sets no-store headers, and calls the same normalized profile service
  as the browser. It never changes or calls the manually parsed legacy
  `PUT /api/v1/profile`, whose cookie/bearer validation order, bodies, and errors
  remain byte-for-byte compatible. The mobile mutation enters
  `CredentialMutationGuard`; its one `BEGIN IMMEDIATE` write transaction
  rechecks final selector activity, `auth_epoch`, account deletion state, and
  `expected_history_epoch` immediately before upsert. Revocation/deletion is the
  generic 401/404 contract and epoch conflict is typed 409; a losing race cannot
  recreate a deleted/reset profile.
- `mobile_profile_writes_enabled` is a validated default-off flag dedicated to
  the additive mobile profile mutation. When false, the route returns the normal
  mobile 404 with zero authentication-independent disclosure or write; it does
  not affect legacy browser profile reads/writes. Capabilities expose only the
  resolved boolean, and rollout enables it independently after mobile reads.
- `GET /api/v1/capabilities` requires existing account auth and returns
  `upload.max_bytes`, `upload.max_video_seconds`, `upload.chunk_bytes`,
  `upload.active_limit`, allowed suffixes, canonical hands/angles/clubs,
  analysis states, effective quota/remaining count, feature flags, Shopify
  physical-store URL, and capability booleans. It never returns secrets.
- `GET /api/v1/progress` returns generated `ProgressResponse` with owned
  `ComparableContextGroupResponse` items and established outcome/decision labels;
  it never exposes raw filesystem paths. Each group’s optional current
  `ProofCycleTargetResponse` is the sole matched-capture launch contract and
  contains exactly `{baseline_session_id, target_fingerprint, drill_id, club,
  hand, angle}`. `BriefResponse.proof_cycle_target` reuses that same closed model.
  The IDs/fingerprint are current-user/current-`history_epoch` owned values and
  the club/hand/angle are canonical baseline context, not client-derived labels.
  A missing, replaced, stale, cross-owner, or reset target is null/404 as
  appropriate and can never yield launch metadata; upload create/completion still
  revalidate the exact target.
- `GET /api/v1/mobile/sessions` and
  `GET /api/v1/mobile/sessions/{session_id}` return only
  `MobileSessionResponse` through the safe serializer. Existing
  `/api/v1/sessions` routes and their values remain byte-for-byte compatible and
  are never called by the native client.
- `GET /api/v1/mobile/sessions/{session_id}/brief` returns only the structured
  `BriefResponse` from the safe serializer after bearer ownership/current-
  `history_epoch` validation. It contains bounded priority, evidence, confidence,
  hypothesis, prescribed drill, measurement boundary, and exact Proof Cycle
  target fields; it never includes report HTML, raw logs/errors/tracebacks,
  filesystem paths, commands, provider data, or arbitrary artifact URLs. Missing
  and cross-owner sessions are indistinguishable 404; queued/running/noncoaching
  states return a typed safe `brief_not_ready`/re-film response without partial
  report data. Version 1 exposes no native downloadable-report/artifact endpoint;
  legacy `/api/v1/sessions/{session_id}/brief` remains browser-compatible and is
  never called by the native client.
- `GET /api/v1/mobile/today` includes server-owned
  `cohort_day_since_first_analysis`
  (`null` before activation, otherwise a bounded nonnegative integer based on
  server UTC) and only safe embedded `MobileSessionResponse` data. The client may
  use days 8–14 to emit the closed week-two diagnostic but never derives cohort
  eligibility from device time. Existing `/api/v1/today` remains byte-for-byte
  compatible and is never called by the native client.
- `POST /api/v1/practice-checkins` continues accepting the exact legacy
  `{session_id}` body. A new `POST /api/v1/practice-evidence` accepts
  `{baseline_session_id, target_fingerprint, drill_id,
  minutes:10|20|45, outcome:"completed"|"still_working", reps:1..300,
  feel:"easier"|"same"|"harder"|null,
  relative_strike:"better"|"same"|"worse"|"unknown"|null,
  start_line:"left"|"target"|"right"|"unknown"|null,
  miss_pattern:"left"|"right"|"thin"|"fat"|"heel"|"toe"|"mixed"|"none"|"unknown"|null,
  expected_history_epoch}` plus `Idempotency-Key` and returns the same
  `PracticeEvidenceReceipt` on replay.
- `UserStore.record_mobile_practice_evidence(user_id, request,
  idempotency_key, now=None) -> PracticeEvidenceReceipt` verifies the current
  owned Proof Cycle target and writes the established
  `proof_cycle_practice_evidence` row plus mobile detail/receipt atomically.
- Every bearer-authenticated unsafe route in this task enters the Task 3
  `CredentialMutationGuard`; practice and legacy token issue/revoke transactions
  revalidate selector activity and `auth_epoch` at their final write boundary.
- `GET /api/v1/devices` and `DELETE /api/v1/devices/{selector}` require bearer
  auth; DELETE also requires a 128-bit `Idempotency-Key` and uses the shared
  credential-revocation journal. It returns 202 until the target token's
  `token_revoke` recovery record is independently published/read back and every
  selector-bound extension drains, then 204. A self-revocation exact replay is
  recognized before ordinary bearer auth. Preserve the current browser-only
  `/api/v1/mobile-tokens` request shapes, authentication/validation ordering,
  201 issuance body, 200 revoke body, and 404 cross-owner behavior, but route
  every token revocation they perform through the same recovery-fenced service
  rather than directly deleting a row. These two legacy mutation routes gain one
  documented operational result: 503 with the existing legacy `{"detail": ...}`
  shape when recovery-fence readiness/publish is unavailable; they never return
  their old success response for a local-only issue/revoke.

- [ ] Add failing capabilities tests for free/Pro quota, the shipped 500 MiB and
  300-second values, configured flags, canonical clubs, no-store headers, and
  absence of environment values.
- [ ] Add failing mobile-profile OpenAPI and route tests for the exact generated
  request body/literal bounds, extra/missing/control/oversize fields, false and
  true independent marketing consent, missing preferred club and exact
  `is_complete` parity, normalization, no-store response, and
  strict rejection of cookie-only auth. Cover bearer success/replay, wrong/
  revoked selector, final-write revocation, history reset, account-deletion race,
  and cross-account context; no losing request may write or resurrect a profile.
  Keep existing legacy cookie/bearer/origin/profile response and validation-order
  fixtures byte-for-byte unchanged, regenerate OpenAPI, and prove the client
  schema exposes `PUT /api/v1/mobile/profile` with `ProfileUpdateRequest`.
  Cover default-off 404/zero writes and independent capability/flag readback.
- [ ] Add failing Progress ownership tests for empty, incomparable, matched,
  capture-only, and cross-account histories. Assert the generated Progress/Brief
  target model carries the exact triple plus canonical baseline club/hand/angle,
  rejects extras, becomes null/404 after target replacement/history reset/
  deletion, and never crosses accounts. Reuse current Proof Cycle and Caddie
  Brief functions; do not recalculate swing truth in the API module.
- [ ] Add mobile-session tests for every state/progress/failure/retry field,
  ownership 404, and injected legacy logs/errors containing paths, commands,
  stderr, and tracebacks. None may enter native JSON/OpenAPI examples, while the
  existing legacy session route contract stays unchanged.
- [ ] Add mobile Brief contract/ownership tests for coaching-ready, queued,
  running, capture-only/re-film, missing, reset epoch, deleted account, and cross-
  account IDs. Seed the legacy report, log, stderr, traceback, command, path, and
  HTML with secret-shaped values; prove the native JSON/OpenAPI example contains
  none of them, `extra="forbid"` catches drift, and the legacy Brief response is
  unchanged.
- [ ] Add Today cohort-day boundary tests before activation and at server UTC days
  0, 7, 8, 14, and 15, including clock skew and account deletion/history reset.
  Seed its latest legacy job with secret-like log/error/traceback/path data and
  prove none enters `/api/v1/mobile/today`, while legacy Today stays unchanged.
- [ ] Add failing practice-evidence tests for field bounds, context ownership,
  ineligible sessions, identical idempotent replay, conflicting-key 409, and
  `history_epoch` conflict. Keep the old simple check-in tests passing.
- [ ] Add `mobile_practice_evidence_details` keyed by the same `(user_id,
  baseline_session_id, target_fingerprint, completed_day)` as the established
  evidence row, with bounded detail columns, opaque receipt ID,
  `idempotency_hmac` plus `idempotency_hmac_key_id`, and canonical request
  SHA-256. Add it to history reset, account deletion,
  privacy export, backup counts, orphan checks, and scratch-restore tests.
- [ ] Bump the cumulative mobile backup extension to generation 2 for
  `mobile_practice_evidence_details`; test restore from generations 0/1 and
  rejection of a generation-2 manifest or database missing any required column.
- [ ] Implement pure serializer/service functions in `mobile_resources.py` and
  keep the router limited to authentication, validation, status translation,
  and response headers.
- [ ] Add default-off flags and explicit numeric settings:
  `mobile_resources_enabled`, `mobile_profile_writes_enabled`,
  `mobile_practice_writes_enabled`,
  `mobile_device_management_enabled`, `mobile_resumable_upload_enabled`,
  `mobile_privacy_enabled`, `mobile_events_enabled`, `mobile_push_enabled`,
  `mobile_native_billing_enabled`, `mobile_upload_chunk_mb: 5`,
  `mobile_active_uploads_per_user: 2`, and
  `mobile_upload_ttl_seconds: 86400`. Validate positive bounds at startup.
- [ ] Return 404 with zero side effects for every route whose specific flag is
  off. `mobile_resources_enabled` gates reads only;
  `mobile_profile_writes_enabled` gates the additive mobile profile PUT,
  `mobile_practice_writes_enabled` gates new practice evidence, and
  `mobile_device_management_enabled` gates device list/revoke. Upload, privacy,
  events, push, and billing use their own flags. Auth has its Task 3 gate, so
  enabling resources alone enables no mutation.
- [ ] Reject `mobile_device_management_enabled: true` at startup unless the
  Task 3 recovery-fence credentials, conditional-write/readback probe, current
  head, and scratch restore evidence are valid. An outage after activation keeps
  a revoke at durable 202/fenced and alerts operators; it never reports 204 or
  silently falls back to a local-only delete.
- [ ] Add bearer device list/revoke handlers based on `MobileAuthContext`.
  Route sign-out, device revoke, and same-installation token rotation through one
  recovery-fenced revocation service. Revoking the current selector succeeds,
  then every later request with that token returns the same generic 401. Test an
  old-backup restore after both self- and other-device revoke; the old bearer and
  selector-bound push state must not reappear, while an unrelated device stays
  active.
- [ ] Add legacy `/api/v1/mobile-tokens` parity tests for unchanged success/error
  bodies/order plus the explicit 503 operational extension. On a valid issuance
  request, require cached startup recovery-fence readiness before inserting a
  token. On revoke, durably fence the owned selector first; publish/read back
  outside SQLite, return the exact old 200 body only on completion, and return
  503 on outage while the selector remains unusable. A retry resumes by the
  owned selector journal and eventually returns 200. Restore a snapshot taken
  before a browser-initiated revoke and prove the token and any selector-bound
  push state remain revoked after recovery-head reconciliation.
- [ ] Add feature-off tests for three exact states: pristine/no checkpoint starts
  with zero provider I/O and valid legacy token issuance returns 503 with no row;
  any existing mobile token/journal/checkpoint requires startup head readback or
  fails closed; configured/read-back-ready state preserves the legacy 201/200
  responses. Inventory existing token rows before deployment and initialize/
  verify the independent head before starting the upgraded app.
- [ ] Run `python -m pytest tests/test_mobile_capabilities.py tests/test_mobile_profile_api.py tests/test_mobile_sessions_api.py tests/test_mobile_progress_api.py tests/test_mobile_practice_api.py tests/test_first_sale_platform.py tests/test_proof_cycle.py tests/test_mobile_api_tokens.py tests/test_backups.py -q`; expect all pass.
- [ ] Regenerate the OpenAPI snapshot and commit:
  `git commit -m "feat: add native coaching resources"`.

## Task 5: Implement durable resumable uploads with atomic job completion

**Files:**

- Create: `swinglab/web/resumable_uploads.py`
- Create: `swinglab/web/session_maintenance_lock.py`
- Create: `swinglab/web/storage_capacity.py`
- Modify: `swinglab/web/credential_mutations.py`
- Modify: `swinglab/ffmpeg.py`
- Modify: `swinglab/web/jobs.py:82-138,218-288,649-714,735-790,1579-1665`
- Modify: `swinglab/proof_cycle_practice.py`
- Modify: `swinglab/api/contracts.py`
- Modify: `swinglab/api/mobile_routes.py`
- Modify: `swinglab/web/app.py:3388-3603,4076-4350`
- Modify: `swinglab/web/users.py:5866-5965`
- Modify: `swinglab/backups/core.py`
- Modify: `swinglab/config.py`
- Modify: `swinglab/cli.py`
- Modify: `config.yaml`
- Modify: `docs/environment.md`
- Modify: `docs/deployment.md`
- Modify: `docs/operations/backup-recovery.md`
- Create: `tests/test_resumable_uploads.py`
- Create: `tests/test_resumable_upload_recovery.py`
- Create: `tests/test_session_maintenance_lock.py`
- Create: `tests/test_storage_capacity.py`
- Create: `tests/test_mobile_analysis_retry.py`
- Create: `tests/test_ffmpeg_failure_kinds.py`
- Modify: `tests/test_credential_mutation_guard.py`
- Modify: `tests/test_backups.py`
- Modify: `tests/test_foundation_contracts.py`

**Interfaces:**

- `POST /api/v1/uploads` consumes `UploadCreateRequest` plus
  `Idempotency-Key` and returns one owned reservation. `file_sha256` is exactly
  64 lowercase hexadecimal characters; chunk checksums remain base64 in the
  `Upload-Checksum` header. The request carries canonical capture `club`, `hand`,
  and `angle`. `comparison` is `null` for an ordinary capture or a
  discriminated union carrying `mode:"matched"|"new_context"` plus the current
  owned `baseline_session_id`, `target_fingerprint`, and `drill_id`.
- `GET /api/v1/uploads/{upload_id}` returns durable acknowledged offset/status.
- `PATCH /api/v1/uploads/{upload_id}` consumes raw bytes with exact
  `Upload-Offset` and `Upload-Checksum: sha256 <base64>` headers.
- `POST /api/v1/uploads/{upload_id}/complete` returns
  `{resource_version: 1, job: MobileSessionResponse, replayed: bool}`.
- `DELETE /api/v1/uploads/{upload_id}` requires bearer auth plus a distinct
  128-bit `Idempotency-Key`. It first journals/locks the owned reservation as
  `aborting`, then removes+directory-fsyncs the part, releases capacity once, and
  completes a seven-day tombstone/receipt before returning 204. Exact same-key
  replay returns 204 even after the upload row/file is gone; a different key for
  that tombstone or reuse of the key for another request returns 409. A client
  must replay a lost DELETE rather than interpret a bare GET 404 as confirmation.
- `POST /api/v1/mobile/sessions/{session_id}/retry` requires bearer auth,
  `Idempotency-Key`, and `expected_retry_attempt`; it requeues one owned retryable
  failure from its retained,
  digest-verified source without creating a second job or consuming another
  allowance. The server atomically assigns that attempt and returns
  `retry_attempt` plus an opaque `retry_receipt_id`; exact replay of that attempt’s
  key returns the same `MobileSessionResponse`. A later attempt is admitted only after
  the prior attempt reaches an acknowledged retryable terminal result and the
  server exposes the next exact attempt number. Permanent,
  exhausted, expired, wrong-owner, stale-epoch, or missing-source attempts return
  their bounded structured error without state change.
- `DELETE /api/v1/mobile/sessions/{session_id}/retry-source` requires bearer auth plus
  `Idempotency-Key`, permanently closes a retryable-failed job’s retry window,
  deletes the retained server source, and releases its capacity allocation exactly
  once before returning/replaying 204. Cross-owner is 404; queued/processing/
  already-successful state cannot be discarded through this route.
- Retry-source discard is a durable per-job journal:
  `prepared -> source_removed -> allocation_released -> complete`. `prepared`
  atomically closes retry admission and binds owner/job/source digest plus the
  versioned discard-idempotency HMAC; only then, under maintenance plus the shared
  job lock, unlink/fsync the source directory and advance `source_removed`.
  Release capacity exactly once in the next transaction, then complete. Startup
  repairs every nonterminal journal before capacity admission, job recovery, or
  requests; a `prepared` journal with its expected source already absent resumes
  as intentional removal, while absence without such a journal remains corruption.
  Retry, analysis worker transition, retention, deletion, and discard all use the
  same per-job lock and fixed maintenance→job→store/transaction ordering.
- Produces `JobManager.complete_mobile_upload(upload_id, *, user_id,
  expected_history_epoch, monthly_limit) -> tuple[Job, bool]`; callers observe
  only a queued job or a retryable failure, never the internal prepared row.
- A `matched` comparison must equal the server's current owned Proof Cycle
  assignment and preserve baseline club/hand/angle; completion atomically writes
  the established `proof_cycle_transfer_checks` row before the job becomes
  queued. `new_context` must still name the current assignment but deliberately
  permits changed capture context and records break provenance without claiming
  a matched transfer. The resulting owned session/job response carries the
  comparison mode/triple; the server never infers matched status from labels.
- `MobileSessionResponse` exposes only a closed bounded `failure_code`, `retryable`,
  `retry_expires_at`, and remaining retry count for failed analysis. Retryable
  source media remains a `job_source` allocation for 24 hours after failure;
  permanent failure, retry expiry/exhaustion, successful analysis, privacy
  erasure, or explicit discard deletes it and releases capacity exactly once.
  Shipped defaults are `mobile_analysis_retry_window_seconds: 86400` and
  `mobile_analysis_retry_max_attempts: 2`; enabling resumable upload requires
  strict positive bounded values and health reports aggregate retained bytes/
  expiring retryable failures only.
- `AnalysisFailureCode` is exactly
  `video_too_long|capture_no_strike|capture_pose_unusable|media_decode_failed|
  analysis_runtime_unavailable|analysis_storage_unavailable|
  analysis_internal_error`. The job classifier maps `VideoTooLongError` to
  permanent `video_too_long`; `ZeroStrikesError` and `EventError` to non-retryable
  `refilm_required` with `capture_no_strike` or `capture_pose_unusable`; typed
  FFmpeg decode/no-stream/invalid-metadata failures to permanent
  `media_decode_failed`; typed binary/process/temporary-I/O FFmpeg failures to
  retryable `analysis_runtime_unavailable`; transient source/artifact filesystem
  availability errors to retryable `analysis_storage_unavailable`; and every
  sanitized unexpected exception to retryable `analysis_internal_error` until
  the attempt cap, then the same code with `retryable=false`. Interrupted process
  restart remains queued/recovered, not a terminal failure. Exception messages,
  commands, paths, and tracebacks never enter the API.
- Refactor the single `FFmpegError` into typed
  `FFmpegMediaError(kind=decode_failed|no_video_stream|invalid_metadata)`,
  `FFmpegRuntimeError(kind=missing_binary|process_start_failed|process_timeout|
  process_signaled)`, and `FFmpegStorageError(kind=temporary_io|disk_unavailable)`
  while preserving `FFmpegError` as their compatibility base. Classify from the
  call site, return code/signaled status, timeout, and explicit `OSError.errno`—
  never by parsing command/stderr/path text. Existing CLI/web human-readable
  errors remain compatible; only the safe native classifier consumes `.kind`.
- `StorageCapacityLedger` is the single durable capacity authority for upload
  parts, completed queued/processing source files, and later privacy-export
  temporary/final files. Each unique `(kind, object_id)` row records reserved
  and materialized bytes; ownership transfers atomically between kinds without
  a release/re-reserve gap, and terminal purge releases exactly once. Admission
  holds its cross-process lock and checks both the configured logical cap and
  filesystem free space minus every still-unmaterialized reservation and the
  protected DB/artifact/backup floor.
- Global admission reserves each active upload’s declared bytes in that ledger.
  `mobile_upload_global_max_reserved_bytes` and
  `mobile_upload_min_filesystem_free_bytes` ship as 0 while the feature is off;
  enabling uploads requires measured positive values. Capacity failure is 507
  `insufficient_storage`, retryable with bounded `Retry-After`, and never appends
  an uncommitted chunk.
- `SessionMaintenanceLock(sessions_dir)` is the one cross-process exclusive lock
  used by the app and backup CLI. It locks a fixed byte of
  `.session-maintenance.lock` using `fcntl.flock` on POSIX and `msvcrt.locking`
  on Windows with bounded timeout; PID/timestamp text is diagnostic only.

- [ ] Write failing tests for create/replay/conflicting idempotency, active-upload
  cap, offset mismatch, truncated/oversized chunk, chunk digest mismatch, full
  digest mismatch, expiry, keyed abort/exact lost-204 replay/conflicting key,
  cross-account 404, and disabled feature 404.
- [ ] Add comparison tests for exact matched assignment, baseline ownership,
  stale/mismatched fingerprint or drill, changed matched club/hand/angle, explicit
  `new_context`, ordinary null, history-epoch change, idempotent replay, and target
  replacement between reservation/completion. A stale completion returns 409
  with no queued job/transfer row; matched completion creates exactly one transfer
  check in the same final transaction and new-context creates none.
- [ ] Add concurrent capacity tests for global declared-byte overcommit, exact-
  limit admission, expiry/abort release, filesystem free-space fall below the
  reserved floor before first/later/final chunk, two writers racing the last
  space, and 507 replay. Assert committed offset/file length stay aligned and
  another account cannot consume the protected DB/artifact/backup headroom.
- [ ] Add abort-journal crash/race tests for every phase before/after part unlink,
  directory fsync, allocation release, and receipt commit; abort versus PATCH,
  complete, expiry, history reset/account deletion, and credential revocation;
  restart repair; same/different-key replay; seven-day tombstone purge; and
  missing file with/without journal. The per-upload lock chooses one outcome:
  abort first makes later writes/completion fail, while completed job-source state
  makes abort return 409 and never deletes it. No 204 precedes durable completion.
- [ ] Add ledger crash/idempotency tests for reserve, materialized-byte update,
  upload-part → job-source transfer, retention purge, and repair from filesystem
  truth. Simulate a queued source held while an upload and export reserve in
  parallel; the shared lock/capacity equation must admit at most the safe set,
  preserve the configured floor, and never double-release.
- [ ] Add startup-boundary tests for zero/default-off values, positive enablement
  requirements, overflow/negative rejection, shipped `config.yaml`, environment
  documentation, and aggregate `/healthz` readback without paths or owner data.
- [ ] Add failing concurrency tests: two completions of one reservation return
  one job; two last-allowance reservations cannot create two jobs; a history
  reset racing completion creates either the old job before reset or a 409, never
  an untracked artifact.
- [ ] Add retry tests for each closed failure code, retryable/permanent
  classification, exact 24-hour boundary, two-attempt cap, idempotent lost
  response, one durable key per server-assigned attempt, attempt-1 failure →
  attempt-2 key rotation, restart/concurrent retry, wrong owner, stale auth/
  history epoch, deleted
  owner, missing/corrupt source, capacity accounting, restart, and one eventual
  success. A retry reuses one job/source and never double-counts quota, transfer
  outcomes, or upload reliability.
- [ ] Add table-driven exception-classifier tests for every mapping above,
  including each typed FFmpeg kind, transient/permanent filesystem errno,
  unexpected exception exhaustion, restart interruption, safe customer copy,
  OpenAPI Literal generation, and proof no path/command/stderr/traceback leaks.
- [ ] Add retry-source discard tests for exact 204 replay, conflicting key,
  wrong owner/state, delete/revoke race, crash between file unlink/allocation
  release/status commit, and restart repair. The source and capacity release occur
  once; the client may delete its local copy only after 204.
- [ ] Crash after each discard phase and before/after file unlink, directory
  fsync, allocation release, and receipt completion; inject same-process replay,
  concurrent retry/worker/retention/deletion, and missing-file-with/without-
  journal cases. No 204 occurs before `complete`, no capacity is over-admitted,
  and restart converges without double release or corruption masking.
- [ ] Add failing crash-recovery tests. Simulate bytes fsynced before offset
  commit and assert startup truncates to SQLite’s acknowledged offset; simulate
  offset committed with a short file and assert the reservation becomes failed
  and no job is created.
- [ ] Add completion crash tests after prepared-row commit, after destination
  directory creation, after `Path.replace`, after finalized-row commit, and
  before `manager.submit`. Every restart converges to the same queued job or a
  clean failed reservation with no orphan artifact and no duplicate quota use.
- [ ] Add `mobile_uploads` to the `JobManager` schema and place parts under
  `sessions_dir/.uploads/<upload_id>.part`. Validate normalized suffix, byte
  size, full SHA-256, club/hand/angle/level, source label, user,
  `history_epoch`, versioned idempotency HMAC pair, created/expiry timestamps,
  offset, status,
  optional comparison mode/baseline/target/drill fields, and optional job ID.
  Validate the comparison triple through the existing server Proof Cycle
  assignment helper at create and again at completion; never trust a client path
  or client-derived comparison truth.
- [ ] Reserve declared bytes through `StorageCapacityLedger` in the same
  `BEGIN IMMEDIATE` transaction as upload creation. Under its cross-process
  lock, recheck `shutil.disk_usage(sessions_dir).free` minus all outstanding
  unmaterialized bytes and the incoming bounded chunk against the configured
  floor before append/fsync and before completion. Update materialized bytes
  with each acknowledged offset. Expiry/abort/failure releases exactly once;
  completion atomically transfers the allocation to `job_source` until terminal
  source retention deletes it. `/healthz` exposes only aggregate reserved/cap/
  free-headroom status.
- [ ] Bump the cumulative mobile backup extension to generation 3 for
  every durable upload/capacity object: `mobile_uploads`,
  `mobile_upload_abort_journals`, `mobile_upload_abort_receipts`,
  `mobile_storage_allocations`, `mobile_analysis_retry_receipts`,
  `mobile_analysis_source_discard_journals`, and discard completion receipts;
  their owner/status/idempotency/unique indexes,
  exact reservation/materialized/category/released/comparison columns, and the
  required `jobs` preparation/source-size/source-digest/source-retention plus
  failure-code/retryability/retry-expiry/retry-attempt/receipt/discard columns,
  comparison-mode/baseline/target/drill columns, and transfer-check linkage. Record
  complete nonterminal source files in the manifest; test generation-2 restore/
  migration and rejection after removing each table, index, required column, or
  referenced source file in turn.
- [ ] Implement chunk persistence in this order: verify requested offset, stream
  and hash a bounded body to the open part file, flush and `os.fsync`, then
  revalidate the credential lease/epoch, update the SQLite offset, and commit.
  On cancellation, revocation, validation failure, or DB failure after any append,
  truncate back to the captured acknowledged offset and fsync before releasing
  the per-upload lock/returning. If truncate/fsync itself fails, mark the
  reservation `repair_required` without advancing its offset and block status/
  mutation until the same-lock repair converges; startup and the next request run
  that repair before exposing the upload. Same-process resume must never observe
  stale unacknowledged bytes.
- [ ] Wrap create/PATCH/complete/abort/retry/retry-source-discard in
  `CredentialMutationGuard`. A long
  PATCH checks its lease before append and again before offset commit; completion
  and retry recheck selector activity plus `auth_epoch` in the final job
  transaction. Inject sign-out/revoke/rotation/reset between byte fsync and DB
  commit and prove file length is synchronously truncated/fsynced to the old
  offset, same-process GET/resume is clean, and no acknowledged offset,
  reservation publication, retry, or job becomes visible after the credential
  closes. Inject truncate failure and require `repair_required` to block access
  until repair, including restart.
- [ ] Implement abort with a separate persisted `upload-abort-idempotency` key-
  ID/HMAC and canonical request hash. Under maintenance→per-upload→store lock
  order, commit `prepared/aborting` before filesystem mutation, unlink and fsync,
  release allocation once, then commit the seven-day 204 receipt. Startup repairs
  all nonterminal abort journals before upload admission; retention never purges
  a journal needed to distinguish intentional removal from corruption.
- [ ] Add an in-process keyed lock registry and acquire the same per-upload lock
  for PATCH, complete, abort, expiry, and recovery. This is the serialization
  contract under the preserved one-replica topology; a concurrent operation
  receives 409 `upload_busy` without reading or appending bytes.
- [ ] Add multi-process lock tests for exclusion, timeout, crash release, and
  Windows/POSIX adapters. Lock ordering is maintenance file lock → per-upload
  lock → UserStore/JobManager lock → SQLite transaction. Backup holds the
  maintenance lock across DB snapshot, manifest enumeration, and file copy;
  upload publish, terminal artifact publish, retention deletion, and privacy
  quarantine acquire it around filesystem mutation. Deletion marks/fences and
  drains workers before acquiring it, so no worker waits behind deletion while
  deletion waits for that worker.
- [ ] Implement completion as a recoverable journal: transaction one verifies
  owner/epoch/quota/digest, assigns the stable job ID, inserts an internal
  `preparing` job, and marks the reservation `finalizing`; commit. Under the
  same per-upload lock, create the deterministic job directory and atomically
  move the part to `source.<suffix>`. Transaction two revalidates size/digest,
  marks the job `queued` and reservation `complete`, and commits; only then call
  `manager.submit`. Startup resumes `finalizing` rows from either the part or
  destination, removes unrecoverable prepared rows, and requeues committed jobs.
  Recovery runs before request acceptance even when new resumable uploads are
  feature-disabled; disabling the flag stops new reservations, not journal repair.
- [ ] Extend startup/maintenance cleanup to expire 24-hour reservations and
  orphaned part files. Extend history reset/account deletion to discard the
  affected user’s active reservations before deleting history.
- [ ] Treat active/finalizing uploads as ephemeral backup state: backup
  validation records their counts but excludes `.uploads` parts. Restore startup
  marks any reservation without its verified part/destination as
  `source_unavailable_after_restore`, releases prepared quota, and requires the
  client to restart from its retained local source. Add scratch-restore tests.
- [ ] Treat a `complete` reservation’s immutable `source.<suffix>` as durable job
  state until analysis is successful, permanently failed, or its bounded retry
  window ends. Include source bytes, size, and SHA-256 in backup manifests for
  every `queued`/`processing`/retryable-failed mobile job, copy them under a
  consistent snapshot lock, and restore them before any requeue. Test complete
  upload → queued/processing/retryable-failed backup → scratch restore → one
  successful or permanent terminal job. A
  missing/mismatched source fails backup/restore rather than yielding a green
  bundle; client retention remains a second recovery path, not the sole copy.
- [ ] Run `python -m pytest tests/test_resumable_uploads.py tests/test_resumable_upload_recovery.py tests/test_mobile_analysis_retry.py tests/test_ffmpeg_failure_kinds.py tests/test_credential_mutation_guard.py tests/test_session_maintenance_lock.py tests/test_storage_capacity.py tests/test_disconnect.py tests/test_history_reset_core.py tests/test_retention_disk.py tests/test_backups.py tests/test_foundation_contracts.py tests/test_web.py -q`; expect all pass.
- [ ] Regenerate the OpenAPI snapshot and commit:
  `git commit -m "feat: add resumable mobile uploads"`.

## Task 6: Add native privacy controls and safe account deletion

**Files:**

- Create: `swinglab/web/mobile_privacy.py`
- Modify: `swinglab/web/recovery_fence_ledger.py`
- Create: `swinglab/web/mobile_mutations.py`
- Modify: `swinglab/web/credential_mutations.py`
- Modify: `swinglab/web/storage_capacity.py`
- Modify: `swinglab/web/mobile_schema.py`
- Modify: `swinglab/web/mobile_auth.py`
- Modify: `swinglab/web/review_auth.py`
- Modify: `swinglab/web/mobile_resources.py`
- Modify: `swinglab/web/resumable_uploads.py`
- Modify: `swinglab/web/throttle.py`
- Modify: `swinglab/web/users.py`
- Modify: `swinglab/web/jobs.py:218-1695`
- Modify: `swinglab/web/app.py`
- Modify: `swinglab/web/digest.py:435-590`
- Modify: `swinglab/web/billing.py:40-110`
- Modify: `swinglab/api/contracts.py`
- Modify: `swinglab/api/mobile_routes.py`
- Modify: `swinglab/web/shopify_billing.py:208-760`
- Modify: `swinglab/integrations/shopify/admin.py:792-900`
- Modify: `swinglab/integrations/shopify/customer_accounts.py:307-500`
- Modify: `swinglab/integrations/shopify/customer_sync.py:168-1150`
- Modify: `swinglab/integrations/shopify/privacy_cli.py:180-260`
- Modify: `swinglab/backups/core.py`
- Modify: `swinglab/backups/store.py`
- Modify: `swinglab/backups/cli.py`
- Modify: `swinglab/cli.py`
- Modify: `swinglab/config.py`
- Modify: `config.yaml`
- Modify: `docs/environment.md`
- Modify: `docs/deployment.md`
- Modify: `docs/operations/backup-recovery.md`
- Create: `tests/test_mobile_privacy_api.py`
- Create: `tests/test_mobile_review_privacy.py`
- Create: `tests/test_privacy_erasure_ledger.py`
- Create: `tests/test_restore_to_service.py`
- Modify: `tests/test_recovery_fence_remote_store.py`
- Modify: `tests/test_mobile_rate_limits.py`
- Modify: `tests/test_credential_mutation_guard.py`
- Create: `tests/test_mobile_mutation_fence.py`
- Create: `tests/test_account_deletion_write_fence.py`
- Create: `docs/security/user-owned-writer-inventory.md`
- Modify: `tests/test_backups.py`
- Modify: `tests/test_history_reset_web.py`
- Modify: `tests/test_shopify_privacy.py`
- Modify: `tests/test_shopify_privacy_cli.py`
- Modify: `tests/test_accounts.py`
- Modify: `tests/test_digest.py`
- Modify: `tests/test_events.py`
- Modify: `tests/test_profile_onboarding.py`
- Modify: `tests/test_proof_cycle_practice.py`
- Modify: `tests/test_shopify_billing.py`
- Modify: `tests/test_shopify_customer_sync.py`

**Interfaces:**

- `POST /api/v1/auth/step-up/start` is bearer-only (ambient cookie is rejected),
  consumes `{purpose:"data_export"|"history_reset"|"account_delete",
  code_challenge:S256_base64url}`, and returns the same no-store HTTP 202
  `{challenge_id, expires_at}` for every eligible owner. The row binds the
  initiating stable user, bearer selector, `auth_epoch`, installation HMAC, and
  purpose. It sends a purpose-bound universal link with challenge ID and grouped
  eight-digit code; neither bearer nor verifier is in the URL.
- `POST /api/v1/auth/step-up/exchange` rejects bearer/cookie fallback and consumes
  only `{challenge_id, email_code, code_verifier}` plus a 128-bit
  `Idempotency-Key`. A valid, still-active initiating selector/epoch/installation
  yields no-store HTTP 201 `{step_up_token, purpose, expires_at}`; exact lost-
  response replay returns the same deterministic token. The opaque token is
  bound to that owner/selector/epoch/installation/purpose, expires in five
  minutes, and is claimed exactly once inside the protected operation's
  transaction. It never authenticates an ordinary API request or becomes a new
  bearer credential.
- `POST /api/v1/auth/review/step-up/start` is the review-only sibling. It requires
  a current review-scoped bearer, immutable `AppIdentityHeaders`, and
  `{purpose:"data_export"|"history_reset"|"account_delete",
  code_challenge:S256_base64url}`; it sends no email and returns the same no-store
  202 challenge shape. The row binds provider, current synthetic-generation/user,
  selector/auth/history epochs, installation, exact app identity, and purpose.
  Ordinary users cannot call it, and a review bearer cannot start the email-code
  route.
- `POST /api/v1/auth/review/step-up/exchange` rejects ambient bearer/cookie and
  consumes `{challenge_id,password,code_verifier}` plus a fresh 128-bit
  `Idempotency-Key`. `ReviewAuthAdmission` re-verifies only that provider's
  dedicated scrypt credential, then rechecks the still-current generation,
  selector/epoch/build/lane and returns the same five-minute single-use
  `step_up_token` shape. It uses distinct review-step-up password-proof, PKCE,
  idempotency, and exact `review-stepup-start-selector`,
  `review-stepup-start-account`, `review-stepup-start-client-ip`,
  `review-stepup-exchange-account`, and `review-stepup-exchange-client-ip` rate
  domains with the same numeric bounds as the email step-up sibling and generic
  401/429 behavior; no
  normal password sentinel, email code, account string, raw password, or verifier
  enters a canonical hash, log, or database.
- Entitlements Task 5 supplies separate generation-7
  `mobile_review_step_up_challenges`, `mobile_review_step_up_exchange_journals`,
  and `mobile_review_step_up_exchange_receipts`; the email-code tables are never
  overloaded. A challenge row carries provider, synthetic generation/user,
  selector/auth/history epoch, installation HMAC/key ID, exact parsed app identity,
  purpose, PKCE challenge, expiry/attempt state, and rate-key HMAC references.
  `ReviewStepUpExchangeJournal` carries provider/generation/purpose plus versioned
  password-proof, PKCE-verifier-proof and idempotency HMACs, sanitized canonical
  request hash, deterministic token-verifier inputs, phases and terminal receipt.
  The resulting shared `mobile_step_up_tokens` row gains a required
  `method="email"|"store_review"` discriminator and nullable provider/generation/
  app-identity columns constrained present only for review. Endpoint/table/method
  mismatch cannot consume a challenge, replay receipt, or operation token.
- `StepUpExchangeJournal` binds exact lost-201 replay with versioned HMAC columns
  for the supplied code and PKCE verifier under purpose-specific proof domains,
  plus challenge/purpose/owner/selector/installation/idempotency HMACs. Its
  canonical request hash covers only those HMAC outputs and non-secret IDs/
  fields—never raw/ordinary hashes of email, eight-digit code, verifier, step-up
  token, or bearer. Conflicting idempotency/proofs return generic 409; missing
  referenced proof-key IDs fail startup/restore closed.
- Step-up challenges expire after 10 minutes, resend no sooner than 60 seconds,
  burn after five failed exchanges, and allow at most two live rows per
  `(selector,purpose)` and five per user. Atomic keyed-throttle caps are 5 starts/
  selector/15 minutes, 5 starts/user/15 minutes, 20 starts/IP/15 minutes,
  5 failed exchanges/user/15 minutes, and 20 failed exchanges/IP/15 minutes.
  Domains are distinct `stepup-start-selector`, `stepup-start-user`,
  `stepup-start-client-ip`, `stepup-exchange-user`, and
  `stepup-exchange-client-ip`; rows contain only versioned HMAC pairs. Limits
  return generic 429 `rate_limited`/bounded `Retry-After` independent of account
  state and never send email beyond the accepted slot.
- Multi-dimensional step-up limits call Task 3’s `consume_many`, so selector/user/
  IP admission is one all-or-none debit. No denied start or exchange may consume
  only a subset of its applicable windows.
- `POST /api/v1/privacy/exports` consumes a `data_export` step-up token and
  `Idempotency-Key`; it returns/replays HTTP 202 with one owned
  `PrivacyExportReceipt(status="pending")`.
- `GET /api/v1/privacy/exports/{export_id}` returns the owned JSON receipt with
  `pending|building|ready|failed|expired`, bounded failure code,
  `retry_after_seconds`, literal `max_download_bytes=1100000000`, byte size only
  when ready constrained to `1..1100000000`, and expiry only when ready. The same
  maximum is encoded in Pydantic/OpenAPI, not left as prose or deployment config.
  `GET /api/v1/privacy/exports/{export_id}/download` streams the ready ZIP with
  `Content-Type: application/zip`, exact `Content-Length` equal to the ready
  receipt's `byte_size`, `Cache-Control: no-store`, and no redirect; pending is
  409 `export_pending`, failed is 409 with its bounded code, and expired is 410.
  Every cross-account ID is 404. The endpoint never redirects to object storage
  or another origin, so the native downloader's bearer remains same-origin.
- `PrivacyExportDownloadGuard.admit(owner, selector, history_epoch) ->
  PrivacyExportDownloadLease` atomically rejects closed/reset/deleting ownership
  before the ready ZIP is opened. It rejects a receipt/stat/content length outside
  `1..1100000000` before allocating a slot or opening a descriptor and marks an
  impossible ready artifact failed for operator investigation. It also rejects any `Range` request with typed
  416 `range_not_supported` before opening the file. Admission transactionally
  acquires one active slot (maximum one per receipt, two per owner, and four
  globally), debits a durable start budget (three starts per receipt and six per
  owner per rolling 24 hours), and reserves the receipt's full `byte_size` against
  a durable byte budget (at most three times that receipt size and 4 GiB per owner
  per rolling 24 hours). A normal completion charges actual emitted bytes and a
  disconnect/error charges emitted bytes with an 8 MiB-or-file-size minimum; the
  unused reservation is released in the terminal transaction. Per-receipt/owner
  pressure or budget exhaustion returns typed 429 plus bounded `Retry-After`;
  global slot pressure returns retryable 503 plus `Retry-After`. Counters and
  reservations survive restart, prune in bounded batches only after their window,
  and contain no bearer or network address. Each lease records a random process-
  instance ID and advances an 8 MiB accounting high-water transactionally before
  emitting the next block. Before request admission at startup, one transaction
  finds leases owned by prior processes, charges the greater of persisted high-
  water or the 8 MiB-or-file-size minimum (bounded by reserved size), releases
  receipt/owner/global active slots and unused byte reservation exactly once, and
  retains start/charged-byte budgets. Crash recovery cannot wait for the 24-hour
  prune window or underflow/double-release counters. The streaming generator holds that lease and
  file descriptor through EOF, checks its cooperative cancel event before every
  bounded chunk, and closes/unregisters in `finally` on completion, disconnect,
  cancellation, or error. Register it with the generic credential-revocation
  extension path: sign-out, self/remote device revoke, token rotation/recovery,
  password reset, and review-credential rotate/close all close selector admission
  and drain matching leases before terminal revocation/new-token activation.
  History reset and account deletion close the owner/epoch. Each path signals and
  drains before purge and terminal 204; a timeout remains durable/replayable 202,
  so no terminal response can coexist with an open descriptor or later byte.
- `PrivacyExportWorker.start/stop/drain_once` leases pending receipts and recovers
  expired leases on startup; `start_background_workers=False` suppresses it.
- `MobileMutationFence.guard(user_id, purpose)` admits a bounded in-process owner
  mutation only after checking the durable deletion state. The guarded write
  rechecks `not deleting` inside its own SQLite transaction; deletion atomically
  closes admission, cancels owner workers, and waits for active guards to drain.
- `OwnerErasureExtension` is a mandatory registry contract for schemas added by
  later plans: `close_and_drain(user_id, deadline) -> bool`,
  `erase_live(connection, user_id, cutoff) -> None`, and
  `reconcile_restored(connection, matched_user_ids, account_delete_record) ->
  None`. Live deletion calls extensions inside the shared deletion phases before
  identity removal. Recovery-chain reconciliation calls them after stable-user-
  HMAC matching and before any provider/job worker or request. If a known owner-
  bound schema exists without its registered extension, startup/deletion fails
  closed; a machine-checked schema-to-extension inventory enforces coverage.
- Export receipts bind the `history_epoch` captured at POST. The worker rechecks
  owner and epoch before reading and immediately before atomic ZIP publication;
  history reset closes export admission, cancels/drains old-epoch workers, bumps
  the epoch, and purges their receipts/files before returning 204.
- `POST /api/v1/privacy/history-reset` consumes `step_up_token`,
  `expected_history_epoch`, and `Idempotency-Key`, then reuses the established
  two-phase history reset beneath a durable
  `prepared -> exports_quiescing -> erasure_recorded -> local_erased -> complete`
  journal. It returns 202 while old-epoch export work drains or any post-record
  local work remains, and 204 only after old receipts/ZIPs are canceled and
  purged and local erasure completes.
- Existing browser `POST /account/history/delete` keeps its recent-auth,
  same-origin form, confirmation, and successful redirect/flash UX, but invokes
  the same history-reset journal and recovery-fence service through a typed
  browser authority instead of calling `JobManager` directly. The server stores
  its opaque operation ID for authenticated retry. Remote outage/pending work
  renders a non-sensitive HTTP 202 page with bounded `Retry-After`; it never
  redirects as complete before `erasure_recorded -> local_erased -> complete`.
- `DELETE /api/v1/account` consumes a purpose-matched `step_up_token` and
  `Idempotency-Key`; it deletes private app data, revokes credentials/push,
  cancels pending uploads, preserves only legally/operationally required
  replay tombstones plus any encrypted provider lifecycle credential explicitly
  defined by Plan 3, and does not silently mutate Shopify itself.
- For a review-scoped token, `DELETE /api/v1/account` invokes Entitlements Task
  5's review-generation erasure extension. It recovery-fences and permanently
  purges the current synthetic user generation, fixture/private data, and scoped
  bearers, then clears the lane's current `user_id`; it does not delete the
  provider credential, supported builds, or standing Google App-access record.
  A later successful current-build review login may create a fresh isolated
  generation/fixture under that task's locked contract. The API confirmation and
  review instructions disclose this review-only regeneration, while ordinary
  customer deletion continues to delete the actual account and never regenerates.
- Deletion first checks the versioned idempotency HMAC against a nonterminal
  journal or completion receipt before normal bearer/step-up authentication.
  Possession of the exact 128-bit key plus matching canonical request resumes
  202 or replays 204 after credentials are revoked; an unseen key still requires
  current bearer and an unused purpose-bound step-up token.
- `MobilePrivacyService` owns a durable deletion journal with
  `prepared -> analysis_quiescing -> jobs_closed -> files_quarantined ->
  erasure_recorded -> identity_deleted -> complete`
  phases. Journal and completed receipt store the deletion idempotency HMAC plus
  its key ID. A completed row retains only that versioned HMAC, a versioned HMAC
  of the deleted stable user ID, and completion time, allowing a lost-response
  retry to return 204 before bearer authentication and write triggers to reject
  stale owners without revealing whether any account existed.
- Every app-owned SQLite connection registers the keyring-backed owner-HMAC UDF.
  Generated INSERT/UPDATE triggers reject a user ID found in either a nonterminal
  journal or completed user-ID-HMAC receipt; deletion-owned nulling/deletes are
  allowed. This remains effective after the raw user row is gone.
- `RecoveryFenceLedger` extends Task 3's append-only, HMAC-chained recovery
  sidecar outside ordinary point-in-time bundles. Each fsynced record has kind
  `cutover_baseline|token_revoke|push_environment_cutoff|review_access_revision|
  account_delete|history_reset|shopify_customer_erase|shopify_shop_erase`,
  sequence, cutoff time, and only the
  fields for that kind: token revocation has versioned selector/token-verifier
  HMACs; app privacy erasure has stable-user-ID HMAC and erased-through history
  epoch; a push cutoff has deployment environment, versioned Expo-project-ID HMAC,
  activation/cutoff revision, last-provider-started/accepted/provider-may-accept-
  until/closed times, persisted cutoff skew/provider-safe-after, and closed state;
  account deletion also has normalized-email HMAC; Shopify customer erase
  has mode `delete|redact`, shop-domain/customer-ID HMACs, optional matched
  stable-user/email HMACs, and cutoff; shop erase has shop-domain HMAC/cutoff. No
  raw token, ID, shop, email, or media is stored. `append_and_publish` uses Task
  3's serialized
  immutable-record put/readback and `HEAD` CAS/readback protocol. No revocation/
  deletion/reset can pass `recovery_fenced` or `erasure_recorded` until the full
  canonical record body and head reference both read back and validate.
- Approval-gated `swinglab backup restore-to-service` takes an exact verified
  bundle ID, configured absolute sessions target, and explicit stopped-service
  confirmation. Its parent-level `ServiceRestoreLock` and fsynced journal live in
  the validated target parent (for Railway, `/data`), never inside the old or
  staged sessions trees. The app startup refuses while that parent promotion
  lock/journal is nonterminal.
- Every export, history-reset, and deletion replay row stores
  `(idempotency_hmac_key_id, idempotency_hmac)` plus a canonical request SHA-256;
  exact replay requires both versioned HMAC match and identical request hash.
  The canonical bytes include only operation kind and semantic non-secret fields
  such as `expected_history_epoch`; they exclude bearer, step-up token, cookies,
  and transport headers. All three endpoints check an existing exact journal/
  receipt before ordinary bearer/step-up authentication, so a consumed step-up
  or changed epoch can recover a lost 202/204/receipt; an unseen key still
  requires a fresh valid bearer and purpose-bound step-up token.

- [ ] Add failing tests for wrong-purpose, expired, copied, replayed, and
  cross-account step-up tokens; prove no bearer alone can reset/delete.
- [ ] In this backend plan, add default-deny review step-up route/OpenAPI tests for
  all three purposes, exact app-identity parsing, bearer-only start/no-ambient-auth
  exchange, generic failure/rate-limit shape, no email, and zero review-table/
  credential/fixture writes while `ReviewAuthAdmission` denies. Prove ordinary
  email step-up cannot cross-consume its challenge or token. Entitlements Task 5
  modifies this file to add the provider/generation/password/full-operation E2E
  once generation 7 and the production admission implementation exist.
- [ ] Add full step-up lifecycle tests: cookie-only start rejected; current
  bearer start bound to user/selector/auth epoch/installation/purpose; wrong
  verifier/code, copied link, other device, selector revoke, sign-out, epoch
  change, expiry, five-attempt burn, lost-201 replay, conflicting idempotency,
  and token copied to another bearer all fail without revealing account state.
  Use distinct `stepup-email-code-verifier`, `stepup-token-verifier`, and
  `stepup-exchange-idempotency` HMAC domains/key-ID columns in addition to the
  five rate domains above. Add cross-domain/rotation/key-retirement, backup/
  scratch-restore, live-cap, resend, and parallel exact-limit tests.
- [ ] Render the grouped code and purpose in plaintext/HTML email beside the
  allowlisted universal link. Redact the entire callback query and every step-up
  request secret from access/application logs; return `Cache-Control: no-store`,
  `Referrer-Policy: no-referrer`, self-only CSP, and no third-party resources.
  A callback without the initiating PKCE verifier offers only safe restart.
- [ ] Add failing export tests covering profile, sessions, practice/proof data,
  entitlement summary, and device metadata while excluding every hash, secret,
  raw provider payload, and internal path. Task 8 extends this same builder/test
  when owner-linked mobile event/receipt tables land; those rows are not silently
  omitted from the final native export contract.
- [ ] Add receipt/worker tests for exact POST replay, conflicting idempotency,
  pending/building/ready/failed/expired JSON, cross-account 404, download before
  ready, one-hour expiry measured from ready time, leased-worker crash/reclaim,
  atomic ZIP publication, purge, and no worker start during OpenAPI export. For
  a ready download assert direct 200/no redirect, exact ZIP content type and
  content length matching `byte_size`, exact OpenAPI const/maximum for
  `max_download_bytes`/`byte_size`, no compression middleware mutation, and
  bounded streaming rather than materializing the archive in server memory.
- [ ] Add download-admission tests at per-receipt, per-owner, and global active-
  stream limits; exact and over-limit parallel starts; durable receipt/owner
  start and byte budgets; `Range` rejection; sequential full downloads; early
  disconnect and one-off retry; budget exhaustion; bounded pruning; and process
  restart with active reservations. Assert exact 429 versus 503 error codes and
  `Retry-After`, no descriptor/open byte before admission, actual/minimum byte
  charging, release in every `finally`, no counter underflow/double release, and
  unrelated owners still progress below the global cap. Kill the process after
  each accounting block and prove startup converges all orphan slots/reservations
  before admitting a fifth stream while retaining the conservative charge.
- [ ] Add active-download barrier tests immediately after authorization/open and
  after the first chunk. Race sign-out, history reset, and account deletion;
  assert new streams are rejected, existing leases receive cancellation, no
  later chunk is emitted, the descriptor closes in `finally`, purge succeeds
  under both Windows-delete and POSIX-unlink semantics, and terminal 204 waits
  for drain. Cover disconnect/error, drain timeout to exact 202/replay, and prove
  no stream or file can reappear after terminal completion.
- [ ] Run the same barriers for self/remote device revoke, same-installation token
  rotation, ownership recovery/password reset, and review-credential rotate/close.
  Exact replay must stay 202 until the matching selector lease/descriptor drains;
  only then may terminal 204 or a replacement credential become active, and no
  post-revocation byte/new stream is possible.
- [ ] Register export cancellation/purge as a server sign-out extension hook.
  Sign-out returns 202 while a building export drains and 204 only after all
  owned transient exports are gone. Test lost-response replay and prove a
  building worker cannot publish after sign-out 204.
- [ ] Implement a separate native export builder; do not reuse the operator-only
  Shopify privacy-request snapshot. Before writing, enumerate bounded inputs,
  reject more than 1,073,741,824 uncompressed bytes, 10,000 entries, or 240 UTF-8
  bytes per safe relative entry path; reserve the exact 1,100,000,000-byte ZIP cap
  through the shared `StorageCapacityLedger`, then write the
  ZIP atomically beneath `sessions_dir/.privacy_exports`. Track temp/final bytes
  under the same allocation, transition kind without a release gap, use opaque
  names/mode 0600, and release exactly once only after failed-temp cleanup or
  final expiry/sign-out/deletion purge. Abort with bounded `export_too_large`,
  delete the temp, and publish no ready receipt if the writer would cross the ZIP
  cap; ZIP/ZIP64 headers, central directory, data descriptors, names, and any
  compression expansion all count toward it. Exclude transient exports from backups.
- [ ] Add upload-vs-export race tests with barriers before reservation, midway
  through each write, and before publish. Include queued source allocations and
  the protected DB/artifact/backup floor; at most the safe writer proceeds, 507
  leaves no partial unreserved file, restart repairs materialized counts, and
  cancellation/purge cannot double-release an allocation.
- [ ] Port the history-reset success, stale epoch, pending Shopify privacy
  export, and filesystem failure cases from the browser route to the mobile
  service. Before remote acceptance, a failure may roll back and reopen the
  old epoch. Once `history_reset` is independently accepted, rollback is
  forbidden: keep the owner/reset fenced at 202 and resume local erasure to
  completion on retry/startup. Seed crashes and filesystem failures immediately
  before and after `erasure_recorded`; prove only the pre-record side can roll
  back and the post-record side eventually returns 204 without admitting a
  write in the old epoch. Call the same core erasure methods; do not create a
  second deletion algorithm.
- [ ] Replace the browser route's direct `manager.reset_user_history` call with
  the shared durable service while preserving current form validation/error
  ordering and exact 303/flash success. Add pending/outage HTML tests, lost-
  response authenticated resume, and pre-browser-reset snapshot → successful
  head publish → old-snapshot restore proving erased history/media stays gone.
  Accepted browser reset journals resume before requests even when
  `mobile_privacy_enabled` is false; that flag gates only new native privacy
  starts. If recovery-fence readiness is absent, the existing browser reset
  feature cannot enable or claim success. Add startup/readback tests with every
  mobile privacy flag off but `web.history_reset_enabled` on, plus transient
  publish-outage 202 UI and recovery after the store returns.
- [ ] Add account-deletion transaction tests covering pending uploads, queued
  and actively processing jobs, mobile credentials, practice evidence, export/
  mutation receipts, idempotent replay, Shopify customer linkage, a fake
  registered deletion-extension hook, and failure rollback. Push cleanup is
  added/tested in Task 7; native entitlement cleanup is added/tested in Plan 3,
  after those schemas exist.
- [ ] Implement the erasure-extension registry with a fake entitlement extension
  now. Test drain timeout/202, live severing in the deletion transaction, crash
  replay, pre-delete snapshot plus newest `account_delete` chain reconciliation,
  and startup failure when a declared owner-bound table lacks an extension.
  Plan 3 replaces the fake with the entitlement implementation and must run these
  same barriers before provider workers start.
- [ ] Add crash tests at every deletion phase. First mark the account `deleting`
  so new auth/jobs fail. Make `JobManager` retain per-job futures/cancel events;
  cancel queued work, fence delivery, and wait for running analysis to quiesce
  before closing rows or deleting identity. A request that cannot drain within
  30 seconds returns 202 `deletion_pending` and the same idempotency key resumes
  it. Then move owned files on the same
  volume into an opaque quarantine, delete/anonymize UserStore rows, replace the
  user-bound journal with the non-PII completion receipt, and purge quarantine.
  Startup resumes a nonterminal journal. Backup creation refuses to snapshot a
  nonterminal deletion. No second SQLite connection is opened for either store.
- [ ] Retrofit native auth convergence, practice evidence, device revocation,
  every upload write/finalize, privacy export/reset, and their worker callbacks
  through `MobileMutationFence`. Task 7 push writes and Task 8 telemetry writes
  must use it when they land. The deletion journal transition to `deleting` and
  each mutation’s final fence check occur in `BEGIN IMMEDIATE` transactions.
- [ ] Inventory every existing user-data writer and external delivery path before
  enabling deletion: PWA upload/profile/practice/proof/session actions, product
  events, digest/email, Shopify sync/privacy/customer linking, Stripe/web billing,
  JobManager state/artifacts/notifications, plus all new mobile workers. Install
  owner-write triggers for every user-owned table (including job-linked tables)
  and wrap file/network side effects in the shared guard with a final deletion-
  receipt check. Keep the inventory machine-checked against schema/known writers.
- [ ] Hold the existing cross-process `shopify_remote_privacy_lock` across the
  operator CLI's integrity-checked snapshot read and atomic private-file
  publication. Account deletion closes owner admission, then acquires that same
  lock before its final Shopify-privacy row purge and holds it through the local
  completion commit. If an export already owns the lock, deletion stays 202/
  waits and the export completes before 204; if deletion owns it first, the CLI
  sees no exportable row. Test both barriers and process-crash release. No output
  publication may begin or finish after 204; a file completed before 204 remains
  under the existing bounded regulatory/operator retention and is disclosed as
  such rather than silently deleted outside the sessions volume.
- [ ] Add one barrier test per inventory category with authentication/work begun
  before `deleting`; release it after deletion starts and prove 204 waits or the
  trigger/guard rejects it. After 204, assert no DB row, file, email, product
  event, Shopify call, billing binding, push, or export can appear. Prove new user
  IDs are never reused and a later same-email account remains writable.
- [ ] Make `PrivacyExportWorker` retain per-receipt futures/cancel events. On
  deletion, cancel queued exports, signal building exports, wait for their guards,
  and fence the final ZIP `Path.replace`; timeout returns 202. Hold an export at
  a publish barrier, delete the account, then prove no ZIP/receipt appears after
  204. Repeat barriers for practice, upload, device, push-fake, and telemetry-fake
  writes and prove no post-delete row/file can commit.
- [ ] Hold export publication at a barrier while history reset starts. Reset must
  return 202 or wait, cancel/drain the old-epoch worker, purge its temporary/final
  ZIP and receipt, then return 204. A later export uses the new epoch and cannot
  contain pre-reset sessions; sign-out likewise leaves no export behind.
- [ ] After old-epoch writers/exports drain but before history reset mutates
  local history, append, conditionally publish, and read back its
  `history_reset` recovery record, then durably enter `erasure_recorded`. A
  publish outage keeps the reset journal fenced at 202 without an irreversible
  event; a crash after acceptance must resume rather than roll back. On startup/
  restore, purge sessions/media/practice/proof at or below the recorded history
  epoch/cutoff but preserve the account/profile and later-epoch work.
- [ ] Test lost 202 and crashes immediately before/after credential revocation:
  retry without bearer or the already-consumed step-up token must resume only
  with the exact idempotency key/request hash. Wrong/conflicting/random keys get
  one generic failure and reveal neither account nor journal existence.
- [ ] Change `create_app` startup order before enabling privacy: construct
  `JobManager(recover_interrupted=False)`, construct the privacy service, load
  nonterminal deletion owner fences, register all available sign-out hooks,
  reconcile the newest recovery-fence head (token revocations first, then
  erasures), resume/cancel deletion/reset journals, resume sign-outs for
  remaining owners, and only then call
  `jobs.recover_interrupted(blocked_user_ids=...)`. The recovery query also
  atomically excludes owners with a nonterminal deletion journal as defense in
  depth. Start export/push/other workers last; do not accept requests until this
  sequence finishes.
- [ ] Run nonterminal deletion recovery regardless of
  `mobile_privacy_enabled`; disabling the route blocks new operations but cannot
  un-fence an owner or abandon an accepted deletion. A missing deletion-journal
  HMAC key is a startup failure, never permission to requeue that owner.
- [ ] Seed a crash at `analysis_quiescing`, reconstruct the full app, and assert
  constructor/startup recovery never invokes analysis for that owner, resumes
  deletion to 204, and leaves no state save, source/artifact, email, or push.
- [ ] Add `swinglab recovery-fence-ledger verify|export-chain`. Wire the approved
  conditional-write/readback client through existing backup store abstractions,
  protected config, and runbooks; credentials stay outside Git and external
  setup remains approval-gated. Every recovery fetches the newest protected
  `HEAD` and every referenced immutable record separately from the chosen point-
  in-time bundle. Before job recovery or request acceptance, validate the full
  monotonic chain; reject tokens
  and purge selector-bound push state matching `token_revoke`, then purge all
  identity, job/media, export, push, and entitlement bindings matching erasure
  records. Missing, stale, invalid-chain, remote readback, or missing-key input
  fails restore closed. Add fake-store conditional conflict/readback, outage,
  credential-absence, CLI, and pre-revocation-backup restore tests.
- [ ] Implement restore-to-service as
  `prepared -> rollback_verified -> staged_verified -> credentials_reset ->
  recovery_reconciled -> swap_started -> old_moved -> new_promoted ->
  postverified -> complete`. Require the service/supervisor stopped, acquire the
  parent promotion lock plus maintenance/SQLite-exclusive checks, create and
  verify a rollback bundle, stage a second same-volume working tree, migrate with
  all providers/workers/mail disabled, apply credential reset and the complete
  recovery chain/extensions, fsync every file/directory, then rename old tree to
  an operation-specific retained rollback tree and promote staged tree. Never
  mutate the immutable source bundle or auto-delete rollback evidence.
- [ ] Add crash/restart tests at every restore phase, exact-operation resume,
  stale/unbound/pre-cutover bundle rejection, cross-volume/unsafe-target refusal,
  lock contention, missing extension/key/record, Windows/POSIX parent-lock
  adapters, and failure between old move/new promotion. Post-promotion validation
  fetches the newest chain again and proves DB/artifacts/auth/erasures before the
  supervisor may start; app startup performs one more chain readback before
  requests/workers.
- [ ] Route verified Shopify `customers/delete`, `customers/redact`, and
  `shop/redact` through durable
  `prepared -> recovery_fenced -> local_applied -> complete` privacy journals.
  Publish/read back the full customer/shop erase record before irreversible local
  mutation; after remote acceptance any local failure stays retryable/fenced and
  resumes rather than rolls back. Return Shopify-compatible 2xx only at complete;
  transient store/local failure returns 503 so Shopify retries, and exact webhook
  replay resumes by its existing event identity without duplicating a record.
- [ ] Reconcile Shopify erase records before requests/workers using the existing
  customer-delete/redact/shop-redact core semantics: claimed app account/history
  survives customer redaction where it does today, while store links/orders/PII,
  unclaimed stubs, pending identities/grants, and delayed-webhook tombstones are
  removed/restored according to mode and cutoff. Test pre-event backup → webhook
  → restore for claimed/unclaimed customer delete/redact and shop redact, delayed
  create/update after restore, same-email later account, remote outage, and crash
  at each phase.
- [ ] Test snapshot-with-user → delete → newest-ledger append → restore the old
  immutable snapshot into a disposable working copy → pre-start erasure
  reconciliation. Prove no account/media/job requeue/export/push/billing binding
  survives. Extend HMAC key-retirement usage through the newest ledger and every
  backup generation that can still contain that user.
- [ ] Test pre-reset snapshot → successful reset/head publish → restore the old
  snapshot with newest head; no erased session/media may return. Then create a
  later-epoch session, back up/restore, and prove that newer work is preserved.
- [ ] Reconcile primarily by deleted stable-user-ID HMAC. Email-HMAC matching may
  remove only unbound/legacy rows proven created at or before the deletion cutoff;
  it never deletes a user or row created later. Test delete → re-register the
  same email under a new stable user ID → back up → restore with the newest head:
  the old lineage stays erased and the new account/data remain.
- [ ] Inject loss after local fsync, before off-volume publish, after remote
  acceptance, and before readback. Until conditional publish/readback succeeds,
  keep the account fenced and return/replay 202—never 204. Retry preserves the
  same logical event/idempotency identity. If the expected head is unchanged it
  reuses the exact orphan record bytes; if another legitimate writer advanced
  `HEAD`, it refetches under the append lock and allocates a new sequence/hash
  that references the winning head. Reject only a conflicting payload for the
  same logical event, never a valid concurrently advanced head, and converge
  without a duplicate logical record. Simulate immediate local-volume loss after each point and
  prove the latest accepted 204 can never restore the deleted user.
- [ ] Keep the recovery-fence chain outside ordinary backup generations. Its
  synchronously read-back immutable records plus `HEAD` at the approved encrypted
  off-volume destination are the authoritative recovery artifact; an asynchronous
  copy is never a prerequisite for, or evidence of, 204. Keep
  `mobile_privacy_enabled` off until conditional record put/readback, head CAS/
  readback, and a scratch restore prove the complete newest chain is retrievable
  without list permission. This plan does not select/provision a new vendor, and
  external destination setup remains approval-gated.
- [ ] Hold a running analysis on a test barrier, start deletion, and assert 202;
  no new job can start and no email/push observer can enqueue. Release the
  barrier, replay deletion to 204, then prove no later state save, artifact,
  email, or push exists for the deleted account.
- [ ] Wrap step-up start/exchange, export start, history reset, and account-delete
  preparation with both the owner fence and Task 3 credential lease. Recheck the
  selector/epoch in each final transaction; after deletion preparation, retain
  only its operation envelope/replay path and admit no ordinary bearer mutation.
  Test sign-out, rotation, password reset, and device revoke at every barrier.
- [ ] Implement purpose-bound challenge rows and hashed step-up tokens in the
  existing mobile schema. A step-up token is never accepted by normal bearer
  auth and is claimed inside the sensitive operation’s transaction. Step-up
  exchange journals use the same secret-safe replay construction as sign-in but
  distinct `stepup-exchange-code-proof` and `stepup-exchange-pkce-proof` HMAC
  domains; their canonical hash never covers raw code/verifier/token bytes.
- [ ] Add step-up DB/backup dictionary tests mirroring native sign-in: enumerate
  all eight-digit codes and legacy plain hashes after lost-201 replay, prove none
  match persisted request/replay values, and prove exact retry works only through
  versioned purpose-specific HMACs with key-rotation coverage.
- [ ] Add the step-up expiry/resend/attempt/live and five keyed-rate bounds to
  defaults, shipped config, `/healthz` readiness, and environment/deployment
  docs. Validate exact positive/minimum/maximum values at startup and test every
  setting with privacy off/on; durable accepted challenges still expire/recover
  when new step-up starts are disabled.
- [ ] Bump the cumulative mobile backup extension to generation 4 for privacy
  state: `mobile_step_up_challenges`, step-up exchange journals/replay receipts
  with code-proof/PKCE-proof key IDs/HMACs and sanitized request hash,
  `mobile_step_up_tokens`,
  `mobile_privacy_export_receipts` (status/lease/epoch/capacity-allocation
  columns), `mobile_privacy_export_download_budgets` (durable start/byte
  reservations and rolling-window counters), `mobile_mutation_receipts`,
  `mobile_history_reset_journals`,
  `mobile_account_deletion_journals`, `shopify_privacy_erasure_journals`, and
  completion/replay receipts, plus every phase, request-hash, versioned-HMAC,
  webhook-event identity, erasure mode/cutoff, owner/shop fence index/trigger,
  and required column.
  Ready/transient export files remain excluded and every restored export receipt
  is canceled/purged before requests. Nonterminal deletion journals still block
  backup; test generation-3 restore/migration and reject a manifest/database
  missing each generation-4 table, index, trigger, or required column in turn.
  Reconstruct nonterminal customer-delete/redact and shop-redact journals from a
  scratch restore and prove startup resumes them before webhook success/requests.
- [ ] Implement `mobile_privacy.py` as the only orchestration layer for native
  reset/export/delete; document every retained tombstone and why it has no PII.
- [ ] Run `python -m pytest tests/test_mobile_privacy_api.py
  tests/test_mobile_review_privacy.py
  tests/test_privacy_erasure_ledger.py tests/test_recovery_fence_ledger.py
  tests/test_recovery_fence_remote_store.py tests/test_restore_to_service.py
  tests/test_mobile_mutation_fence.py
  tests/test_account_deletion_write_fence.py tests/test_storage_capacity.py
  tests/test_backups.py tests/test_history_reset_web.py
  tests/test_shopify_privacy.py tests/test_shopify_privacy_cli.py
  tests/test_digest.py tests/test_events.py
  tests/test_profile_onboarding.py tests/test_proof_cycle_practice.py
  tests/test_shopify_billing.py tests/test_shopify_customer_sync.py
  tests/test_resumable_uploads.py tests/test_mobile_api_tokens.py -q`; expect all
  pass.
- [ ] Regenerate the OpenAPI snapshot and commit:
  `git commit -m "feat: add native privacy controls"`.

## Task 7: Add device-bound Expo push registration and a durable outbox

**Files:**

- Create: `swinglab/web/push_store.py`
- Create: `swinglab/web/push_delivery.py`
- Modify: `swinglab/cli.py`
- Modify: `swinglab/web/credential_mutations.py`
- Modify: `swinglab/web/mobile_schema.py`
- Modify: `swinglab/web/mobile_privacy.py`
- Modify: `swinglab/web/jobs.py:218-288,735-830`
- Modify: `swinglab/api/contracts.py`
- Modify: `swinglab/api/mobile_routes.py`
- Modify: `swinglab/web/app.py:477-585,4730-4739`
- Modify: `swinglab/web/users.py` privacy/export/deletion sections
- Modify: `swinglab/backups/core.py`
- Modify: `swinglab/config.py`
- Modify: `config.yaml`
- Modify: `pyproject.toml`
- Modify: `docs/environment.md`
- Modify: `docs/deployment.md`
- Create: `tests/test_mobile_push.py`
- Modify: `tests/test_credential_mutation_guard.py`
- Create: `tests/test_push_outbox.py`
- Create: `tests/test_mobile_push_cutover.py`
- Modify: `tests/test_backups.py`

**Interfaces:**

- `PUT /api/v1/devices/push` requires `MobileAuthContext.selector`, the centrally
  parsed immutable app-identity tuple, and consumes `{provider:"expo", token,
  platform:"ios"|"android", app_version, expo_project_id,
  practice_reminders_enabled:bool}`. The body platform/version must equal the
  parsed headers and `expo_project_id` must equal the deployment's configured
  public EAS project UUID. It upserts the registration without
  accepting a user ID or selector from the body. It is absolute desired-state
  replacement under unique `(environment, expo_project_id, provider, token)` and
  current-selector constraints; the row immutably records environment, project,
  application ID, platform, version, build, activation generation, and selector.
  byte-identical lost-response replay returns the same sanitized registration
  and never creates another row/outbox effect.
- `PATCH /api/v1/devices/push/preferences` toggles the current device’s optional
  72-hour practice reminder by absolute boolean desired state; a completed
  practice resets its next due time. Same-body replay is a no-op.
- `DELETE /api/v1/devices/push` removes the current device registration and
  returns/replays 204 when it is already absent. These three routes use their
  explicit desired-state/idempotent contract rather than mutation keys; request
  loss cannot duplicate a registration, reminder, or removal side effect.
- `PushProvider.send(messages: Sequence[PushMessage]) -> Sequence[PushTicket]`
  and `.receipts(ticket_ids) -> Sequence[PushReceipt]`.
- `ExpoPushProvider` posts only to `https://exp.host/--/api/v2/push/send`, follows
  bounded retry/backoff, then polls only
  `https://exp.host/--/api/v2/push/getReceipts` to retire
  `DeviceNotRegistered` after a durable ticket. Both calls require the protected
  `EXPO_ACCESS_TOKEN` as an Authorization bearer; a missing token disables
  delivery before enqueue, and an Expo `UNAUTHORIZED` response is terminal for
  the current credential plus an operator alert, never an unauthenticated retry.
  Every message sets integer `ttl=900`; no call may omit TTL or rely on Expo's
  provider defaults. The outbox stores the exact expiry and refuses a send when
  fewer than the required request/backoff seconds remain.
- Push configuration adds exact keys `mobile_push_expo_project_id: ""`,
  `mobile_push_send_envelope_seconds: 30`, and
  `mobile_push_cutover_clock_skew_seconds: 60`; the project also has the exact
  non-secret override `CADDIEINSIGHT_EXPO_PROJECT_ID`. Flag-on startup requires a
  canonical configured UUID equal to the client/EAS project and the envelope/skew
  values within `5..60`/`30..300`; release readback requires the shipped 30/60.
  The outer monotonic send envelope covers all connect/write/read/pool timeouts,
  at most three attempts, and all backoff; no attempt starts without enough
  remaining time and no runtime setting bypasses that deadline.
- `PushOutboxWorker.start()`, `.stop()`, and `.drain_once(now=None) -> int` are
  owned by app startup/shutdown. Rows use
  `pending|leased|awaiting_receipt|delivered|dead`, random lease owner,
  `lease_expires_at`, provider ticket ID, receipt-due time, bounded attempts/
  backoff, and expired-lease reclaim before send or receipt polling.
- `PushDeliveryGuard` tracks in-flight send/receipt work by owner and selector.
  Immediately before provider I/O and before each receipt-state commit, the
  worker rechecks active registration, sign-out, and deletion fences. Sign-out/
  deletion closes admission, invalidates unstarted leases, and waits active
  guarded calls to finish; timeout keeps the parent operation at 202, never 204.
- Each push registration stores immutable `registered_at`; a durable server
  `push_not_before` watermark is created on first activation and never moves
  backward. Job notification eligibility requires
  `terminal_at >= max(registered_at, push_not_before)`.
- Generation 5 also owns `mobile_push_environment_fences` and
  `mobile_push_cutover_operations`. A fence is keyed by deployment environment
  and configured Expo project ID and carries `open|closing|closed`, monotonic
  activation/cutoff revision, last provider-started/accepted times, maximum
  persisted `provider_may_accept_until`, closed time, frozen cutoff-skew seconds,
  computed `provider_safe_after`, and recovery-head binding. Registrations and
  outbox rows bind the active revision.
  A deployment with push disabled may have no row; its first approved flag-on
  startup atomically creates `open` revision 1 only after credential/project/
  recovery readiness. A previously closed remote or local fence makes that same
  startup fail push closed instead of creating or reopening a row.
  A closing/closed or mismatched fence rejects registration, enqueue, lease claim,
  send, and receipt work before provider I/O. Startup never reopens a closed fence
  from config and rejects a restore older than the off-volume cutoff revision.
- `swinglab mobile-push-cutover` is the approval-gated environment-wide operator
  interface. `status` requires `--sessions-dir <dir> --environment
  staging|production --expo-project-id <uuid> --json`; `close` and `purge` add a
  distinct 128-bit `--operation-id` plus explicit `--dry-run|--apply` (dry-run is
  default). Every command rejects an environment or Expo project ID different
  from server-owned configuration. `close --apply` recovery-fences the exact environment/project,
  atomically blocks new registration/enqueue/provider work, terminalizes unsent
  rows, and reports only aggregate registrations/outbox states, active leases,
  the cutoff revision, and sanitized last-accepted time. Exact replay is
  idempotent; a conflicting request hash fails. Before a logical send's first HTTP
  byte the worker durably advances `last_provider_started_at` and the absolute
  `provider_may_accept_until = started_at +` that send's validated/persisted outer
  envelope. Every retry remains within that one deadline. An accepted result may
  only advance the monotonic `last_provider_accepted_at` safety field after close
  and cannot commit any other outbox effect. Close freezes the current cutoff-skew
  seconds. `purge` computes and reports `provider_safe_after =
  max(last_provider_accepted_at, provider_may_accept_until) + 900 seconds +
  frozen_cutoff_skew_seconds` (or the already-safe close time when no provider
  call was ever started) from durable state, never current mutable config.
  `purge --apply` refuses until the fence is closed,
  all guarded calls/leases are drained, and authoritative current UTC is at or
  after that computed time; it accepts no operator-supplied expiry override. It
  publishes/readbacks a later final `push_environment_cutoff` recovery revision
  containing the drained start/accept/deadline times and absolute safe-after before it deletes all raw registrations/
  tokens and provider ticket/receipt/nonterminal outbox state,
  preserves only recovery-fenced aggregate counts and HMAC cutover tombstones,
  and can never reopen delivery. Health exposes fence state/revision, aggregate
  row/lease counts, and last-accepted/expiry-safe booleans without identifiers.
- Push admission is bounded independently of job success. With no valid Expo
  delivery credential, observers/scanners create no outbox row. When configured,
  shipped caps are 10,000 nonterminal rows globally, 50 per selector, and a
  24-hour maximum notification age; a full cap coalesces/skips generic work and
  increments only a non-PII aggregate drop counter. `delivered|dead` rows retain
  30 days, provider ticket/receipt details 7 days, and a daily/startup purge
  deletes at most 1,000 rows per transaction. Polling the job remains canonical.

- [ ] Add failing registration tests for cookie rejection, selector binding,
  token rotation, provider-token takeover by a new authenticated device,
  revocation, sign-out removal, account deletion, invalid Expo token, and
  cross-account isolation, lost PUT/PATCH/DELETE response replay, same desired-
  state concurrency, and proof that replay creates no duplicate row/outbox work.
- [ ] Add app-identity/project tests for missing, duplicate, malformed, body/header
  mismatch, wrong configured EAS project, cross-environment replay, and the same
  Expo token surviving a version/build upgrade. The old environment row must not
  authorize or mutate the new environment registration.
- [ ] Add configuration tests for the closed deployment environment, safe default,
  exact environment override, blank/malformed/wrong Expo project ID, exact
  client/backend project equality, send-envelope/skew min/max, flag-off tolerance,
  flag-on fail-closed behavior, and non-secret health readback. A CLI target that
  differs from configured environment/project performs no write.
- [ ] Wrap registration/preferences/removal, security enqueue, reminder enqueue,
  and job-observer enqueue in the shared owner mutation fence. Bearer registration/
  preference/removal also holds a credential lease and rechecks selector/epoch in
  its final transaction. Add deletion/revoke/sign-out race barriers and prove
  neither registration nor outbox rows appear after the applicable 204.
- [ ] Register current-selector push deletion as a sign-out extension hook using
  the shared connection/lock. Exact sign-out replay must find no registration,
  outbox work, or Expo delivery for that selector while other devices remain.
- [ ] Add provider-send barriers before HTTP start, during the provider call, and
  before receipt commit. Sign-out/account deletion must cancel the pre-send case,
  return/stay 202 while a call is in flight, then remove rows and reach 204 only
  after the guard drains. Prove no provider call begins and no receipt/outbox row
  changes after 204. Document that a generic message already accepted by Expo
  before the fence may still be delivered by that external provider.
- [ ] Require `ttl=900` in every serialized Expo message and test that retry/backoff
  cannot send after its absolute expiry. Add close/purge dry-run, apply, replay,
  conflict, crash-at-each-phase, active-lease refusal, aggregate-only output,
  recovery-head rollback, crash during a pre-close in-flight send at the latest
  possible acceptance, shortened/lengthed config after close without deadline/
  safe-after drift, early-purge/future-time/clock-skew refusal, command help/
  registration/invalid-argv, and old-backup restore tests. With two isolated database
  deployments holding the same Expo token/project, close staging, drain guarded
  work, advance through TTL plus skew, purge it, register production, and prove a
  staging scan/restart/config mistake performs zero provider I/O while production
  alone can enqueue/send.
- [ ] Add failing outbox tests proving terminal job state commits before enqueue,
  each `(source_kind, source_id, kind, selector)` is unique and non-null, a
  provider outage never changes job
  status, stale `auth_epoch`/revoked devices are skipped, and no metrics/names/
  emails appear in serialized provider requests.
- [ ] Add provider-disabled, long-outage, and flood tests for no-enqueue without
  credentials, exact global/per-selector caps under parallel observers, reminder
  coalescing, 24-hour pending/scan cutoff, terminal/ticket/receipt expiry, 1,000-
  row purge restart, bounded backup counts, and aggregate-only health counters.
  Cap/drop behavior never changes job state or prevents report polling.
- [ ] Add enhanced-push-security tests proving send and receipt calls carry the
  protected bearer, missing credentials create no outbox row, `UNAUTHORIZED`
  never falls back to an unauthenticated call, token rotation uses only the new
  credential, and health/logs expose presence/key-HMAC plus aggregate failures—
  never the token or Authorization header.
- [ ] Add worker-crash tests before send, after provider acceptance, and before
  ticket persistence, plus before/after receipt polling; expired leases retry
  safely, `awaiting_receipt` survives restart, and terminal-job scanning
  backfills a missing unique outbox row after a crash between job commit and
  observer execution.
- [ ] Test first registration against years of terminal history, token rotation,
  disable/re-enable, and a crash gap. The scanner may repair only missing outbox
  rows satisfying the registration/activation cutoff; it never backfills an old
  report. A job that finished before registration remains available by polling.
- [ ] Add additive registration/outbox/receipt tables through
  `mobile_schema.py`. Store the Expo token only where delivery requires it;
  exclude it from logs, exports, analytics, and API list responses.
- [ ] Bump the cumulative mobile backup extension to generation 5 for push
  registrations, activation watermark, environment fences/cutover journals,
  cutoff recovery-head revision, outbox rows/leases, tickets, receipts,
  aggregate drop/purge counters, owner/selector uniqueness indexes, and every
  guard/retry/status/retention timestamp column. Test generation-4 restore/
  migration, removal of each required table/index/column, cap-respecting startup
  repair, partial generation-5 rejection, and no token in manifest/log text.
- [ ] Add an optional `JobManager` completion observer. `_run` persists terminal
  state, then invokes observers through exception isolation. The observer writes
  only a generic outbox kind and owned job ID; provider calls happen in the
  outbox thread.
- [ ] Implement four generic kinds: analysis ready (“Your swing analysis is
  ready.”), re-film (“A quick re-film is needed.”), optional practice reminder
  (“Ready for your next practice check-in.”), and security notice (“A new device
  signed in to your CaddieInsight account.”). Data contains only allowlisted
  kind/path; the client refetches authorization/detail after opening. Enqueue a
  security notice to other active devices when a new installation registers.
- [ ] Add `httpx>=0.27,<1` to the production `web` extra and test a container
  import of `ExpoPushProvider`; `httpx` must not remain dev-only.
- [ ] Add `EXPO_ACCESS_TOKEN` as an optional secret plus the public project/
  envelope/skew settings above and document that zero
  configured credentials means registration can exist but delivery stays
  disabled/healthy with no outbox enqueue. Add the cap/age/retention/purge
  settings above to defaults, shipped config, health readback, and environment/
  deployment docs with strict startup bounds. Never commit Expo/Apple/Google
  credentials.
- [ ] Persist the first-enable push watermark transactionally and expose only its
  configured/not-configured status in health. Registration updates preserve the
  original cutoff; deleting then re-registering creates a new cutoff.
- [ ] Run `python -m pytest tests/test_mobile_push.py tests/test_push_outbox.py tests/test_mobile_push_cutover.py tests/test_access_log_privacy.py tests/test_retention_disk.py tests/test_backups.py -q`; expect all pass.
- [ ] Regenerate OpenAPI and commit:
  `git commit -m "feat: add private mobile completion push"`.

## Task 8: Add native telemetry and production activation gates

**Files:**

- Modify: `swinglab/api/errors.py`
- Create: `swinglab/web/mobile_telemetry.py`
- Modify: `swinglab/web/throttle.py`
- Modify: `swinglab/web/mobile_schema.py`
- Modify: `swinglab/api/contracts.py`
- Modify: `swinglab/api/mobile_routes.py`
- Modify: `swinglab/web/users.py:409-422,6200-6400`
- Modify: `swinglab/backups/core.py`
- Modify: `swinglab/config.py`
- Modify: `config.yaml`
- Modify: `swinglab/web/app.py:777-812,4350-4385,4614-4697`
- Modify: `docs/api/openapi-v1.json`
- Modify: `Dockerfile`
- Create: `scripts/write_mobile_build_identity.py`
- Modify: `swinglab/kpis.py`
- Modify: `docs/deployment.md`
- Modify: `docs/environment.md`
- Modify: `pyproject.toml`
- Create: `tests/test_mobile_telemetry.py`
- Modify: `tests/test_mobile_privacy_api.py`
- Modify: `tests/test_privacy_erasure_ledger.py`
- Create: `scripts/run_mobile_docker_smokes.py`
- Create: `tests/integration/recovery_https_fake.py`
- Create: `tests/fixtures/mobile-smoke-flags-off.yaml`
- Modify: `tests/test_mobile_rate_limits.py`
- Modify: `tests/test_credential_mutation_guard.py`
- Modify: `tests/test_mobile_api_errors.py`
- Modify: `tests/test_admin_kpis.py`
- Modify: `tests/test_ops.py`
- Modify: `tests/test_backups.py`
- Modify: `tests/test_foundation_contracts.py`
- Modify: `.github/workflows/ci.yml`
- Create: `tests/test_mobile_docker_smokes.py`
- Create: `tests/test_mobile_build_identity.py`

**Interfaces:**

- `POST /api/v1/mobile/events` requires bearer auth plus `Idempotency-Key` and
  accepts only a closed event enum, a discriminated `MobileEventEntity`,
  `platform`, app version, coarse network class, duration bucket, and failure code
  in at most 16 KiB. Entity kinds are exactly `current_auth`, `upload`, `session`,
  and `proof_cycle_target`; the latter carries the owned baseline session, target
  fingerprint, and drill ID. A fixed event-to-entity-kind table rejects every
  irrelevant combination. `current_auth` resolves server-side to the current
  token row and is valid only for `auth_completed`; upload/session/target entities
  are looked up under the bearer owner/current history epoch before admission.
  Free-form metadata and caller-defined entity strings are forbidden. Throttle
  is 60 accepted attempts/minute per bearer
  selector and 300/hour per IP; 429 includes bounded `Retry-After`.
- Telemetry calls Task 3's atomic `KeyedThrottle.consume_many` once with distinct
  `telemetry-selector` and `telemetry-client-ip` domains. It stores only
  versioned HMAC pairs, never raw selector/IP, counts across active key versions,
  and retains rate rows no longer than 24 hours even though event rows retain 90
  days. Both slots must be atomically admitted before an event receipt can write;
  parallel limit crossings cannot over-admit.
- Existing browser `/api/v1/events` remains cookie/same-origin only.
- `tests/integration/recovery_https_fake.py` is a deterministic, test-only HTTPS
  process implementing exactly the recovery ledger's least-privilege conditional
  `PUT`/`GET`/`HEAD` object surface, ETag, `If-Match`, and `If-None-Match`; it has
  no list/delete API. It generates an ephemeral CA/server keypair at runtime with
  a pinned test-only `cryptography>=46,<47` dependency. No private key, provider
  credential, or HTTP fallback is committed or included in the production image.
- The production image bakes `/app/mobile-build-identity.json` from the canonical
  tracked OpenAPI bytes and an exact build-time source commit. Local/protected CI
  supplies `CADDIEINSIGHT_SOURCE_COMMIT`; a Railway GitHub-source build supplies
  provider-owned `RAILWAY_GIT_COMMIT_SHA`. The Dockerfile declares both as `ARG`
  only, never `ENV`: exactly one valid lowercase 40-hex value is required, or
  both must be byte-identical; missing/conflicting input fails the image build.
  Its closed schema is `{source_commit,mobile_api_contract_sha256}`; the
  writer validates lowercase Git SHA and 64-hex digest, writes canonical mode-0444
  JSON, and the Docker build fails if the source tree's freshly exported OpenAPI
  differs from the tracked snapshot. Staging/production startup requires this
  file, re-canonicalizes `app.openapi()` after every route is registered, and
  fails before requests if its digest differs. Local/test mode may inject an
  explicit fixture; it never guesses a commit from a mutable runtime variable.
- `/healthz` adds that baked `source_commit` and
  `mobile_api_contract_sha256`, configured deployment environment/EAS project,
  canonical `mobile_public_origin`, active sorted
  `mobile_allowed_application_ids`, and application-ID policy revision,
  non-sensitive mobile feature state, and aggregate counts for active/expired
  uploads and unclaimed push-outbox rows; it never exposes user, job, token, or
  provider identifiers.
- Mobile event replay persistence stores a versioned idempotency HMAC pair plus
  canonical request SHA-256; the public `dedupe_key` never embeds the raw key.
- The server, not device time, owns semantic deduplication. After entity ownership
  resolution it forms a non-secret canonical internal entity key and enforces one
  unique `(user_id, event, entity_kind, canonical_entity_key)` receipt. This
  second constraint survives a rotated/lost client idempotency key, reinstall,
  clock skew, UTC boundary, and offline replay; a different key for the same
  semantic action returns the existing sanitized receipt without incrementing
  aggregates. `accepted_server_utc_day` is computed once on first server receipt
  only for daily aggregation and is never caller input. Distinct next-day actions
  remain possible because they have a distinct server-owned upload/session/auth
  credential or proof-cycle target; repeat views/transitions of the same entity
  are intentionally one funnel observation. `week_two_return` additionally
  verifies server-owned cohort day 8–14 and a successful referenced action.
- Owned event rows and replay receipts retain 90 days; a daily bounded purge
  deletes at most 5,000 rows/transaction. Closed-dimension daily aggregates retain
  24 months with no user/selector/session ID. These bounds apply even if the
  authenticated client continually rotates valid idempotency keys.
- Owned event rows and receipts are classified as account data. Task 8 extends
  native export with their bounded safe fields (event, entity kind, platform/app
  version, coarse network/duration/failure dimensions, accepted time, and opaque
  receipt ID) while excluding HMACs, raw/canonical entity keys, selector/IP,
  internal request hashes, and cross-owner data. History reset atomically purges
  old-epoch upload/session/proof-target events and receipts before terminal 204;
  account-level `current_auth` records may follow their disclosed 90-day account
  retention. Account deletion registers a mandatory `OwnerErasureExtension`
  that drains telemetry admission/writers and deletes every owned event/receipt
  before deletion completes and after restore reconciliation. Anonymous closed-
  dimension daily aggregates contain no reversible owner/entity identifier and
  remain under their disclosed aggregate retention.
- Shipped validated defaults are `mobile_events_per_minute_per_selector: 60`,
  `mobile_events_per_hour_per_ip: 300`, `mobile_event_retention_days: 90`,
  `mobile_event_aggregate_retention_days: 730`, and
  `mobile_event_purge_batch: 5000`.
- KPI output derives upload attempts/completion/failure/duplicate suppression
  from authoritative `mobile_uploads` and job transitions. Allowlisted client
  events are UX/funnel diagnostics only and can never satisfy the release upload-
  reliability gate.
- `install_mobile_error_handlers(app, mobile_route_names)` maps new-route
  `HTTPException`, `RequestValidationError`, throttling, and uncaught failures to
  `APIError` without changing any legacy route. It preserves `WWW-Authenticate`
  and `Retry-After`; 5xx receives a random non-sensitive reference ID.

- [ ] Add failing schema/privacy tests for every allowed event and rejection of
  email, names, token-shaped strings, paths, report text, metric keys, arbitrary
  metadata, invalid/cross-owner/stale-epoch entities, every invalid event/entity-
  kind pairing, and stale credentials.
- [ ] Add throttle/retention tests for per-selector and per-IP floods, exact 429/
  Retry-After, idempotent replay under the cap, 90-day event/receipt purge,
  24-month aggregate purge, batch restart, account deletion, backup row bounds,
  and proof that rotating keys cannot grow the DB outside rate/time bounds.
- [ ] Extend native export/history-reset/account-deletion tests with telemetry:
  export returns only the safe owned fields and no HMAC/internal entity/request
  key; reset removes old-epoch upload/session/proof-target rows and semantic/
  idempotency receipts while retaining only disclosed account-level auth rows;
  deletion barriers drain concurrent telemetry writers and remove every owned
  row/receipt before 204. Restore pre-reset/pre-delete snapshots through the
  recovery fence and prove erased telemetry cannot reappear; anonymous aggregate
  counts remain ownerless and cannot be joined back to the account.
- [ ] Add semantic-receipt races/tests for same action with different idempotency
  keys, parallel devices, app reinstall, device clock ±48 hours, UTC boundary,
  seven-day offline replay, process restart, and idempotency-HMAC key rotation.
  Prove the same event/entity produces one aggregate while a genuinely distinct
  server-owned next-day entity is admitted. Cover `current_auth`, upload, session,
  and proof-cycle ownership plus week-two server-cohort/action validation.
- [ ] Add parallel exact-limit, cross-domain non-equality, current/old-key
  rotation, 24-hour rate-row purge, restart, flags-off missing-key, and DB/log
  scans proving no raw selector/IP enters `mobile_rate_limit_events`. Force one
  side of the selector/IP pair to reject and inject the second insert failure;
  neither case may debit only one dimension.
- [ ] Add startup-boundary tests for all five shipped telemetry settings: exact
  defaults, minimum/maximum and non-integer rejection, `config.yaml` parity,
  environment documentation, and feature-off/on `/healthz` readback.
- [ ] Wrap event receipt/aggregate persistence in the shared owner mutation
  fence and credential lease, rechecking selector/epoch in the receipt
  transaction. Add deletion/sign-out/device-revoke barriers proving no event row
  commits after the corresponding 204.
- [ ] Add contract tests for 401, 403, cross-account 404, stale-epoch/conflict
  409, validation 422, rate-limit 429, and injected 500 across new routes. Assert
  exact `APIError` keys/codes, no raw validation input/path, documented OpenAPI
  responses, and unchanged legacy `{"detail": ...}` behavior.
- [ ] Add failing health/KPI tests for feature-off/on states, aggregate counts,
  no identifiers, and safe behavior while provider configuration is absent.
- [ ] Test build-identity generation with tracked/fresh-export byte equality,
  malformed/missing local and Railway commit args, conflicting dual args, equal
  dual args, stale snapshot, missing/image-mutable identity file,
  runtime OpenAPI drift, and exact health readback. Build the production image with
  a known commit, inspect the baked file, and prove a runtime environment override
  cannot change either value.
- [ ] Add startup/health/app-identity tests for required normalized HTTPS
  `PUBLIC_BASE_URL`, request/proxy-header spoofing, the exact closed revision-1
  application-ID policy, staging's two intentional IDs, production's sole ID,
  and rejection before side effects. Prove caller headers, a database row, and
  mutable runtime config cannot change the exposed origin or widen the policy.
- [ ] Add a KPI integrity test that injects/replays arbitrary allowed client
  upload events and proves the hard upload denominator/numerator remain derived
  solely from backend reservation/job state.
- [ ] Implement a dedicated native allowlist including `auth_completed`,
  `analysis_started`, `upload_started`, `upload_resumed`, `upload_completed`,
  `upload_failed`, `upload_canceled`, `upload_duplicate_suppressed`,
  `brief_viewed`, `practice_started`, `practice_completed`,
  `matched_refilm_started`, `matched_refilm_completed`, and `week_two_return`.
  Persist the versioned idempotency HMAC pair and canonical request hash in the
  mobile receipt columns covered by one unique index and the resolved semantic
  entity columns under their independent unique index; `dedupe_key` is an opaque
  receipt identifier. Exact replay and semantic duplicate return the same
  receipt, while reuse of one idempotency key for a conflicting request returns
  409. Keep reliability failures as bounded codes, not text.
- [ ] Bump the cumulative mobile backup extension to generation 6 for mobile
  `mobile_event_receipts`, owned `mobile_events`, closed-dimension
  `mobile_event_daily_aggregates`, their retention/purge timestamps, request-
  hash/versioned-idempotency columns, owner/time/dedupe indexes, and every
  required status/dimension column. Require the closed telemetry rate-domain
  enum and its live key IDs in generation-6 counts/audit even though the shared
  rate table landed in generation 1. Test generation-5 restore/migration,
  removal of each table/index/column, partial generation-6 rejection, current-
  manifest round trip, retention-state recovery, and referenced-key audit.
- [ ] Entitlements Task 5 owns cumulative generation 7. Update
  `MOBILE_STATE_GENERATIONS` there for all four `production_review_*` tables, the
  three `mobile_review_step_up_*` tables plus review discriminator/owner columns on
  `mobile_step_up_tokens`, indexes/triggers/journals, credential/build/revision columns, `store_review`
  user marker, and referenced review HMAC domains. Test generation-6 restore/
  migration, every partial-generation rejection, scratch restore, and convergence
  to the newest off-volume `review_access_revision` before requests/workers.
- [ ] Add default-off capability activation guidance: deploy code with all flags
  off; verify migrations/health/OpenAPI; then enable and read back native auth →
  resources → profile writes → practice writes → device management → resumable
  upload → privacy/export → mobile events → native-billing readiness. Keep push
  off through this generic activation sequence: Release Task 7 alone may enable
  staging push after its exact processed-preview store-artifact gate, and Release
  Task 9 owns production activation only after the staging sender cutoff. Every flag,
  including Apple/Google’s independent Plan 3 flags, has an explicit rollback/
  readback that stops new mobile work and leaves `/upload`/PWA operational only
  while the now-permanent recovery-fence store dependency is healthy. Flags do
  not remove that dependency once any token/journal/checkpoint exists. Document
  remote-store outage as a full-service fail-closed incident with alerting,
  credential/endpoint recovery, head+chain readback, and no unsafe local bypass.
- [ ] Run `python -m pytest tests/test_mobile_telemetry.py tests/test_admin_kpis.py tests/test_ops.py tests/test_first_sale_platform.py tests/test_backups.py tests/test_foundation_contracts.py -q`; expect all pass.
- [ ] Implement `scripts/run_mobile_docker_smokes.py` with two isolated modes.
  `pristine-flags-off` bind-mounts the explicit fixture config, whose browser
  history reset and every dependent mobile/Shopify privacy flag are false, into
  the production image; it asserts generation 0, zero recovery-provider I/O,
  healthy `/healthz`, and PWA smoke. `recovered-shipped-config` uses the shipped
  history-reset setting with a disposable persisted volume plus a fake
  S3-compatible least-privilege store seeded with an accepted baseline/full
  chain; it asserts immutable-record/HEAD readback occurs before healthy/PWA.
  Inject an unavailable/missing head and require container startup failure.
- [ ] For `recovered-shipped-config`, start the HTTPS fake as a host test process
  on an OS-assigned port reachable only for the test, create an isolated Docker
  bridge, and launch the app container with the explicit
  `recovery-fake:host-gateway` alias. Generate its CA/cert/key in a disposable
  directory with server-auth EKU and SAN `recovery-fake`, mount only the public
  CA read-only into the container, set `AWS_CA_BUNDLE`, and pass the exact
  `https://recovery-fake:<port>` endpoint plus fake least-privilege credentials.
  Hard-fail if the Docker engine cannot provide the host-gateway alias; never
  fall back to HTTP, disabled verification, or an unvalidated hostname. Seed the
  accepted baseline/full record chain through conditional requests, verify stale
  ETag/HTTP/untrusted-CA/wrong-hostname/list/delete attempts fail, and clean up
  the host process, container, network, volume, and ephemeral keys on success or interruption. Unit-test the
  fake protocol and harness cleanup; production code must use the same signed
  HTTPS conditional path it uses for the real provider.
- [ ] Replace CI’s legacy production-image `docker run` with the
  `pristine-flags-off` harness; keep `recovered-shipped-config` as a protected
  integration/manual gate until its fake-store runtime is deterministic enough
  for required CI. Add a contract test that parses `.github/workflows/ci.yml` and
  rejects any raw shipped-config container smoke that supplies only `PORT` or
  omits the explicit harness/config mode.
- [ ] After the final Task 8 contracts/routes are in place, deterministically run
  `python scripts/export_openapi.py --output docs/api/openapi-v1.json`, export a
  second time to a UUID-named file under `[System.IO.Path]::GetTempPath()`, and
  byte-compare the two files inside `try/finally` that deletes the temporary file
  on success, mismatch, or interruption. Commit the
  tracked snapshot with this task; a diff after a third clean export or a byte mismatch blocks
  the backend gate and the client's `api:check`.
- [ ] Run the complete pre-commit source gate:
  `python -m pytest -q`,
  a fresh deterministic export/byte comparison using another protected temporary
  path outside the worktree, and assert its cleanup. Then intentionally stage the Task 8 source plus
  tracked OpenAPI snapshot and commit
  `git commit -m "feat: gate mobile backend activation"`. The provenance image
  is never built from pre-commit or dirty Task 8 source.
- [ ] After that commit, require `git diff --quiet`, `git diff --cached --quiet`,
  and empty `git ls-files --others --exclude-standard` output; verify the Docker
  context manifest/`.dockerignore` contains no ignored local secret, database,
  media, or generated artifact. In PowerShell capture and validate once with
  `$sourceCommit = (git rev-parse HEAD).Trim(); if ($LASTEXITCODE -ne 0 -or
  $sourceCommit -cnotmatch '^[0-9a-f]{40}$') { throw 'invalid source commit' }`,
  then run
  `docker build --build-arg "CADDIEINSIGHT_SOURCE_COMMIT=$sourceCommit" --tag
  caddieinsight-mobile-smoke .`,
  `python scripts/run_mobile_docker_smokes.py --image caddieinsight-mobile-smoke
  --mode pristine-flags-off`, and the same command with
  `--mode recovered-shipped-config`. Expected result: all pass; OpenAPI bytes
  match; inspect `/app/mobile-build-identity.json` and require its source commit
  equal that exact validated Git HEAD before either smoke mode. The production
  image imports push HTTP dependencies; the explicit
  flags-off fixture makes no provider call, while shipped history-reset state is
  healthy only after verified recovery-chain readback. Any failure requires a
  source fix, a new reviewed commit, a recaptured SHA, and a full rebuild/retest;
  never amend bytes beneath an already attested commit.

## Backend plan completion gate

- [ ] Confirm `git diff main...HEAD --name-only` contains no deployment secrets,
  generated media, SQLite files, or unrelated storefront changes.
- [ ] Confirm all pre-existing route tests, passwordless/browser token tests,
  Shopify webhook/privacy tests, history-reset tests, and upload tests pass.
- [ ] Confirm the OpenAPI snapshot is deterministic on two consecutive exports.
- [ ] Confirm two startup modes separately: a pristine generation-0/no-token/no-
  checkpoint fixture with all flags off makes zero provider calls and passes PWA
  smoke; a production-state fixture with any token/journal/checkpoint requires a
  successful recovery head+chain readback before the same PWA smoke. Inject a
  remote outage and prove the latter fails before requests rather than starting
  partially or bypassing recovery.
- [ ] Record the implementation commit and verification evidence. Do not call
  the backend deployed, live, or activated until Railway deployment and live
  readback are separately authorized and completed.
