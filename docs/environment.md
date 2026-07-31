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

Buyer-facing Shopify commerce requires `SHOPIFY_STORE_DOMAIN` plus the primary
`SHOPIFY_WEBHOOK_SECRET`. That pair enables Pro purchase links, manual
order/customer deliveries, and Shopify-connected inbox-proof signup semantics.
The shared webhook endpoint itself stays available with the store domain plus
either signing secret, so `SHOPIFY_PRIVACY_WEBHOOK_SECRET` alone can authenticate
mandatory compliance deliveries. Privacy-only configuration does not expose a
Pro checkout link or turn a local signup into a Shopify-connected claim.

| Variable | Sensitivity | Purpose |
| --- | --- | --- |
| `SHOPIFY_STORE_DOMAIN` | Non-secret | Store hostname used for the Pro purchase link and shared with the Storefront client. |
| `SHOPIFY_WEBHOOK_SECRET` | Secret | HMAC key for the existing Shopify Admin notification webhooks. |
| `SHOPIFY_PRIVACY_WEBHOOK_SECRET` | Secret | Optional HMAC key for mandatory privacy topics delivered by the dedicated bridge app. It is that app's client secret, not its access token, and does not enable buyer-facing commerce by itself. |

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
| `SHOPIFY_API_VERSION` | Non-secret | Optional Storefront API version override; code supplies a default. |

## Shopify Admin API customer sync

Outbound app-account-to-Shopify customer sync is a separate, backend-only
capability. It remains inert while `shopify_customer_sync.enabled` is false,
even if every environment variable below is present.

| Variable | Sensitivity | Purpose |
| --- | --- | --- |
| `SHOPIFY_ADMIN_STORE_DOMAIN` | Non-secret | Canonical `your-store.myshopify.com` Admin API host. When outbound sync is enabled, it must exactly match the normalized `SHOPIFY_STORE_DOMAIN` used to validate inbound webhooks. |
| `SHOPIFY_ADMIN_CLIENT_ID` | Non-secret identifier | Client ID of the dedicated Dev Dashboard app. Configure it only with `SHOPIFY_ADMIN_CLIENT_SECRET`. |
| `SHOPIFY_ADMIN_CLIENT_SECRET` | Secret | Backend-only Dev Dashboard app credential. The server exchanges the pair for a short-lived Admin API token, caches it until shortly before expiry, and refreshes it once if Shopify rejects it early. |
| `SHOPIFY_ADMIN_ACCESS_TOKEN` | Secret | Legacy static-token alternative. Configure this alone, never together with either client-credentials variable. |
| `SHOPIFY_ADMIN_API_VERSION` | Non-secret | Explicit Admin GraphQL version. Review it against Shopify's stable-version schedule each quarter. |
| `SHOPIFY_CUSTOMER_SYNC_COHORT_PERCENT` | Non-secret | Explicit second gate for automatic registration sync, from `0` through `100`. It defaults to `0`, so enabling the global feature flag alone syncs nobody. A nonzero value also requires a stable `SWINGLAB_SECRET`; selection is a deterministic keyed bucket and no email is logged or persisted for cohorting. |

Keep the Admin and Storefront API-version variables separate. While outbound
customer sync is enabled, however, normalized `SHOPIFY_STORE_DOMAIN` must be
the same canonical `*.myshopify.com` hostname as
`SHOPIFY_ADMIN_STORE_DOMAIN`. The persisted database binding must match that
same domain and the authenticated exact Shop GID. A missing, custom, or
different webhook domain hard-blocks outbound enrollment, worker startup, and
customer calls while leaving the signed inbound bridge available. A custom
storefront hostname remains supported only while outbound customer sync is
disabled. Exactly one Admin authentication mode is valid: the complete
client-ID/client-secret pair or the legacy static access token. Missing halves
and mixed modes fail configuration validation before a worker can contact
Shopify.

