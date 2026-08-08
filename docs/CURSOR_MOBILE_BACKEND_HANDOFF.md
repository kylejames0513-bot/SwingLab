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

Implement device-bound Expo push registration and a durable outbox from the plan Task 7 section. **First vertical slice (registration only) is landed** on this branch; delivery/outbox/cutover remain deferred.

### Task 7 progress (in this branch)

**Push registration slice** — tip after OpenAPI/handoff commit (registration impl `a84d269`).

- Schema generation **3**: additive `mobile_push_registrations` + `mobile_push_activation_watermarks`; restore allowlists include generation `3`; HMAC domains `push-expo-project` and `push-cutover-operation-id`.
- Config: `mobile_push_enabled: false`, `mobile_push_expo_project_id: ""`; non-secret `CADDIEINSIGHT_EXPO_PROJECT_ID` override; flag-on requires canonical UUID; flag-off tolerates blank.
- `PushRegistrationService` in `swinglab/web/push_store.py`: PUT upsert (token takeover + selector replacement), PATCH preferences, DELETE (absent → 204); credential lease + selector/epoch recheck; sign-out extension clears selector registration.
- Routes (flag check before auth; concealed when off; static `/devices/push` before `{selector}`):
  - `PUT /api/v1/devices/push`
  - `PATCH /api/v1/devices/push/preferences`
  - `DELETE /api/v1/devices/push`
- Capabilities already expose `features.push` from `mobile_push_enabled`.
- Tests: `tests/test_mobile_push.py` (12); backup/rate-limit gen bump covered.

**Still deferred (next Task 7 slices — do not implement in the registration-only gate):**

- `PushOutboxWorker` / Expo HTTP delivery / `EXPO_ACCESS_TOKEN`
- Environment fence cutover CLI / `PushEnvironmentCutoff` publishing
- JobManager completion observer / reminder enqueue
- Full plan “generation 5” numbering for outbox/fences (this slice used code gen **3**)
- Envelope/skew send settings beyond project-id config
- Deploy or mutate live providers

**Earlier deferrals still open (non-blocking):**

- Store-review step-up variant / full review-scoped account deletion.
- Broader mobile backup registration for step-up/export/erasure/capacity/upload tables (partially advanced by gen-3 push tables).
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
