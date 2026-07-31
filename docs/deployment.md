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

## Rollback

1. Redeploy the previously successful application commit or revert the
   foundation commit.
2. Keep the existing Railway volume attached at the same mount point. Never
   create a replacement volume as a rollback shortcut.
3. Verify `GET /healthz`.
4. Verify a signed Shopify test delivery reaches `/webhooks/shopify`.
5. Verify existing account and entitlement records are visible before accepting
   new purchases.

Rollback changes application code only. It must not delete data, rotate secrets,
alter DNS, or replace the persistent volume.
