# Environment-variable contract

No environment values are stored in this repository. `.env.example` is a
value-free inventory and is not loaded automatically; provide variables through
the process environment or the deployment platform.

The application can start with optional integrations disabled. "Required"
below therefore means required for the stated production capability.

## Core production runtime

| Variable | Sensitivity | Required when | Behavior if absent |
| --- | --- | --- | --- |
| `PORT` | Non-secret | Supplied by Railway or another platform | The container command and health check default to port 8000. |
| `SWINGLAB_SECRET` | Secret | Accounts are enabled, as in the shipped config | A random key is generated at each start, so login sessions do not survive restarts. |
| `PUBLIC_BASE_URL` | Non-secret | Canonical production links, Stripe redirects, or digest email links are used | Request-derived URLs are used where possible; digest links cannot be generated reliably. |

`PUBLIC_BASE_URL` is the application origin, not the Shopify storefront.

## Shopify purchase bridge

Both variables are required to enable signed purchase and customer webhooks.

| Variable | Sensitivity | Purpose |
| --- | --- | --- |
| `SHOPIFY_STORE_DOMAIN` | Non-secret | Store hostname used for the Pro purchase link and shared with the Storefront client. |
| `SHOPIFY_WEBHOOK_SECRET` | Secret | HMAC key used to validate the exact raw Shopify webhook body. |

The preserved webhook URLs are `POST /webhooks/shopify` and
`POST /webhooks/shopify/`. The product handle, SKUs, tags, and collection paths
that contain `swinglab` are external merchant identifiers and must not be
renamed casually.

## Shopify Storefront catalog

The in-app gear catalog is enabled only when the domain and token are both
present.

| Variable | Sensitivity | Purpose |
| --- | --- | --- |
| `SHOPIFY_STORE_DOMAIN` | Non-secret | Store hostname shared with the purchase bridge. |
| `SHOPIFY_STOREFRONT_TOKEN` | Secret | Read-only Storefront API credential. |
| `SHOPIFY_API_VERSION` | Non-secret | Optional Storefront API version override; code supplies a default. |

## Stripe billing

Stripe is optional. Treat all three variables as one production bundle even
though checkout availability can be detected from the key and price alone.

| Variable | Sensitivity | Purpose |
| --- | --- | --- |
| `STRIPE_SECRET_KEY` | Secret | Server-side Stripe API credential. |
| `STRIPE_PRICE_ID` | Non-secret identifier | Recurring price used to create checkout sessions. |
| `STRIPE_WEBHOOK_SECRET` | Secret | Validates subscription webhook deliveries. |
| `PUBLIC_BASE_URL` | Non-secret | Canonical checkout success, cancellation, and portal return URLs. |

## Email

Both variables are required to enable email. Without them, passwordless login,
password reset email, and practice-plan email remain disabled or fall back to
the documented password flow.

| Variable | Sensitivity | Purpose |
| --- | --- | --- |
| `SWINGLAB_SMTP_URL` | Secret-bearing connection string | SMTP transport and credentials. |
| `SWINGLAB_MAIL_FROM` | Non-secret | From address for application email. |
| `PUBLIC_BASE_URL` | Non-secret | Absolute report, progress, and unsubscribe links. |

## Optional operations

| Variable | Sensitivity | Purpose |
| --- | --- | --- |
| `SENTRY_DSN` | Secret-bearing | Enables Sentry only when the `ops` package extra is also installed. |
| `SWINGLAB_ADMIN_TOKEN` | Secret | Enables bearer-token access to `GET /admin/kpis`; the route returns 404 without a valid token. |

## Optional Litestream backup recipe

These variables belong to the documented Litestream setup and are not consumed
by the application process itself.

| Variable | Sensitivity | Purpose |
| --- | --- | --- |
| `LITESTREAM_ACCESS_KEY_ID` | Secret | Object-storage access identifier. |
| `LITESTREAM_SECRET_ACCESS_KEY` | Secret | Object-storage secret credential. |

Backup enablement and credential values are deployment state. Do not commit
them, change them in a foundation migration, or infer that backups are active
from this documentation alone.

## Inactive Stage 0B backup and scratch restore

The Stage 0B operator commands are inert unless the matching enable gate is
explicitly set to true. There is no scheduler or application-startup hook, and
the production image does not install the optional backup transport.

### Activation gates

| Variable | Sensitivity | Purpose |
| --- | --- | --- |
| `CADDIE_BACKUP_ENABLED` | Non-secret | Must equal true before a create or upload command is accepted. |
| `CADDIE_RESTORE_ENABLED` | Non-secret | Must equal true before a download or scratch restore drill is accepted. |

### Object-storage settings

| Variable | Sensitivity | Purpose |
| --- | --- | --- |
| `CADDIE_BACKUP_BUCKET` | Non-secret | Existing private bucket name. |
| `CADDIE_BACKUP_PREFIX` | Non-secret | Dedicated private prefix for immutable backup generations. |
| `CADDIE_BACKUP_REGION` | Non-secret | Provider region value used by the compatible client. |
| `CADDIE_BACKUP_ENDPOINT_URL` | Non-secret | Optional absolute HTTPS endpoint for a non-default compatible provider. |
| `CADDIE_BACKUP_ADDRESSING_STYLE` | Non-secret | Optional auto, path, or virtual addressing selection. |
| `CADDIE_BACKUP_SSE` | Non-secret | Required server-side encryption mode selection. |
| `CADDIE_BACKUP_KMS_KEY_ID` | Sensitive configuration | Stable key UUID or key ARN when customer-managed encryption is selected. |

### Writer credentials

| Variable | Sensitivity | Purpose |
| --- | --- | --- |
| `CADDIE_BACKUP_ACCESS_KEY_ID` | Secret credential | Prefix-scoped backup-writer access identifier. |
| `CADDIE_BACKUP_SECRET_ACCESS_KEY` | Secret credential | Prefix-scoped backup-writer secret. |
| `CADDIE_BACKUP_SESSION_TOKEN` | Secret credential | Optional token for temporary writer credentials. |

### Restore credentials

| Variable | Sensitivity | Purpose |
| --- | --- | --- |
| `CADDIE_RESTORE_ACCESS_KEY_ID` | Secret credential | Read-only restore access identifier. |
| `CADDIE_RESTORE_SECRET_ACCESS_KEY` | Secret credential | Read-only restore secret. |
| `CADDIE_RESTORE_SESSION_TOKEN` | Secret credential | Optional token for temporary restore credentials. |

Keep every value in an approved secret manager or protected operator session.
Setting either gate alone schedules nothing, and this integration work does not
create a bucket, inject credentials, enable a backup, or access production data.
