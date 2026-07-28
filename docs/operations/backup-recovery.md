# CaddieInsight backup and recovery runbook

<!-- markdownlint-disable MD013 MD060 -->

**Stage:** 0B foundation only

**Last reviewed:** 2026-07-27

**Activation status:** Inactive. No storage account, credentials, schedule,
Railway setting, production backup, or automatic restore is configured by this
repository change.

## Safety contract

The production application stores its SQLite database at
`/data/sessions/swinglab.db` and enables WAL mode. Committed data can therefore
reside in `swinglab.db-wal` while the service is running. Never use `cp`,
`Copy-Item`, `rclone`, a volume-file download, or a similar raw file copy on the
live database.

The Stage 0B CLI instead uses Python's SQLite online backup API to create a
transactionally consistent, closed snapshot. It never checkpoints, replaces, or
opens the production database writable. The web process, Dockerfile command,
Railway service, volume, and existing retention behavior are unchanged.

Restore tooling has a second hard boundary:

- restore commands require `CADDIE_RESTORE_ENABLED=true`;
- a restore always creates a unique child under an existing scratch root;
- `/data`, anything below `/data`, filesystem roots, existing destinations,
  overlapping backup paths, and live session trees are rejected;
- the verifier opens only the restored scratch database read-only;
- it never initializes `UserStore`, `JobManager`, or the web application, whose
  startup behavior could migrate tables, run retention, or requeue jobs.

