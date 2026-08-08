# Native coaching resources

The version 1 native read API publishes server-owned policy and coaching state.
All routes require an existing account session or a valid mobile Bearer token,
return `Cache-Control: no-store` and `Pragma: no-cache`, and use the closed
`APIError` response on named native routes. If an `Authorization` header is
present but invalid, it is rejected; the request never falls back to a browser
cookie.

## Read rollout

`web.mobile_resources_enabled` is the one default-off gate for these reads:

- `GET /api/v1/capabilities`
- `GET /api/v1/progress`
- `GET /api/v1/mobile/sessions`
- `GET /api/v1/mobile/sessions/{session_id}`
- `GET /api/v1/mobile/sessions/{session_id}/brief`
- `GET /api/v1/mobile/today`

The gate is checked before authentication, database reads, or filesystem work.
While it is false, every route returns the same no-store 404 and performs no
resource work. Enabling it enables no mutation. Existing browser compatibility
routes under `/api/v1/sessions*` and `/api/v1/today` keep their existing payloads
and are not native client contracts.

Responses are owned by the authenticated account and rechecked under the
history-delivery guard. Session JSON is allowlisted: it does not contain logs,
errors, commands, tracebacks, report HTML, filesystem paths, provider data, or
arbitrary artifact URLs. Briefs contain bounded structured coaching fields.
Progress and Brief share one exact Proof Cycle target shape:
`baseline_session_id`, `target_fingerprint`, `drill_id`, `club`, `hand`, and
`angle`. Today derives cohort day from the server clock and the earliest current
coaching-ready session, never device time.

## Independent feature flags

Every native mutation family remains independently default-off:

- `web.mobile_profile_writes_enabled`
- `web.mobile_practice_writes_enabled`
- `web.mobile_device_management_enabled`
- `web.mobile_resumable_upload_enabled`
- `web.mobile_privacy_enabled`
- `web.mobile_events_enabled`
- `web.mobile_push_enabled`
- `web.mobile_native_billing_enabled`

Capabilities publish only each resolved boolean. A true value does not bypass
that feature's own route, authentication, recovery, or rollout requirements.

## Profile write

`PUT /api/v1/mobile/profile` is the additive native profile mutation. It is gated
only by `web.mobile_profile_writes_enabled` (default off) and is independent of
`mobile_resources_enabled`. While the write flag is false, the route returns the
same no-store 404 before bearer authentication, body validation, database work,
or filesystem access.

When enabled:

- Authentication is strict Bearer only. Cookie-only requests are rejected, and an
  invalid `Authorization` header never falls back to a browser session cookie.
- The generated `ProfileUpdateRequest` body is closed (`extra=forbid`). Required
  fields include normalized `display_name` (1–50 characters after NFKC /
  whitespace / control validation), `primary_goal`, `preferred_club`, closed
  practice/hand/angle literals, strict booleans `reduced_motion` and
  `marketing_email_opt_in`, and nonnegative `expected_history_epoch`.
- `display_name`, `primary_goal`, and `preferred_club` are required so returned
  `is_complete` matches the browser contract. `marketing_email_opt_in: false` is
  valid and is never treated as inferred consent or as part of completion.
- The write enters `CredentialMutationGuard`, uses one `BEGIN IMMEDIATE`
  transaction, and rechecks selector activity, `auth_epoch`, account deletion,
  ownership, and exact `expected_history_epoch` immediately before upsert. A
  revoked, deleted, or history-reset losing race never recreates a profile.
- Success returns typed `ProfileResponse`. Revoked/deleted identity maps to the
  generic typed 401/404 contract; history-epoch conflict is typed 409. All
  native success and error responses set `Cache-Control: no-store` and
  `Pragma: no-cache`.

The legacy browser route `PUT /api/v1/profile` keeps its existing cookie/bearer
validation order, bodies, and errors. The native route does not call that
handler.

## Practice evidence

