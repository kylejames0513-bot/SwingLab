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

## Current gate: Task 5

Implement durable resumable uploads with atomic job completion from the plan Task 5 section. Do not start Task 6 until Task 5 is implemented, committed, and independently reviewed.

### Task 5 progress (in this branch)

Implemented and committed:

- Typed FFmpeg failure kinds (`FFmpegMediaError`/`FFmpegRuntimeError`/`FFmpegStorageError`) classified by call site/return code/signal/timeout/`errno`, never by text.
- Server-owned analysis-failure classifier (`swinglab/web/analysis_failures.py`) mapping exceptions to the closed `AnalysisFailureCode` set with retryability and customer-safe messages; wired into `JobManager._run` and surfaced (failure code / retryable / retry-expiry / remaining-retry) in `MobileSessionResponse`.
- Cross-process `SessionMaintenanceLock` and the durable `StorageCapacityLedger` (logical reserved cap + filesystem free floor, atomic upload_part→job_source transfer, exactly-once release, filesystem reconcile).
- Closed upload/retry contracts and the durable `ResumableUploadManager` (create/status/patch/complete/abort + crash recovery, per-upload keyed lock, versioned idempotency HMAC pairs, comparison hook, seven-day abort receipts).
- HTTP wiring behind `web.mobile_resumable_upload_enabled` (default off): `POST /api/v1/uploads`, `GET/PATCH /api/v1/uploads/{id}`, `POST /api/v1/uploads/{id}/complete`, `DELETE /api/v1/uploads/{id}`, each with `Idempotency-Key` (where applicable) and `CredentialMutationGuard` admission. `create_app` constructs the manager unconditionally so recovery runs even when the feature is off. Frozen OpenAPI regenerated.
- New config bounds (`mobile_analysis_retry_window_seconds`, `mobile_analysis_retry_max_attempts`, `mobile_upload_global_max_reserved_bytes`, `mobile_upload_min_filesystem_free_bytes`) with strict enable-time validation.

Deferrals still open before Task 5 can be called complete:

- Generation-3 mobile backup registration of the durable upload/capacity/retry objects and their restore/migration tests. The upload/capacity tables currently live outside the closed mobile-state inventory (`resumable_uploads`, `resumable_upload_abort_receipts`, `storage_capacity_allocations`) and are reconciled from filesystem truth on restart rather than restored from backup.
- The `POST` analysis retry and retry-source-discard endpoints/journals (the classifier and session serialization already expose retryability, but the retry mutation route and its discard journal are not yet wired).
- Server-side matched/new-context comparison resolution: with no comparison resolver injected in `create_app`, a non-null comparison claim conservatively returns 409 `comparison_conflict`; null-comparison uploads work fully.

## Standing decisions and hazards

- Preserve the existing five-device cap.
- Task 5 owns exact typed analysis failure persistence/classification and supported-build admission.
- Task 6 owns later reset/delete erasure record kinds.
- Recovery baseline initialization is offline and approval-gated before fail-closed production startup.
- Preserve Railway's one-replica SQLite contract until durable state/job coordination is externalized.
- Never use `api_payload()` or wholesale `Job.as_dict()` in native resources.
- Do not kill or interact with unrelated Python processes.
