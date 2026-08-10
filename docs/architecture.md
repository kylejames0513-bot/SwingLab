# CaddieInsight architecture

## Project map

```text
swinglab/
  analysis/              Stable facade for the swing-analysis engine
  api/                   Stable, lazy web application/API facade
  integrations/shopify/ Stable facade for Shopify catalog and webhook code
  web/                   FastAPI pages, API routes, accounts, jobs, and adapters
  *.py                   Existing engine modules kept at compatible import paths
  templates/             Web and report templates
deploy/                  Operator scripts and deployment documentation
docs/                    Architecture, environment, deployment, and ADRs
tests/                   Unit, integration, web, and production-contract tests
.github/workflows/       Test and security validation
Dockerfile               Live Railway container contract
```

The internal distribution, import namespace, and console command remain
`swinglab`. CaddieInsight is the customer-facing product name.

## Responsibility boundaries

### Swing analysis engine

`swinglab.pipeline` is the single orchestration path for a video. It coordinates
audio detection, frame extraction, pose tracking, event detection, metrics,
coaching, and deliverable generation. The CLI and background job runner both
call this implementation. New callers should import the supported entrypoints
from `swinglab.analysis`; existing imports remain supported.

The engine must not import the web, API, or integration layers.

### Web application

`swinglab.web` owns HTML pages, authentication, account persistence, job
coordination, throttling, email, billing presentation, and application
composition. The current FastAPI factory remains
`swinglab.web.app.create_app`. New deployments and embedding code may import
the lazy facade `swinglab.api.create_app`.

### API

The JSON endpoints currently share the FastAPI application in
`swinglab.web.app`. `swinglab.api` establishes a stable boundary before routers
and serializers are extracted. Route URLs remain unchanged during that later
migration.

### Shopify integration

The current implementations remain at `swinglab.web.shop` for Storefront
catalog access and `swinglab.web.shopify_billing` for signed webhooks and
purchase entitlements. `swinglab.integrations.shopify` exposes those modules as
`storefront` and `webhooks`, giving future code a stable integration boundary
without breaking existing imports.

Merged
[GitHub PR #28](https://github.com/kylejames0513-bot/caddieinsight/pull/28) is the
inbound identity foundation: customer webhooks provision or link store-first
accounts, the Shopify customer ID is the durable identity, and email changes,
deletion/redaction, replay, and entitlement conflicts are handled
conservatively. That behavior remains compatible.

The outbound customer bridge also lives behind
`swinglab.integrations.shopify`. Its backend-only Admin GraphQL client links
verified app-first registrations and provides controlled reconciliation. It is
inert while `shopify_customer_sync.enabled` is false. Registration is
local-first: an Admin API outage can leave durable pending work but cannot
prevent the user from accessing CaddieInsight.

Email is only the normalized initial matching key. After a successful link,
`shopify_customer_id` is authoritative. Shopify updates never silently replace
the verified CaddieInsight authentication email, and golf or authentication
data is never copied into Shopify.

Shopify checkout remains hosted by Shopify. The app continues to consume
signed webhooks for commerce effects; outbound customer sync does not import
orders or change entitlement semantics. See
[Shopify customer sync](shopify-customer-sync.md) for the security, backfill,
rollout, and rollback contract.

### Deployment and state

The root `Dockerfile` is the live Railway build and runtime contract. The
console command is `swinglab`, Railway supplies `PORT`, and persistent state is
rooted at `/data/sessions`. `swinglab.db` contains accounts, purchases, and job
history, including durable customer-sync status. Swing-history resets retain
pseudonymous `analysis_usage_monthly` quota receipts and use
`history_reset_operations` as a crash-recovery journal. A per-account
`history_epoch` fences practice, evidence, transfer, and product-event writes
that began before a reset. Platform settings, secrets, volumes, DNS, and
production deployment state live outside this repository.

The current SQLite database and in-process job queue require a single
application replica. Horizontal scaling must wait until durable state and job
coordination are externalized.

The reset protocol also depends on that single-replica contract. The checked-in
CaddieInsight config activates its customer surface only after the disabled
compatibility floor was verified live; bare-code defaults remain off. Rollback
must stop at that floor or later. The protocol serializes the Shopify privacy lock,
history-delivery guard, and job-manager lock, rejects active work, renames
validated regular session directories into a same-volume quarantine, commits
quota archival plus related-row/job deletion in one `BEGIN IMMEDIATE`
transaction, then purges quarantine. Startup restores prepared operations and
finishes committed cleanup. See [Swing-history reset](history-reset.md).

## Dependency direction

```text
web / API / integrations
          |
          v
      analysis
```

The analysis layer is reusable by the CLI and must stay independent of FastAPI,
Shopify, Stripe, SMTP, and deployment concerns. Web and integrations may use
analysis services; the reverse dependency is prohibited.

## Incremental migration sequence

1. Use the new facades for new code while preserving every legacy import.
2. Extract API routers and serializers from the web composition module without
   changing URLs or response shapes.
3. Move Shopify implementation modules behind the integration facade, leaving
   compatibility shims at their old paths.
4. Split persistence and background jobs only after their transactional and
   multi-replica contracts are designed.
5. Rename internal identifiers only through separately reviewed, versioned,
   and independently reversible migrations.

This foundation deliberately avoids a `src/` conversion or wholesale module
move. Both would add import and deployment risk without changing customer
behavior.
