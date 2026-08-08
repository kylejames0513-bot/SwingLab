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

**Privacy export** — independently reviewed **PASS** at `e300a82` (never unlink ready ZIP on lost lease).

- `POST /api/v1/privacy/exports` consumes a `data_export` step-up token + `Idempotency-Key` → 202 pending receipt (exact replay).
- Leased `PrivacyExportWorker` builds ZIP under `sessions_dir/.privacy_exports` (profile + sessions summary); rechecks `history_epoch` before publish.
- `GET /api/v1/privacy/exports/{id}` and `.../download` (same-origin stream, exact `Content-Length`, Range rejected).
- Tests: `tests/test_mobile_privacy_export_api.py`.

**History reset + account deletion** — implementation at `67d1fe1`; focused suites green (pending independent review).

- Recovery-fence `history_reset` / `account_delete` kinds + restore reconcilers.
- Durable journals/receipts in UserStore; `PrivacyErasureService` drives phases.
- `POST /api/v1/privacy/history-reset` (`step_up_token` + `expected_history_epoch` + `Idempotency-Key`) → 202/204; pre-auth exact replay.
- `DELETE /api/v1/account` (`step_up_token` + `Idempotency-Key`) → 202/204; pre-auth replay after credential revoke.
- Browser `POST /account/history/delete` uses the same journal/fence authority.
- Tests: `tests/test_mobile_privacy_history_reset_api.py`, `tests/test_mobile_account_delete_api.py`, plus browser/core regressions.

**Still next after Task 6 PASS:** review step-up (Entitlements); gen-3 backup registration; full download-admission budgets; full OwnerErasureExtension inventory if not deferred.

**Deferrals (acceptable for Task 6 PASS):**

- Store-review step-up variant / full review-scoped account deletion.
- Gen-3 mobile backup registration for step-up/export/erasure/capacity/upload tables.
- Full durable download-admission slot/byte budgets (minimal in-process guard may remain).
- Full machine-checked writer inventory / every OwnerErasureExtension if oversized — ordinary customer delete + history reset must work.

## Standing decisions and hazards

- Preserve the existing five-device cap.
- Task 5 owns exact typed analysis failure persistence/classification and supported-build admission.
- Task 6 owns later reset/delete erasure record kinds.
- Recovery baseline initialization is offline and approval-gated before fail-closed production startup.
- Preserve Railway's one-replica SQLite contract until durable state/job coordination is externalized.
- Never use `api_payload()` or wholesale `Job.as_dict()` in native resources.
- Do not kill or interact with unrelated Python processes.
