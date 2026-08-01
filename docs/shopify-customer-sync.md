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
   It is controlled by `shopify_customer_sync.enabled`. Bare-code defaults
   remain `false`; the checked-in CaddieInsight `config.yaml` is `true` after
   the verified production binding and worker rollout. Do not copy that
   enabled value into another environment without completing this runbook.
3. The separately gated Customer Account authorization-code/PKCE path provides
   Shopify-owned sign-in and recovery after durable identity reconciliation.
   It is disabled by default and is documented in
   [Shopify Customer Account migration](shopify-customer-accounts.md).

Merely deploying the code, adding Admin credentials, or running ordinary tests
must not contact Shopify or start a customer backfill. Production activation
is a separate operator decision after the checklist below.

Shopify availability is never a prerequisite for a CaddieInsight account. The
local authentication account and database user commit first. Outbound work is
recorded as pending and handled after registration; a timeout, throttle, API
error, or Shopify outage cannot roll the local account back.

## Ownership and identity rules

CaddieInsight remains authoritative for golf profiles, clubs, distances,
sessions, shots, goals, progress, reports, recommendations, coaching data,
and app preferences. Until the separately gated Customer Account cutover,
legacy app authentication remains the fallback. At cutover, Shopify Customer
Accounts becomes authoritative for sign-in, recovery, commerce identity, and
Shopify marketing consent. Shopify remains authoritative for products,
apparel, orders, shipping, fulfillment, refunds, discounts, and commerce
history.

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
`swinglab.integrations.shopify.admin.ShopifyAdminClient`. The preferred
authentication mode exchanges the dedicated Dev Dashboard app's client ID and
client secret for a short-lived access token. The process caches that token
until shortly before expiry and performs one bounded refresh/replay if Shopify
rejects it early. A legacy static Admin token remains an alternative for older
installations; mixed or incomplete modes are rejected. Every request uses the
canonical store domain and an explicit stable API version.

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
`unstable` or a release candidate in production. This release and the
version-controlled Shopify app configuration are verified against `2026-07`.

## Configuration

The checked-in CaddieInsight deployment behavior lives in `config.yaml`:

```yaml
shopify_customer_sync:
  enabled: true
  auto_sync_new_users: true
  request_timeout_seconds: 10
  max_attempts: 5
  retry_base_seconds: 30
  retry_max_seconds: 3600
  retry_jitter_ratio: 0.2
```

`auto_sync_new_users` has no effect unless `enabled` is true. Required backend
environment variables are:

- `SHOPIFY_STORE_DOMAIN` (the same canonical `your-store.myshopify.com`
  hostname used to validate inbound webhook store headers)
- `SHOPIFY_ADMIN_STORE_DOMAIN` (the canonical `your-store.myshopify.com`
  hostname; it must exactly match normalized `SHOPIFY_STORE_DOMAIN`)
- `SHOPIFY_ADMIN_CLIENT_ID` and `SHOPIFY_ADMIN_CLIENT_SECRET` (preferred), or
  the legacy `SHOPIFY_ADMIN_ACCESS_TOKEN`, but never both modes
- `SHOPIFY_ADMIN_API_VERSION`
- `SHOPIFY_CUSTOMER_SYNC_COHORT_PERCENT` (`0` by default; set explicitly from
  `0` through `100`)

The manual purchase/customer webhook bridge also requires
`SHOPIFY_WEBHOOK_SECRET`. Mandatory privacy deliveries from the dedicated app use
`SHOPIFY_PRIVACY_WEBHOOK_SECRET`; the handler validates the raw body against
the correct eligible secret and also requires the exact
`X-Shopify-Shop-Domain` for every recognized mutating topic. The privacy secret
by itself keeps this shared endpoint available but does not enable Pro purchase
links or commerce-connected signup semantics. When outbound sync is enabled,
the worker rechecks the normalized webhook store against the
Admin client before enrollment and every batch. A missing or split-store
configuration reports a PII-free `mismatch`, starts no outbound worker, and
makes no Admin customer request; inbound signed webhooks remain available.
`SWINGLAB_ADMIN_TOKEN` protects the local operator routes. Keep all secret
values in the deployment platform's secret manager; `.env.example` is an empty
inventory, not a configuration file.

The global flag and cohort percentage are independent gates. Enabling the flag
while the percentage remains `0` synchronizes nobody. A nonzero cohort also
requires a stable `SWINGLAB_SECRET`; the backend uses it to select a
deterministic keyed bucket without logging or persisting a cohort identifier.

