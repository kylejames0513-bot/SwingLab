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

## Current gate: Task 6

Task 5 resumable-upload crash recovery is implemented, committed, and independently reviewed **PASS** at tip `99bcd36` (documented deferrals: gen-3 backup registration, analysis-retry HTTP/discard journals, comparison resolver remain open and are tracked separately).

Implement native privacy controls and safe account deletion from the plan Task 6 section. First vertical slice: email step-up start/exchange behind `mobile_privacy_enabled`, then exports, history-reset journal wrap, and account deletion.

### Task 6 progress (in this branch)

**Email step-up** — independently reviewed **PASS** at `c7cf7bd` (60s same-PKCE resend; versioned `STEP_UP_TOKEN_VERIFIER` HMAC).

- `POST /api/v1/auth/step-up/start` / `POST /api/v1/auth/step-up/exchange` behind `mobile_privacy_enabled`.
- Tests: `tests/test_mobile_privacy_api.py`.

**Privacy export** — landed at `fe35762` (pending independent review).

- `POST /api/v1/privacy/exports` consumes a `data_export` step-up token + `Idempotency-Key` → 202 pending receipt (exact replay).
- Leased `PrivacyExportWorker` builds ZIP under `sessions_dir/.privacy_exports` (profile + sessions summary); rechecks `history_epoch` before publish.
- `GET /api/v1/privacy/exports/{id}` and `.../download` (same-origin stream, exact `Content-Length`, Range rejected).
- Tests: `tests/test_mobile_privacy_export_api.py` (19 passed).

**Still next:** history-reset journal wrap, then account deletion; review step-up; full download-admission budgets if not yet complete; gen-3 backup registration for step-up/export tables.

**Deferrals:**

- Store-review step-up variant.
- Gen-3 mobile backup registration for step-up/export/capacity/upload tables (outside closed inventory for now).
- Full durable download-admission slot/byte budgets if the export slice shipped a minimal guard — confirm in review.

## Standing decisions and hazards

- Preserve the existing five-device cap.
- Task 5 owns exact typed analysis failure persistence/classification and supported-build admission.
- Task 6 owns later reset/delete erasure record kinds.
- Recovery baseline initialization is offline and approval-gated before fail-closed production startup.
- Preserve Railway's one-replica SQLite contract until durable state/job coordination is externalized.
- Never use `api_payload()` or wholesale `Job.as_dict()` in native resources.
- Do not kill or interact with unrelated Python processes.