The app installation needs the minimum `read_customers` and `write_customers`
scopes. Customer email is protected customer data, so configure and, where
applicable, obtain approval for that field in the Shopify Partner Dashboard
before enabling sync. Do not request names, addresses, phone numbers, orders,
or other customer fields unless a separately reviewed feature requires them.
See Shopify's
[protected customer data requirements](https://shopify.dev/docs/apps/launch/protected-customer-data)
and the [customerSet mutation](https://shopify.dev/docs/api/admin-graphql/latest/mutations/customerSet).

`SWINGLAB_ADMIN_TOKEN` separately protects local sync-health and retry routes;
it is not a Shopify credential. Full setup and rollout instructions are in
[Shopify customer sync](shopify-customer-sync.md).

## Shopify Customer Account sign-in migration

Customer Account sign-in is independent of the backend Admin customer-sync
worker and remains disabled unless its own feature flag is exactly true. It
uses authorization code + PKCE, the Customer Account API discovery endpoints,
and a backend-only confidential-client secret. It never sends or copies an app
password.

| Variable | Sensitivity | Purpose |
| --- | --- | --- |
| `SHOPIFY_CUSTOMER_ACCOUNTS_ENABLED` | Non-secret | Exact `true` enables `/auth/shopify/*`; empty/false leaves all routes unavailable. |
| `SHOPIFY_CUSTOMER_ACCOUNT_STOREFRONT_DOMAIN` | Non-secret | HTTPS storefront domain used for Customer Account discovery; not necessarily the Admin `*.myshopify.com` host. |
| `SHOPIFY_CUSTOMER_ACCOUNT_CLIENT_ID` | Non-secret identifier | Customer Account API client ID. |
| `SHOPIFY_CUSTOMER_ACCOUNT_CLIENT_SECRET` | Secret | Confidential-client credential retained only on the backend. |
| `SHOPIFY_CUSTOMER_ACCOUNT_REDIRECT_URI` | Non-secret | Must exactly equal `${PUBLIC_BASE_URL}/auth/shopify/callback`. |
| `SHOPIFY_CUSTOMER_ACCOUNT_TIMEOUT_SECONDS` | Non-secret | Optional 1–30 second outbound timeout; default 10. |

An enabled-but-incomplete configuration fails startup rather than silently
falling back to legacy local login. Before enabling it anywhere, register the
exact callback and post-logout URLs in Shopify Customer Account settings and
follow [Shopify Customer Account migration](shopify-customer-accounts.md).

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

`SWINGLAB_MAIL_FROM` and one delivery transport are required to enable email.
Resend's HTTPS API is preferred on Railway and other hosts that block SMTP.
An existing official Resend SMTP URL is automatically upgraded to HTTPS using
its embedded credential, so migrating transport does not require duplicating
that secret.
Without a complete configuration, passwordless login, password reset email,
and practice-plan email remain disabled or fall back to the documented
password flow.

| Variable | Sensitivity | Purpose |
| --- | --- | --- |
| `RESEND_API_KEY` | Secret | Preferred HTTPS delivery through Resend. |
| `SWINGLAB_SMTP_URL` | Secret-bearing connection string | Optional SMTP fallback for hosts that permit outbound SMTP. |
| `SWINGLAB_MAIL_FROM` | Non-secret | From address for application email. |
| `SWINGLAB_MAIL_TRANSPORT` | Non-secret | Optional `auto` (default), `resend`, or `smtp` selection. Use `smtp` only as an explicit rollback on a host that permits SMTP. |
| `PUBLIC_BASE_URL` | Non-secret | Absolute report, progress, and unsubscribe links. |

## Optional operations

| Variable | Sensitivity | Purpose |
| --- | --- | --- |
| `SENTRY_DSN` | Secret-bearing | Enables Sentry only when the `ops` package extra is also installed; default PII, request bodies, and frame locals are disabled in code. |
| `SWINGLAB_ADMIN_TOKEN` | Secret | Enables bearer-token access to `GET /admin/kpis`, exact Shopify sync health, and opaque-reference retry; protected routes return 404 without a valid token. Privacy export delivery uses the host-level operator CLI and filesystem permissions instead. |

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
