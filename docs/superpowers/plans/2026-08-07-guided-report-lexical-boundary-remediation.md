# Guided Report Lexical Boundary Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind structured report completion and quota classification to the exact no-follow lexical job directory chain.

**Architecture:** Add one context-managed pinned job-bundle loader in `swinglab.report_artifacts`. It parses canonical job-relative report paths, pins sessions/job/out/analysis/bundle directories with platform-specific no-follow handles, reuses the existing manifest/topology/checksum validator, and keeps the boundary pinned through completion commit or classification. `JobManager` stops resolving structured paths before this loader; legacy paths remain unchanged.

**Tech Stack:** Python 3.11+, `pathlib`, `os.open`/descriptor-relative POSIX operations, existing Windows `CreateFileW` handle primitives, SQLite, pytest.

## Global Constraints

- Work only in `C:\Users\mahon\OneDrive\Desktop\SwingLab\.worktrees\guided-swing-report` on `codex/guided-swing-report-implementation`.
- Start from `c97d2d00d5d2f2b0ff119edd8d497f9a88c632fd` and preserve all prior guided-report contracts.
- Use strict TDD: each new regression must fail for the confirmed redirect acceptance before production code changes, then pass after the minimal fix.
- Use one implementation owner. Do not push, merge, deploy, enable a feature flag, modify live data, or begin web/API/native work in this task.
- Structured paths fail closed. Genuine legacy completion and quota behavior remain unchanged.
- The read validator must not recursively enumerate the job tree and must not request delete/quarantine privileges.
- Every acquired OS handle closes exactly once on success and every failure path.
- On POSIX, missing `O_NOFOLLOW`, `O_DIRECTORY`, or descriptor-relative support fails closed.
- On Windows, reject every reparse point by file attributes and final-path/identity checks; do not rely on `Path.is_junction()`.
- Keep redirect targets and sentinels under pytest temporary directories and detach redirects before cleanup.
- FFmpeg/ffprobe remain unavailable; deterministic boundary tests must not require codecs.

---

## Task 1: Pin the structured job publication boundary end to end

**Files:**

- Modify: `swinglab/report_artifacts.py`
- Modify: `swinglab/web/jobs.py`
- Modify: `tests/test_report_artifacts.py`
- Modify: `tests/test_report_bundle_job_publication.py`
- Modify: `tests/test_caps.py`
- Regression-only: `tests/test_history_reset_core.py`
- Regression-only: `tests/test_backups.py`

**Interfaces:**

- Produce `PinnedJobReportBundle(bundle, report_rels)` with `verify_lexical_identity()`.
- Produce `open_job_published_bundle(sessions_dir, *, job_id, report_rel, report_view_rel, manifest_rel, checksums_rel)` as a context manager.
- Consume four full job-relative persisted paths in canonical report/view/manifest/checksums order.
- Retain `load_published_bundle(analysis_session, direct_rels...)` for existing non-JobManager callers, sharing one internal bundle-validation core instead of duplicating validation.
- Completion boundary errors remain `ReportArtifactValidationError`/`ValueError`; classification translates every structured boundary failure to `_COMPLETION_CORRUPT`.

- [ ] **Step 1: Add the cross-platform temporary redirect fixture**

  Add a context-managed test helper near the existing guided-result helpers. On POSIX it creates a directory symlink. On Windows it uses the existing `cmd /c mklink /J` pattern and skips only when junction creation is unavailable. It must move the original plain directory to an explicitly named saved sibling, create the redirect, detach it in `finally` with non-recursive link removal, and restore the original. Put sentinel files in saved and target trees and assert both remain byte-identical.

- [ ] **Step 2: Write failing artifact-boundary tests**

  In `tests/test_report_artifacts.py`, construct valid canonical guided bundles under a temporary sessions/job/out/analysis chain and add behavioral tests that name the production break:

  - `test_job_bundle_loader_rejects_redirected_job_root`
  - `test_job_bundle_loader_rejects_redirected_out_root`
  - `test_job_bundle_loader_rejects_redirected_analysis_child`
  - `test_job_bundle_loader_rejects_redirected_bundle_root`
  - `test_job_bundle_loader_rejects_noncanonical_job_relative_rels`
  - `test_job_bundle_loader_detects_lexical_replacement_while_pinned`
  - `test_job_bundle_loader_closes_partial_handle_chain_on_failure`

  The valid control must return the exact four canonical job-relative paths and the same validated outcome/manifest identities as `load_published_bundle`.

- [ ] **Step 3: Write failing JobManager completion tests**

  In `tests/test_report_bundle_job_publication.py`, add:

  - `test_guided_completion_rejects_redirected_job_root_without_committing_artifact_rels`
  - `test_guided_completion_rejects_redirected_out_root_without_committing_artifact_rels`
  - `test_guided_completion_rejects_redirected_analysis_child_without_committing_artifact_rels`

  Each calls real `_complete_job()` with a coherent temporary donor bundle. It must fail before publication and assert both the live `Job` and SQLite row remain `processing`, all four artifact rels are null, and `structured_report` is false. Donor and saved-tree sentinels remain unchanged.

