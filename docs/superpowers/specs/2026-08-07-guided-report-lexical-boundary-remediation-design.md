# Guided Report Lexical Boundary Remediation Design

**Status:** Approved on 2026-08-07 by the user's instruction to proceed with the previously described narrow remediation.

## Goal

Ensure a structured report can be completed, classified for coaching, or counted for quota only when its immutable bundle is reached through the exact lexical directory chain owned by that job:

`<sessions>/<job-id>/out/<analysis-child>/report-bundle-<attempt-id>/...`

The change must reject symlinks, junctions, reparse points, aliases, and directory-entry substitutions at every boundary before a report becomes authoritative.

## Root Cause

`JobManager._complete_job()` and `JobManager._completed_report_classification()` currently call `Path.resolve()` before invoking `load_published_bundle()`. Resolution erases whether the lexical job root, `out`, or direct analysis child was a symlink or Windows junction. The bundle loader then validates a coherent physical target instead of the job-owned lexical chain.

This creates two failures:

- completion can associate one processing row with another job's coherent published bundle;
- structured quota classification can continue treating a redirected bundle as coaching-ready.

The existing bundle loader correctly pins and validates the bundle itself. The missing boundary is its ancestry from the configured sessions directory through the job, `out`, and analysis directories.

## Considered Approaches

### 1. Lexical `lstat` preflight only

Check the three directory entries before calling the existing loader.

This is the smallest change and rejects static redirects, but it remains raceable on POSIX: an ancestor can be renamed after the check and before the path-based bundle open. Rejected.

### 2. Pinned read-only ancestry chain — selected

Parse the four persisted job-relative report paths without resolving them. Pin the sessions root, job root, `out`, analysis child, and bundle root in order. On POSIX, traverse with descriptor-relative no-follow operations; on Windows, open reparse points explicitly for inspection, reject them, verify handle identity/final path, and keep delete sharing disabled. Feed the already pinned bundle root into the existing manifest/topology/checksum validation core.

This directly protects the trust boundary, uses only five directory handles, and keeps read validation separate from deletion privileges.

### 3. Reuse the owned-tree deletion planner

The deletion planner already has strong no-follow behavior, but it recursively scans whole trees, requests deletion/quarantine capabilities, and couples read authorization to cleanup mechanics. Rejected as unnecessarily broad and risky.

## Selected Interface

`swinglab.report_artifacts` will expose a context-managed structured-job loader:

```python
@dataclass(frozen=True)
class PinnedJobReportBundle:
    bundle: PublishedReportBundle
    report_rels: tuple[str, str, str, str]

    def verify_lexical_identity(self) -> None: ...


@contextmanager
def open_job_published_bundle(
    sessions_dir: Path,
    *,
    job_id: object,
    report_rel: object,
    report_view_rel: object,
    manifest_rel: object,
    checksums_rel: object,
) -> Iterator[PinnedJobReportBundle]: ...
```

All four input paths are job-relative persisted paths. The loader requires exact POSIX spelling and exactly this shape:

`out/<one-safe-analysis-child>/report-bundle-<32-lowercase-hex>/<canonical-filename>`

The four paths must share one analysis child and one bundle root. The loader returns canonical job-relative paths in report, view, manifest, checksums order.

## Filesystem Contract

The context owns every handle and closes each exactly once in reverse order, including partial-open and validation failures.

On POSIX:

- require nonzero `O_DIRECTORY` and `O_NOFOLLOW` plus descriptor-relative stat/open support;
- pin the sessions root, then traverse job ID, `out`, analysis child, and bundle root with `follow_symlinks=False` and `openat` semantics;
- compare pre-open stat identity with `fstat` identity;
- pass the pinned bundle handle into existing bundle validation;
- verify lexical identities again before leaving the context.

On Windows:

- use the existing `CreateFileW` primitives with `FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS`;
- reject `FILE_ATTRIBUTE_REPARSE_POINT` at every depth;
- require directory type and compare volume/file-index identity plus final path;
- do not share delete access while the boundary is authoritative;
- close all handles before reset, retention, or cleanup operations need to rename paths.

Missing platform capabilities fail closed.

## Completion Flow

Guided completion will stop using `_result_rel()` for structured artifacts because it resolves paths too early.

It will instead:

1. validate the job ID and derive job-relative report paths lexically with `absolute()`/`abspath`, never `resolve()`;
2. require `result.session_dir` to be exactly `<sessions>/<job-id>/out/<one child>` lexically;
3. enter `open_job_published_bundle(...)`;
4. validate manifest presentation and persisted entitlement policy;
5. verify the lexical chain immediately before the guarded SQLite update;
6. keep the context open through the transaction commit;
7. expose all four report paths atomically only after every check succeeds.

Any boundary failure leaves both in-memory and persisted state processing with all report paths null and `structured_report = false`.

Legacy completion remains unchanged.

## Classification and Quota Flow

Structured classification will call the same context-managed loader directly from the persisted row. It will not resolve the job, `out`, or analysis path first.

- A valid bundle returns coaching or capture from `bundle.view.outcome`.
- Any malformed path, link, reparse point, substitution, missing capability, integrity failure, or policy mismatch returns the existing `corrupt` classification.
- Corrupt structured output cannot power coaching or trends and conservatively consumes one allowance; it is not a courtesy re-film rejection.
- Genuine legacy rows retain their existing compatibility path.

## Verification Matrix

The implementation must prove:

- valid coaching and capture bundles retain current behavior;
- job-root redirect: completion rejected; classification corrupt and charged;
- `out` redirect: completion rejected; classification corrupt and charged;
- analysis-child redirect: completion rejected; classification corrupt and charged;
- bundle-root redirect remains rejected by existing bundle validation;
- wrong-case, nested, cross-child, duplicate, reserved, trailing-dot/space, colon, backslash, and parent paths fail closed;
- bundle/manifest attempt IDs must still agree;
- a directory rename/replacement while pinned is refused on Windows or detected before commit on POSIX;
- capability/open/identity failures close every acquired handle;
- corrupted paths do not alter donor/target sentinels;
- reset and retention still work after read contexts close;
- legacy behavior is unchanged.

Tests must use temporary directories only and detach redirects explicitly before cleanup.

## Non-Goals

- No web layout, API, native client, rollout, feature flag, or deployment change.
- No changes to legacy report interpretation.
- No recursive tree scan and no delete/quarantine rights in the read validator.
- No claim that SQLite and filesystem directory mutation become atomically linearizable. Pinned validation prevents redirected reads; every later structured read revalidates and fails closed after subsequent tampering.

## Rollback

The remediation is isolated to the structured loader and its two JobManager call sites. Reverting its focused commit restores the previous behavior without changing persisted schema or bundle format. The guided presentation remains default-off and no live state is changed by this work.
