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

## Fix round 1 — review remediation

### Status and scope

Fix round 1 started from `cf2e8f1732e9715939d17329ab11c87694f5ca16`
after the task review found three boundary defects. The round changes only:

- `swinglab/report_artifacts.py`
- `swinglab/web/jobs.py`
- `tests/test_report_artifacts.py`
- `tests/test_report_bundle_job_publication.py`
- this report

No push, merge, deployment, feature activation, live mutation, or worktree
cleanup was performed. The fix commit containing this section has subject
`fix: complete guided report boundary pinning`; its SHA is reported in the
handoff because a commit cannot contain its own final object ID.

### RED evidence

The first exact run exercised all three reviewed defects before production
edits:

```text
python -m pytest tests/test_report_artifacts.py::test_windows_job_bundle_opens_every_component_relative_to_its_pinned_parent tests/test_report_artifacts.py::test_windows_relative_children_ignore_injected_lexical_ancestor_redirection tests/test_report_artifacts.py::test_job_bundle_context_exit_is_cleanup_only_after_explicit_verify tests/test_report_artifacts.py::test_fdopen_failure_closes_owned_descriptor_exactly_once tests/test_report_artifacts.py::test_windows_fdopen_failure_closes_transferred_handle_exactly_once tests/test_report_bundle_job_publication.py::test_guided_completion_rolls_back_when_final_precommit_identity_verify_fails -q
6 failed in 1.89s
```

The intended failures were:

- the two Windows traversal tests could not find `_win_open_relative`;
- context exit invoked a third, fallible lexical verification;
- the descriptor ownership helper/seam was absent and the real Windows
  `fdopen()` failure recorded zero descriptor closes;
- guided completion verified only once, committed, and did not raise the
  injected final pre-commit identity failure.

Handle-based Windows enumeration received its own RED run before the native
enumeration implementation:

```text
python -m pytest tests/test_report_artifacts.py::test_windows_handle_enumeration_is_case_exact_and_not_path_redirectable -q
1 failed in 0.42s: AttributeError: no _win_scan_directory
```

The cleanup-only wording was also tested explicitly before suppressing teardown
close errors:

```text
python -m pytest tests/test_report_artifacts.py::test_job_bundle_context_exit_does_not_raise_after_cleanup_attempts -q
1 failed in 0.54s: injected cleanup close failure escaped context exit
```

All RED failures reached the intended behavior; there were no fixture,
permission, or collection errors.

### Corrections

1. Windows child binding now uses `NtOpenFile` with
   `OBJECT_ATTRIBUTES.RootDirectory`. Only the initial sessions root uses
   `CreateFileW`; job, literal `out`, analysis, bundle, nested directory, and
   artifact file components are opened one exact component at a time relative
   to their still-open parent handles. Object attributes remain case-sensitive,
   delete sharing remains disabled, and directory/file type, reparse attribute,
   final path, and handle identity checks remain enforced.
2. Windows exact-name and topology inspection now uses
   `GetFileInformationByHandleEx(FileFullDirectoryRestartInfo)` against the open
   directory handle. Path inspection remains defense in depth only and cannot
   bind or redirect a child. Enumeration remains bounded and nonrecursive.
3. `open_job_published_bundle` performs its validation before yielding and has
   cleanup-only, nonthrowing teardown. Guided completion verifies once before
   the guarded update and again after the update while the transaction is still
   active, then commits with the already-verified handles still open. A final
   verification failure rolls the update back, leaving persisted and in-memory
   state processing with all four rels null.
4. Artifact file opening retains ownership in this order: Windows raw handle,
   CRT descriptor after successful `open_osfhandle`, then Python file object
   after successful `fdopen`. Failure closes the currently owned raw handle or
   descriptor exactly once. POSIX uses the same descriptor-to-file ownership
   rule.

### Exact GREEN verification

Exact fix-round selectors on the final code:

```text
python -m pytest tests/test_report_artifacts.py::test_windows_job_bundle_opens_every_component_relative_to_its_pinned_parent tests/test_report_artifacts.py::test_windows_relative_children_ignore_injected_lexical_ancestor_redirection tests/test_report_artifacts.py::test_windows_handle_enumeration_is_case_exact_and_not_path_redirectable tests/test_report_artifacts.py::test_job_bundle_context_exit_is_cleanup_only_after_explicit_verify tests/test_report_artifacts.py::test_job_bundle_context_exit_does_not_raise_after_cleanup_attempts tests/test_report_artifacts.py::test_posix_fdopen_failure_closes_owned_descriptor_exactly_once tests/test_report_artifacts.py::test_windows_fdopen_failure_closes_transferred_handle_exactly_once tests/test_report_bundle_job_publication.py::test_guided_completion_rolls_back_when_final_precommit_identity_verify_fails -q
7 passed, 1 skipped in 1.37s
```

All three Windows native traversal/enumeration tests and the Windows descriptor
ownership test executed. The sole skip is the POSIX-only descriptor ownership
counterpart on this Windows host.

Required focused and lifecycle gates:

```text
python -m pytest tests/test_report_artifacts.py tests/test_report_bundle_job_publication.py tests/test_caps.py -q
293 passed, 9 skipped in 17.75s

python -m pytest tests/test_history_reset_core.py tests/test_backups.py tests/test_report_bundle.py -q
123 passed in 21.44s
```

Because the correction changes the shared artifact open/enumeration core, the
complete suite was rerun:

```text
python -m pytest -q
1846 passed, 33 skipped, 1 warning in 357.13s (0:05:57)

python -m compileall -q swinglab tests
exit 0

git diff --check
exit 0 (Git emitted only working-tree LF-to-CRLF conversion notices)

git status --short
 M swinglab/report_artifacts.py
 M swinglab/web/jobs.py
 M tests/test_report_artifacts.py
 M tests/test_report_bundle_job_publication.py
```

The single warning is the existing Starlette deprecation warning for `httpx`
with `starlette.testclient`. No skipped codec-dependent case is represented as
executed.

### Self-review and mutation coverage

- Replacing `_win_open_relative` with a pathname open fails the component-chain
  and injected ancestor-redirection tests; both record the actual native seam,
  not source text.
- Replacing handle enumeration with `os.scandir(path)` fails the injected path
  redirection assertion, and wrong-case selection fails the exact-name test.
- Restoring exit-time identity verification fails the cleanup-only call-count
  test. Allowing teardown close errors to escape fails the cleanup-attempt test.
- Removing the second in-transaction verification lets the injected final
  failure commit DONE, so the rollback test fails its raise, persisted-state,
  and in-memory-state assertions.
- Clearing the raw handle or descriptor before the next ownership conversion
  succeeds makes the Windows/POSIX `fdopen()` tests observe a leak; closing both
  layers makes their exact-once assertions fail.
- Existing redirected job/out/analysis, replacement, partial-open cleanup,
  valid guided, genuine legacy, backup, reset, and direct bundle tests remain
  green.

### Concerns

- Windows native traversal and enumeration were executed on this Windows host.
  The POSIX descriptor failure case is present but requires POSIX CI to execute.
- `GetFileInformationByHandleEx` full-directory enumeration is available on
  supported modern Windows versions; an unavailable native capability fails the
  structured boundary closed.
- The approved non-goal remains unchanged: filesystem mutation and SQLite
  commit are not made atomically linearizable. Every fallible verification now
  occurs before commit, handles remain pinned through commit, and later reads
  revalidate the complete structured boundary.

## Fix round 2 — stream exact Windows child discovery

### Status and scope

Fix round 2 started from
`16803b978751fc25acd5ed12e8c5dbc923507f97`. It changes only:

- `swinglab/report_artifacts.py`
- `tests/test_report_artifacts.py`
- this report

No push, merge, deployment, feature activation, live mutation, or worktree
cleanup was performed. The fix commit containing this section has subject
`fix: stream exact Windows child lookup`; its SHA is reported in the handoff
because a commit cannot contain its own final object ID.

### RED evidence

The new Windows regression creates 4,097 ordinary entries plus an exact target
in one parent directory. Against the pre-fix implementation, the exact child
lookup fully materialized `_win_scan_directory(..., limit=4096)` and failed at
entry 4,097 even though only one exact child was requested:

