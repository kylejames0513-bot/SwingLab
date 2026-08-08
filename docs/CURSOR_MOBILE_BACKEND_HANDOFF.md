# Cursor agent resume script: CaddieInsight mobile backend

Use this file as the operating prompt for the next coding agent. Work only in the isolated worktree / branch below; do not switch to a wrapper/empty CaddieInsight checkout.

## Frozen checkpoint

- Branch: `cursor/caddieinsight-mobile-gate-4b-909d` (continues `codex/caddieinsight-mobile-implementation`)
- Main plan: `docs/superpowers/plans/2026-08-06-caddieinsight-mobile-backend-foundation.md`
- Progress ledger: `.superpowers/sdd/2026-08-06-caddieinsight-mobile-backend-foundation/progress.md`

Do not deploy, publish, change Shopify/Railway/store settings, or mutate any live provider. Full-repository and container smoke tests are reserved for final integration. Keep CaddieInsight customer-facing and preserve `swinglab` compatibility.

## Work completed

- Tasks 1–3 and Gate 4A complete and independently reviewed.
- Gate 4B (profile write): complete; independent re-review PASS.
- Gate 4C (practice evidence + schema gen 2): complete; independent re-review PASS.
- Gate 4D (devices + fenced legacy revoke): complete; independent re-review PASS.
- Task 4 finish matrix: run combined focused suites + deterministic OpenAPI; backup gen-2 fixture drift closed.
- Task 5 resumable-upload crash recovery: independently reviewed PASS.
- Task 6 privacy (step-up → export → history-reset → account-delete): complete for ordinary-customer privacy.

## Current gate: Task 7

Implement device-bound Expo push registration and a durable outbox from the plan Task 7 section. **Registration + outbox delivery + environment-fence cutover slices are landed** on this branch; receipt polling / caps / full recovery-ledger publish remain deferred.

### Task 7 progress (in this branch)

**Push registration slice** — tip `1fe0a66` / impl `a84d269` (earlier tip `46d7e13`).

- Schema generation **3**: additive `mobile_push_registrations` + `mobile_push_activation_watermarks`; restore allowlists include generation `3`; HMAC domains `push-expo-project` and `push-cutover-operation-id`.
- Config: `mobile_push_enabled: false`, `mobile_push_expo_project_id: ""`; non-secret `CADDIEINSIGHT_EXPO_PROJECT_ID` override; flag-on requires canonical UUID; flag-off tolerates blank.
- `PushRegistrationService` in `swinglab/web/push_store.py`: PUT upsert (token takeover + selector replacement), PATCH preferences, DELETE (absent → 204); credential lease + selector/epoch recheck; sign-out extension clears selector registration.
- Routes (flag check before auth; concealed when off; static `/devices/push` before `{selector}`):
  - `PUT /api/v1/devices/push`
  - `PATCH /api/v1/devices/push/preferences`
  - `DELETE /api/v1/devices/push`
- Capabilities already expose `features.push` from `mobile_push_enabled`.
- Tests: `tests/test_mobile_push.py` (12); backup/rate-limit gen bump covered.

**Push outbox delivery slice** — tip `815a04d` (impl `8b32eb3`, race/test harden `8f72512`, unregister/token dead-letter `815a04d`).

- Schema generation **4**: additive `mobile_push_outbox`; restore allowlists include generation `4`.
- `swinglab/web/push_delivery.py`: `FakeExpoPushProvider` / `ExpoPushProvider`, `PushOutboxStore`, `PushOutboxWorker`, `attach_job_push_observer`.
- Missing `EXPO_ACCESS_TOKEN` → no enqueue/send; jobs still succeed. Payload `ttl=900`. Unique `(source_kind, source_id, kind, selector)`.
- `JobManager.add_completion_observer`: DONE → `analysis_ready` enqueue after terminal `_save`.
- Sign-out / DELETE unregister / token-rotating PUT dead-letter pending/leased outbox and clear leases; worker completion CAS on `status='leased' AND lease_owner=?`; drain binds to live registration token (mismatch → dead).
- Expired pending/leased rows are marked `dead` on drain. Config envelope/skew validated when push on; outbox global/per-selector caps are **config placeholders only** (not enforced yet).
- `httpx` added to the `web` extra. App wires outbox store/worker + observer when push enabled.
- Tests: `tests/test_push_outbox.py` (10) including outage, FAILED-no-enqueue, leased+sign-out race, TTL expiry, unregister→re-register, token rotation.

**Push environment fence cutover slice** — tip `123fe18` (impl `c8e165b`, CLI/backup fix `568175b`).

- Schema generation **5**: additive `mobile_push_environment_fences` + `mobile_push_cutover_operations`; restore allowlists include generation `5`; detect/ensure stepwise after gen 4.
- `swinglab/web/push_cutover.py`: `ensure_open_fence` / `require_open_fence` / `fence_status` / `close_fence` / `purge_fence`; fail-closed never-reopen; close terminalizes pending/leased outbox; purge waits `provider_safe_after` then deletes registrations+outbox while keeping fence closed.
- Admission: register/preferences require open fence; enqueue returns false when closed; worker dead-letters without send when fence not open; flag-on startup calls `ensure_open_fence` (fails closed if previously closed).
- CLI: `swinglab mobile-push-cutover status|close|purge` with `--sessions-dir` / `--environment` / `--expo-project-id`; close/purge `--operation-id` + dry-run default / `--apply`; rejects env/project mismatch vs server config.
- Optional `ledger`+`keyring` kwargs on close can publish `PushEnvironmentCutoffEvent`; without them local close/purge still completes (full recovery publish deferred).
- Tests: `tests/test_mobile_push_cutover.py` (7); focused suite with outbox/push/rate-limits/backups green.

**Still deferred (next Task 7 slices):**

- Full mandatory recovery-ledger `PushEnvironmentCutoff` publish/readback on every close/purge (optional callback path exists)
- Receipt polling / `awaiting_receipt` lifecycle / `PushDeliveryGuard` drain-before-sign-out
- Outbox caps/flood/purge + terminal-job scanner backfill
- Practice-reminder enqueue; refilm kind classification; security notice on new device
- Deploy or mutate live providers

**Earlier deferrals still open (non-blocking):**

- Store-review step-up variant / full review-scoped account deletion.
- Broader mobile backup registration for step-up/export/erasure/capacity/upload tables (partially advanced by gen-3/4/5 push tables).
- Full durable download-admission slot/byte budgets.
- Full machine-checked writer inventory / every OwnerErasureExtension if oversized.

## Standing decisions and hazards

- Preserve the existing five-device cap.
- Task 5 owns exact typed analysis failure persistence/classification and supported-build admission.
- Task 6 owns later reset/delete erasure record kinds.
- Recovery baseline initialization is offline and approval-gated before fail-closed production startup.
- Preserve Railway's one-replica SQLite contract until durable state/job coordination is externalized.
- Never use `api_payload()` or wholesale `Job.as_dict()` in native resources.
- Do not kill or interact with unrelated Python processes.
- Register `/api/v1/devices/push` before `/api/v1/devices/{selector}` so `push` is never treated as a selector.