## Registration and retry behavior

For an eligible verified registration:

1. Validate signup input.
2. Store a ten-minute signup intent containing only the scrypt password hash,
   never the plaintext password, and email a single-use verification code.
3. After inbox proof, atomically consume the intent and commit the
   CaddieInsight account as verified.
4. Persist Shopify sync as `pending` and queue backend work without delaying
   the successful signup response.
5. Reuse the existing Shopify customer when the exact normalized email has one
   unambiguous match; otherwise use the supported idempotent customer upsert.
6. Persist the canonical Shopify customer ID, mark `synced`, record the success
   time, and clear the prior safe error.

Passwordless registration already proves the email with its sign-in code. A
Shopify-linked, passwordless, pre-purchase, or outbound-cohort identity cannot
be claimed or linked while email delivery is unavailable. A deployment with a
primary Shopify commerce bridge configured requires inbox proof before a
password account can become an identity candidate. Without that primary
bridge, an address with no linked, purchased, or outbound-cohort identity can
retain the documented local no-mail fallback.

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

- `GET /admin/shopify-sync` for exact sync counts/times, coarse binding state,
  safe `user_ref` records, last success, safe error, attempt count, scheduled
  retry time, manual-retry availability, and manual-review state;
- `GET /admin/shopify-sync/ref/{user_ref}` for an on-demand exact Shopify
  customer ID and the same sync-health fields for one record;
- `POST /admin/shopify-sync/ref/{user_ref}/retry` for an explicit retry.

These routes must retain the current admin convention: absent or incorrect
credentials receive the same 404 as an unknown route, and token comparison is
constant-time. Raw local user IDs and Shopify customer IDs are not pagination
or URL tokens. The broad list remains PII-minimized; the exact Shopify ID is
revealed only by the protected single-record detail route and every
administrative response uses `Cache-Control: no-store`. Public `/healthz`
exposes only coarse booleans, not customer counts, cohort size, or exact
timestamps. Regular account pages may say that the store is connected but
must not expose sync errors, attempt history, or the customer ID.

## Existing-user backfill

Backfill is an explicit operator workflow:

```shell
# Database/schema only; no Shopify request and no write.
swinglab shopify-backfill --sessions-dir /data/sessions --preflight-only --json

# Authenticate the canonical store and persist only its domain + exact Shop GID.
swinglab shopify-backfill --sessions-dir /data/sessions --bind-only \
  --confirm-store your-store.myshopify.com --json

# Review one page or the complete restartable dry run.
swinglab shopify-backfill --sessions-dir /data/sessions \
  --batch-size 25 --json
swinglab shopify-backfill --sessions-dir /data/sessions \
  --all-batches --json

# Apply only after review, still in controlled batches.
swinglab shopify-backfill --sessions-dir /data/sessions --apply \
  --batch-size 25 --json
```

Those commands show the live Railway path; use the actual existing sessions
directory in other environments. The CLI refuses a missing `swinglab.db`
instead of creating a misleading empty database. Dry-run is the default, and
`--apply` is always explicit. An unbound or wrong-store database is refused
before customer requests. `--bind-only` verifies the exact confirmation,
authenticates the shop, persists the full private Shop GID, and exits without
customer lookup or mutation. The command must:

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

Manual resolution uses a protected environment variable for the target
customer ID rather than placing it in shell history:

```shell
swinglab shopify-resolve-customer --sessions-dir /data/sessions \
  --user-ref <safe-user-ref> --customer-id-env SHOPIFY_RESOLUTION_CUSTOMER_ID \
  --json
```

Opening the database uses the application's normal `UserStore` initialization.
After schema and binding preflight, dry-run does not change customer links or
per-user sync state and makes only read-only Shopify lookups. A nonzero exit
means the batch still contains a failure or review item; do not ignore it.

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
`customers/data_request`, `customers/redact`, and `shop/redact`.
`customers/data_request` atomically captures an integrity-checked, expiring
export snapshot for protected operator delivery; event replays reuse the same
request. Customer redaction deletes the subject's Shopify order and gear
ledgers, parked value, pending links, and any stored privacy export containing
that subject. It preserves only independently owned CaddieInsight credentials,
analysis history, and the non-identifying entitlement end on a claimed
account. One minimal tombstone retains the stable Shopify customer id,
redaction flag, and event time—never email or local account id—solely to reject
delayed deliveries that would otherwise recreate erased identity or value.
This suppression record has no expiry because expiry would reopen that
resurrection path. Explicit `orders_to_redact` values are retained only as
keyed one-way order fences, using a per-database random key, so paid or
cancellation replays without a customer id cannot restore order email, gear,
or entitlement data. Exact-store `shop/redact` transactionally fences active
workers and erases the local Shopify ledgers, bindings, pending links,
store-only stubs, and sync state while preserving independently owned
CaddieInsight credentials and golf analyses. Opaque composite delivery
receipts for all three privacy topics survive shop erasure, so an exact replay
cannot recapture or mutate state created after the original event. A distinct
signed request or redaction still applies, and a new explicit authenticated
store bind is required before outbound sync reopens.