```text
python -m pytest tests/test_report_artifacts.py::test_windows_relative_open_finds_exact_child_beyond_topology_scan_limit -q
1 failed in 1.19s
ReportArtifactValidationError: report bundle contains too many filesystem entries
```

The failure reached native handle enumeration and the intended production
boundary; it was not a collection, permission, or fixture failure.

### Correction

- Extracted `_win_iter_directory`, a lazy `GetFileInformationByHandleEx`
  iterator over the already-open directory handle. It remains nonrecursive and
  validates native entry sizes and offsets as entries are consumed.
- Kept `_win_scan_directory` as the capped, materialized topology operation.
  It still rejects an entry beyond its supplied limit, so bundle topology
  validation retains the existing 4,096-entry resource bound.
- Added `_win_has_exact_child` for child binding. It uses exact case-sensitive
  name comparison and constant auxiliary memory, stops immediately at a match,
  and does not reuse the topology limit. For an absent name, the native
  end-of-directory result is the completeness bound: a fixed entry-position
  cap would make a legitimate child beyond that cap indistinguishable from an
  absent child in a sessions directory that is allowed to keep growing.
- `_win_open_relative` still performs its `NtOpenFile` call relative to the
  pinned `RootDirectory` handle. Delete sharing, reparse/type/identity checks,
  exact final-name validation, and final-path validation are unchanged.

The regression also asserts that the capped full scan still rejects the same
oversized directory and that an actually absent exact child returns
`FileNotFoundError` after handle enumeration reaches native end-of-directory.

### Verification

Exact round-two regression on the final tree:

```text
python -m pytest tests/test_report_artifacts.py::test_windows_relative_open_finds_exact_child_beyond_topology_scan_limit -q
1 passed in 0.97s
```

Prior fix-round selectors and the original boundary selector set:

```text
prior 8 selectors: 7 passed, 1 skipped in 1.48s
original 15 named selectors (35 parametrized cases): 35 passed in 4.04s
```

Required focused and lifecycle gates:

```text
python -m pytest tests/test_report_artifacts.py tests/test_report_bundle_job_publication.py tests/test_caps.py -q
294 passed, 9 skipped in 19.00s

python -m pytest tests/test_history_reset_core.py tests/test_backups.py tests/test_report_bundle.py -q
123 passed in 22.69s
```

Complete verification:

```text
python -m pytest -q
1847 passed, 33 skipped, 1 warning in 345.92s (0:05:45)

python -m compileall -q swinglab tests
exit 0

git diff --check
exit 0 (Git emitted only working-tree LF-to-CRLF conversion notices)
```

The single warning is the existing Starlette deprecation warning for `httpx`
with `starlette.testclient`. The sole skip in the prior fix selectors is the
POSIX-only descriptor ownership test on this Windows host.

### Concerns

- An absent exact-name lookup is linear in the number of entries in the pinned
  parent directory. This is intentional: native end-of-directory is the only
  complete absence result when legitimate sessions may exceed any fixed
  entry-position cap, while the streaming implementation keeps memory bounded.
- Windows native enumeration and exact relative opening were executed on this
  Windows host. The change is isolated inside the existing Windows-only branch;
  POSIX traversal behavior is unchanged.

## Final whole-plan fix wave — cleanup serialization and direct relative opens

### Status and scope

This single consolidated fix wave started from
`08e2dba8bb2fda267dbe0df75345a1f16841b8c5`. It changes only:

- `swinglab/report_artifacts.py`
- `swinglab/web/jobs.py`
- `tests/test_report_artifacts.py`
- `tests/test_report_bundle_job_publication.py`
- this report

No push, merge, deployment, feature activation, live mutation, or worktree
cleanup was performed. The one fix commit containing this section has subject
`fix: tighten publication cleanup and Windows opens`; its SHA is reported in
the handoff because a commit cannot contain its own final object ID.

### RED evidence

Three behavioral selectors were run together against the pre-fix production
code:

```text
python -m pytest tests/test_report_bundle_job_publication.py::test_guided_completion_keeps_manager_lock_through_pinned_cleanup tests/test_report_artifacts.py::test_windows_handle_enumeration_is_case_exact_and_not_path_redirectable tests/test_report_artifacts.py::test_windows_relative_open_finds_exact_child_beyond_topology_scan_limit -q
3 failed in 2.55s
```

