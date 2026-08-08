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

The server-owned resumable-upload policy is also explicit, even while its
mutation route is disabled:

- `web.mobile_upload_chunk_mb: 5` (valid range 1-64 MiB)
- `web.mobile_active_uploads_per_user: 2` (valid range 1-10)
- `web.mobile_upload_ttl_seconds: 86400` (valid range 60-604800)

Invalid booleans or numeric bounds stop application startup. The native chunk
policy does not change the legacy web upload reader, which remains at 1 MiB per
read.

The generated contract is frozen in `docs/api/openapi-v1.json`. After a
deliberate contract change, regenerate it with:

```powershell
python scripts/export_openapi.py --output docs/api/openapi-v1.json
```