`POST /api/v1/practice-evidence` is the additive native practice mutation. It is
gated only by `web.mobile_practice_writes_enabled` (default off). While the flag
is false, the route returns the same no-store 404 before bearer authentication,
body validation, Idempotency-Key parsing, database work, or filesystem access.

When enabled:

- Authentication is strict Bearer only; cookie-only and invalid Authorization
  never fall back to a browser cookie.
- Requests require exactly one 128-bit hex `Idempotency-Key` header and a closed
  `PracticeEvidenceRequest` body (`extra=forbid`) including the current owned
  Proof Cycle target triple, `minutes`/`outcome`/`reps`, optional feel fields,
  and `expected_history_epoch`.
- Exact replay of the same key and body returns the same
  `PracticeEvidenceReceipt`. Reusing a key with a different body is typed 409
  (`idempotency_conflict`). History-epoch mismatch is typed 409
  (`history_epoch_conflict`). Missing/stale targets fail closed as typed 404.
- The write enters `CredentialMutationGuard`, uses one `BEGIN IMMEDIATE`
  transaction, and atomically upserts `proof_cycle_practice_evidence` plus
  `mobile_practice_evidence_details` (mobile schema generation 2).

Legacy `POST /api/v1/practice-checkins` keeps its `{session_id}` body and errors.

## Device management

`GET /api/v1/devices` and `DELETE /api/v1/devices/{selector}` are the additive
native device-management surface. They are gated only by
`web.mobile_device_management_enabled` (default off). While the flag is false,
both routes return the same no-store 404 before bearer authentication, body
validation, `Idempotency-Key` parsing, database work, or any write.

When enabled:

- Authentication is strict Bearer only; cookie-only and an invalid
  `Authorization` header never fall back to a browser cookie.
- `GET /api/v1/devices` returns the owner's closed `DeviceListResponse`
  (`resource_version` plus a list of `MobileTokenMetadata`), never a secret.
- `DELETE /api/v1/devices/{selector}` requires exactly one 128-bit hex
  `Idempotency-Key` header. It runs a recovery-fenced revocation that journals
  to `mobile_device_revoke_journals` and, on completion, writes
  `mobile_device_revoke_receipts`. The phases are
  `prepared -> recovery_fenced -> extensions_closed -> token_revoked ->
  complete`, mirroring native sign-out.
- The revoke enters `CredentialMutationGuard`. A self-revoke fences the caller's
  own selector through `validate_and_close_caller`, so a revoked bearer can
  still replay its exact `Idempotency-Key` to a terminal `204`. An
  other-device revoke fences only the target and keeps the initiator's lease
  valid.
- The route returns `202 {status:"pending", retry_after_seconds}` with a
  `Retry-After` header until the `TokenRevokeEvent` is published and read back
  and any selector-bound extensions drain, then `204`. A recovery publish
  outage returns a durable `202` and never a local-only `204`.
- Self-revocation replay recognition precedes ordinary bearer authentication so
  a lost `204` is recoverable; unrelated credentials disclose nothing.

The legacy browser routes under `/api/v1/mobile-tokens` keep their cookie /
same-origin authentication, their `201` issue body, their `200`
`{resource_version, revoked}` revoke body, and their `404` cross-owner
behavior. Their revoke no longer performs a local-only delete: it routes
through the same recovery-fenced service. When the fence is unready or a
publish outage occurs, the browser revoke returns `503` with the legacy
`{"detail": ...}` shape and never a success that only the local database saw.

Startup fails closed: when `web.mobile_device_management_enabled` is true,
`create_app` requires a valid recovery-fence readiness (a configured keyring
and recovery publisher), mirroring native authentication.

## Resumable uploads

The durable resumable-upload surface is gated only by
`web.mobile_resumable_upload_enabled` (default off). While the flag is false,
every upload route returns the same no-store 404 before bearer authentication,
`Idempotency-Key` parsing, database work, or filesystem access. Crash recovery
for any in-flight reservation still runs at startup even while the feature is
disabled, so a flag flip never resumes onto unconverged state.

