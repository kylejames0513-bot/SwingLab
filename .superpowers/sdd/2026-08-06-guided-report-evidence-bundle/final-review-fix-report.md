# Final review fix report — Guided Report Evidence Bundle

## Status

IMPLEMENTED_AND_VERIFIED; independent parent review is still pending.

This pass addresses the seven final-review findings against starting commit
`7fd855c1524bc47876f26d3e1b82dd66f4903cdf`. It does not declare the wider
guided-report plan complete and performs no push, merge, deployment, or live
state change.

## Worktree and implementation commit

- Worktree: `C:\Users\mahon\OneDrive\Desktop\SwingLab\.worktrees\guided-swing-report`
- Branch: `codex/guided-swing-report-implementation`
- Starting commit: `7fd855c1524bc47876f26d3e1b82dd66f4903cdf`
- Implementation commit: `d7dd47f` (`fix: harden guided report publication boundaries`)
- `.superpowers/sdd/2026-08-06-guided-report-evidence-bundle/progress.md`
  was not edited.

## Findings closed

1. Published bundle identity is now canonical. The loader accepts exactly one
   direct `report-bundle-<attempt-id>` child of the caller's validated analysis
   session, binds the suffix to the manifest attempt, and rejects attempts,
   arbitrary names, nested roots, and mismatched suffixes. Publication compares
   the complete loaded manifest/checksum/view graph with the staged graph after
   the no-clobber rename. Job completion validates the direct analysis boundary
   before its guarded SQLite transaction and leaves no terminal exposure on a
   validation failure.

2. `validate_persisted_report_policy` is the shared persisted-policy-to-bundle
   authority used by JobManager completion/quota reads and backup create/restore.
   It requires the guided presentation, canonical entitlement JSON, and replay
   state consistent with the validated view. Backup structured-schema detection
   includes presentation and entitlement columns, rejects partial schemas, and
   applies the same policy validation during restore before scratch exposure.

3. Structured quota and trend decisions now use a three-state validated bundle
   result: coaching, genuine capture-only, or corrupt. Only genuine capture-only
   results receive courtesy re-film treatment. Integrity/policy corruption is
   coaching-ineligible but still consumes usage, including across restart and
   durable history-reset receipts. Raw report/metrics fallback remains limited
   to genuine legacy rows.

4. Presentation routing uses one strict parser and typed
   `UnsupportedReportPresentationVersion` exception. Pipeline, session creation,
   workers, completion, restart recovery, and backup row classification accept
   only the legacy and guided enum values; unknown values cannot fall through to
   legacy analysis or artifact generation.

5. Guided evidence rendering now scales the already accepted analysis
   observation to full-resolution event frames. It does not run a second pose
   inference that could disagree with the visibility gates and trust decision.
   Legacy rendering behavior is unchanged.

6. Explicit manual strikes `[]` serialize as `[]` instead of SQL `NULL` and
   survive persistence, restart, and requeue as an explicit empty list.

7. Analysis failures and restart-orphaned missing-source jobs reclaim exact
   owned report attempts/finals before transitioning to `FAILED`. If conservative
   cleanup refuses ownership, the job remains active/actionable with durable
   error and log text. Completion-validation failures likewise remain processing
   with all publication columns null for safe recovery.

## TDD evidence

Focused regressions were observed red before implementation for canonical root
acceptance, coherent post-rename substitution, the missing shared validator,
backup replay-policy mismatch, quota artifact mutation, unknown pipeline
presentation, divergent full-resolution pose inference, empty-list strike
persistence, worker/restart cleanup ordering, cleanup refusal, completion
validation state, corrupted structured flags, and backup row classification.
Each was rerun green after the corresponding minimal production change.

## Verification

- Baseline before edits:
  `python -m pytest -q` — 1769 passed, 29 skipped, 1 warning in 387.52s.
- Publication/JobManager focused suite:
  `python -m pytest -q tests/test_report_bundle_job_publication.py` —
  56 passed in 8.46s.
- Artifact, publication, backup, and pipeline focused suites:
  `python -m pytest -q tests/test_report_artifacts.py tests/test_report_bundle.py tests/test_backups.py tests/test_caps.py` —
  302 passed, 5 skipped in 27.03s.
- Wider JobManager/history/accounts/web/proof/retention compatibility run —
  167 passed, 1 warning in 70.76s.
- Final full repository suite:
  `python -m pytest -q` — 1804 passed, 29 skipped, 1 warning in 340.50s.
- `python -m compileall -q swinglab tests` — exit 0.
- `git diff --check` — exit 0; Git emitted only Windows LF-to-CRLF notices.

The one warning is the existing FastAPI/Starlette TestClient deprecation for the
installed httpx integration. The 29 skips match the baseline count and include
environment-dependent codec cases; no skipped case is reported as executed.

## Release boundary

All evidence above is local source/test evidence only. Nothing was pushed,
merged, deployed, published to Shopify, or verified live.
