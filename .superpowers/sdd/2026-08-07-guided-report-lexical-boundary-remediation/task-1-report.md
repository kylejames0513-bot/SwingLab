# Task 1 report — pin the structured job publication boundary

## Status

Implemented and verified on `codex/guided-swing-report-implementation` from base
`09290548285e5fdf873fdbdb5a9d2a2e39bd27a3`. No push, merge, deployment,
feature activation, live mutation, API work, or cleanup of the worktree was
performed.

The focused commit containing this report has subject:

`fix: pin guided report job boundaries`

Its SHA is reported in the task handoff because a commit cannot contain its own
final object ID.

## RED evidence

The baseline focused suite was green before test additions:

```text
python -m pytest tests/test_report_artifacts.py tests/test_report_bundle_job_publication.py tests/test_caps.py -q
251 passed, 5 skipped in 14.79s
```

The required regressions were then run against the pre-fix production code.
They reached the intended boundary rather than failing in fixture setup:

- The artifact loader regressions failed at runtime with
  `AttributeError: no open_job_published_bundle`; redirect creation, nonrecursive
  detach, restoration, and both sentinels succeeded.
- Completion accepted the redirected job and analysis chains, so their named
  tests failed with `Failed: DID NOT RAISE`. A static `out` redirect was already
  rejected by the old direct-analysis layout check, so the exact named `out`
  regression used a deterministic loader-boundary swap to expose the confirmed
  resolve-before-validate window; it also failed with `DID NOT RAISE` before the
  fix.
- Structured classification through redirected `job`, `out`, and `analysis`
  depths returned `coaching`, not the required `corrupt`. After correcting the
  job-depth donor to remain a sibling under the sessions root, all three failures
  were acceptance failures rather than permission or fixture failures.
- An additional exact-parent regression demonstrated that a coherent bundle
  from a sibling analysis child was accepted before the final parent check:
  `1 failed in 1.48s` with `Failed: DID NOT RAISE <class 'ValueError'>`.

## Changed interfaces and behavior

- Added `PinnedJobReportBundle(bundle, report_rels)` with
  `verify_lexical_identity()`.
- Added the context manager
  `open_job_published_bundle(sessions_dir, *, job_id, report_rel,
  report_view_rel, manifest_rel, checksums_rel)`.
- Added a pure parser for four distinct exact POSIX job-relative paths shaped as
  `out/<safe-child>/report-bundle-<32 lowercase hex>/<canonical filename>`.
- Pinned sessions, job, literal `out`, analysis child, and bundle directories in
  order. POSIX requires descriptor-relative no-follow capabilities; Windows
  checks reparse attributes, directory type, opened-handle identity, and final
  path without using `Path.is_junction()` in the new validator.
- Extracted one internal pinned-bundle validation core. Existing
  `load_published_bundle(analysis_session, direct_rels...)` remains available and
  uses the same manifest, topology, checksum, media, HTML, metrics, and lexical
  identity validation.
- Guided `_complete_job()` now derives paths with lexical `abspath` handling,
  requires every artifact to use `result.session_dir`, verifies the pinned chain
  immediately before the guarded update, and holds handles through SQLite
  commit. Its legacy branch still uses the existing `_result_rel()` behavior.
- Structured `_completed_report_classification()` now reads policy and outcome
  only inside the shared pinned context and translates every boundary failure to
  `corrupt`. `_legacy_coaching_eligible()` was not changed.
- Added a tmp-root-only redirect fixture. It moves the original to a saved
  sibling, creates a POSIX symlink or Windows junction, detaches the redirect
  nonrecursively, restores the original, and proves saved/target sentinel bytes
  are unchanged.

## Verification

Exact new boundary selectors:

