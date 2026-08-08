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

First vertical slice landed: email step-up start/exchange behind default-off `mobile_privacy_enabled`, modeled on native email auth.

- `POST /api/v1/auth/step-up/start` — bearer-only (ambient cookie rejected, review-scoped bearer rejected), body `{purpose, code_challenge}`. Binds the initiating stable user, bearer selector, `auth_epoch`, installation HMAC, and purpose; emails a purpose-bound 10-minute grouped 8-digit code plus a challenge-only universal link (never bearer or verifier). Returns no-store `202 {challenge_id, expires_at}`.
- `POST /api/v1/auth/step-up/exchange` — never falls back to bearer/cookie; body `{challenge_id, email_code, code_verifier}` + 128-bit `Idempotency-Key`. Rechecks the still-active initiating selector/epoch/installation (including that the token's `auth_epoch` still matches the user's current epoch), mints a no-store `201 {step_up_token, purpose, expires_at}` bound to owner/selector/epoch/installation/purpose with 5-minute expiry. An exact lost-response replay returns the same deterministic token; conflicting idempotency/proofs return generic `409`; wrong code/verifier return generic `401` and burn after five failures.
- Flag off → `404` before auth/body/DB (routes registered but concealed; runtime 404, mirroring email-auth/upload/device concealment).
- Rate limits via `KeyedThrottle.consume_many`: `stepup-start-selector` (5/15m), `stepup-start-user` (5/15m), `stepup-start-client-ip` (20/15m); failed-exchange `stepup-exchange-user` (5/15m), `stepup-exchange-client-ip` (20/15m). Live-challenge caps: ≤2 per `(selector, purpose)`, ≤5 per user; resend ≥60s.
- New `MobileStateDomain` values added (`step-up-*`) with distinct `VersionedHMAC` domains; challenge/journal/receipt/token rows store only versioned HMAC pairs.
- New service `swinglab/web/mobile_privacy.py::MobileStepUpService`; DB methods + tables in `users.py`; contracts in `swinglab/api/contracts.py`; routes in `swinglab/api/mobile_routes.py`; wiring in `app.py`.
- Tests: `tests/test_mobile_privacy_api.py` (16 tests). OpenAPI regenerated (step-up paths always appear in the schema, consistent with other default-off native routes).

**Deferrals (this slice):**

- Export / history-reset / account-delete endpoints that will *consume* the minted step-up token are not implemented; the token binding is asserted, not its consumption.
- Store-review step-up variant is not implemented; a `method='email'` discriminator is carried on every step-up row so the review path can share the shape later without overloading the email path.
- New tables (`step_up_challenges`, `step_up_exchange_journals`, `step_up_exchange_receipts`, `step_up_tokens`) are held **outside** the closed mobile-state generation inventory (deliberately not `mobile_`-prefixed), mirroring the Task 5 resumable-upload precedent. Registering them in a bumped gen-3 for backup attestation remains a documented deferral tracked alongside the existing gen-3 backup-registration item.

## Standing decisions and hazards

- Preserve the existing five-device cap.
- Task 5 owns exact typed analysis failure persistence/classification and supported-build admission.
- Task 6 owns later reset/delete erasure record kinds.
- Recovery baseline initialization is offline and approval-gated before fail-closed production startup.
- Preserve Railway's one-replica SQLite contract until durable state/job coordination is externalized.
- Never use `api_payload()` or wholesale `Job.as_dict()` in native resources.
- Do not kill or interact with unrelated Python processes.