The failures reached the intended boundaries:

- The completion context blocked while its real pinned publication context was
  still open, but a concurrent real retention pass reached its first
  lock-protected recovery step. This proved `_lock` had already been released
  before pinned-handle cleanup.
- Both ordinary and oversized-parent Windows relative opens raised the injected
  `AssertionError: relative child open enumerated its parent`, proving the open
  path still called `_win_iter_directory` through `_win_has_exact_child`.

There were no collection, fixture, permission, or setup failures.

### Corrections

1. Guided completion now uses `with self._lock, publication_stack:`. Contexts
   exit in reverse order, so the `ExitStack` finishes all pinned-handle cleanup
   before the manager lock is released. Transaction, commit, verification, and
   in-memory state behavior are otherwise unchanged.
2. `_win_open_relative` no longer performs `_win_has_exact_child` enumeration.
   It opens the requested component directly with the existing `NtOpenFile`
   call, the pinned parent as `OBJECT_ATTRIBUTES.RootDirectory`, and
   `OBJECT_ATTRIBUTES.Attributes = 0` so no case-insensitive lookup flag is
   supplied. The existing exact final-name check remains in the open helper;
   caller-side reparse, file/directory type, final-path containment, and handle
   identity checks remain unchanged.
3. `_win_iter_directory` and `_win_scan_directory` remain available only for
   bounded bundle-topology validation. The existing 4,096-entry topology cap
   and nonrecursive handle-based enumeration are unchanged.

### Regression coverage

- The lifecycle test blocks the actual guided publication context during
  cleanup, starts a real `_cleanup_expired()` retention pass, and observes its
  first operation after acquiring the manager lock. Retention cannot reach that
  point until cleanup is released, and both threads must terminate without an
  error.
- The Windows handle test performs a real capped handle scan, then replaces the
  enumeration iterator with a fail-fast sentinel. An exact-case relative file
  open succeeds and resolves to the intended file without enumeration; a
  wrong-case open still fails behaviorally.
- The oversized-parent test creates 4,097 ordinary entries plus the requested
  directory. A full topology scan still rejects the extra entry; after
  enumeration is disabled, exact and missing relative opens are resolved
  directly through `NtOpenFile` without scanning the parent.

### Verification

Exact final-fix selectors:

```text
3 passed in 2.93s
```

Prior fix selectors and original boundary cases:

```text
prior 8 selectors: 7 passed, 1 skipped in 1.36s
original 15 named selectors (35 parametrized cases): 35 passed in 3.59s
```

Required focused and lifecycle gates:

```text
python -m pytest tests/test_report_artifacts.py tests/test_report_bundle_job_publication.py tests/test_caps.py -q
295 passed, 9 skipped in 20.85s

python -m pytest tests/test_history_reset_core.py tests/test_backups.py tests/test_report_bundle.py -q
123 passed in 23.09s
```

Complete verification on the unchanged final code tree:

```text
python -m pytest -q
1848 passed, 33 skipped, 1 warning in 352.52s (0:05:52)

python -m compileall -q swinglab tests
exit 0

git diff --check
exit 0 (Git emitted only working-tree LF-to-CRLF conversion notices)
```

The first full-suite attempt exposed the existing polling race in
`test_failed_job_source_also_deleted_when_configured`: its poll observed the
durable `FAILED` status before the additive deletion note was visible in that
response; the subsequent assertion saw source deletion complete. That untouched
failure-path selector passed four consecutive isolated reruns, and the full
suite then passed without any code change. The single warning is the existing
Starlette deprecation warning for `httpx` with `starlette.testclient`.

### Concerns

- Case-exact Windows child lookup now relies on the native case-sensitive
  `NtOpenFile` object attributes plus the existing exact final-name check. Both
  exact and wrong-case behavior executed successfully on this Windows host.
- The unrelated failed-source polling race remains outside this fix wave. It is
  documented above rather than expanded into an unrequested fifth production
  area.
- The sole skip in the prior fix selectors is the POSIX-only descriptor
  ownership test on this Windows host; POSIX production traversal is unchanged.