These controls make the repository tooling safe to review and test. They do not
make production backed up: that requires the separately approved actions in
[Production activation checklist](#production-activation-checklist).

## What one backup contains

Each generation is a new immutable prefix and local bundle:

```text
<backup-id>/
  database/swinglab.db
  artifacts/<job-id>/<report-relative paths>
  manifest.json
  COMPLETE.json
```

The database is a WAL-safe online snapshot. Artifacts are selected from `done`
jobs in that snapshot and are limited to:

- the `report.html` identified by `jobs.report_rel`;
- the sibling `metrics.json`, when present;
- regular files below the sibling `media/` directory.

The backup deliberately excludes:

- `swinglab.db`, `swinglab.db-wal`, and `swinglab.db-shm` as raw source files;
- uploaded `source.*` videos;
- analysis `work/` trees;
- files for queued, processing, and failed jobs;
- symlinks, devices, path traversal, and files outside the selected
  deliverable directory.

This preserves retained customer reports and generated media without defeating
the application's source-video minimization. The shipped application deletes
raw uploads after terminal jobs and deletes terminal session directories after
180 days.

Every database snapshot and artifact has a SHA-256 digest and byte size in
`manifest.json`. The manifest also contains:

- `PRAGMA integrity_check` result;
- `PRAGMA user_version`;
- exact counts and deterministic whole-table digests for `jobs`, `users`,
  `pro_grants`, `shopify_orders`, `gear_orders`, `email_codes`, and
  `auth_attempts`;
- aggregate entitlement state at the capture timestamp;
- active and voided Shopify-order counts and remaining recorded days;
- gear order, quantity, and cancellation totals;
- non-negative entitlement/purchase invariant results.

Reconciliation proves that the restored snapshot exactly preserves the captured
database state and its current invariants. It is not an independent comparison
with Shopify or Stripe and cannot reconstruct all historic entitlement events:
for example, the current cancellation flow zeros recorded Shopify grant days.

No database rows, emails, names, order identifiers, source filenames,
credentials, or signed URLs appear in the manifest or normal command output.
`COMPLETE.json` contains the manifest digest and is written and uploaded last.
A partial object prefix without this marker is not a completed backup.
Remote database and artifact object keys are opaque ordinals; logical job paths
exist only inside the encrypted/private manifest body, not in provider-visible
object names or metadata.

SHA-256 detects corruption but is not proof against a malicious party that can
replace both data and metadata. The approved store must therefore also provide
private access, versioning or bounded object lock where available, and
independent administrative control. A signed manifest can be added later if the
threat model requires cryptographic authenticity outside the storage account.

## Required environment variables

Environment variable names are documented here; values must live only in an
approved secret manager or protected operator session.

### Explicit activation gates

| Variable | Required for | Meaning |
|---|---|---|
| `CADDIE_BACKUP_ENABLED` | create, upload | Must equal `true`; absent or any other value is inert |
| `CADDIE_RESTORE_ENABLED` | download, restore drill | Must equal `true`; absent or any other value is inert |

Setting either gate alone does nothing. There is no scheduler or application
startup hook.

### Non-secret object-store settings

| Variable | Required | Meaning |
|---|---|---|
| `CADDIE_BACKUP_BUCKET` | yes | Existing private bucket name |
| `CADDIE_BACKUP_PREFIX` | yes | Dedicated non-public prefix, for example `caddieinsight/backups` |
| `CADDIE_BACKUP_REGION` | yes | Provider's S3 region value |
| `CADDIE_BACKUP_ENDPOINT_URL` | for non-AWS-compatible endpoints | Absolute HTTPS S3-compatible endpoint; plaintext HTTP is rejected |
| `CADDIE_BACKUP_ADDRESSING_STYLE` | no | `auto` (default), `path`, or `virtual` |
| `CADDIE_BACKUP_SSE` | yes | `AES256`, `aws:kms`, or `provider-managed` |
| `CADDIE_BACKUP_KMS_KEY_ID` | only for `aws:kms` | Stable key UUID or key ARN; aliases are rejected because S3 resolves them before verification; treat as sensitive configuration |

`provider-managed` omits an encryption request header. It is acceptable only
after verifying that the selected private bucket encrypts every new object by
default. Some providers encrypt automatically; others require a bucket setting.
Unlike `AES256` and `aws:kms`, this mode cannot be confirmed generically through
the S3-compatible response, so the provider guarantee and bucket configuration
remain an explicit approval record.

For `aws:kms`, use a stable key UUID or key ARN. The tooling verifies the key
reported by object metadata. KMS aliases are deliberately rejected because S3
may report the resolved backing-key ARN, making a generic alias comparison
ambiguous.

### Secret writer credentials

| Variable | Required | Permission target |
|---|---|---|
| `CADDIE_BACKUP_ACCESS_KEY_ID` | yes for upload | Dedicated backup writer |
| `CADDIE_BACKUP_SECRET_ACCESS_KEY` | yes for upload | Dedicated backup writer |
| `CADDIE_BACKUP_SESSION_TOKEN` | only for temporary credentials | Dedicated backup writer |

The writer should be limited to create/multipart-upload operations and the
minimum read permission needed for `HeadObject` within the dedicated backup
prefix. On AWS S3 and some compatible providers, `HeadObject` is authorized by
`GetObject`, so object-body read capability cannot always be separated from the
metadata checks this tooling requires. Prefer short-lived credentials, scope
read/write to this prefix only, and deny list access outside it, deletion,
bucket administration, public-access changes, and credential administration.
Retention deletion should be a separate lifecycle policy controlled by a
different identity. Confirm the selected provider's actual IAM semantics.

### Secret restore credentials

| Variable | Required | Permission target |
|---|---|---|
| `CADDIE_RESTORE_ACCESS_KEY_ID` | yes for download | Dedicated read-only restore identity |
| `CADDIE_RESTORE_SECRET_ACCESS_KEY` | yes for download | Dedicated read-only restore identity |
| `CADDIE_RESTORE_SESSION_TOKEN` | only for temporary credentials | Dedicated read-only restore identity |

The restore identity needs read access only to completed backup prefixes. Keep
it separate from the writer so a compromised runtime cannot read historical
customer data.

The CLI never accepts credentials as command-line arguments, never prints its
settings object, and suppresses raw SDK errors that might contain signed URLs,
authorization material, or provider request details.

## Encryption and access requirements

Do not approve a storage target until all of these are true:

1. The bucket is private and has no public development URL or anonymous list/get.
2. API access uses TLS with normal certificate verification. The CLI rejects a
   custom endpoint that is not HTTPS.
3. Every object, including metadata, is encrypted at rest. For
   `provider-managed`, verify the provider's default; for `AES256` or `aws:kms`,
   test that the requested mode is honored.
4. Backup writer, restore reader, and lifecycle administrator are separate
   least-privilege identities where the provider permits.
5. Object versioning or bounded object lock protects completed generations from
   accidental deletion, without retaining customer media forever.
6. Bucket names, prefixes, and object metadata contain no customer identifiers.
7. Account MFA, billing alerts, storage alerts, access logging, and credential
   rotation are enabled.
8. The provider's region, data-processing terms, deletion semantics, and
   incident-response process are approved for identifiable customer media.

The database itself contains account and purchase data. Server-side encryption
protects storage media, not a compromised storage administrator. If that threat
is in scope, add client-side authenticated encryption and independent key
management before production activation.

## Retention policy

The initial policy is one complete daily generation retained for 30 days. It is
simple to restore and avoids content-reference and garbage-collection failure
modes. No lifecycle rule is created by Stage 0B.

Before enabling a lifecycle rule, obtain privacy and operations approval for:

- a maximum nominal database/artifact RPO of 24 hours;
- a maximum 30-day period in which a locally expired or deleted report can
  remain in backup storage;
- no raw upload or work-file retention;
- no `rclone sync` or mirror operation that can propagate local deletion into
  every recovery copy;
- lifecycle deletion only after a completed-generation age reaches 30 days;
- any legal hold as an explicit exception with an owner and expiry;
- quarterly scratch restore drills.

Longer database-ledger retention may eventually be required for financial or
legal reasons, while customer media should remain short-lived. Because Stage 0B
bundles them together, do not extend the bundle retention period silently.
Split database-only archival from media backup before adopting a longer policy.

## RPO and RTO options

| Mode | Nominal database RPO | Artifact RPO | Cost/complexity | Status |
|---|---:|---:|---|---|
| Repository tooling only | Unbounded | Unbounded | No operating cost | Current Stage 0B state |
| Manual complete generation | Time since last successful command | Same | Low | Requires approval and operator |
| Daily complete generation | Up to 24 hours | Up to 24 hours | Lowest practical scheduled option | Recommended first activation |
| Every 6 hours | Up to 6 hours | Up to 6 hours | Roughly four times the writes/storage before lifecycle expiry | Future option |
| Litestream plus daily artifacts | Potentially minutes for SQLite | Up to 24 hours | Supervisor/configuration plus two recovery paths | Not implemented |

These are objectives, not guarantees. An RPO exists only when monitoring proves
fresh completed generations.

RTO is currently unmeasured. For the first drill, record:

- download start/end and bytes;
- checksum-verification duration;
- SQLite integrity/reconciliation duration;
- operator review duration;
- total time to a validated scratch copy.

An initial planning target is a validated scratch restore within two hours at
the current small data size. Do not promote that target to an operational
commitment until a production-derived, non-destructive drill measures it.

## Backup monitoring and failure alerts

Any future scheduler must treat the command exit code and remote
`COMPLETE.json` as the success signal. Alert on:

- any nonzero create, upload, download, or restore-drill exit;
- no completed generation newer than the selected RPO;
- a missing or mismatched completion/manifest digest;
- SQLite integrity, required-table, invariant, row-count, table-digest, or
  ledger-reconciliation failure;
- artifact disappearance, mutation, size mismatch, or SHA-256 mismatch;
- an abrupt backup-size or artifact-count drop;
- partial prefixes accumulating without a completion marker;
- credential expiry, access denial, bucket encryption/privacy drift, quota, or
  billing threshold;
- a missed quarterly restore drill.

Logs may include only backup ID, timestamps, counts, byte totals, duration, and
sanitized failure category. Never log environment dictionaries, credentials,
customer paths, database rows, email addresses, order IDs, presigned URLs, or
raw SDK exceptions.

## Exact first-backup procedure

This procedure is documentation, not approval to run it. Production activation
requires every item in [Production activation checklist](#production-activation-checklist).
It is exact only after an execution mechanism with legitimate access to the
mounted `/data` volume is separately approved.

1. Approve a provider, region, private bucket, encryption mode, 30-day
   lifecycle policy, budget alert, and data-processing terms.
2. Create three scoped identities: a prefix-scoped backup writer without
   delete, a read-only restore identity, and a lifecycle administrator. Store
   their values in an approved secret manager.
3. Approve the execution locus. The local `/data/sessions` path exists inside
   the single Railway service instance that has the volume mounted; a laptop or
   unrelated runner cannot read it. Use a time-bounded operator session that
   already sees that mount, or another reviewed mechanism that accesses the
   same volume without creating a replica, remounting it, or copying the live
   database. Do not proceed while this mechanism is undecided.
4. In that approved session, confirm the exact deployed commit and readable
   source path, use private ephemeral space such as `/tmp`, and install the
   optional transport only through the separately approved mechanism:

   ```text
   python -m pip install ".[backup]"
   ```

   Installing a dependency in a production session is itself a production
   action. Stage 0B does not perform or authorize it.
5. Create private local parents outside the sessions tree. On Linux:

   ```text
   install -d -m 0700 /tmp/caddie-backups /tmp/caddie-restore
   ```

6. Inject the documented non-secret settings and writer credentials without
   echoing them. Set the explicit gate:

   ```text
   CADDIE_BACKUP_ENABLED=true
   ```

7. Choose a new output name and create the local generation:

   ```text
   python -m swinglab.backups create \
     --sessions-dir /data/sessions \
     --output-dir /tmp/caddie-backups/first-generation
   ```

   The command must end successfully and create both `manifest.json` and
   `COMPLETE.json`. It must not change `/data/sessions`.

8. Upload only after an operator independently verifies bucket privacy,
   encryption, and credential scope:

   ```text
   python -m swinglab.backups upload \
     --bundle /tmp/caddie-backups/first-generation \
     --confirm-private-bucket
   ```

9. Confirm the remote generation contains `COMPLETE.json` as the final object
   and record its safe backup ID, timestamps, byte totals, and command duration.
10. Disable the writer gate and remove writer credentials from the operator
   environment.
11. Do not declare success until the complete scratch restore drill below passes
    using the separate read-only identity.
12. After the verified drill and according to an approved secure-erasure
    procedure, remove the temporary local bundle. Do not delete the remote
    generation.

The local full bundle needs temporary free space roughly equal to the SQLite
snapshot plus retained deliverables. Check free space before the first run.

## Complete scratch restore drill

1. Use an isolated operator environment. Do not stop, reconfigure, or connect
   the production application to the restored database.
2. Create two new private parent directories outside `/data`:

   ```text
   install -d -m 0700 /tmp/caddie-downloads /tmp/caddie-restore
   ```

3. Inject only the common non-secret settings and read-only restore credentials.
   Set:

   ```text
   CADDIE_RESTORE_ENABLED=true
   ```

4. Download the chosen completed generation into a new path:

   ```text
   python -m swinglab.backups download \
     --backup-id <recorded-backup-id> \
     --output-dir /tmp/caddie-downloads/drill-input
   ```

5. Run the drill:

   ```text
   python -m swinglab.backups restore-drill \
     --bundle /tmp/caddie-downloads/drill-input \
     --scratch-root /tmp/caddie-restore
   ```

6. The command succeeds only after:

   - database and artifact SHA-256 verification;
   - `PRAGMA integrity_check` returns exactly `ok`;
   - every critical-table count and deterministic digest matches the manifest;
   - entitlement and Shopify/gear purchase aggregates match the capture-time
     manifest;
   - all artifact checksums match.

7. Review the generated `restore-report.json`. Record the backup ID, drill date,
   durations, restored bytes, operator, result, and any alert ticket. Do not
   record customer data.
8. Never start the web application against the scratch database. A future
   disaster-recovery cutover requires a separate, approved runbook and change
   window.
9. Disable the restore gate, remove restore credentials, and securely erase
   local drill files under the approved policy.

## Rollback and disable procedure

Because Stage 0B has no runtime hook, rollback before activation is simply
leaving both enable gates unset and not installing the optional transport.

After a separately approved scheduler exists:

1. disable the scheduler without changing the web application's start command;
2. unset `CADDIE_BACKUP_ENABLED` and `CADDIE_RESTORE_ENABLED`;
3. revoke writer and restore credentials;
4. confirm the application still starts with the existing Dockerfile `CMD` and
   `/data/sessions` path;
5. retain already completed remote generations until their approved lifecycle
   expiry—do not mass-delete them as part of disabling the job;
6. preserve the last successful restore record and investigate the failure;
7. re-enable only through a new approval after a synthetic and scratch drill.

Disabling backup tooling does not require an application, database, Railway
volume, Shopify, authentication, or purchase-behavior rollback.

## Cost comparison

Prices were checked against official provider documentation on 2026-07-27 and
must be rechecked before approval.

| Option | Fixed account effect | Backup storage | Transfer/requests | Recovery boundary |
|---|---:|---:|---|---|
| Railway Pro plus volume backups | Pro plan minimum `$20/month` instead of the current Hobby `$5/month`; subscription counts toward Railway usage | Incremental unique backup blocks use Railway volume pricing, `$0.15/GB-month` | Existing Railway billing model | Same Railway project/environment; volume wipe deletes backups |
| Private S3-compatible, Cloudflare R2 example | No storage minimum within published free tier | Standard `$0.015/GB-month`; first `10 GB-month` free | Standard Class A `$4.50/million`, Class B `$0.36/million`, Internet egress free; free request allowances apply | Separate provider/account when configured independently |
| Private S3-compatible, Backblaze B2 example | No storage minimum within published free allowance | `$6.95/TB-month` (about `$0.00695/GB-month`); first 10 GB free | Upload free; published free egress up to three times average stored data, then provider rates | Separate provider/account when configured independently |

Railway's backup documentation describes daily snapshots retained six days,
weekly snapshots retained one month, and monthly snapshots retained three
months. It describes snapshots as covering SQLite and charges only unique
incremental blocks, but it does not document an application quiesce, WAL
checkpoint, or SQLite consistency guarantee. Treat a native volume snapshot as
unproven until a restored SQLite copy passes this runbook's integrity and
ledger checks. Railway backups are also restored only in the same project and
environment, and wiping the volume deletes its backups.

The Stage 0A live dashboard reported that backups were unavailable on the
current Hobby workspace and presented them as Pro-only. Railway's public volume
backup page does not currently state that plan restriction. Confirm the live
account's eligibility and quote immediately before approval; do not upgrade
based only on this document.

At the Stage 0A observed `171.9 MB` used volume size, 30 complete uncompressed
daily copies would be approximately `5.2 GB` before growth. That can fit inside
the examples' 10 GB free tiers, although actual usage will grow and each
Railway-to-object-store upload incurs Railway service egress (currently
`$0.05/GB`). Complete generations trade a small amount of storage for simpler,
more reliable recovery. Revisit content-addressed deduplication only when
measured storage cost justifies its reference-management risk.

Railway native backups are operationally simpler and may be worth the higher
plan floor, but they share the Railway failure domain. Private S3-compatible
storage is materially cheaper per GB and supplies an independent failure
domain, but requires credentials, monitoring, lifecycle configuration, and
tested recovery. The strongest future posture may use both; Stage 0B purchases
or configures neither.

Official sources:

- [Railway pricing](https://docs.railway.com/pricing)
- [Railway volume backups](https://docs.railway.com/volumes/backups)
- [Railway volume reference](https://docs.railway.com/volumes/reference)
- [Cloudflare R2 pricing](https://developers.cloudflare.com/r2/pricing/)
- [Cloudflare R2 S3 compatibility](https://developers.cloudflare.com/r2/get-started/s3/)
- [Cloudflare R2 data security](https://developers.cloudflare.com/r2/reference/data-security/)
- [Backblaze B2 pricing](https://www.backblaze.com/cloud-storage/pricing)
- [Backblaze B2 S3-compatible API](https://www.backblaze.com/apidocs/introduction-to-the-s3-compatible-api)
- [Backblaze B2 server-side encryption](https://www.backblaze.com/docs/cloud-storage-server-side-encryption)
- [SQLite online backup API](https://www.sqlite.org/backup.html)

## Production activation checklist

Every item below is still a production action and is not authorized by Stage 0B:

- select and purchase/enable Railway Pro backups or a private object store;
- approve provider, region, data-processing terms, retention, privacy impact,
  and budget;
- create a private bucket and configure encryption, versioning/object lock,
  lifecycle, access logs, MFA, quotas, and billing alerts;
- create and securely inject the documented writer and restore credentials;
- install the optional `backup` dependency in an approved operator/runtime
  environment;
- approve a time-bounded execution mechanism that can read the existing
  `/data/sessions` mount without changing replicas or copying the live database;
- choose an approved scheduler and alert receiver;
- run the first production backup;
- perform and time a production-derived scratch restore drill;
- approve measured RPO/RTO and on-call ownership;
- decide whether to add Railway native snapshots as a second recovery layer;
- separately design any actual disaster-recovery cutover.

No production backup should be called operational until a completed remote
generation and successful scratch drill are both recorded.