Provider calls and redaction share a dedicated per-database advisory lock.
Fence validation uses only a brief SQLite transaction; the application
database lock is released before network I/O, so a Shopify timeout does not
block unrelated accounts or local writes. Redaction waits for an already
authorized provider call, then invalidates its compare-and-set result. The
webhook route runs synchronous application work in a worker thread so waiting
for that ordering lock cannot block the ASGI event loop. Dry-run backfill uses
a read-only SQLite connection and the same advisory ordering lock. Data-request
rows come from one read transaction, while filesystem inventory and JSON
encoding run without a SQLite or `UserStore` lock; a short final transaction
atomically claims the delivery and publishes the ready snapshot.

The repository's linked `shopify.app.toml` declares `2026-07`, only
`read_customers,write_customers`, and all three compliance topics at the
existing HTTPS endpoint. `shopify app config validate` is safe; do not run
`shopify app deploy` until the endpoint code and the dedicated app's client
secret are deployed together. Shopify configuration release and production app
installation are separately monitored actions.

### Privacy request operator handoff

The privacy webhook stores an export; it never emails customer data. An
authorized operator can inspect only PII-free request metadata, then write one
integrity-checked export to an explicit new file:

```text
swinglab shopify-privacy --sessions-dir /data/sessions list
swinglab shopify-privacy --sessions-dir /data/sessions export REQUEST_ID --output <new-private-file>
```

Export refuses to overwrite any existing path, creates the file with owner-only
permissions where the platform supports them, removes a partial file on
failure, and never prints customer data. Deliver that file through the approved
privacy/support channel. Only after that external handoff succeeds, record it:

```text
swinglab shopify-privacy --sessions-dir /data/sessions mark-delivered REQUEST_ID --confirm-external-delivery
swinglab shopify-privacy --sessions-dir /data/sessions purge-expired
```

Marking delivery does not extend the fixed retention deadline.

Do not change webhook paths, HMAC handling, order idempotency, merchant SKUs,
product handles, or entitlement behavior as part of enabling outbound customer
sync.

## Staged rollout

### Stage 0: deploy inert

- Keep `shopify_customer_sync.enabled: false`.
- Run the full Python 3.11 suite and production-container smoke test.
- Confirm ordinary signup, login, purchase webhooks, and `/healthz` are
  unchanged.
- Confirm no Admin credential or exchanged token appears in built assets,
  HTML, JSON, logs, or errors.

### Stage 1: development store

- Install only the required customer scopes and protected email access.
- Set the preferred Admin client ID/client secret and explicit API version in
  a development environment.
- Set a stable `SWINGLAB_SECRET`, set the cohort percentage to `100`, and
  enable the flag only there.
- Test no-existing-customer, existing-customer, outage, throttle, invalid
  input, duplicate match, email change, and manual retry cases.

### Stage 2: internal accounts

- Set a small nonzero `SHOPIFY_CUSTOMER_SYNC_COHORT_PERCENT`; keep the global
  flag enabled only after development-store checks pass.
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

- Raise the cohort percentage deliberately toward `100` for new verified
  signups; do not treat the global flag alone as activation.
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
- [ ] Exactly one Admin authentication mode and the explicit API version exist
      only in the backend secret manager.
- [ ] Existing manual webhook HMAC and dedicated-app privacy HMAC have each
      been verified against the deployed Railway values.
- [ ] Normalized `SHOPIFY_STORE_DOMAIN`,
      `SHOPIFY_ADMIN_STORE_DOMAIN`, and the persisted database binding all
      identify the same canonical `*.myshopify.com` store; the protected
      health view is not `mismatch`.
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
- Privacy request snapshots still require an authorized operator to deliver
  the export through the approved support/privacy channel before retention
  expiry; the webhook does not email sensitive data automatically.
- Disabling or reverting the bridge stops new work but does not erase customers
  already created in Shopify.
