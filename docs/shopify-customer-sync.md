# Shopify customer sync

## Status and safety boundary

CaddieInsight has two complementary Shopify identity paths:

1. The existing inbound bridge from merged
   [GitHub PR #28](https://github.com/kylejames0513-bot/SwingLab/pull/28),
   “Harden Shopify account and entitlement synchronization,” consumes signed
   Shopify customer and order webhooks. It provisions store-first account
   stubs, preserves the Shopify customer ID as the durable identity, and keeps
   purchases attached through account claim and email-change edge cases.
2. The outbound Admin GraphQL bridge links app-first registrations to Shopify.
   It is controlled by `shopify_customer_sync.enabled`, which is `false` in
   both code defaults and the shipped `config.yaml`.

Merely deploying the code, adding an Admin token, or running ordinary tests
must not contact Shopify or start a customer backfill. Production activation
is a separate operator decision after the checklist below.

Shopify availability is never a prerequisite for a CaddieInsight account. The
local authentication account and database user commit first. Outbound work is
recorded as pending and handled after registration; a timeout, throttle, API
error, or Shopify outage cannot roll the local account back.

## Ownership and identity rules

CaddieInsight remains authoritative for authentication, golf profiles, clubs,
distances, sessions, shots, goals, progress, reports, recommendations,
coaching data, and app preferences. Shopify remains authoritative for
products, apparel, orders, shipping, fulfillment, refunds, discounts, and
commerce history.

The bridge follows these rules:

- Passwords, password hashes, login codes, and sessions are never sent to
  Shopify.
- Normalize email only for the initial lookup/upsert. Once linked, the stored
  Shopify customer ID is the permanent relationship.
- Automatic email matching requires the normalized, verified CaddieInsight
  authentication email. If an installation allows password signup without
  inbox verification, leave outbound sync pending or `requires_review` until
  verification; typing an address is not proof that the registrant owns an
  existing Shopify customer.
- A Shopify email update never silently changes the CaddieInsight login
  identity. A claimed app account keeps its verified app email until a
  separate inbox-verification flow approves a change.
- An account whose prior Shopify identity is locked but currently unlinked
  must not be linked to a different Shopify customer merely because an email
  matches. Deletion, redaction, duplicate matches, and conflicting customer
  IDs require review.
- Sync only the shared identity fields the app actually owns. The current user
  model does not need Shopify order history, addresses, phone numbers, or golf
  data.

The local sync states are:

- `not_started`
- `pending`
- `synced`
- `failed`
- `requires_review`

## Shopify Admin API contract

All Admin API traffic originates in the backend through
`swinglab.integrations.shopify.admin.ShopifyAdminClient`. The client uses the
store domain, a backend-only access token, and an explicit stable API version.

The installation needs:

- `read_customers` for exact-email lookup, reconciliation, and dry-run
  backfill;
- `write_customers` for the
  [`customerSet`](https://shopify.dev/docs/api/admin-graphql/latest/mutations/customerSet)
  upsert/update operation;
- access to protected customer data and the email field, as applicable to the
  app's distribution type.

The bridge uses Shopify's
[`customerByIdentifier`](https://shopify.dev/docs/api/admin-graphql/latest/queries/customerByIdentifier)
query for read-only exact-email reconciliation and `customerSet` for the
idempotent upsert. Local backfill pages are bounded, and an invalid or
conflicting identity stops for review rather than being guessed. Shopify's
[protected customer data requirements](https://shopify.dev/docs/apps/launch/protected-customer-data)
require data minimization, transparency, security, and purpose limitation.

Use the latest stable version verified by the project. Shopify releases stable
versions quarterly and recommends explicitly versioning requests; review
`SHOPIFY_ADMIN_API_VERSION` each quarter against the
[versioning schedule](https://shopify.dev/docs/api/usage/versioning). Do not use
`unstable` or a release candidate in production.

## Configuration

The non-secret behavior lives in `config.yaml`:

```yaml
shopify_customer_sync:
  enabled: false
  auto_sync_new_users: true
  request_timeout_seconds: 10
  max_attempts: 5
  retry_base_seconds: 30
  retry_max_seconds: 3600
  retry_jitter_ratio: 0.2
```

`auto_sync_new_users` has no effect unless `enabled` is true. Required backend
environment variables are:

- `SHOPIFY_ADMIN_STORE_DOMAIN` (the canonical `your-store.myshopify.com`
  hostname, never a custom storefront domain)
- `SHOPIFY_ADMIN_ACCESS_TOKEN`
- `SHOPIFY_ADMIN_API_VERSION`

The existing inbound webhook bridge separately requires
`SHOPIFY_STORE_DOMAIN` and `SHOPIFY_WEBHOOK_SECRET`.
`SWINGLAB_ADMIN_TOKEN` protects the local operator routes. Keep all secret
values in the deployment platform's secret manager; `.env.example` is an empty
inventory, not a configuration file.

## Registration and retry behavior

For an eligible verified registration:

1. Validate signup input.
2. Commit the CaddieInsight authentication account and user.
3. Persist Shopify sync as `pending`.
4. Queue backend sync without delaying the successful signup response.
5. Reuse the existing Shopify customer when the exact normalized email has one
   unambiguous match; otherwise use the supported idempotent customer upsert.
6. Persist the canonical Shopify customer ID, mark `synced`, record the success
   time, and clear the prior safe error.

Passwordless registration already proves the email with its sign-in code. When
the outbound bridge and email delivery are both enabled, the classic password
signup reuses the existing claim-code step before creating and queueing a new
account. If email delivery is unavailable, local signup still succeeds, but an
unverified row stops at `requires_review` without contacting Shopify.

Transport failures, Shopify 5xx responses, throttling, and explicitly retryable
errors use bounded exponential backoff beginning at
`retry_base_seconds`, stable per-user jitter, and a cap of
`retry_max_seconds`. A provider `Retry-After` longer than the short in-request
retry cap is persisted on the durable row rather than slept through or retried
early. Stop after `max_attempts` automatic attempts and leave the row available
for manual retry.

Validation errors, authentication/permission failures, duplicate matches,
identity locks, redaction conflicts, and other permanent or ambiguous outcomes
must not loop automatically. Store a short, non-PII error code/summary and use
`failed` or `requires_review` as appropriate. Never persist raw Admin API
tokens, full request headers, or unnecessary Shopify response bodies.

## Administrative visibility

The existing `SWINGLAB_ADMIN_TOKEN` bearer guard protects:

- `GET /admin/shopify-sync` for linked state, sync status, last success, safe
  error, attempt count, customer ID, matching backfill `user_ref`, scheduled
  retry time, manual-retry availability, and manual-review state;
- `POST /admin/shopify-sync/{user_id}/retry` for an explicit retry.

These routes must retain the current admin convention: absent or incorrect
credentials receive the same 404 as an unknown route, and token comparison is
constant-time. Regular account pages may say that the store is connected but
must not expose sync errors, attempt history, or the Shopify customer ID.

## Existing-user backfill

Backfill is an explicit operator workflow:

```text
swinglab shopify-backfill --sessions-dir /data/sessions [--batch-size N] [--after CURSOR] [--json]
swinglab shopify-backfill --sessions-dir /data/sessions --apply [--batch-size N] [--after CURSOR] [--json]
```

Those commands show the live Railway path; use the actual existing sessions
directory in other environments. The CLI refuses a missing `swinglab.db`
instead of creating a misleading empty database. Dry-run is the default.
`--apply` is always explicit. The command must:

- process a deterministic, bounded batch;
- skip already-linked users;
- skip or flag locked/unlinked and redacted identities;
- match only an exact normalized email;
- report duplicate or ambiguous matches as `requires_review`;
- avoid creating a customer when ownership is uncertain;
- record per-user success/failure only in apply mode;
- be restartable with `--after` and idempotent across reruns;
- print counts and safe identifiers rather than customer names or emails;
- return a final summary, with `--json` available for protected operator
  automation.

Opening the database uses the application's normal `UserStore` initialization,
so the additive sync columns/index may be installed before either mode runs.
After that schema preflight, dry-run does not change customer links or per-user
sync state and makes only read-only Shopify lookups.

Do not schedule this command and do not run `--apply` automatically during
application startup, deployment, or production activation.

## Webhook configuration

Keep the existing raw-body HMAC endpoint and both accepted paths:

- `POST /webhooks/shopify`
- `POST /webhooks/shopify/`

The operational topics currently used are:

- `orders/paid`
- `orders/cancelled`
- `refunds/create`
- `customers/create`
- `customers/update`
- `customers/delete`

The same signed handler also recognizes the required privacy events
`customers/data_request`, `customers/redact`, and `shop/redact`. Configure
privacy webhooks as required for the app. The current bridge acknowledges data
request and shop-redaction events but does not yet implement a complete export
or shop-wide erasure workflow; do not describe it as full privacy compliance.

Do not change webhook paths, HMAC handling, order idempotency, merchant SKUs,
product handles, or entitlement behavior as part of enabling outbound customer
sync.

## Staged rollout

### Stage 0: deploy inert

- Keep `shopify_customer_sync.enabled: false`.
- Run the full Python 3.11 suite and production-container smoke test.
- Confirm ordinary signup, login, purchase webhooks, and `/healthz` are
  unchanged.
- Confirm no Admin token appears in built assets, HTML, JSON, logs, or errors.

### Stage 1: development store

- Install only the required customer scopes and protected email access.
- Set the Admin variables in a development environment.
- Enable the flag only there.
- Test no-existing-customer, existing-customer, outage, throttle, invalid
  input, duplicate match, email change, and manual retry cases.

### Stage 2: internal accounts

- Enable a small internal cohort.
- Verify the stored customer IDs in both systems.
- Confirm existing Shopify customers and order history are reused, not
  duplicated.
- Review admin health, errors, attempt counts, and rate-limit behavior.

### Stage 3: existing-user backfill

- Run dry-run only and review every conflict category.
- Apply one small batch, verify manually, then increase batch size gradually.
- Stop immediately on unexplained duplicate IDs, elevated errors, unexpected
  customer creation, or rate-limit pressure.

### Stage 4: production activation

- Enable automatic sync for new verified signups.
- Keep manual retry available.
- Monitor sync success/failure counts and Shopify throttling.
- Do not activate unrelated order-import, apparel, fulfillment, or profile
  overwrite features.

No stage in this document authorizes a production deploy, secret change,
Shopify configuration mutation, or backfill run.

## Rollback

1. Set `shopify_customer_sync.enabled: false` first and redeploy/restart so no
   new outbound attempts begin.
2. Leave the inbound signed webhook bridge enabled.
3. Redeploy the previously successful application commit if code rollback is
   needed.
4. Keep the existing `/data/sessions` volume and `swinglab.db`; additive sync
   metadata is retained.
5. Never delete Shopify customers created during the rollout as a rollback
   shortcut. Preserve stored IDs and reconcile them after the incident.
6. Verify `/healthz`, ordinary registration while Shopify is unavailable,
   signed Shopify test delivery, existing account links, and entitlements.

Code rollback cannot undo an external customer creation safely. Database or
Shopify cleanup is a separate reviewed data operation, not part of routine
rollback.

## Manual production checklist

- [ ] Feature flag is still false before the release.
- [ ] Python 3.11 CI, security checks, and container smoke are green.
- [ ] A current WAL-safe backup and scratch restore have been verified by the
      operator; repository backup tooling alone does not prove production is
      backed up.
- [ ] Duplicate non-null Shopify customer IDs are zero and the database unique
      constraint is present.
- [ ] `read_customers`, `write_customers`, and protected email access are
      approved for this installation.
- [ ] Admin token and API version exist only in the backend secret manager.
- [ ] The canonical `SHOPIFY_ADMIN_STORE_DOMAIN` is configured separately
      from any custom storefront domain.
- [ ] Email delivery/code verification is working for automatic new-user sync.
- [ ] Registration succeeds with Shopify offline.
- [ ] Existing-customer reuse and no-customer creation both pass in development.
- [ ] Duplicate email, locked identity, deletion, redaction, and email-change
      cases produce the expected safe outcome.
- [ ] Admin health and manual retry work with a valid operator token and remain
      hidden otherwise.
- [ ] Backfill dry-run summary has been reviewed; production `--apply` has not
      been scheduled automatically.
- [ ] Rollback owner, previous application commit, monitoring window, and stop
      criteria are recorded.

## Known limitations

- The deployment remains single-replica while SQLite, local files, and
  in-process coordination are authoritative.
- Sync links identity only. It does not import order history, fulfillment,
  refunds, addresses, passwords, or golf data.
- Customer names are not currently part of the local user model; do not invent
  or infer them during sync.
- A claimed user's Shopify-side email change does not change the app login
  email automatically.
- Complete `customers/data_request` export and `shop/redact` erasure remain
  separate privacy-compliance work.
- Disabling or reverting the bridge stops new work but does not erase customers
  already created in Shopify.
