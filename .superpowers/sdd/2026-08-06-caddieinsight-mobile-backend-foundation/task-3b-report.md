# Task 3B report — recovery-fence ledger foundation

## Status

DONE. The controller-routed Gate 3B foundation is implemented and verified on
`codex/caddieinsight-mobile-implementation`. The intended commit subject is
`feat: add recovery fence ledger foundation`.

No web-startup composition, live backup provider, real scratch restore,
restore-to-service reconciliation, dependent-route activation, deployment, or
production mutation was added or run.

## Implemented

- Added the closed recovery-record set: `cutover_baseline`, `token_revoke`,
  `push_environment_cutoff`, and `review_access_revision`. No Task-6 erasure
  kind is accepted. Token records persist only versioned, domain-separated
  selector and stored-token-verifier HMACs; reserved push/review shapes accept
  only their bounded non-secret fields and HMAC identities.
- Added strict sorted/compact UTF-8 JSON with a final newline, duplicate-key and
  non-finite-number rejection, exact record/HEAD shapes, canonical UUID/time/
  integer/SHA/key-ID validation, content-addressed record hashes, retained-key
  chain HMAC verification, explicit predecessor traversal, genesis/sequence
  enforcement, and duplicate logical-event rejection.
- Added `RecoveryFenceLedger.append_and_publish`: every append runs under one
  resolved-path in-process mutex plus the fixed-byte cross-process
  `.recovery-fence.lock`, refetches and validates the complete remote chain,
  writes/fsyncs the local immutable record, performs immutable remote put plus
  readback, advances `HEAD` with CAS plus readback, then durably writes local
  `HEAD` and the SQLite checkpoint. Provider I/O occurs outside SQLite
  transactions.
- Added CAS rebase, harmless orphan behavior, exact logical retry, immediate
  local-volume-loss reconstruction, and exact orphan recovery across active
  chain-HMAC key rotation by reusing the journaled retained key/record identity.
  Missing retained keys fail closed.
- Added monotonic local checkpoint validation. A prior checkpoint must appear at
  its exact sequence/key/hash/HMAC-key position in every newly validated remote
  ancestry, both on chain read and again inside the checkpoint-save transaction.
- Added durable local record, `HEAD`, checkpoint, and baseline-journal writes.
  File contents and parent renames are fsynced; real directory open/fsync errors
  raise safe failures. Only explicitly known Windows directory-fsync
  unsupported cases are tolerated.
- Added dedicated `RecoveryFenceStoreSettings` using only
  `CADDIE_RECOVERY_FENCE_*`. It rejects backup-prefix overlap in a shared
  bucket, exposes exactly `HEAD` and canonical immutable
  `records/<sequence>-<sha256>.json` keys, hides credentials from repr, and has
  no backup fallback, list, or delete API.
- Added `RecoveryFenceRemoteStore` with HEAD plus pinned GET validation,
  checksum/size/SSE/KMS/ETag enforcement, immutable `If-None-Match` writes,
  `If-Match` HEAD CAS, exact readback, and safe provider-error suppression. It
  negatively probes stale/conflicting conditions and fails closed when a
  provider ignores or does not support them.
- Added the approval-gated offline baseline journal with durable phases
  `lineage_prepared -> backup_verified -> record_published -> head_published ->
  scratch_verified -> accepted`. One operation/journal is allowed globally.
  Exact retry reuses lineage, facts, record, and operation ID. Acceptance
  requires an injected affirmative exact scratch proof and a matching current
  remote chain/checkpoint.
- `VerifiedBackupFacts` independently carries the verified manifest file hash,
  verified manifest `database.sha256`, and `baseline_db_checkpoint`; validation
  requires the last two lowercase SHA-256 values to be exactly equal before any
  remote record write. Gate 3B does not compute that value.
- Added `swinglab recovery-fence-ledger initialize-baseline` with an exact
  `CADDIE_RECOVERY_FENCE_ENABLED=true` gate and four explicit approvals. The
  shipped composition intentionally has no real verifier, refuses safely, and
  creates no lock/store/state. Gate 3C must inject the real immutable-backup and
  scratch-restore evidence adapters.
- Added a pure `StartupRecoveryInputs -> StartupRecoveryDecision` policy. A
  pristine generation-0/all-false input requires no remote I/O and calls no
  provider; any checkpoint/journal/nonterminal work or dependent feature
  requires an accepted baseline, dedicated credentials, both write/readback
  proofs, and current-chain validation. `app.py`, `config.py`, and `config.yaml`
  remain unchanged.
- Added `boto3>=1.37.32,<2` to the production `web` extra while retaining it in
  the operator `backup` extra. Added protected-setting, IAM, cutover ordering,
  rollback, conditional-write, and credential-rotation documentation.

## Files changed

- `swinglab/web/recovery_fence_ledger.py` — canonical ledger, locking,
  checkpoint ancestry, baseline journal/state machine, injected proof
  protocols, and pure startup policy.
- `swinglab/backups/store.py` — dedicated recovery-fence settings and narrow
  conditional/readback store adapter.
- `swinglab/backups/cli.py`, `swinglab/cli.py` — inert approval-gated offline
  command and top-level routing before ordinary app configuration.
- `tests/test_recovery_fence_ledger.py` — integrity, retry, crash, race,
  durability, cross-process lock, baseline, CLI, and startup-policy coverage.
- `tests/test_recovery_fence_remote_store.py` — dedicated settings, key grammar,
  conditional semantics, readback, error-suppression, and dependency coverage.
- `pyproject.toml`, `.env.example`, `docs/environment.md`,
  `docs/deployment.md`, and `docs/operations/backup-recovery.md` — production
  dependency and operational contracts.