```text
python -m pytest tests/test_report_artifacts.py::test_job_bundle_loader_returns_canonical_rels_and_matches_direct_loader tests/test_report_artifacts.py::test_job_bundle_loader_rejects_redirected_job_root tests/test_report_artifacts.py::test_job_bundle_loader_rejects_redirected_out_root tests/test_report_artifacts.py::test_job_bundle_loader_rejects_redirected_analysis_child tests/test_report_artifacts.py::test_job_bundle_loader_rejects_redirected_bundle_root tests/test_report_artifacts.py::test_job_bundle_loader_rejects_noncanonical_job_relative_rels tests/test_report_artifacts.py::test_job_bundle_loader_detects_lexical_replacement_while_pinned tests/test_report_artifacts.py::test_job_bundle_loader_closes_partial_handle_chain_on_failure tests/test_report_bundle_job_publication.py::test_guided_completion_rejects_redirected_job_root_without_committing_artifact_rels tests/test_report_bundle_job_publication.py::test_guided_completion_rejects_redirected_out_root_without_committing_artifact_rels tests/test_report_bundle_job_publication.py::test_guided_completion_rejects_redirected_analysis_child_without_committing_artifact_rels tests/test_report_bundle_job_publication.py::test_guided_completion_rejects_artifacts_from_a_different_analysis_child tests/test_caps.py::test_structured_classification_rejects_redirected_job_chain tests/test_caps.py::test_structured_classification_valid_controls tests/test_caps.py::test_genuine_legacy_classification_control -q
35 passed in 3.28s
```

Required focused suites on the final code tree:

```text
python -m pytest tests/test_report_artifacts.py tests/test_report_bundle_job_publication.py tests/test_caps.py -q
286 passed, 8 skipped in 17.71s

python -m pytest tests/test_history_reset_core.py tests/test_backups.py tests/test_report_bundle.py -q
123 passed in 24.58s
```

Complete verification:

```text
python -m pytest -q
1839 passed, 32 skipped, 1 warning in 352.03s (0:05:52)

python -m compileall -q swinglab tests
exit 0

git diff --check
exit 0 (Git emitted only working-tree LF-to-CRLF conversion notices)

git status --short
A  .superpowers/sdd/2026-08-07-guided-report-lexical-boundary-remediation/task-1-report.md
M  swinglab/report_artifacts.py
M  swinglab/web/jobs.py
M  tests/report_bundle_fixtures.py
M  tests/test_caps.py
M  tests/test_report_artifacts.py
M  tests/test_report_bundle_job_publication.py
```

The one pytest warning is the existing Starlette deprecation warning for
`httpx` with `starlette.testclient`. POSIX-only capability tests are included
but skipped on this Windows run; no skipped codec-dependent case is represented
as executed.

## Self-review and mutation coverage

- Restoring the prior resolve-first completion/classification flow reproduces
  the recorded RED failures in the named completion and classification redirect
  tests.
- Omitting no-follow rejection at any of job, `out`, analysis, or bundle depth
  is covered by the matching named artifact redirect test.
- Relaxing exact shape, spelling, canonical filename, shared child/bundle,
  duplicate, reserved-name, or traversal checks is covered by
  `test_job_bundle_loader_rejects_noncanonical_job_relative_rels` (18 damage
  variants).
- Removing opened-handle versus lexical-identity comparison is covered by
  `test_job_bundle_loader_detects_lexical_replacement_while_pinned`.
- Removing partial-open unwind or closing a handle more than once is covered by
  `test_job_bundle_loader_closes_partial_handle_chain_on_failure`.
- Removing the direct `SessionResult.session_dir` parent relationship is covered
  by `test_guided_completion_rejects_artifacts_from_a_different_analysis_child`.
- Bypassing the shared loader in quota classification is covered by the three
  redirected-depth quota assertions plus valid coaching/capture and genuine
  legacy controls.

The final diff was reviewed against `0929054`; the existing validation algorithm
remains single-source, no job tree is recursively enumerated, and no delete or
quarantine permission was added.

## Concerns and boundaries

- This run exercised the Windows implementation directly. POSIX behavior is
  guarded by fail-closed capability checks and POSIX-specific tests, but those
  tests require a POSIX CI runner for execution.
- As documented in the approved design, filesystem mutation and SQLite commit
  are not made atomically linearizable. The exact validated chain is rechecked
  immediately before publication and again on context exit; every later
  structured read revalidates and fails closed after subsequent tampering.
- The guided presentation remains default-off. The branch and worktree are
  intentionally preserved for the parent implementation owner.
