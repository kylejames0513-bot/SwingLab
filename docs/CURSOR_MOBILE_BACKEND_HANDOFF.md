# Cursor agent resume script: CaddieInsight mobile backend

Use this file as the operating prompt for the next coding agent. Work only in the isolated worktree / branch below; do not switch to a wrapper/empty CaddieInsight checkout.

## Frozen checkpoint

- Branch: `cursor/caddieinsight-mobile-gate-4b-909d` (continues `codex/caddieinsight-mobile-implementation`)
- Gate 4B tip: see latest commit on this branch
- Main plan: `docs/superpowers/plans/2026-08-06-caddieinsight-mobile-backend-foundation.md`
- Progress ledger: `.superpowers/sdd/2026-08-06-caddieinsight-mobile-backend-foundation/progress.md`
- Reports: `.superpowers/sdd/2026-08-06-caddieinsight-mobile-backend-foundation/task-*-report.md`

Do not deploy, publish, change Shopify/Railway/store settings, or mutate any live provider. Full-repository and container smoke tests are reserved for final integration. Keep CaddieInsight customer-facing and preserve `swinglab` compatibility.

## Work completed

- Tasks 1 and 2 are complete and independently reviewed.
- Task 3A through 3F are complete and independently reviewed.
- Gate 4A is complete at commits `258496a` and `c5c6e39`.
- Gate 4B is complete:
  - Additive `PUT /api/v1/mobile/profile` behind default-off `mobile_profile_writes_enabled`
  - Closed `ProfileUpdateRequest` / `ProfileResponse`; post-normalize `display_name` length
  - Strict bearer-only; flag-off 404 before auth/body/DB/writes
  - `CredentialMutationGuard` + `BEGIN IMMEDIATE`; deleted owner at epoch fence → 401 (never 409)
  - Typed 401/404/409; no-store headers; legacy `PUT /api/v1/profile` byte parity preserved
  - Docs in `docs/mobile-api-resources.md`; OpenAPI regenerated
  - Focused profile suite: 17 passed; adjacent aggregate green

## Current gate: 4C — practice evidence

Do not start Gate 4D until 4C is implemented, committed, and independently reviewed.

Implement Gate 4C only:

1. Strict bearer/idempotent `POST /api/v1/practice-evidence` behind `mobile_practice_writes_enabled` (default-off).
2. Closed request/receipt contracts; `Idempotency-Key`; verify current owned Proof Cycle target.
3. `UserStore.record_mobile_practice_evidence` writes `proof_cycle_practice_evidence` plus `mobile_practice_evidence_details` atomically under `CredentialMutationGuard`.
4. Add details table to history reset, account deletion, privacy export, backup counts, orphan/HMAC scans.
5. Bump cumulative mobile backup extension to generation 2 with generation 0/1 restore compatibility.
6. Keep legacy `POST /api/v1/practice-checkins` byte-compatible.
7. Regenerate `docs/api/openapi-v1.json` deterministically.

## After Gate 4C

- Gate 4D: recovery-fenced device list/revoke and legacy `/api/v1/mobile-tokens` parity.
- Finish Task 4 with the combined focused matrix and deterministic OpenAPI check.
- Continue Tasks 5–8 sequentially from the plan.

## Standing decisions and hazards

- Preserve the existing five-device cap.
- Task 5 owns exact typed analysis failure persistence/classification and supported-build admission.
- Task 6 owns later reset/delete erasure record kinds.
- Recovery baseline initialization is offline and approval-gated before fail-closed production startup.
- Preserve Railway's one-replica SQLite contract until durable state/job coordination is externalized.
- Never use `api_payload()` or wholesale `Job.as_dict()` in native resources.
- Do not kill or interact with unrelated Python processes.
