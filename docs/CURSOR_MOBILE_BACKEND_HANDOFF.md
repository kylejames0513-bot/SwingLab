# Cursor agent resume script: CaddieInsight mobile backend

Use this file as the operating prompt for the next coding agent. Work only in the isolated worktree below; do not switch to a wrapper/empty CaddieInsight checkout.

## Frozen checkpoint

- Worktree: `C:\Users\mahon\OneDrive\Desktop\SwingLab\.worktrees\caddieinsight-mobile-implementation`
- Branch: `codex/caddieinsight-mobile-implementation`
- Last functional code commit: `c5c6e39de8dfd61be8032df8748ff225b3f1ff50` (`fix: harden native read contracts`)
- Status when paused: clean; no partial Gate 4B implementation and no test process remained.
- Main plan: `docs/superpowers/plans/2026-08-06-caddieinsight-mobile-backend-foundation.md`
- Progress ledger: `.superpowers/sdd/2026-08-06-caddieinsight-mobile-backend-foundation/progress.md`
- Reports: `.superpowers/sdd/2026-08-06-caddieinsight-mobile-backend-foundation/task-*-report.md`
- Exact Python: `.superpowers/sdd/2026-08-06-caddieinsight-mobile-backend-foundation/.venv/Scripts/python.exe`
- Git Bash if needed: `C:\Program Files\Git\bin\bash.exe`

Do not deploy, publish, change Shopify/Railway/store settings, or mutate any live provider. Full-repository and container smoke tests are reserved for final integration. Keep CaddieInsight customer-facing and preserve `swinglab` compatibility.

## Work completed

- Tasks 1 and 2 are complete and independently reviewed.
- Task 3A through 3F are complete and independently reviewed. This includes mobile schema/HMAC/rate limiting, recovery ledger and backup/restore safety, crash-safe sign-out, native email authentication, review authentication, app identity, and production recovery startup composition.
- Gate 4A is complete at commits `258496a` and `c5c6e39`:
  - Default-off, authenticated `GET /api/v1/capabilities`.
  - Safe native session list/detail, Brief, Progress, and Today resources.
  - Closed generated contracts and deterministic `docs/api/openapi-v1.json`.
  - Current-owner/current-history-epoch checks under `history_delivery_guard()`.
  - Active Proof Cycle target freshness checks; replaced/corrupt targets fail closed.
  - Safe serializers exclude logs, raw errors, tracebacks, paths, commands, report HTML, provider data, and arbitrary artifact URLs.
  - Capabilities quota calculation is SELECT-only; legacy quota cleanup is unchanged.
  - Failed jobs return `failure_code: null` until Task 5 owns typed persisted classification.
  - Exact mobile resource method errors use native no-store responses; default-off paths remain concealed; legacy routes retain old behavior.
- Final Gate 4A external re-review was clean: 125 focused/adjacent tests passed. Full repository/container smoke was intentionally deferred.

## Current gate: 4B only — guarded mobile profile write

Gate 4B was mapped but no code or tests were written before the pause. Do not start Gate 4C until 4B is implemented, committed, and independently reviewed.

Implement only additive `PUT /api/v1/mobile/profile` and its generated contracts/tests/docs/OpenAPI:

1. Keep legacy `PUT /api/v1/profile` handler, request ordering, bodies, and errors byte-for-byte compatible. Do not call the legacy HTTP handler from the native route.
2. It is acceptable to refactor `UserStore.upsert_golfer_profile` internals into a shared normalization/upsert helper, provided all existing legacy tests remain unchanged.
3. `mobile_profile_writes_enabled` is a dedicated default-off flag. When off, return the normal native 404 before bearer authentication, body validation, database/filesystem access, or writes. Explicitly test invalid/missing bodies and bad/cookie auth while off. FastAPI normally validates typed bodies before entering a handler, so design the gate carefully while still publishing the generated request model in OpenAPI.
4. The route is strict bearer-only. Cookie-only auth is rejected; an invalid `Authorization` header must never fall back to a cookie.
5. Add closed `ProfileUpdateRequest`/`ProfileResponse` contracts (`extra="forbid"`):
   - required normalized `display_name`, 1-50 characters after NFKC, whitespace, and control-character validation;
   - closed existing literals for `experience_mode`, `handicap_range`, and `primary_goal`;
   - `practice_minutes`: `10 | 20 | 45`;
   - `sessions_per_week`: `1 | 2 | 3`;
   - `handedness`: `right | left`;
   - `camera_angle`: `face-on | dtl`;
   - approved `preferred_club`;
   - strict booleans `reduced_motion` and `marketing_email_opt_in`;
   - nonnegative `expected_history_epoch`.