Routes (all strict Bearer, `Cache-Control: no-store`, closed `APIError`):

- `POST /api/v1/uploads` reserves one upload. Requires exactly one 128-bit hex
  `Idempotency-Key` and a closed `UploadCreateRequest` body (`source_name`,
  lowercase-hex `file_sha256`, positive `file_bytes`, `club`/`hand`/`angle`,
  optional `level`, optional comparison triple, and `expected_history_epoch`).
  Returns `201 UploadReservationResponse` with the acknowledged `offset`,
  `file_bytes`, `chunk_bytes`, and `expires_at`. Exact replay of the same key
  and body returns the same reservation; reuse with a different body is typed
  409 (`idempotency_conflict`). History-epoch mismatch is 409
  (`history_epoch_conflict`); a comparison claim that no longer matches the
  current owned Proof Cycle assignment is 409 (`comparison_conflict`); exceeding
  the per-user active cap is 409 (`upload_conflict`). Capacity exhaustion is a
  retryable 507 (`insufficient_storage`) with `Retry-After`.
- `GET /api/v1/uploads/{upload_id}` returns the owned reservation status. A
  reservation being repaired is 409 (`upload_repairing`, retryable).
- `PATCH /api/v1/uploads/{upload_id}` appends one chunk. The body is the raw
  bytes (`application/offset+octet-stream`); `Upload-Offset` must equal the
  acknowledged offset and `Upload-Checksum` is the base64 SHA-256 of the chunk.
  Returns `200 UploadReservationResponse` with the new acknowledged offset.
  An offset mismatch is 409 (`offset_mismatch`) and echoes the acknowledged
  `Upload-Offset` header; an oversized chunk or one beyond the declared size is
  413 (`chunk_too_large`); a bad chunk digest is 422 (`checksum_mismatch`) and
  never advances; an expired reservation is 410 (`upload_expired`); a
  concurrent operation on the same upload is 409 (`upload_busy`, retryable).
- `POST /api/v1/uploads/{upload_id}/complete` verifies the full digest and
  atomically publishes exactly one queued job, returning
  `200 UploadCompleteResponse` (`job` as a `MobileSessionResponse`, `replayed`).
  Re-completing an already-completed upload replays the same job with
  `replayed: true`. A digest mismatch fails the reservation with no job.
- `DELETE /api/v1/uploads/{upload_id}` aborts, releasing capacity and returning
  `204`. It requires its own 128-bit hex `Idempotency-Key`; an exact replay
  returns `204` and a different key against the abort receipt is 409
  (`idempotency_conflict`). A completed upload cannot be aborted (409).

Every create/PATCH/complete/abort enters `CredentialMutationGuard`, so a
concurrent sign-out, revoke, or history reset closes the credential and the
route returns the generic 401 rather than mutating durable state.

The server-owned resumable-upload policy is explicit even while the route is
disabled:

- `web.mobile_upload_chunk_mb: 5` (valid range 1-64 MiB)
- `web.mobile_active_uploads_per_user: 2` (valid range 1-10)
- `web.mobile_upload_ttl_seconds: 86400` (valid range 60-604800)
- `web.mobile_upload_global_max_reserved_bytes` and
  `web.mobile_upload_min_filesystem_free_bytes` ship as `0` and must be measured
  strictly-positive values before `mobile_resumable_upload_enabled` is turned on.

Invalid booleans or numeric bounds stop application startup. The native chunk
policy does not change the legacy web upload reader, which remains at 1 MiB per
read. Upload reservations and the storage-capacity ledger are transient
operational tables (`resumable_uploads`, `resumable_upload_abort_receipts`,
`storage_capacity_allocations`) that are reconciled from filesystem truth on
restart; they are intentionally outside the closed mobile-state backup
inventory, so registering them for backup remains a follow-up.

The generated contract is frozen in `docs/api/openapi-v1.json`. After a
deliberate contract change, regenerate it with:

```powershell
python scripts/export_openapi.py --output docs/api/openapi-v1.json
```