- [ ] **Step 4: Write failing structured-classification tests**

  In `tests/test_caps.py` or the existing structured quota section of `tests/test_report_bundle_job_publication.py`, add one parameterized regression over `job`, `out`, and `analysis` redirect depths. For each case assert literal behavior:

  ```python
  assert manager._completed_report_classification(row) == jobs_module._COMPLETION_CORRUPT
  assert manager.coaching_eligible(stored) is False
  assert manager.refilm_rejections_this_month(user_id) == 0
  assert manager.usage_this_month(user_id) == 1
  ```

  Include valid coaching and capture controls and a genuine legacy control.

- [ ] **Step 5: Run the new tests and verify RED**

  Run the exact new test selectors. Confirm each redirect test fails because current code accepts a resolved target or the new loader API is absent—not because of fixture setup, permissions, or cleanup errors. Record the failing selectors and messages in the task report.

- [ ] **Step 6: Implement the pure job-relative path parser**

  In `swinglab/report_artifacts.py`, parse input objects without filesystem resolution. Require four distinct, exact POSIX strings shaped as:

  `out/<safe-child>/report-bundle-<32 lowercase hex>/<canonical filename>`

  Require one shared child and bundle name; reject absolute paths, empty/dot/parent components, backslashes, colons, reserved Windows names, trailing dots/spaces, nested analysis children, wrong case, duplicate/cross-child paths, and noncanonical filenames. Return the analysis child, bundle name, and canonical full/direct rel tuples.

- [ ] **Step 7: Implement the pinned lexical directory chain**

  Add the smallest read-only internal owner in `swinglab.report_artifacts`:

  - pin the configured sessions root;
  - traverse job ID, literal `out`, and analysis child handle-relatively;
  - reject links/reparse points/non-directories before use;
  - compare lexical stat identity with opened-handle identity;
  - keep handles open in parent-to-child order and close them once in reverse order;
  - expose the final analysis handle to the bundle-root pinning path;
  - revalidate lexical identities on demand and at context exit.

  Refactor the existing bundle loader internally so a bundle root beneath a pinned analysis handle is opened without a second path-based ancestor traversal. Preserve all current direct-session loader behavior and error messages where their contract is tested.

- [ ] **Step 8: Implement `open_job_published_bundle`**

  Combine the pure parser, pinned chain, and existing staged/published graph validation. Return `PinnedJobReportBundle` only after canonical root/manifest ID, four-file graph, topology, checksums, media, HTML, metrics, and lexical identity all validate. Keep the boundary open until the caller exits.

- [ ] **Step 9: Replace guided completion's resolve-first path**

  In `_complete_job()`, keep `_result_rel()` only for legacy output. For guided output, derive the four job-relative strings lexically from `SessionResult` with `absolute()`/`abspath` and exact parent relationships; never call `resolve()` first. Enter `open_job_published_bundle`, validate presentation and persisted policy, call `verify_lexical_identity()` immediately before the guarded update, keep handles through SQLite commit, and expose the returned canonical rels atomically.

- [ ] **Step 10: Replace structured classification's resolve-first path**

  In `_completed_report_classification()`, call `open_job_published_bundle` with persisted job-relative rels. Validate policy and outcome inside the context. Any exception returns `_COMPLETION_CORRUPT`; coaching and capture come only from the validated view. Do not change `_legacy_coaching_eligible()`.

- [ ] **Step 11: Verify GREEN and lifecycle compatibility**

  Run:

  ```powershell
  python -m pytest tests/test_report_artifacts.py tests/test_report_bundle_job_publication.py tests/test_caps.py -q
  python -m pytest tests/test_history_reset_core.py tests/test_backups.py tests/test_report_bundle.py -q
  ```

  Expected: all deterministic tests pass; only existing codec-dependent skips remain. Confirm reset/retention operations run after all read handles are closed.

- [ ] **Step 12: Run the complete gate**

  Run:

  ```powershell
  python -m pytest -q
  python -m compileall -q swinglab tests
  git diff --check
  git status --short
  ```

  Record exact pass/skip/warning counts, compilation exit status, and changed files. Do not describe unavailable codec cases as executed.

- [ ] **Step 13: Self-review, document, and commit**

  Mutation-check each boundary: removing the job, `out`, or analysis validation must fail a named test; restoring `resolve()` before the shared loader must fail completion and classification tests. Confirm no public route, schema, flag, deployment, or legacy behavior changed. Write the task report, then commit only the focused implementation, tests, spec, plan, and report with message `fix: pin guided report job boundaries`.

## Completion Gate

- [ ] Every static job/out/analysis redirect is rejected by both completion and classification.
- [ ] Rename/replacement during the pinned interval cannot produce a committed foreign bundle.
- [ ] Completion stays processing with null rels on boundary failure.
- [ ] Corrupt structured classification is coaching-ineligible and charged once, never a courtesy rejection.
- [ ] Valid coaching/capture, backup, reset, retention, and genuine legacy behavior remain green.
- [ ] All handles close on success/failure; the worktree is clean after commit.
- [ ] One independent task review reports spec compliance and code quality approved before the web presentation plan resumes.
