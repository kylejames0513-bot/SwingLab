# Production and Railway contract

## Current topology

- The Shopify storefront and CaddieInsight application use separate origins.
- `PUBLIC_BASE_URL` identifies the Railway application origin.
- The GitHub repository remains `kylejames0513-bot/SwingLab`.
- Railway platform settings, environment values, persistent-volume settings,
  and DNS are managed outside this repository.

This document records the existing topology. It does not authorize or perform
DNS, secret, data, Railway-setting, or production-deployment changes.
Actual production hostnames and environment values are intentionally omitted.

## Repository deployment contract

Railway builds the root `Dockerfile`; there is intentionally no Railway-specific
manifest in this foundation.

The following are compatibility constraints:

- Python 3.11 remains the container runtime.
- The root `config.yaml` is copied to `/app/config.yaml`.
- The Python distribution and console command remain `swinglab`.
- The pose model is warmed during the image build.
- The platform-provided `PORT` is used, with 8000 as the local default.
- `GET /healthz` remains the container health check.
- The start command remains functionally equivalent to:

  ```text
  swinglab serve --host 0.0.0.0 --port ${PORT:-8000} --sessions-dir /data/sessions
  ```

- The existing persistent volume remains mounted so `/data/sessions/swinglab.db`
  and session artifacts survive image replacement.

Changing `/data/sessions`, `swinglab.db`, the command name, or the volume can
make production appear to have lost accounts and paid entitlements even when
the old data still exists.

## Scaling constraint

Use one application replica while SQLite, local session files, the in-process
job queue, and startup requeueing remain in use. Before horizontal scaling,
move job coordination and durable state to services that provide cross-replica
locking and transactional guarantees.

## Pull-request deployment impact

A draft pull request has no production effect. Merging to the production branch
may trigger Railway's existing automatic deployment. Review the complete image,
route, environment, and data-path contracts before merge. This repository must
not initiate a production deployment as part of foundation work.

## Shopify customer-sync deployment gate

Bare-code installs keep outbound Admin API customer sync disabled. The
checked-in CaddieInsight configuration enables it only after the verified
binding and worker rollout; Admin credentials alone must never authorize a
new activation or run the existing-user backfill.

Before enabling the flag:

1. verify a current WAL-safe backup and scratch restore;
2. confirm the database has no duplicate non-null Shopify customer IDs and its
   unique constraint is present;
3. confirm `read_customers`, `write_customers`, and protected email access for
   the installed Shopify app;
4. configure the canonical Admin store, explicit stable API version, and
   exactly one authentication mode: preferred client ID/client secret or
   legacy static access token;
5. run the read-only schema preflight, then use `--bind-only` with exact
   canonical-store confirmation to persist the authenticated Shop GID without
   reading or mutating customers;
6. test existing-customer reuse, new-customer creation, Shopify outage,
   throttling, duplicate matches, and verified-email behavior in development;
7. verify coarse public `/healthz`, exact protected
   `GET /admin/shopify-sync`, and `user_ref`-based manual retry;
8. run `swinglab shopify-backfill --sessions-dir /data/sessions` without
   `--apply`, review the dry-run summary, then use explicit small `--apply`
   batches only during a monitored stage.

Merging code, setting credentials, and enabling automatic sync are separate
actions. None authorizes a production backfill. The complete staged checklist
is in [Shopify customer sync](shopify-customer-sync.md).

## Rollback

1. If outbound customer sync is active, set
   `shopify_customer_sync.enabled: false` first and redeploy/restart so no new
   attempts begin.
2. Redeploy the previously successful application commit or revert the
   application commit.
3. Keep the existing Railway volume attached at the same mount point. Never
   create a replacement volume as a rollback shortcut.
4. Keep the existing inbound Shopify webhooks configured.
5. Verify `GET /healthz`.
6. Verify registration still succeeds while the Admin API is unavailable.
7. Verify a signed Shopify test delivery reaches `/webhooks/shopify`.
8. Verify existing account, customer-link, and entitlement records are visible
   before accepting new purchases.

Rollback changes application code only. It must not delete data, rotate secrets,
alter DNS, replace the persistent volume, or delete Shopify customers created
during the rollout. External customer creation is reconciled after the
incident; it is never undone as a routine rollback shortcut.

## Recovery-fence cutover ordering

The web composition now enforces the recovery gate before `JobManager`, workers,
or routes exist. Because the shipped configuration exposes account and history
surfaces, a staging/production process without an accepted baseline will fail
startup by design. Do not make the first strict-environment deployment the
baseline initializer.

Keep the existing web release serving while an explicitly approved offline
operator process runs `recovery-fence-ledger initialize-baseline` against the
same persistent database. That operation must prepare one lineage, create and
read back the immutable backup, bind its exact lowercase database SHA-256 as
`baseline_db_checkpoint`, publish/read back genesis plus conditional `HEAD`,
complete the exact scratch service-restore proof, and mark that same journal
accepted. Then deploy this web composition with the explicit
`CADDIEINSIGHT_MOBILE_DEPLOYMENT_ENVIRONMENT=staging|production`, canonical
HTTPS `PUBLIC_BASE_URL`, dedicated recovery-fence credentials, and the complete
retained `MOBILE_STATE_HMAC_KEYRING`.

Before traffic, verify `/healthz` reports the intended closed mobile environment,
normalized public origin, application-ID policy revision/list, and expected
native-auth status. Startup must read and apply every newer remote token revoke
before a request can authenticate. An unreconciled reserved push/review record,
missing record/key, stale head, provider outage, or missing accepted proof is a
deployment stop—not a reason to bypass the gate.

## Native email-auth activation

Keep `web.mobile_native_auth_enabled: false` through schema/recovery cutover.
Before changing it, verify the accepted baseline and current conditional HEAD
readback, complete the approved scratch recovery drill, retain every referenced
`MOBILE_STATE_HMAC_KEYRING` key, configure the canonical HTTPS
`PUBLIC_BASE_URL`, and prove plaintext/HTML email delivery. The startup
readiness check must pass before either native email endpoint admits requests;
`/healthz.native_email_auth` provides the non-sensitive flag and abuse-bound
readback.

Rollback turns the endpoint flag off, but must retain the HMAC keyring and
recovery-fence access until every nonterminal auth rotation is complete.
Feature-off startup deliberately resumes those journals before workers or
route admission; removing recovery access is not a safe rollback.

## Store-review auth activation

The shipped review admission is default-deny, so the documented review routes
remain `404` even when native email auth is enabled. Entitlements Task 5 owns the
provider-specific Apple submission window, standing Google App-access record,
dedicated credential hash, exact supported builds, and recovery reconciler.
Activate a lane only after that composition is present, its exact identity tuple
matches the intended submitted build, and startup validates the accepted remote
chain. Lane activation is latched at process startup; opening a lane that was
closed at startup requires a fresh recovery-validated deployment. Do not place
reviewer accounts or passwords in YAML/environment values;
do not expose lane state in `/healthz`.

Closing a lane must make the admission recheck fail. Review bearers are then
rejected and revoked on their next authenticated request; their short-lived
scope expiry remains authoritative even if cleanup is delayed.

Do not enable horizontal replicas while recovery state, SQLite, filesystem
artifacts, and worker coordination remain process/local-volume based. The root
container contract and one-replica topology remain unchanged.