6. `display_name`, `primary_goal`, and `preferred_club` are required so `is_complete` exactly matches the browser contract. `marketing_email_opt_in: false` is valid and never contributes to completion or becomes inferred consent.
7. Enter the Task 3 `CredentialMutationGuard`. Use one `BEGIN IMMEDIATE` transaction. Immediately before upsert, validate the admitted lease and re-read/recheck selector activity, `auth_epoch`, account deletion state, ownership, and exact `expected_history_epoch`. A revoked/deleted/reset losing race must never recreate a profile.
8. Return generic typed 401/404 for revoked/deleted identity and typed 409 for history-epoch conflict. All native success/error responses must be `Cache-Control: no-store` and `Pragma: no-cache`.
9. Add route names to the native error boundary where required. Do not add practice, device, upload, privacy, event, push, billing, or live-service behavior in this gate.
10. Regenerate `docs/api/openapi-v1.json` deterministically.

Likely files are `swinglab/api/contracts.py`, `swinglab/api/mobile_routes.py`, `swinglab/web/users.py`, `swinglab/web/credential_mutations.py`, `swinglab/web/app.py`, `tests/test_mobile_profile_api.py`, legacy profile/auth tests, and the OpenAPI snapshot. Touch only what the evidence requires.

## Gate 4B execution sequence

Run from PowerShell:

```powershell
$worktree = 'C:\Users\mahon\OneDrive\Desktop\SwingLab\.worktrees\caddieinsight-mobile-implementation'
Set-Location -LiteralPath $worktree
$taskPython = Join-Path $worktree '.superpowers\sdd\2026-08-06-caddieinsight-mobile-backend-foundation\.venv\Scripts\python.exe'

git status --short
git rev-parse HEAD
git merge-base --is-ancestor c5c6e39de8dfd61be8032df8748ff225b3f1ff50 HEAD
```

Then:

1. Read the full Task 4 plan and progress ledger before editing.
2. Write focused failing tests first for schema, default-off zero work, bearer-only behavior, success/replay, wrong/revoked selector, final-write revocation, history reset, deletion race, cross-account context, normalization, completion parity, and legacy byte parity.
3. Implement the minimum production change and rerun the focused tests after each RED checkpoint.
4. Regenerate OpenAPI:

```powershell
& $taskPython scripts/export_openapi.py --output docs/api/openapi-v1.json
```

5. Run a focused aggregate such as:

```powershell
& $taskPython -m pytest `
  tests/test_mobile_profile_api.py `
  tests/test_profile_onboarding.py `
  tests/test_golfer_profile_identity.py `
  tests/test_mobile_api_tokens.py `
  tests/test_mobile_api_errors.py `
  tests/test_mobile_openapi_contract.py `
  tests/test_first_sale_platform.py `
  tests/test_foundation_contracts.py -q
```

6. Run `git diff --check`, inspect the full diff, update `task-4b-report.md` and `progress.md`, and make one focused Gate 4B commit.
7. Have a different agent review the base-to-head diff. Fix findings in bounded rounds and require a clean re-review before 4C.

## After Gate 4B

- Gate 4C: strict bearer/idempotent practice evidence; `mobile_practice_evidence_details`; history reset, deletion, privacy export, backup counts, orphan/HMAC scans; cumulative mobile backup generation 2 with generation 0/1 compatibility.
- Gate 4D: recovery-fenced device list/revoke, shared selector revocation, self-replay behavior, and exact legacy `/api/v1/mobile-tokens` parity including the documented 503 readiness extension.
- Finish Task 4 with the combined focused matrix and deterministic OpenAPI check.
- Continue Tasks 5-8 sequentially from the plan. Only after all gates and independent reviews are clean, run the full repository suite and container smoke/integration gates.

## Standing decisions and hazards

- Preserve the existing five-device cap.
- Task 5 owns exact typed analysis failure persistence/classification and supported-build admission.
- Task 6 owns later reset/delete erasure record kinds.
- Recovery baseline initialization is offline and approval-gated before fail-closed production startup.
- Preserve Railway's one-replica SQLite contract until durable state/job coordination is externalized.
- Never use `api_payload()` or wholesale `Job.as_dict()` in native resources.
- Do not kill or interact with unrelated Python processes. Use only the exact task virtual environment above.
- A nonfatal Git warning may mention inability to remove an unrelated stale `SwingLab-stage0b-baseline` worktree directory. Do not repair or delete it without separate user scope.
