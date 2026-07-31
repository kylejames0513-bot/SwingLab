# Shopify Customer Account migration

## Status

The authorization-code/PKCE implementation is present but **disabled by
default**. It does not contact Shopify unless
`SHOPIFY_CUSTOMER_ACCOUNTS_ENABLED=true` and all required environment values
are configured. This document is a release runbook, not authorization to turn
the feature on, migrate users, change Shopify settings, or retire local login.

The existing Admin customer bridge and this sign-in path have distinct jobs:

| System | Owns |
| --- | --- |
| Shopify Customer Accounts | Sign-in, account recovery, verified customer identity, commerce identity, and Shopify marketing consent. |
| CaddieInsight | Golfer profile, videos, swing analysis, reports, practice check-ins, progress, recommendations, and app preferences. |
| Existing Admin customer bridge | Reconciliation and durable Shopify customer mapping only. It never transfers passwords or golf data. |

## Implemented boundary

The application exposes these feature-gated routes:

- `GET /auth/shopify/start` starts a discovery-based OAuth authorization-code
  flow with PKCE, state, and nonce.
- `GET /auth/shopify/callback` validates a one-use server-side state,
  exchanges the code, validates the returned nonce/audience, and uses the
  Customer Account API to obtain the authenticated customer ID.
- `POST /auth/shopify/logout` clears the local session and redirects to the
  discovered Shopify logout endpoint when a valid browser provider session is
  available.

Customer Account endpoints are discovered from the configured storefront
domain (`/.well-known/openid-configuration` and
`/.well-known/customer-account-api`); no Customer Account API version or
authentication URL is hard-coded. The only Customer Account GraphQL operation
resolves the authenticated `customer { id emailAddress { emailAddress } }`.
The email is never used as a login or linking key.

The PKCE verifier, nonce, and one-use state live in the local database for ten
minutes, not in the signed browser cookie. The short-lived ID token required
by Shopify logout is retained server-side behind an opaque browser-session key
and is deleted on logout or expiry. Refresh tokens are never persisted. No
Shopify password is copied into CaddieInsight.

## Required configuration

All values below are deployment secrets/configuration, not repository values:

| Variable | Sensitivity | Required when the feature is enabled |
| --- | --- | --- |
| `SHOPIFY_CUSTOMER_ACCOUNTS_ENABLED` | Non-secret | Exact `true` enables code paths. Any other shipped/default value leaves them unavailable. |
| `SHOPIFY_CUSTOMER_ACCOUNT_STOREFRONT_DOMAIN` | Non-secret | Storefront domain used for Customer Account discovery. It may be the customer-account vanity domain; it is not the Admin API host. |
| `SHOPIFY_CUSTOMER_ACCOUNT_CLIENT_ID` | Identifier | Customer Account API client ID. |
| `SHOPIFY_CUSTOMER_ACCOUNT_CLIENT_SECRET` | Secret | Confidential-client credential, backend-only. |
| `PUBLIC_BASE_URL` | Non-secret | Canonical HTTPS CaddieInsight origin. |
| `SHOPIFY_CUSTOMER_ACCOUNT_REDIRECT_URI` | Non-secret | Must exactly equal `${PUBLIC_BASE_URL}/auth/shopify/callback`. |
| `SHOPIFY_CUSTOMER_ACCOUNT_TIMEOUT_SECONDS` | Non-secret | Optional bounded request timeout (1–30 seconds; default 10). |

Before a development-store test, configure the exact callback URL and the
post-logout URL in Shopify Customer Account settings. Do not add production
URLs, rotate credentials, or change customer-account settings as part of a
code deployment without the owner’s explicit approval.

Shopify’s current Customer Account API documentation covers the required
discovery endpoints, authorization-code parameters, PKCE, confidential-client
credentials, logout `id_token_hint`, and dynamic GraphQL endpoint:
[Customer Account API](https://shopify.dev/docs/api/customer/latest).

## Linking and migration rules

`shopify_customer_id` remains the durable cross-system mapping. The app also
records a unique Customer Account subject and one explicit migration state:

- `local_only`
- `linked_pending_first_sign_in`
- `shopify_authenticated`
- `reverification_required`
- `manual_review`
- `redacted`

A Shopify sign-in is accepted only when it resolves to an existing exact
Customer Account subject or existing durable Shopify customer ID. A missing
mapping never auto-creates an app account, guesses from email, merges two
accounts, or applies a conflict record. An already signed-in user can
explicitly link the Shopify identity they authenticated with; any subject or
customer-ID collision moves the affected records to `manual_review`.

The protected `GET /admin/product-events` response includes PII-free migration
state counts. It is an aid to reconciliation, not an activation switch.

## Activation sequence

1. Complete the Admin bridge preflight in
   [Shopify customer sync](shopify-customer-sync.md): backup-and-restore
   drill, one Railway replica, canonical domain, token rotation, protected
   customer-data approval, HMAC/replay tests, duplicate-ID audit, and a
   development-store migration test.
2. Enable Customer Accounts only in a development store. Test local-login
   link, Shopify-first account, sign-out, expired state, replayed callback,
   wrong state, duplicate customer ID, and customer recovery.
3. Test internal accounts. Keep the outbound bridge at cohort `0` until the
   owner approves the next stage.
4. Activate bridge enrollment only for new verified registrations. Monitor the
   existing sync health endpoint and resolve every `requires_review` row.
5. Run historical backfill as a read-only dry run. Review every conflict; then
   apply explicit batches of 25, reconcile, and repeat.
6. Run two clean reconciliation windows. “100% migrated” means every app
   account is in an explicit terminal migration state—migrated/authenticated,
   linked pending first sign-in, re-verification required, redacted, or manual
   review—with **zero unclassified rows**. Review exceptions; never suppress
   them to make a dashboard look complete.
7. Keep legacy local login feature-gated until Shopify sign-in/recovery,
   support recovery, unique mappings, and owner approval all pass. Only then
   plan a separate legacy-login retirement change.

## Privacy and rollback

Customer Account identity is covered by the existing raw-body HMAC/replay
webhook protections. The product profile, check-ins, and first-party events
are included in the application privacy export snapshot for a linked account;
the customer redaction path removes their profile/event/check-in data and
short-lived Customer Account auth rows. Analysis artifacts and the complete
public-App privacy workflow still require their documented acceptance review
before any App Store distribution.

Rollback means disabling the Customer Account feature flag and retaining the
existing legacy-login fallback. Do not delete mappings, merge customer
records, or re-enable an unreviewed cohort as a rollback shortcut.