## RED and GREEN evidence

Every command used the required plan virtualenv:

`& '.superpowers\sdd\2026-08-06-caddieinsight-mobile-backend-foundation\.venv\Scripts\python.exe' -m pytest ...`

1. Pre-change direct regression checkpoint:
   - `tests/test_mobile_rate_limits.py tests/test_backups.py tests/test_config.py`
   - `78 passed in 17.52s`
2. Initial Gate 3B RED:
   - `tests/test_recovery_fence_ledger.py tests/test_recovery_fence_remote_store.py`
   - `30 failed in 2.36s`, all expected missing ledger/store/CLI contracts.
3. Additional test-first RED checkpoints captured conditional-header
   enforcement (`3 failed, 7 passed`), web-extra metadata (`1 failed`), accepted
   replay/prefix overlap (`2 failed`), generation-0 rejection (`1 failed`),
   integer-cutoff exact retry (`1 failed`), and CLI verifier-secret suppression
   (`1 failed`). Each corresponding focused test was green before proceeding.
4. Singleton-baseline RED:
   - a second operation after a preparation crash was accepted;
   - `1 failed in 1.56s`.
   - GREEN after the `BEGIN IMMEDIATE` singleton guard: `1 passed in 1.31s`.
5. Independent-audit RED batch:
   - manifest database/checkpoint binding, directory-open EIO, directory-fsync
     EIO, and checkpoint ancestry;
   - `4 failed in 1.95s`.
   - GREEN after the four root fixes: `4 passed in 1.72s`.
6. Same-root and spawned-process lock verification:
   - `2 passed in 4.74s`.
7. Retained-chain-key exact-retry RED:
   - after `record_published`, rotating the active key rebuilt a different
     orphan and conflicted with the journal;
   - `1 failed in 2.00s`.
   - GREEN after binding append to the journaled retained key/identity:
     `1 passed in 2.12s`.
8. Final focused Gate 3B GREEN after all fixes:
   - `49 passed in 7.65s`.

## Fresh completion verification

- Final combined focused/direct regression command:
  `tests/test_recovery_fence_ledger.py tests/test_recovery_fence_remote_store.py
  tests/test_mobile_rate_limits.py tests/test_backups.py tests/test_config.py -q`
- Result before the final retained-key refinement: `126 passed in 20.57s`, with
  no pytest warnings.
- Fresh final result after the refinement: `127 passed in 20.73s`, with no
  pytest warnings.
- Python compilation of `swinglab` and both new test modules exited 0.
- `git diff --check` exited 0. Git emitted only the checkout's existing
  LF-to-CRLF working-copy conversion notices; there were no whitespace errors.
- A base-to-worktree diff check confirmed `swinglab/web/app.py`, `config.yaml`,
  and `swinglab/config.py` are unchanged.

## Independent review

The first independent read-only review requested changes for four real gaps:
manifest database/checkpoint binding, global baseline-operation singleton,
directory-fsync error handling, and checkpoint ancestry. All four were fixed
test-first. The re-review reported no remaining Critical or Important findings
and approved Gate 3B, with `48 passed in 8.58s` and a clean diff check. The final
narrow re-review of the retained-key exact-retry refinement also approved with
no Critical or Important regressions and `49 passed in 7.85s`.

## Self-review

- **Integrity and privacy:** Remote truth is reconstructible from `HEAD` and
  explicit immutable predecessor keys alone. Canonical body, key/hash identity,
  per-record HMAC, HEAD identity, sequence, genesis, and retained-key coverage
  are all independently validated. Raw selectors, token verifiers, project IDs,
  reviewer accounts, passwords, bearer credentials, and provider errors are not
  persisted or displayed by this gate.
- **Concurrency and durability:** Same-root threads serialize before provider
  I/O; a real spawned process is blocked on the fixed lock byte until the first
  process releases it. Distinct hosts use provider CAS and rebase. Directory EIO
  is injected and fails closed. No SQLite transaction spans backup verification,
  scratch verification, or object-store I/O.
- **Baseline correctness:** One durable operation and one genesis are allowed.
  Immutable facts conflict on replay. The database checkpoint is the supplied
  verified manifest database SHA, not a locally computed WAL/checkpoint token.
  `scratch_verified` and `accepted` require an injected exact proof tied to
  lineage, backup, manifest, database checkpoint, and record hash.
- **Least privilege:** The recovery role has a separate namespace, prefix, and
  credentials; the adapter offers no list/delete or backup operation. Both
  conditional write modes are negatively proven before semantic acceptance.
- **Startup purity:** Policy evaluation is data-only. No provider factory,
  client, startup hook, worker, request route, or live feature flag is wired.
- **Scope:** No schema migration was needed because Gate 3A already landed the
  exact checkpoint/journal/accepted-baseline tables. No Task 3C restore logic,
  Task 3D credential mutation, Task 6 erasure records, or live operation was
  introduced.

## Concerns and deferred items

- Gate 3C still owns real immutable-backup manifest lineage, real scratch
  restore, restore-to-service rejection/reconciliation, and production accepted-
  baseline composition. The default CLI refusal is intentional until then.
- The shipped browser history-reset surface is enabled. Startup reconciliation
  must remain unwired until an accepted baseline exists and Gate 3C proves the
  complete activation order.
- The repository-wide suite and container smoke were intentionally not run; the
  controller reserved broader combined gates and explicitly limited this task
  to focused/direct regressions. No live provider or deployment verification was
  attempted.
- Gate 3A's previously documented deferred boolean-version validation issue is
  unchanged and outside Gate 3B.
