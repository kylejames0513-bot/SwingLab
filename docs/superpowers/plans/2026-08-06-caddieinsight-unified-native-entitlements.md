# CaddieInsight Unified Native Entitlements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Apple, Google, Stripe/web, Shopify, and legacy memberships coexist in one server-authoritative entitlement model, then add native monthly/annual purchase and restore without duplicate purchase pressure.

**Architecture:** Immutable normalized provider events feed source-specific grant projections and one effective entitlement snapshot. The store shares `UserStore`’s SQLite connection/lock; no independent billing database or aggregator is introduced. Legacy reads remain active while shadow parity is measured, existing sources dual-write, and backup/privacy coverage lands. Apple JWS is verified with Apple’s official Python server library; a minimal Apple server-API transaction credential and Google purchase tokens are encrypted for durable lifecycle reconciliation. Token-bearing Google lifecycle RTDN is resolved through the appropriate authoritative Play Developer API, while tokenless `testNotification` messages create only durable delivery-test receipts. The Expo client uses `expo-iap` only as the managed-workflow StoreKit 2/Play Billing bridge and never grants access itself.

**Tech Stack:** Python 3.11+, SQLite WAL, `app-store-server-library` 3.1.2+, `google-auth`, `cryptography` AES-GCM, `httpx`, Stripe webhook verification, Shopify HMAC, FastAPI/Pydantic, `expo-iap` 5.0.1, Jest/pytest, Apple sandbox and Google Play internal testing.

## Global Constraints

- A refund/revocation from one source must never revoke another source’s access.
- An account is Pro only when at least one verified grant is active at `now`.
  `confirming` never grants Pro; cancellation preserves access through the
  verified paid-through time.
- Store localized title, price, currency, trial, and renewal text come from the
  native store response. Never hardcode native prices from `config.yaml`.
- Do not store raw JWS bodies, plaintext provider lifecycle credentials, complete
  receipts, provider private keys, service-account JSON, full notification
  bodies, or payment instruments. Store normalized fields plus SHA-256/HMAC
  identifiers. The only recoverable provider credentials are the exact Google
  purchase token and one minimal verified Apple `originalTransactionId` used as
  the App Store Server API `anyTransactionId` reconciliation credential. Encrypt
  each with AES-256-GCM under a
  separately provisioned versioned key and expose plaintext only inside its
  source adapter.
- Apple/Google original purchases bind uniquely to one CaddieInsight account.
  Restore cannot silently transfer value between accounts.
- Preserve `users.plan`, `subscription_status`, `pro_until`, `shopify_orders`,
  and every existing Shopify/Stripe path through the shadow/dual-write period.
- Each provider has two independent controls: lifecycle ingestion/reconciliation
  readiness and reversible new-purchase/claim admission exposed to the client.
  Both purchase-admission flags default off and can be disabled independently;
  doing so never stops verified restore for an existing binding, notifications,
  renewal/refund/revoke ingestion, acknowledgement, or reconciliation. Once a
  provider has any retained event, nonterminal grant, or credential, its
  lifecycle path and verifier credentials are a permanent startup dependency
  until bounded retention proves no state remains. Billing outages never block
  free coaching, but they must not silently freeze paid lifecycle truth.
- Native purchase/claim binding uses Backend Task 6’s owner mutation fence and
  rechecks deletion state in the entitlement transaction. Provider events after
  deletion may update only unbound lifecycle/tombstone state; they never recreate
  an account binding or access.
- No production App Store Connect/Play Console configuration, product creation,
  secret provisioning, sandbox purchase, submission, or publication occurs
  without separate approval.
- Production review purchases use no general sandbox exception. A protected,
  provider-specific purchase-test cycle may accept fully provider-verified Apple
  sandbox or Google `testPurchase` input only for predeclared synthetic reviewer
  generations, exact signed build/version, approved products, and a separate
  bounded purchase-test opening/expiry. It writes a
  separate review intent/event/grant namespace resolvable only for that synthetic user;
  it cannot bind a real account or enter production grants and is disabled/
  purged after the test cycle. Google’s purchase-disabled standing demo/auth may
  remain while its listing requires sign-in. All other production sandbox/test input is quarantined
  without access. There is no auth or billing bypass.

---

## Task 1: Add immutable entitlement types, tables, resolver, and backup/privacy coverage

**Files:**

- Create: `swinglab/entitlements/__init__.py`
- Create: `swinglab/entitlements/models.py`
- Create: `swinglab/entitlements/service.py`
- Create: `swinglab/entitlements/privacy.py`
- Modify: `swinglab/web/users.py:222-435,622-667,831-1200,7901-7940`
- Modify: `swinglab/web/mobile_privacy.py`
- Modify: `swinglab/web/recovery_fence_ledger.py`
- Modify: `swinglab/web/mobile_mutations.py`
- Modify: `docs/security/user-owned-writer-inventory.md`
- Modify: `swinglab/backups/core.py:1-310`
- Modify: `swinglab/config.py:197-242`
- Modify: `config.yaml:237-293`
- Create: `tests/test_entitlement_store.py`
- Create: `tests/test_entitlement_migrations.py`
- Create: `tests/test_entitlement_privacy_extension.py`
- Modify: `tests/test_account_deletion_write_fence.py`
- Modify: `tests/test_privacy_erasure_ledger.py`
- Modify: `tests/test_backups.py`
- Modify: `tests/test_shopify_privacy.py`

**Interfaces:**

- `EntitlementSource = Literal["apple","google","shopify","stripe","legacy"]`.
- `EntitlementState = Literal["confirming","active","grace","billing_retry",
  "on_hold","paused","canceled","expired","refunded","revoked"]`.
- `EntitlementService(connection, lock, versioned_hmac, credential_cipher)` shares `UserStore` state and
  produces:
  `apply_verified_event(user_id, event) -> ApplyEntitlementResult`,
  `resolve(user_id, now=None) -> EntitlementSnapshot`, and
  `bind_purchase(user_id, source, original_purchase_id) -> None`.
- Entitlements reuse Backend Task 3's protected `VersionedHMAC`; no singular
  entitlement secret or unversioned state HMAC exists. Extend its closed domains
  with exactly `entitlement-event-id`, `entitlement-original-purchase`,
  `entitlement-provider-account-key`, `entitlement-purchase-token`,
  `entitlement-intent-ticket`, `entitlement-intent-create-idempotency`,
  `entitlement-intent-open-idempotency`,
  `entitlement-intent-cancel-idempotency`,
  `entitlement-apple-submit-idempotency`,
  `entitlement-google-submit-idempotency`, `entitlement-claim-idempotency`,
  `entitlement-review-challenge-idempotency`,
  `entitlement-provider-delivery-message`, `entitlement-review-user`,
  `entitlement-review-credential-id`, `entitlement-review-operation-id`, and
  `entitlement-staging-billing-user`. Review credential account lookup reuses
  Backend Task 3's exact `review-auth account` domain; it never invents a second
  account domain. Every persisted
  digest has adjacent bounded key ID; lookup computes candidates for every active
  key under the exact domain while writes use current. Application transactions
  candidate-check under the shared lock before insert, so rotation cannot create
  a duplicate raw provider identifier under another key version.
- `EntitlementSnapshot` includes `active`, `status`, `paid_through`,
  `purchase_allowed`, `managing_sources`, `active_grants`, `confirming_sources`,
  and `updated_at`; it contains no receipt/provider token.
- `EntitlementErasureExtension` implements Backend Task 6's
  `OwnerErasureExtension`. Live account deletion closes entitlement admission,
  drains provider/binding work, atomically severs every user ID/account key/grant
  binding, cancels active/uncertain purchase intents, and leaves only the bounded
  unbound lifecycle credential/tombstone below. Recovery reconciliation performs
  the same severing for stable-user-HMAC matches before provider/reconciliation
  workers or requests.
- Account deletion retains only a non-PII reclaim tombstone containing versioned
  HMACs of deleted stable user ID, normalized verified email, source, and original
  purchase fingerprint plus cutoff/terminal times. It restores no account/history
  data. A later account can reclaim an unbound still-current purchase only through
  provider re-verification, fresh `purchase_claim` email step-up, exact normalized-
  email HMAC match to that deleted lineage, explicit confirmation, and proof no
  live binding exists; mismatch/ambiguity stays unbound for protected support.
- Apple `appAccountToken` and Google `obfuscatedAccountId` are independent random
  provider-visible account keys, never HMACs of user ID/email. While the account
  is live, the random value is backed up so it stays stable across app/HMAC-key
  rotation and restore, plus its domain-separated key-ID/digest pair supports
  provider lookup. Deletion removes the raw value and retains only the versioned
  lineage HMAC; a later account receives a fresh random key and can reclaim only
  through the explicit provider/email-step-up flow above.
- A durable native-lifecycle watermark is written in the same transaction as the
  first verified Apple/Google event, grant, or provider credential. Before that
  point, v2 reads may be rolled back under the parity gate. Afterward, startup
  with `billing.entitlement_v2_reads_enabled=false`, a lifecycle flag off, missing
  verifier credentials, or a binary that cannot read the current entitlement
  schema fails closed; rollback means disabling new purchase admission/UI while
  retaining v2 resolution and lifecycle workers.
- Access is granted only by `active`, unexpired `grace`, or unexpired `canceled`.
  A live `confirming`, `billing_retry`, `on_hold`, `paused`, or ambiguous legacy
  grant gives no access but sets `purchase_allowed=false` and exposes its
  provider management action. `expired`, `refunded`, and `revoked` neither grant
  nor block a new purchase unless another source does.
- Confirming records distinguish `verified_deferred` from
  `verification_pending`. A verified deferred purchase blocks another purchase
  until its provider start/terminal state. An unverified attempt has a retry
  lease and 24-hour `confirmation_expires_at`; after a final authoritative
  lookup it becomes verified or `expired`. If that lookup is unavailable, the
  attempt’s ordinary lease may expire, but it stops individually blocking only
  after an authoritative absent/terminal result. If that final lookup is
  unavailable or uncertain, the global new-purchase-intent gate fails closed
  across Apple and Google until lifecycle verification recovers; restore,
  reconciliation, and notifications continue. Store restore/current-purchase
  reconciliation can later activate or clear the transaction without creating
  duplicate purchase pressure.

- [ ] Add failing resolver tests for free, confirming, active finite, active
  indefinite legacy, grace with end, canceled before/after paid-through,
  billing retry without grace, account hold, paused, expired, refunded, revoked,
  lifetime, and overlapping grants. Assert access, management sources, paid-
  through, and purchase pressure for every state.
- [ ] Add confirmation-lifecycle tests for retryable verification, lease reclaim,
  provider recovery, abandoned-attempt expiry at 24 hours, final authoritative
  lookup, outage through/after expiry, cross-provider global admission closure,
  verified future/deferred activation, and restore after expiry. Only an
  authoritative absent/terminal result reopens admission; uncertain state keeps
  sales closed while lifecycle/restore continues.
- [ ] Add failing idempotency/order tests: duplicate source event is replay;
  same external event with a different payload hash is 409/manual review; older
  events are stored but do not regress a newer projection.
- [ ] Add failing source-isolation tests where Apple refund leaves Shopify active,
  Google revoke leaves Stripe active, and cancellation does not end access early.
- [ ] Add additive tables inside `UserStore` migration:
  `entitlement_events` indexed by `(source, external_event_hmac_key_id,
  external_event_hmac)`, `entitlement_grants` by `(source,
  original_purchase_hmac_key_id, original_purchase_hmac)`,
  `billing_account_keys` by `(source, account_key_hmac_key_id,
  account_key_hmac)` plus the live random provider-visible value and a unique
  live `(user_id, source)` constraint, and
  `entitlement_migrations` unique migration version. Add
  `provider_delivery_receipts` with source/environment, closed
  `kind: "test"|"non_owning_quarantine"`, versioned external-message HMAC,
  canonical-body SHA-256, bounded reason/time/30-day expiry, and a uniqueness constraint
  over source/environment/kind/key version/digest; it stores no raw body, token,
  account identifier, user, event, credential, binding, or grant. Add
  `provider_purchase_credentials` with
  `(source, token_fingerprint_hmac_key_id, token_fingerprint_hmac)` lookup,
  ciphertext, nonce, encryption-key version, acknowledgement/retry/retention/
  expiry fields, and an optional user binding. For Apple, the fingerprint is the
  stored `entitlement-original-purchase` HMAC pair used as AES-GCM associated
  data for the verified `originalTransactionId`/`anyTransactionId` credential;
  for Google, it is the
  `entitlement-purchase-token` pair used as associated data for the exact token.
  Add `entitlement_runtime_state` for the irreversible native-
  lifecycle watermark; and isolated `entitlement_review_purchase_intents`,
  `entitlement_review_events`, and `entitlement_review_grants` with synthetic-
  user HMAC, source, exact build, purchase-test opening/expiry, operation-specific idempotency HMACs,
  status, and purge deadline but no real-user foreign key. Add foreign keys/
  indexes for user, state, paid-through, projection, review expiry, and due-retry
  lookup. Each HMAC-pair index is unique within a key version; the mandatory
  all-active-key candidate lookup under the shared transaction prevents a raw ID
  duplicate across versions. Add `native_purchase_intents` with one active/uncertain intent per user,
  source/product/build/account-key binding, opaque ticket and operation-specific
  idempotency key-ID/HMAC pairs,
  phase, expiry, and resolution fields. Database triggers reject any review row whose synthetic-user key is
  referenced by a production grant/binding and vice versa.
- [ ] Implement immutable event fields: source/external event/original purchase,
  canonical product, normalized state, event/purchase/paid-through/revocation/
  verification times, optional invoice coverage start/end and paid/refunded/
  disputed totals, auto-renew flag, environment, payload SHA-256, and optional
  user. Reject unknown sources/states and impossible time or coverage ordering.
- [ ] Implement projections in the same `BEGIN IMMEDIATE` transaction as event
  insertion. Resolver uses the latest verified projection for each source key;
  `confirming` is visible but inactive, while overlap uses the latest valid
  paid-through without adding subscription durations together.
- [ ] Add all entitlement tables, row counts, schema digests, uniqueness checks,
  review/production namespace separation triggers, runtime watermark, provider-
  credential ciphertext, nonce, HMAC key ID/digest, AEAD key version, retry/
  retention fields, and orphan checks to backup validation before any read
  cutover; neither keyring is backed up. Add scratch-restore tests with old and
  new schemas. Expired review rows purge in bounded batches; restore never re-
  enables an expired purchase-test cycle or synthetic review grant.
- [ ] Add cross-domain/current-old-key/rotation/restart/scratch-restore tests for
  every entitlement HMAC column, including Apple notification UUID and Google
  subscription/message-ID identities under the provider-delivery domain. Backup
  manifests record all referenced HMAC and
  credential-encryption key IDs; startup/restore fail before workers if either
  keyring cannot cover a live row or retained backup. Old HMAC keys remain until
  live usage and every retained backup reference reach zero. Do not claim offline
  re-keying without raw input; prove candidate lookup still finds old event,
  purchase, account, Apple/Google credential, ticket, and idempotency rows and
  blocks duplicate current-key insertion. Associated-data HMAC columns never
  rotate independently of their ciphertext: re-HMAC and AEAD re-encryption occur
  atomically before an old HMAC key can retire, so candidate lookup and decryption
  cannot strand a retained credential.
- [ ] Extend privacy export with human-readable grant summaries and events;
  exclude payload hashes/account keys/ciphertext. Account deletion severs user
  IDs and retains keyed-hash replay tombstones plus normalized source/state/time.
  While an account is active, retain its encrypted Apple/Google lifecycle
  credential while the grant is nonterminal and for at most 30 days after a
  verified terminal state. Account deletion immediately unbinds it and retains
  ciphertext only until the earlier of 30 days after a known provider terminal
  state or 90 days after deletion, so missed lifecycle events can be processed;
  it is never exported, logged, or usable outside its source adapter. After
  ciphertext deletion, retain only the versioned HMAC tombstone so a later
  notification cannot rebind or recreate deleted-account access.
  Preserve the separate reclaim HMAC tombstone through the provider dispute/
  refund horizon; document its purpose/retention, include it in keyed-erasure
  recovery, and never use it to restore coaching history or silently bind access.
- [ ] Add Apple/Google delete → same-email re-register → authenticated restore →
  fresh step-up → explicit reclaim tests, including ciphertext already purged.
  Prove only current entitlement rebinds. Reject different email, copied receipt/
  token, another live binding, terminal/refunded purchase, stale step-up, deleted-
  lineage mismatch, and any automatic restore; exercise a support-only unresolved
  state without granting access.
- [ ] Register `EntitlementErasureExtension` in the backend schema-to-extension
  inventory and owner-write triggers. Add barriers for purchase bind, event
  projection, credential retry, and provider notification racing deletion; 204
  waits/rejects and no later row rebinds the owner. Restore a pre-delete snapshot
  with the newest `account_delete` chain, run extension reconciliation before
  workers, and prove all effective access/account bindings are severed while only
  allowed unbound lifecycle state remains. Missing extension registration or a
  newly added owner column fails startup/tests closed.
- [ ] Add `billing.entitlement_v2_reads_enabled: false`,
  `native_apple_lifecycle_enabled: false`,
  `native_google_lifecycle_enabled: false`,
  `native_apple_purchase_admission_enabled: false`, and
  `native_google_purchase_admission_enabled: false` to defaults and shipped
  config. Health reports lifecycle readiness and purchase admission separately.
  Test flags-off pristine startup, first-event watermark atomicity/crash recovery,
  permanent v2/lifecycle startup enforcement, provider independence, and that
  disabling sales still processes restore, renew, cancel, refund, revoke, and
  paid-through expiry.
- [ ] Run `python -m pytest tests/test_entitlement_store.py tests/test_entitlement_migrations.py tests/test_entitlement_privacy_extension.py tests/test_account_deletion_write_fence.py tests/test_privacy_erasure_ledger.py tests/test_backups.py tests/test_shopify_privacy.py -q`; expect all pass.
- [ ] Commit: `git commit -m "feat: add unified entitlement ledger"`.

## Task 2: Backfill legacy state and prove shadow parity

**Files:**

- Create: `swinglab/entitlements/backfill.py`
- Create: `swinglab/entitlements/cli.py`
- Modify: `swinglab/cli.py`
- Modify: `swinglab/web/users.py:1470-1900,6780-6805,7089-7535`
- Modify: `swinglab/web/billing.py`
- Modify: `swinglab/kpis.py`
- Create: `tests/test_entitlement_backfill.py`
- Create: `tests/test_entitlement_shadow_parity.py`
- Create: `tests/test_stripe_entitlement_reconciliation.py`
- Create: `tests/test_shopify_entitlement_reconciliation.py`
- Modify: `docs/deployment.md`
- Modify: `docs/environment.md`
- Modify: `docs/operations/backup-recovery.md`

**Interfaces:**

- `swinglab entitlements-backfill --db <path> --dry-run|--apply` produces only
  counts/digests, never emails, customer IDs, provider IDs, or purchase tokens.
- `backfill_entitlements(store, *, apply: bool) -> BackfillSummary` is
  idempotent under migration key `entitlements-v1`.
- `compare_legacy_and_v2(store, now=None) -> ParitySummary` reports aggregate
  active-active, inactive-inactive, and mismatches without PII.

- [ ] Add fixture databases for pre-`pro_until`, current schema, active/inactive
  Stripe states, finite/lifetime `pro_until`, stacked/canceled/ambiguous Shopify
  rows, unmatched `pro_grants`, and combined Stripe + time grant.
- [ ] Add failing dry-run tests proving zero writes and apply tests proving exact
  idempotent events/grants on two consecutive runs.
- [ ] Backfill active Stripe status as an explicitly `legacy` quarantine grant
  ending at `migration_started_at + 30 days`, marked reconciliation-required
  because existing rows lack subscription ID, product, event history, and
  paid-through. Legacy reads remain authoritative during reconciliation; this
  temporary grant can never become permanent access.
- [ ] Backfill the aggregate `users.pro_until` as one `legacy` finite/lifetime
  grant. Import `shopify_orders` as source audit projections, but do not subtract
  or reclassify the aggregate: it may include Shopify, manual, promotional, or
  historical value. Existing Shopify mutations continue updating the aggregate
  legacy grant until an approved reconciliation removes ambiguity.
- [ ] Preserve unmatched `pro_grants`; preserve canceled Shopify orders as replay
  tombstones. Mark `grant_ambiguous=1` for review instead of guessing.
- [ ] Add `swinglab entitlements-reconcile-shopify --db <path> --dry-run|--apply
  --decisions <protected-json>`. Automatically reconcile only exact verified
  paid/canceled order slices. Residual aggregate/manual/promotional/lifetime
  value requires an operator-reviewed, uncommitted decision keyed by opaque user
  and evidence source; otherwise it remains active ambiguous and blocks cutover.
  Validate decision-file schema/digest and never subtract an inferred order.
- [ ] Add shadow parity to an operator-only KPI: compare `User.is_pro` legacy
  result with `EntitlementSnapshot.active` for every account at one captured
  timestamp. Log only counts and mismatch reason categories.
- [ ] Add a fake-provider-tested `swinglab entitlements-reconcile-stripe --db
  <path> --dry-run|--apply --limit <n>` command. For each legacy active Stripe
  customer, retrieve authoritative subscriptions, create the exact Stripe grant
  and paid-through, then retire its quarantine grant in one transaction. Unknown
  product, missing customer, provider failure, or multiple active subscriptions
  remains an explicit unmatched blocker; never guess.
- [ ] Run the backfill against a scratch-restored production-format database,
  rerun backup validation, and require zero unexplained active-access mismatches
  plus zero active Stripe quarantine or Shopify/legacy ambiguous grants before
  Task 5. Do not run against production in this plan execution.
- [ ] Run `python -m pytest tests/test_entitlement_backfill.py tests/test_entitlement_shadow_parity.py tests/test_stripe_entitlement_reconciliation.py tests/test_shopify_entitlement_reconciliation.py tests/test_membership_tiers.py tests/test_account_sync.py -q`; expect all pass.
- [ ] Commit: `git commit -m "feat: backfill legacy entitlements safely"`.

## Task 3: Dual-write Shopify into the unified ledger without weakening replay/privacy

**Files:**

- Modify: `swinglab/web/users.py:7089-7535`
- Modify: `swinglab/web/shopify_billing.py:208-307,503-730`
- Modify: `tests/test_shopify_billing.py`
- Modify: `tests/test_membership_tiers.py`
- Modify: `tests/test_shopify_privacy.py`
- Modify: `tests/test_gear_ledger.py`

**Interfaces:**

- Paid Shopify event ID is `shopify:<shop-domain>:order:<normalized-order-id>:paid`.
- Cancellation/refund event IDs include the signed Shopify webhook ID and event
  kind; grant key remains the original order ID.
- Existing `apply_shopify_order(...)` and `cancel_shopify_order(...)` signatures
  and return values remain unchanged.

- [ ] Add failing tests that assert the existing order row and entitlement event/
  projection commit or roll back together for paid, replay, cancel-before-paid,
  cancel-after-paid, partial refund, full refund, lifetime, stacked, and
  redaction cases.
- [ ] Add cross-source tests: refunding Shopify leaves Apple/Google/Stripe active;
  replay after redaction cannot rebind a tombstoned order.
- [ ] Apply the normalized event inside the same `UserStore` lock/transaction as
  the current Shopify order operation. Reuse existing HMAC, exact-shop,
  cancellation tombstone, and privacy fences; do not add a second webhook route.
- [ ] Continue updating `pro_until` and legacy aggregate during shadow mode.
  Ledger writes are additive and must not change current member access yet.
- [ ] Run `python -m pytest tests/test_shopify_billing.py tests/test_membership_tiers.py tests/test_shopify_privacy.py tests/test_gear_ledger.py -q`; expect all pass.
- [ ] Commit: `git commit -m "feat: dual-write Shopify entitlements"`.

## Task 4: Make Stripe webhooks durable, bounded, ordered, and source-aware

**Files:**

- Modify: `swinglab/web/billing.py:1-106`
- Modify: `swinglab/web/app.py:3320-3378`
- Modify: `swinglab/web/users.py:6780-6805`
- Modify: `swinglab/config.py:197-242`
- Modify: `config.yaml:237-293`
- Create: `tests/test_stripe_entitlements.py`
- Modify: `tests/test_accounts.py`
- Modify: `docs/environment.md`

**Interfaces:**

- Stripe event uniqueness is the verified `event.id`; event IDs never determine
  chronology. A relevant webhook persists once, then the worker retrieves the
  current authoritative Subscription plus affected/current invoice coverage
  before projection.
- Canonical grant key is verified subscription ID, not checkout session/customer.
- Monthly/annual web price env names are `STRIPE_PRICE_MONTHLY_ID` and
  `STRIPE_PRICE_ANNUAL_ID`; legacy `STRIPE_PRICE_ID` remains a documented
  monthly fallback during migration.
- `past_due` maps to bounded `grace` through authoritative `current_period_end`,
  not indefinite access.

- [ ] Add failing webhook tests for maximum body size, invalid signature, event
  replay, hash conflict, same-second and out-of-order checkout/update/delete,
  authoritative-object retrieval, active/trialing/
  past_due/unpaid/paused/canceled statuses, paid-through, price mapping, unknown
  product, partial/full refund, dispute open/won/lost, and provider outage.
- [ ] Replace unbounded `request.body()` with the existing bounded-body helper
  before signature verification. Keep Stripe’s official signature verifier.
- [ ] Record every verified supported event as pending before projection. A
  checkout event may create `confirming`; only a freshly retrieved Subscription
  with supported product/state creates active/grace access. If Stripe retrieval
  fails, retain the pending event for a leased reconciler retry and leave access
  unchanged.
- [ ] Project from subscription lifecycle fields and retrieval time, never
  lexical event-ID order. Same-second/out-of-order deliveries converge by
  retrieving the same current object. Cancellation stops auto-renew but retains
  verified paid-through; refund/revocation ends only Stripe’s grant.
- [ ] Accept verified `charge.refunded`, `refund.created|updated`, and
  `charge.dispute.created|closed` events. Resolve charge → invoice → subscription
  through Stripe, persist the affected invoice ID plus coverage start/end and
  paid/refunded/disputed totals, then retrieve the authoritative current
  subscription and current-period payment before applying. Partial refund is
  audit-only. Full refund or open/lost dispute changes access only when it covers
  the currently effective paid interval and no later verified invoice funds that
  interval; a reversal of an older invoice never revokes a later paid period.
  Won/withdrawn dispute refreshes and can reinstate. Unsupported/unattributed
  events stay pending/manual review and never mutate access.
- [ ] Test partial/full refund and dispute open/won/lost against the current
  invoice, an older invoice after a successful renewal, overlapping coverage,
  and a later replacement payment. Assert exact coverage recomputation and that
  one Stripe reversal still cannot revoke another provider’s grant.
- [ ] Dual-write current user columns through shadow mode so web account pages
  and rollback binaries stay compatible.
- [ ] Run `python -m pytest tests/test_stripe_entitlements.py tests/test_accounts.py tests/test_membership_tiers.py tests/test_entitlement_shadow_parity.py -q`; expect all pass.
- [ ] Commit: `git commit -m "fix: harden Stripe entitlement lifecycle"`.

## Task 5: Switch access, quota, API, and account rendering to the unified snapshot

**Files:**

- Modify: `swinglab/web/users.py:622-667,7901-7940`
- Modify: `swinglab/web/app.py:833-850,1168-1208,2220-2385,4101-4117`
- Modify: `swinglab/web/jobs.py:717-733`
- Modify: `swinglab/api/contracts.py`
- Modify: `swinglab/api/mobile_routes.py`
- Create: `swinglab/api/native_billing.py`
- Create: `swinglab/entitlements/purchase_intents.py`
- Create: `swinglab/entitlements/account_keys.py`
- Create: `swinglab/entitlements/review_integrity.py`
- Create: `swinglab/entitlements/review_access.py`
- Create: `swinglab/entitlements/review_fixture.py`
- Create: `swinglab/entitlements/review_evidence.py`
- Create: `swinglab/entitlements/release_evidence_keys.json`
- Create: `swinglab/entitlements/fixtures/review-coaching-v1.schema.json`
- Create: `swinglab/entitlements/fixtures/review-coaching-v1.json`
- Create: `swinglab/entitlements/staging_billing_lane.py`
- Modify: `swinglab/cli.py`
- Modify: `pyproject.toml`
- Modify: `swinglab/web/mobile_schema.py`
- Modify: `swinglab/backups/core.py`
- Modify: `swinglab/web/credential_mutations.py`
- Modify: `swinglab/config.py`
- Modify: `config.yaml`
- Modify: `docs/environment.md`
- Modify: `docs/api/openapi-v1.json`
- Modify: `mobile/src/api/schema.generated.ts`
- Modify: `swinglab/templates/web_account.html.j2:150-210`
- Create: `tests/test_entitlement_api.py`
- Create: `tests/test_native_purchase_intents.py`
- Create: `tests/test_billing_account_key_backfill.py`
- Create: `tests/test_review_integrity_challenges.py`
- Create: `tests/test_review_access_cli.py`
- Create: `tests/test_review_fixture.py`
- Modify: `tests/test_mobile_review_privacy.py`
- Create: `tests/test_staging_billing_lane.py`
- Modify: `tests/test_credential_mutation_guard.py`
- Modify: `tests/test_replay_gate.py`
- Modify: `tests/test_progress_gate.py`
- Modify: `tests/test_membership_tiers.py`

**Interfaces:**

- `GET /api/v1/entitlements` returns `EntitlementSnapshotResponse` with active/
  confirming summaries, paid-through, provider management labels/URLs,
  `purchase_allowed`, `purchase_context:
  "ordinary"|"staging_test"|"production_review"|null`, `review_demo_active`, and
  server capabilities; no provider credential or allowlist identity.
- Its generated `management_targets` is a closed union:
  `AppleSubscriptionManagementTarget {source:"apple"}` or
  `GoogleSubscriptionManagementTarget {source:"google", product_id}` plus the
  existing provider label/fallback URL. A Google target exists only for a current
  server-verified binding and its exact approved canonical product; it remains
  available for active/confirming/grace/hold/paused/canceled-paid-through users
  even when purchase admission and `/api/v1/billing/config` are off. No client-
  supplied SKU, stale local purchase, or purchase-config response can create or
  override a management target.
- `GET /api/v1/billing/config?platform=ios|android` is the sole native purchase-
  configuration interface. It is bearer-only, rejects cookie-only auth, sets
  `Cache-Control: no-store`, validates the query platform against the immutable
  app-environment/platform/version/build headers, and uses the generated mobile
  error envelope. Missing/malformed input is 422; an unapproved or mismatched
  build is 409 `billing_build_mismatch`. With
  `mobile_native_billing_enabled=false` it returns 404 with zero reads that can
  provision state and zero side effects.
- The generated `NativeBillingConfigResponse` is a closed discriminated union
  with common fields `resource_version`, `provider: "apple"|"google"`,
  `provider_ready`, `purchase_allowed`, `purchase_context`, `terms_url`, and
  `privacy_url`. Its `provider_config` is exactly one of:
  `AppleNativeBillingConfig {kind:"apple", monthly_product_id,
  annual_product_id, app_account_token: UUID|null}` or
  `GoogleNativeBillingConfig {kind:"google", products:
  GoogleSubscriptionConfig[], obfuscated_account_id: string|null}`, where each
  Google product has a closed `plan: "monthly"|"annual"`, exact `product_id`,
  exact `base_plan_id`, and an `approved_offer_ids` list whose explicit `null`
  value is the regular-base sentinel. The response contains no localized price,
  Play offer token, provider credential, tester/reviewer identity, or allowlist.
- Provider-visible account keys are never lazily created by that GET. Once the
  entitlement migration is current, `UserStore` account creation atomically
  provisions both keys even while purchase admission is off. The explicit
  idempotent backfill command and staging/review-lane enrollment call that same
  transactional service before admission can become true. It writes Apple’s
  random UUID and Google’s
  random 128-bit base64url value to `billing_account_keys`; the values remain
  stable across HMAC rotation, backup, and restore, while deletion removes the
  raw values. The config response exposes the matching platform key only when
  provider readiness and the current user/build/context all permit a purchase
  sheet; otherwise that key is null and `purchase_allowed=false`.
- Purchase-intent creation carries the latest `billing_config_resource_version`.
  The server re-resolves the exact provider, approved product/base-plan/offer,
  account key, app environment/platform/version/build, and purchase context in
  the intent transaction; any stale/mismatched config is 409 and the client must
  refetch without opening a sheet. The ticket binds that same config revision and
  account key, so configuration, disclosure, native request, and verification
  cannot silently cross accounts or builds.
- `swinglab billing-account-keys-backfill --sessions-dir <dir> --batch-size 200
  [--after <opaque-cursor>] [--dry-run|--apply] --json` is the only bulk
  provisioning interface. Dry-run is the default and performs no write; apply
  requires the explicit flag. Batch size is validated in `1..1000`, each page is
  ordered by stable internal user key, and apply uses one `BEGIN IMMEDIATE`
  transaction with the owner mutation fence and a final deletion-state recheck.
  The unique `(user_id, source)` rows plus insert-if-absent semantics make
  concurrent runs/restarts/account creation idempotent; deletion wins and can
  never be followed by a key insert. JSON contains only `mode`, `scanned`,
  `eligible`, `already_complete`, `created_apple`, `created_google`,
  `skipped_deletion`, `conflicts`, `remaining_missing_apple`,
  `remaining_missing_google`, opaque `next_cursor`, and `complete`—never a user
  ID, email, raw key, digest, or provider credential. Operators iterate bounded
  pages to `complete=true`, then a fresh dry-run over the live population must
  report both remaining counts zero.
- `User.effective_is_pro: bool | None`; `User.is_pro` uses it when v2 reads are
  enabled and otherwise preserves the exact legacy expression.
- `GET /api/v1/me` adds an `entitlement` object without changing identity/profile.
- `StagingBillingLane` is a non-review, staging-only exception for a bounded
  synthetic billing-test cohort while ordinary staging purchase admission stays
  off. It is default-off and matches only configured `(hmac_key_id, digest)`
  entries for stable user ID under `entitlement-staging-billing-user`, the exact
  preview build/store identity, staging database/environment, and approved
  products. `/api/v1/entitlements`, `/me`, and purchase-intent creation return
  `purchase_allowed=true`/`purchase_context="staging_test"` only for a matching
  user. Apple accepts only verified
  sandbox transactions. A new Google binding also requires an active lane,
  approved product/base plan, and an opened staging intent whose candidate
  matches the authoritative `externalAccountIdentifiers.obfuscatedExternalAccountId`;
  a previously persisted staging token fingerprint may receive lifecycle updates
  after lane closure. Any unknown, absent, or mismatched account identifier/token
  is quarantined before token, event, credential, or grant writes. Google accepts
  only authoritative `testPurchase`; neither provider can enter the production
  review namespace. Startup rejects this lane, its allowlist, or its provider
  modes in production, and `/healthz` exposes only enabled state plus aggregate
  allowlist count, never identifiers/digests.
- Every environment decision in this plan consumes Backend Task 3's server-owned
  `mobile_deployment_environment`; it is never inferred from a request header,
  host/origin, database path, provider payload, or CLI argument. Staging billing
  requires exact `staging`; production review access/evidence requires exact
  `production`; a mismatch fails startup or the operator mutation before provider
  I/O and is read back in non-secret health.
- `ProductionReviewLane` consists of independent Apple and Google durable records,
  each with a different nullable current synthetic-user generation, review-only
  account-HMAC/scrypt credential,
  and exact supported-build rows. Apple uses `mode="submission_window"` with
  required opening/closing times. Google uses `mode="standing_app_access"`: its
  credential/demo lane has no review-window expiry and remains active while the
  published Play listing requires sign-in details, while each issued bearer still
  expires within 24 hours and rechecks the current build set. Startup rejects the
  same stable user, account HMAC, credential, or intent namespace in both
  providers. The lane implements Backend Task 3's `ReviewAuthAdmission`; only a
  matching review-scoped bearer can retain that scope.
- For that matching bearer, `resolve_mobile_capabilities` applies a request-scoped
  Pro demo overlay and returns `review_demo_active=true`, allowing every native
  Pro API/screen to be reviewed without payment. The overlay is never assigned to
  `User.effective_is_pro`, exposed to browser/cookie auth, persisted as an event/
  grant/binding, included in renewal management, or used by a real account. Lane
  Apple window closure immediately revokes its scoped bearers/overlay. Google
  bearer expiry or build retirement does the same for that session, but its
  standing credential/demo access remains login-capable until an explicitly
  verified permanent delisting/App-access-clear operation. Neither provider can
  affect the other.
- Add exact tables `production_review_access` (provider PK, nullable unique current
  synthetic `user_id`, monotonic synthetic-generation number, mode/state,
  fixture-template SHA-256, demo/purchase-test booleans, Apple window, independent
  purchase-test opening/expiry, monotonic review-access revision, config version),
  `production_review_credentials` (provider, random credential ID, account-HMAC
  key ID/digest, scrypt password hash, `pending|active|retiring|revoked`, phase
  timestamps), `production_review_builds` (provider/version/build,
  `pending|active|retiring`, overlap/retire times), and
  `production_review_operations` (provider, operation-ID HMAC key/digest,
  canonical request hash, kind/phase/timestamps/sanitized result), plus Backend
  Task 6's exact `mobile_review_step_up_challenges`,
  `mobile_review_step_up_exchange_journals`, and
  `mobile_review_step_up_exchange_receipts` and its review discriminator/provider/
  generation/app-identity columns on `mobile_step_up_tokens`. Foreign keys,
  partial unique indexes, and triggers prevent two providers sharing a user or
  live account HMAC, more than two credentials during rotation, purchase-test
  without an open lane, or review rows for a non-`store_review` user. These tables,
  indexes, operation journals, credential versions, and synthetic-user marker are
  cumulative mobile-state generation 7. Extend Backend Task 3's
  `MOBILE_STATE_GENERATIONS` with the exact tables, columns, indexes, triggers,
  operation journals, fixture generation/ownership columns, closed HMAC domains/
  key IDs, and `store_review` marker;
  require exact backup,
  scratch-restore, retention, privacy-export exclusion, and owner-erasure checks.
- `swinglab review-access` is the only provisioning/lifecycle interface. Its
  provider-scoped subcommands are `provision`, `open`, `seed-fixture`,
  `reset-fixture`, `status-fixture`, `roll-build`,
  `rotate-credential`, `set-purchase-test`, `close`, `status`, and `purge`; every command requires
  `--sessions-dir <dir> --provider apple|google --json`, every mutation requires
  a 128-bit `--operation-id` and explicit `--dry-run|--apply`, and dry-run is the
  default. The CLI loads the server-owned deployment environment and rejects any
  database, signed evidence, review mode, or operation inconsistent with it;
  request fields and shell context cannot override that value. `provision`
  requires exact `--app-version`/`--app-build`; Apple also
  requires `--opens-at`/`--closes-at`, while Google requires
  `--standing-app-access` and rejects a closing time. `roll-build` is Google-only
  and requires exact new version/build plus bounded old/new overlap/retire time.
  `set-purchase-test` requires exactly one of `--enabled|--disabled`. Enabling
  requires `--expires-at` and optionally `--opens-at` (default now); Google is
  limited to a two-hour controlled cycle, while Apple is limited to the lesser
  of seven days or its open submission window. Disabling rejects either time.
  `rotate-credential` requires `--phase prepare|activate|retire|rollback` and a
  stable rotation ID. Only `provision` and rotation `prepare` accept
  `--secret-stdin`, whose single bounded JSON object is `{account,password}`.
  Rotation `activate` and `retire` each require `--console-evidence-stdin`; Google
  permanent `close`/`purge` requires `--permanent --console-evidence-stdin`.
  That flag reads exactly one bounded JSON object
  `{payload:{...closed unsigned fields...},signature:"<base64url>"}` generated by
  Release Task 5's protected evidence-signer implementation. Extra top-level/payload keys and
  padded/noncanonical base64url are rejected; only canonical UTF-8 bytes of
  `payload` are Ed25519-signed. The payload's closed schema carries kind
  (`credential_login_verified|credential_old_absent|app_access_clear`),
  provider, deployment environment, canonical backend origin/commit, exact app
  identity and active artifact/build set, console field
  revision/readback SHA-256, clean-login or delisting evidence ID, UTC capture
  time, independent approver ID, and signing-key ID. The wrapper's top-level
  signature covers only this payload. The CLI verifies
  it against the repository-pinned release-evidence public key, requires the
  operation-appropriate kind, and stores only canonical envelope SHA-256/signing-
  key ID/evidence ID. Evidence older than 30 minutes, for the wrong provider/
  environment/backend/app/build/phase, with an unknown/retired key or invalid
  signature, or reused by a
  conflicting operation is rejected; a bare operator assertion can never satisfy
  the gate.
  `release_evidence_keys.json` is the single packaged trust registry loaded with
  `importlib.resources`; Entitlements ships it with no production-active key and
  therefore fails these mutations closed until Release Task 7 approval-generates
  a keypair, commits only its public key/key ID/activation time, builds the exact
  production image, and verifies readback. Private signing key bytes never enter
  the repository, Railway, argv, logs, or the application process.
  Secret input is rejected from argv, environment-derived option values, TTY
  prompts, files, and config; it is never echoed, logged, or persisted plaintext.
- `provision --apply` generates a unique internal email
  `store-review-<128-bit-base32>@example.invalid`, creates a
  `source="store_review"` synthetic user generation whose normal password hash is the
  existing empty/unconfigured sentinel, plus a
  separate provider review credential containing only versioned account HMAC and
  scrypt password hash. The access/credential record survives independently if
  this generation is deleted; the user row does not own the reusable review
  secret. All browser/password/passwordless/OTP/reset/signup/PWA
  paths reject `store_review`; only `ReviewAuthAdmission` can verify the separate
  credential. The reserved address is never disclosed, normalized into a real
  identity, delivered to, or accepted by any email-based lookup; uniqueness is
  generated/rechecked in the create transaction. Provisioning never runs normal
  identity convergence, pending-Pro claim, Shopify sync, or email delivery. It
  rejects an existing normal/other-provider account, a shared stable user/
  credential, overlapping provider record, weak password, or invalid/overlong
  Apple window. `open` activates reusable review auth plus demo access only;
  purchase-test remains false until its separate command.
- Packaged resource `swinglab/entitlements/fixtures/review-coaching-v1.json` is
  the single immutable schema-validated synthetic template
  containing only generated profile/session/report/practice/proof/progress and
  media-fixture references needed to traverse Today → guided capture result →
  Caddie Brief → Practice → matched Progress plus every current Pro API/screen.
  It contains no copied customer/provider data, real email/video, subscription,
  binding, purchase intent, grant, token, or external order. `seed-fixture`
  idempotently binds its exact SHA-256 to the current provider/user/history epoch;
  `reset-fixture` uses the owner mutation/recovery-fence path to purge and recreate
  only that generation's private fixture; `status-fixture` emits template hash,
  generation/epoch and aggregate entity/media counts only. Provisioning calls the
  same seeder. Tests and store asset manifests bind to that hash, never mutable
  ad-hoc rows. `importlib.resources` loads it; `pyproject.toml` includes the fixture,
  schema, and release-evidence trust-registry JSON resources in wheels, while the
  existing Dockerfile's `COPY swinglab/` includes them in the
  Railway image, and no duplicate mobile/store copy may drift. The store asset
  manifest references this one template SHA-256.
- Fixture creation/reset is a crash-safe `production_review_operations` journal
  kind with phases `prepared -> files_staged -> files_fsynced -> db_seeded ->
  files_published -> revision_published -> complete`. It stages only generated
  synthetic files under the protected generation root, hashes/fsyncs each file and
  directory, commits owner/history-epoch-bound rows while that generation remains
  non-admitted, atomically publishes the directory, then publishes/reads back the
  review-access recovery revision before admission. Fixed ordering is owner fence
  → review auth/credential admission → maintenance file lock → generation lock →
  UserStore/SQLite transaction. Startup resumes or rolls back every phase and
  removes unreferenced staging/final orphans; no bearer/demo response is issued
  until `complete`. A review exchange that must create a generation follows the
  existing safe 202 pending/replay contract until the fixture is complete.
- Review-only privacy re-authentication is an extension of Backend Task 6: a
  currently scoped review bearer can obtain a five-minute single-use step-up for
  `data_export|history_reset|account_delete` by re-verifying the same dedicated
  review password under a separate challenge/PKCE/idempotency/rate-limit domain.
  It never sends email or accepts the normal user password sentinel. Deleting a
  review account recovery-fences and purges the current synthetic user generation,
  its fixture and scoped bearers exactly like private app data, but deliberately
  leaves the provider access record, account-HMAC/scrypt credential, supported
  builds and (for Google) standing App-access mode. The access row becomes
  `user_id=null` with a terminal generation cutoff. On the next successful review
  exchange—only while that provider/build remains admitted—one locked transaction
  creates a fresh unique `store_review` user/generation and seeds the immutable
  fixture before issuing a bearer. The deleted generation never reattaches or
  restores; ordinary customer deletion semantics are unchanged. Review notes
  disclose this synthetic-review-only regeneration plainly.
- Google `roll-build --apply` adds the new exact submitted/public build before
  retiring the old one, retaining at most the current and immediately prior build
  through a bounded overlap. It never changes the stable user/credential/demo
  lane. Credential rotation `prepare` installs a second accepted hash while the
  old remains active; after separate Play Console sign-in-details update/readback
  and clean-device login, `activate` makes it primary, then the console old entry
  is removed/read back before `retire` recovery-fences old scoped bearers/hash.
  `rollback` removes only the prepared credential. At least one tested credential
  remains valid throughout; every phase is crash/replay safe and stores the
  protected console-readback digest/evidence reference, not credentials. Exact
  replay returns the recorded result, while a changed evidence binding or phase
  under the same operation/rotation ID conflicts. Apple uses the same phases
  within a held submission window but need not retain a standing credential.
- A purchase-test opening/expiry is persisted independently from the reusable
  demo credential. Before every capability/config/intent response and on startup,
  expiry atomically fails closed, blocks new review purchase intents, and queues
  the same drain/reconcile/purge state machine; a crash cannot extend the window.
  `status` reports only active/not-yet-open/expired plus timestamp hashes and the
  recovery revision. Disabling `set-purchase-test` first blocks new isolated intents, then drains,
  reconciles, records a sanitized digest, and purges only that provider's review
  purchase intents/events/grants while demo/auth remains unchanged. `close` makes
  new auth/demo/purchases impossible and recovery-fences scoped bearers. Apple
  closes after terminal review. Google `close`/`purge` require an explicit
  permanent mode plus readback that the listing is delisted or Play App-access
  details no longer require sign-in; one terminal review is insufficient.
  `purge` then uses the owner-erasure/recovery fence to delete only that provider's
  synthetic user, credential, review rows, and private fixture data.
  Every applied mutation stages a new monotonic `review_access_revision`, publishes
  and reads back one `review_access_revision` record through Backend Task 3's
  off-volume recovery-fence chain, and only then exposes the new state or marks a
  credential/build/review generation terminal. The record contains provider,
  revision, lane state/window, purchase-test state/expiry, exact supported builds,
  and versioned `entitlement-review-credential-id` HMACs of admitted credential
  IDs plus current synthetic-generation state/cutoff—never an account, password/
  hash, user ID, or bearer. Service restore validates
  the newest head and converges the migrated database to that revision before
  review auth/billing starts: unlisted credentials/builds are retired, closed or
  expired purchase testing stays closed, and a purged/closed generation cannot
  reappear from an older backup. An unavailable/mismatched head leaves all review
  auth, demo, and purchase testing closed. Mutations use idempotent phase journals,
  the owner/maintenance lock order, exact replay/conflict detection, and crash
  recovery. `status`/all JSON output contains
  provider, mode/phase, enabled/demo/purchase-test booleans, credential/build
  counts, build/window hashes, fixture-template hash, and synthetic-generation
  state/count,
  aggregate row/token/intent counts, and recovery readback only—never account,
  user ID, email, password/hash, bearer, HMAC digest, or provider credential.
- Each provider record has an independent default-false
  `review_purchase_test_enabled` switch. Demo access and reusable review auth do
  not turn it on. Only when that provider's bounded store-test lane is explicitly
  opened does the exact synthetic user/bearer/build/window receive
  `purchase_allowed=true`/`purchase_context="production_review"` and an approved
  product sheet; otherwise even that demo account receives
  `purchase_allowed=false`/`purchase_context=null`. Google License Testing may
  open this switch only for a controlled pre-submission device cycle whose OS
  Play account membership is read back; final public Play review keeps it off
  because the provider reviewer's OS account is not the supplied app-login
  identity. Apple may independently open it for its provider sandbox review path.
  Purchase-intent create/open/cancel branch atomically into isolated
  `entitlement_review_purchase_intents`; verified sandbox/test submissions can
  consume only that review intent and project only review events/grants. The
  response stays `purchase_allowed=false`/`purchase_context=null` for every real/
  nonmatching account, and a review intent can never satisfy or bind a production
  transaction.
- `POST /api/v1/billing/purchase-intents` requires bearer auth and
  `Idempotency-Key`, then in one `BEGIN IMMEDIATE` transaction checks the unified
  snapshot, both providers’ health/uncertain attempts, admission flags, owner
  mutation fence, and the unique per-user intent. It returns an opaque ticket
  bound to user, auth/history epoch, source, canonical product/base plan/exact
  offer identity, account key, exact app build, and a 10-minute `open_by` time.
  Google offer identity is `(product_id, base_plan_id, offer_id)` where
  `offer_id=null` is the explicit regular-base sentinel; Apple has no Google
  offer fields. The server accepts only a configured approved identity and
  rejects a named `offer_id` equal to `base_plan_id`, because expo-iap uses the
  base-plan ID as the client-side `id` for a regular offer.
- Request-scoped review demo access is deliberately excluded from ordinary
  duplicate-subscription denial only when that provider's separately enabled,
  matching isolated `production_review` context opens a sheet; its intent/event/
  grant never enters the canonical ledger. Every normal entitlement source still
  disables a duplicate purchase exactly as before.
- `POST /api/v1/billing/purchase-intents/{id}/opened` requires the same bearer,
  ticket, and its own `Idempotency-Key`, persists key-ID/HMAC plus canonical
  request hash, and atomically marks the ticket immediately before native
  `requestPurchase`. Exact lost-response replay returns the same opened receipt;
  conflicting reuse is 409. The client waits for that receipt before opening the
  sheet and never opens another sheet merely because the response was lost. A verified transaction consumes
  that exact ticket in the entitlement transaction. A provider-declared user
  cancellation may close it only through the exact endpoint below; timeout/crash after `opened` becomes
  `uncertain` and blocks new Apple and Google intents until a notification,
  restore/current-purchase reconciliation, authoritative terminal/absent result,
  or protected support resolution clears it. Expiry alone never reopens purchase
  pressure.
- `POST /api/v1/billing/purchase-intents/{id}/cancel` requires the same bearer,
  `Idempotency-Key`, owner/auth/history epoch, source, product, build, and ticket,
  and accepts only closed native result `user_cancelled`. Under the credential
  lease and final owner mutation fence it may transition `issued|opened` to
  `cancelled` only while no verified transaction/event/credential/grant is bound
  and no concurrent submit/reconciliation has made the intent uncertain or
  consumed. Exact replay returns the same sanitized receipt; conflicting body,
  timeout, network/unknown error, app termination, pending purchase, or any
  ambiguous provider result never closes the ticket. A late verified provider
  event always supersedes cancellation and atomically closes any newer purchase
  intent before entitlement resolution.
- Bearer purchase-intent create/open/cancel and transaction submission hold the
  Backend Task 3 credential lease plus owner mutation fence and recheck selector,
  `auth_epoch`, and deletion state in their final transaction. Provider webhooks/
  reconciliation do not use a bearer lease, but entitlement binding still uses
  the owner fence and deletion tombstone rules.
- `POST /api/v1/billing/review-integrity/challenges` is bearer-only, requires a
  separately persisted `Idempotency-Key`, and is available only to a protected
  synthetic-review generation, admitted build, and active provider purchase-test
  cycle. It accepts platform,
  source/product, and app version/build, persists a random single-use five-minute
  challenge plus sanitized request hash, and returns the challenge ID. Android
  also receives the base64url SHA-256 `requestHash` and
  `cloud_project_number` as a canonical positive decimal string. The project
  number comes only from typed server configuration bound to the current
  environment, package, signing certificate, version, and approved review build;
  it must parse within signed 64-bit range and cannot be supplied or overridden
  by the client. Both response fields are bound to user-HMAC, package,
  certificate/version, product, build, and nonce; no email or secret enters them.
  Raw Apple App Transaction JWS/Google integrity tokens are bounded to 64 KiB,
  verified once, never logged or persisted, and represented only by hashes plus
  normalized verdict fields.
- Challenge creation stores its versioned key-ID/HMAC and canonical request hash.
  Exact replay returns the same unexpired challenge; conflicting reuse is 409,
  and replay of a consumed/expired challenge reports that terminal state rather
  than minting another. A fresh challenge requires a fresh key.

- [ ] Add failing tests that flip `billing.entitlement_v2_reads_enabled` off/on
  and prove every quota, replay, Progress, pricing/account, Today, and API gate
  resolves identically under a pre-native parity fixture. After seeding the first
  Apple/Google event, grant, or credential, assert flag-off startup is rejected
  and v2 access remains intact while only purchase admission is disabled.
- [ ] Add overlap UI/API tests showing every renewing provider, one latest
  paid-through, and `purchase_allowed=false` for active/confirming Apple, Google,
  Stripe, Shopify/lifetime, or ambiguous legacy access.
- [ ] Add OpenAPI/generated-client tests for
  `GET /api/v1/billing/config` and the complete `NativeBillingConfigResponse`
  union. Cover bearer versus cookie-only auth, `no-store`, invalid/mismatched
  platform/build/environment, flag-off 404 with zero side effects, exact Apple
  product IDs, exact Google product/base-plan/offer IDs including the null regular
  sentinel, and ordinary/staging-test/production-review contexts. Provider not
  ready, admission off, or a nonmatching lane user/build must return a null
  account key and no purchase sheet.
- [ ] Test explicit account-key provisioning/backfill, concurrent idempotent
  enrollment, stable random values across restart/HMAC rotation/backup/restore,
  and deletion/re-registration. Prove neither key derives from user ID/email,
  crosses accounts, or survives deletion; a GET never creates or changes one.
  Assert config responses contain no localized prices, Play offer tokens,
  provider secrets, tester/reviewer identity, or allowlist, and that a stale
  config revision or provider/product/account-key/build/context mismatch blocks
  purchase-intent creation before a native sheet.
- [ ] Add CLI/service tests for default dry-run immutability, explicit apply,
  batch bounds, opaque cursor resume, crash/restart, two concurrent runners,
  concurrent account creation, deletion before/during the final transaction,
  insert collision, PII-free JSON/logs, and convergence. Seed old live users with
  neither/one/both keys and deletion-pending/deleted users; after bounded apply
  pages plus a fresh dry-run, require zero eligible live users missing either
  platform key and no key created for a deleted owner.
- [ ] Make `UserStore._from_row` resolve a snapshot through its shared entitlement
  service only when the feature flag is on, assigning `effective_is_pro` without
  changing legacy fields. Avoid recursive user lookups.
- [ ] Update browser quota/replay/progress/account renderers to consume
  `User.is_pro` and the snapshot summary. Mobile routes consume one
  `resolve_mobile_capabilities(auth_context, app_identity)` result so the exact
  review-scoped bearer can receive its nonpersistent demo overlay; no other
  renderer or cookie path may infer it. Do not create independent source
  precedence.
- [ ] Render multiple provider management actions honestly. Existing lifetime or
  Season/Founders access shows “Managed by CaddieInsight” and no duplicate native
  purchase prompt.
- [ ] Add generated management-target tests for Apple, an authoritative current
  Google product binding, no binding, stale/replaced/unapproved Google product,
  overlap, admission off, lifecycle outage, and history/account invalidation.
  Prove the target contains no token/account key and never depends on the native
  purchase-config endpoint; a missing/ambiguous target yields only the approved
  generic provider URL/support path.
- [ ] Add purchase-intent tests for simultaneous iOS/Android and two-device
  requests, exact replay/conflicting idempotency, active/confirming/overlap,
  provider outage, open-before-sheet ordering, user cancel, success consumption,
  timeout/crash/restart to uncertain, notification/restore resolution, stale
  epoch/build, deletion race, and cross-account ticket use. Exactly one native
  sheet may be admitted per user across providers; uncertainty closes global new
  purchase admission while lifecycle/restore remains live.
- [ ] Add `billing.staging_test_lane_enabled: false`, an empty list of versioned
  stable-user HMAC pairs, and an empty exact-preview-build allowlist to defaults,
  shipped config, validation, environment docs, and health readback. When the
  deployment environment is not staging, fail startup if the flag or either list
  is nonempty—even if ordinary admission is off. Never accept raw user ID/email
  in configuration or reuse the production review-user domain/namespace.
- [ ] Add only the coarse emergency kill switch
  `billing.production_review_enabled: false` to defaults/shipped config/docs.
  Durable provider records above—not environment variables or raw configured
  identities—are authoritative for Apple’s bounded submission window, Google’s
  standing App-access mode, builds, credentials, and purchase-test expiry. Startup
  rejects the same user/account/credential in both providers, a purchase-test
  cycle without its demo/auth lane, a missing/stale recovery revision, or an
  incomplete tuple. Health exposes only the coarse flag, provider mode/state and
  demo/purchase-test booleans, aggregate credential/build/fixture counts, and
  recovery-head/revision match; it never exposes identity, credential, digest, or
  timestamps.
- [ ] Add review-access CLI/service tests for default dry-run immutability,
  secret-stdin-only parsing/redaction, distinct provider user creation, scrypt
  verification only through `ReviewAuthAdmission`, normal-user password sentinel,
  browser/passwordless/OTP/reset/signup/convergence denial, bounded Apple versus
  nonexpiring standing-Google demo access, exact active build binding, idempotent
  replay/conflict, weak/shared/normal-account rejection, and cross-provider
  isolation. For purchase testing require provider-specific opening/expiry bounds,
  startup/request-time auto-close, status/readback, and crash recovery; disabling
  must drain/reconcile/purge only isolated purchase state while demo/auth stays live.
  Test Google build roll-forward adds new before retiring old, permits at most two
  exact builds during bounded overlap, invalidates retired-build bearers, and never
  changes the standing identity. Test credential prepare → protected console
  readback/clean login → activate → console old-removal readback → retire, replay/
  rollback/crash at every phase, and at least one tested credential throughout.
  Reject missing/stale/wrong-app/provider/build/replayed evidence, bare booleans,
  and Google permanent close/purge without protected delisting/App-access-clear
  proof. Crash and race every disable/close/purge phase with scoped auth, API
  lease, review intent/event, account deletion, backup, and process restart; prove
  the off-volume revision is read back before exposure/terminal state, active and
  old HMAC-key lookup survives legitimate rotation, an old credential/build/window/
  purchase-test state never revives after point-in-time restore, and sanitized
  JSON/log/DB/backup/recovery-record scans contain no secret.
- [ ] Add generation-7 review-step-up schema tests for every required provider/
  generation/user/selector/epoch/installation/app-identity/purpose/discriminator,
  password-proof/PKCE/idempotency HMAC key ID, canonical request hash, phase,
  expiry and index/constraint. Prove email/review endpoints and rows cannot cross-
  consume, current/old HMAC keys survive valid rotation, missing keys/partial
  schemas fail startup/restore, service restore purges nonterminal secrets, and
  backup/log scans contain no account/password/verifier/token material.
- [ ] Add fixture/generation tests for schema/hash drift, initial idempotent seed,
  a crash at every file/DB/fsync/publish/revision phase and restart/orphan cleanup,
  duplicate-provider isolation, no customer/provider/
  entitlement data, exact owner/history-epoch binding, complete Today-to-Progress
  and every-Pro-screen traversal, sanitized status counts, reset, backup/restore,
  export inclusion, history reset, and account deletion. After review deletion,
  assert the old user/fixture/bearers remain recovery-fenced and absent; the same
  standing Google credential on a current build creates exactly one fresh user
  generation/fixture under concurrency and receives no bearer before its terminal
  revision readback, while wrong/closed/retired credentials
  create nothing. Apple/Google generations never share rows or assets.
- [ ] Build the wheel and production Docker image, then run review-access
  provision/seed/status plus the full Today-to-Progress fixture traversal inside
  each artifact. Add package-data/repository tests that fail if any of the three
  JSON resources is absent; if the fixture/schema is duplicated elsewhere,
  schema-invalid, or hashes differently from the store asset manifest binding; or
  if the shipped trust registry is malformed or unexpectedly production-active
  before Release Task 7.
- [ ] Add staging-lane tests for a matching/nonmatching/rotated-key user, exact/
  stale preview build, ordinary admission off, per-user `/me`/entitlement
  capability and purchase-sheet UI, concurrent intents, Apple verified sandbox
  versus production JWS, Google authoritative `testPurchase` versus unmarked
  purchase, and lifecycle/restore after the lane closes. Prove default-off,
  production/review-namespace startup rejection, cross-database isolation, no
  allowlist digest in API/log/backup evidence, and no sheet for the ordinary beta
  cohort.
- [ ] Add cancel-route contract/race tests for issued/opened exact cancellation,
  stable idempotent replay, conflicting replay, wrong owner/epoch/source/product/
  build/ticket, cookie-only/invalid bearer, cancel versus verified submit/event,
  late event after cancel, and credential/deletion races. Prove timeout, network/
  unknown native error, pending, termination, or any non-`user_cancelled` result
  leaves the intent uncertain/blocked rather than calling cancel.
- [ ] Race sign-out, self/other-device revoke, token rotation, password reset, and
  account deletion against intent create/open/cancel and transaction binding.
  A closed selector cannot publish intent/binding state; an already verified
  provider event remains safely unbound/reconcilable rather than being lost or
  rebound to a deleted owner.
- [ ] Add review-integrity challenge tests for exact account/build/product/window,
  lost-response exact idempotent replay, conflicting-key body, expiry/single use/
  terminal replay/new-key creation, request-hash canonicalization, wrong account/epoch,
  deletion/sign-out race, 64-KiB limits, malformed proof, and DB/backup/log scans.
  For Android also test a missing/zero/non-decimal/overflow project number, wrong
  staging-versus-production project/build/package/certificate binding, and a
  client-supplied override; all fail before proof generation or token decode.
  Raw JWS/integrity token, nonce input, email, and device identifiers must not
  persist. Each challenge is single-use, but a verified Apple AppTransaction JWS
  hash may legitimately recur across separately challenged monthly, annual, and
  restore requests for the same approved app build/window; Google token replay
  remains governed by its request hash/challenge.
- [ ] Add review capability/intent tests with general production admission off:
  only the exact provider-scoped synthetic bearer/generation/active build and,
  for Apple, open submission window gets every Pro
  mobile capability and `review_demo_active`. With its purchase-test switch off,
  assert null context/no sheet/no intent; independently turn on Apple or Google
  and prove only that provider gets one isolated intent. For Google distinguish a
  controlled license-tester device cycle from final public review, which keeps
  purchase testing off while demo access remains complete. A wrong/ordinary
  bearer, real user, provider, build, product, source, expired Apple window or
  purchase-test cycle, or ordinary intent ID is rejected. Use separate Apple/
  Google users and simultaneous provider cycles/intents to prove neither can block, grant, authenticate,
  clean up, or alter the other. Race review purge/expiry/deletion/submission and
  prove no `native_purchase_intents`, provider credential, production event/
  grant/binding, `User.effective_is_pro`, lifecycle watermark, or ordinary
  capability is created.
- [ ] Enable v2 reads only in tests after zero shadow mismatches. Run
  `python -m pytest tests/test_entitlement_api.py tests/test_native_purchase_intents.py tests/test_billing_account_key_backfill.py tests/test_review_integrity_challenges.py tests/test_review_access_cli.py tests/test_review_fixture.py tests/test_mobile_review_privacy.py tests/test_staging_billing_lane.py tests/test_credential_mutation_guard.py tests/test_entitlement_shadow_parity.py tests/test_membership_tiers.py tests/test_replay_gate.py tests/test_progress_gate.py tests/test_web.py -q`; expect all pass. The review privacy file now adds all three purposes, exact provider/build/generation/selector/epoch, dedicated-password/PKCE/replay/rate limits, credential closure/rotation/deletion races, export/reset/delete E2E, recovery revision before 204, non-revival of the old generation, and standing-Google fresh-generation login.
- [ ] Keep the deploy flag off unless there are zero active Stripe quarantine
  grants, zero unexplained parity mismatches, and no unprocessed provider event.
  Record the native-lifecycle watermark atomically only with the first verified
  Apple/Google event, grant (including an isolated review event/grant), or
  encrypted provider purchase credential—the exact Task 1 trigger. Flag/config
  activation, account-key provisioning, authenticated provider test receipts,
  and non-owning mismatch-quarantine receipts do not set it. Before that trigger
  a failed lifecycle smoke may roll back its flag; afterward do not deploy a
  binary or schema that lacks the current resolver/lifecycle paths.
- [ ] Regenerate `docs/api/openapi-v1.json`, run
  `npm --prefix mobile run api:generate && npm --prefix mobile run api:check &&
  npm --prefix mobile run typecheck`, and commit the byte-matched generated
  client type with this contract change:
  `git commit -m "feat: resolve access from unified entitlements"`.

## Task 6: Verify Apple StoreKit 2 transactions and App Store Server Notifications V2

**Files:**

- Modify: `pyproject.toml`
- Create: `swinglab/integrations/apple/__init__.py`
- Create: `swinglab/integrations/apple/storekit.py`
- Modify: `swinglab/entitlements/service.py`
- Modify: `swinglab/api/native_billing.py`
- Modify: `swinglab/api/contracts.py`
- Modify: `swinglab/web/mobile_auth.py`
- Modify: `swinglab/web/mobile_schema.py`
- Modify: `swinglab/web/app.py:3350-3378`
- Modify: `docs/environment.md`
- Modify: `docs/api/openapi-v1.json`
- Modify: `mobile/src/api/schema.generated.ts`
- Create: `tests/test_apple_billing.py`
- Create: `tests/test_apple_notifications.py`

**Interfaces:**

- Add web dependencies `app-store-server-library>=3.1.2,<4` and
  `httpx>=0.27,<1` (the latter may already be present from Backend Task 7).
- `AppleStoreVerifier.verify_transaction(jws) -> VerifiedAppleTransaction` and
  `.verify_notification(signed_payload) -> VerifiedAppleNotification` use
  Apple’s `SignedDataVerifier`, configured bundle ID/environment/app Apple ID and
  pinned Apple root certificates.
- `POST /api/v1/billing/apple/transactions` requires `Idempotency-Key` and accepts
  one bounded StoreKit 2 JWS from the authenticated current account. It stores the
  Apple-submit key-ID/HMAC, canonical request hash, provider transaction
  fingerprint, and sanitized result receipt atomically; exact replay returns the
  same result, conflicting key/body is 409, and the same verified transaction
  under a new key resolves to the existing provider result without another grant.
  A newly unbound purchase must consume
  the exact Task 5 purchase-intent ticket; already-bound restore and lifecycle
  reconciliation do not require or create an intent.
- A production review-lane Apple submission additionally requires its single-use
  integrity challenge ID and the raw `VerificationResult.jwsRepresentation` for
  StoreKit `AppTransaction.shared`. The server calls Apple library
  `verify_and_decode_app_transaction` and matches bundle ID/environment. The
  production verifier requires the exact configured `appAppleId`. The production
  review lane's sandbox verifier is constructed with `app_apple_id=None` and
  requires decoded `appAppleId` to be absent; a non-null sandbox value is rejected/
  quarantined rather than compared to the production ID. Both require decoded
  `applicationVersion` (StoreKit `appVersion`) to equal the
  approved submitted IPA `CFBundleShortVersionString` marketing version before
  consuming the challenge. If the decoded library model exposes Apple's
  `appVersionID`, it must equal the exact App Store Connect app-version ID read
  back for the submission. `CFBundleVersion` is a separate build identifier:
  bind it through signed IPA provenance, App Store Connect's selected-build
  readback, immutable client build header, and challenge—not by claiming
  AppTransaction signs it. `originalApplicationVersion` is the customer's first-download build,
  so it is bounded/format-validated and retained only as a normalized diagnostic;
  it is never compared to the current review build or used as current-build proof.
  AppTransaction proves
  the signed app transaction/marketing-version context, not the current build
  number or IAP itself; the separately
  submitted StoreKit transaction JWS remains bound to challenge/account/product.
  The same valid AppTransaction JWS/hash may be reused under a fresh challenge for
  another monthly/annual/restore action in the same build/window. A client-decoded
  `AppTransaction` object or unsigned header is never accepted as proof.
- `POST /api/v1/billing/apple/transactions/claim` additionally consumes a
  `purchase_claim` step-up token and `Idempotency-Key` for the guarded missing-
  token or deleted-lineage reclaim path; normal purchases never use it.
- `POST /webhooks/apple/app-store` accepts `{signedPayload}` and returns 2xx only
  after a bounded 256-KiB read, JWS verification, and idempotent lifecycle-event
  or `TEST` delivery-receipt persistence.
- A verified App Store Server Notifications V2 `TEST` notification has no
  transaction lifecycle to project. Persist one idempotent
  `provider_delivery_receipts(kind="test")` row keyed by source/environment/
  signed notification UUID under `entitlement-provider-delivery-message` plus
  canonical signed-payload SHA-256, then return
  2xx with no event/credential/grant/binding/watermark write. Duplicate replay is
  the same receipt; verification or receipt-storage failure remains non-2xx.
- Production normally instantiates only the production `SignedDataVerifier`.
  `AppleReviewLane.verify_and_apply(...)` may invoke a separate sandbox verifier
  only after authenticated user ID HMAC, protected review-window/config, exact
  approved product, and provider-signed App Transaction bundle/version proof all
  match the predeclared synthetic reviewer account/build. It writes only the
  isolated review namespace. Apple exposes one Sandbox Server URL for the app;
  during Release Task 9's explicit staging-drain/reconciliation cutover that slot
  points to the production review endpoint. That endpoint may update an already-
  known review original transaction but cannot create a production grant; every
  other sandbox JWS—including a staging/TestFlight transaction—is verified then
  quarantined with only a message HMAC/reason and no access or cross-database
  forwarding. Staging keeps lifecycle truth current through its encrypted-
  credential reconciliation until the Sandbox URL is restored after review.
- Extends the existing email step-up enum with `purchase_claim`; the token can be
  consumed only by the Apple/Google unbound-purchase claim endpoint.

- [ ] Add failing verifier tests using Apple library test fixtures for valid/
  invalid signature, wrong bundle/app ID, sandbox/production mismatch, unknown
  product, wrong `appAccountToken`, renewal, grace, cancel, expire, refund,
  revoke, missing `appAccountToken`, offer-code/out-of-app acquisition, family/
  shared transaction rejection where ownership is unclear, and replay/out-of-
  order notification.
- [ ] Add signed Apple `TEST` notification tests for production/sandbox verifier
  selection, notification UUID replay/body conflict, durable test receipt before
  2xx, injected storage failure, bounded retention, and proof that no entitlement
  event, provider credential, grant, binding, or lifecycle watermark is written.
- [ ] Add production-review-lane tests for exact synthetic account, signed app
  bundle/version, monthly/annual product, enabled build/window, sandbox renewal/
  refund/revoke notification, expiration, and purge. Reject wrong/real user,
  spoofed header without signed App Transaction, other build/product, before/
  after window, sandbox payload on the production endpoint, and any attempt to
  create/bind a production event or grant. Arbitrary production users must gain
  nothing from valid Apple sandbox JWS. Add the one-Sandbox-URL transition matrix:
  drained staging cursor, staging notification delivered to production-review
  quarantine, review notification applied only to review namespace, no cross-DB
  mutation/forward, authoritative staging reconciliation catch-up, URL restoration,
  duplicate retry, and crash/restart at every cutover record phase.
- [ ] Add App Transaction proof tests for valid raw JWS, wrong signature/bundle/
  environment/current `applicationVersion` versus
  `CFBundleShortVersionString`, optional `appVersionID` mismatch, decoded-object substitution,
  transaction-JWS substitution,
  challenge replay/expiry/account mismatch, oversize input, and redaction from
  DB/backups/logs. Prove monthly → annual → restore may reuse one valid
  AppTransaction JWS only with three fresh single-use challenges and separately
  verified product transactions; replaying any challenge still fails. Prove a
  different valid `originalApplicationVersion` does not fail when current
  `applicationVersion` matches, while a matching original plus wrong current
  version fails. Use fixtures where marketing version and `CFBundleVersion`
  differ; prove AppTransaction is not compared to the build number, while signed-
  artifact/provider-selected-build/client-header/challenge disagreement still
  fails independently. Accept sandbox-null `appAppleId`, reject/quarantine sandbox non-
  null, and require the exact ID in production. Only the normalized verdict/JWS
  hash may persist.
- [ ] Add failing account-binding tests: generate one random UUID
  `appAccountToken` per user, return it only through the generated
  `GET /api/v1/billing/config` `AppleNativeBillingConfig`,
  require it on first purchase, and prevent one original transaction ID binding
  to two users. Never derive the UUID from email/user ID.
- [ ] Add Apple purchase-intent tests for ticket/user/product/account-key/build
  binding, lost opened-response exact replay/conflict, lost transaction-submit
  response exact replay/conflict, same provider transaction under a new key,
  exact consume/replay, transaction after intent timeout, notification
  resolving a crashed `opened` intent, restore without a new intent, and rejection
  of a second-device/Google intent while Apple is active or uncertain.
- [ ] Define guarded restore for a verified unbound transaction whose optional
  `appAccountToken` is missing: require authenticated restore plus email step-up
  and explicit claim, recheck the original transaction is unbound, call Apple’s
  Set App Account Token API with this user’s random token, verify readback, then
  bind. An existing other-user binding or unsupported family-share state returns
  a non-transferable conflict and support path; never weaken the rule silently.
- [ ] For a deleted-lineage match, require the Task 1 reclaim proof and explicit
  request bit, then set/read back the new account’s Apple App Account Token and
  atomically move only the current grant from unbound to the new user. Test same-
  email re-registration, a purchase continuing after deletion, old-token/new-
  token notification order, and no coaching-history resurrection.
- [ ] Load Apple private key, issuer/key IDs, root certificate paths, bundle ID,
  app Apple ID, and environment only from protected server configuration. Startup
  with Apple disabled requires none; enabled-invalid configuration fails closed.
- [ ] Split Apple lifecycle readiness from new-purchase/claim admission. Existing-
  binding restore, server notifications, history/status reconciliation, and
  terminal lifecycle events continue when admission is off. Once an Apple event,
  grant, credential, or review record exists, missing lifecycle verifier config
  fails startup; test incident disablement preserves paid-through and still
  applies renewal, expiry, refund, and revoke.
- [ ] Map only the approved monthly/annual product IDs to canonical Pro products.
  Store transaction/original IDs, dates, state, environment, and JWS hash; never
  persist the JWS.
- [ ] After a verified transaction or notification, persist only its verified
  Apple `originalTransactionId` as the encrypted `anyTransactionId` credential
  in `provider_purchase_credentials`, bound by AES-GCM associated
  data to its stored `entitlement-original-purchase` key-ID/digest pair. Never
  accept an unverified/client-decoded identifier, expose it outside the Apple
  adapter, or put plaintext/ciphertext in export, telemetry, errors, or logs.
  Exercise notification-first insertion, transaction-first replay, and atomic
  HMAC/AEAD re-encryption so rotation cannot make the credential undecryptable.
- [ ] On client transaction, verify, bind, apply, return the updated snapshot,
  then allow the client to finish the transaction. If verification is retryable,
  return `confirming` and leave free features available.
- [ ] On server notification, verify nested signed transaction/renewal data,
  normalize a source event, apply it idempotently, and never trust top-level JSON
  before JWS verification.
- [ ] Run `python -m pytest tests/test_apple_billing.py tests/test_apple_notifications.py tests/test_entitlement_store.py tests/test_entitlement_api.py -q`; expect all pass.
- [ ] Regenerate OpenAPI, then run
  `npm --prefix mobile run api:generate && npm --prefix mobile run api:check &&
  npm --prefix mobile run typecheck`; commit both deterministic contract files:
  `git commit -m "feat: verify Apple Pro subscriptions"`.

## Task 7: Verify Google Play purchases, acknowledge them, and reconcile RTDN

**Files:**

- Modify: `pyproject.toml`
- Create: `swinglab/integrations/google/__init__.py`
- Create: `swinglab/integrations/google/play_billing.py`
- Modify: `swinglab/entitlements/service.py`
- Modify: `swinglab/api/native_billing.py`
- Modify: `swinglab/api/contracts.py`
- Modify: `swinglab/web/app.py:3350-3378`
- Modify: `swinglab/web/users.py`
- Modify: `swinglab/web/mobile_auth.py`
- Modify: `swinglab/web/mobile_schema.py`
- Modify: `swinglab/backups/core.py`
- Modify: `swinglab/config.py`
- Modify: `config.yaml`
- Modify: `docs/environment.md`
- Modify: `docs/api/openapi-v1.json`
- Modify: `mobile/src/api/schema.generated.ts`
- Create: `tests/test_google_billing.py`
- Create: `tests/test_google_rtdn.py`

**Interfaces:**

- Add web dependencies `google-auth>=2.40,<3` and `cryptography>=46,<47`.
- `GooglePlayDeveloperClient.from_protected_config(...)` loads only
  `GOOGLE_PLAY_DEVELOPER_CREDENTIALS_JSON` from the server secret environment,
  parses `google.oauth2.service_account.Credentials` with the sole OAuth scope
  `https://www.googleapis.com/auth/androidpublisher`, validates the approved
  service-account subject HMAC and allowlisted HTTPS token URI
  `https://oauth2.googleapis.com/token`, pins the REST base to
  `https://androidpublisher.googleapis.com`, binds package
  `com.caddieinsight.app`, and exposes only
  subscription-v2 get, acknowledge, and voided-purchase read methods. No refund,
  cancel, catalog, release, tester, permission, or publishing method exists in the
  application adapter. This dedicated identity is distinct from the Play
  Integrity decode credential, Pub/Sub push OIDC caller, and Google Play system
  RTDN publisher.
- Enabling Google lifecycle requires that credential, Android Publisher API
  readiness, exact package binding, and a successful bounded read-only provider
  probe. `/healthz` exposes only `developer_api_ready`,
  `credential_subject_matches`, and last-success age/error class—never the email,
  JSON, token, project secret, or permission list. Missing/wrong/expired/
  unusable runtime credentials fail closed before workers or purchase admission;
  an overprivileged Play Console role readback blocks operational activation.
- `GooglePlayVerifier.verify_subscription(purchase_token) -> VerifiedGooglePurchase`
  calls `purchases.subscriptionsv2.get`; `.acknowledge(...)` calls the developer
  API when authoritative ownership/product binding succeeds, at least one
  purchased present or future line item exists, subscription state is exactly
  `SUBSCRIPTION_STATE_ACTIVE`, `SUBSCRIPTION_STATE_PAUSED`,
  `SUBSCRIPTION_STATE_IN_GRACE_PERIOD`, `SUBSCRIPTION_STATE_ON_HOLD`, or
  `SUBSCRIPTION_STATE_CANCELED`, and
  `acknowledgementState` is `ACKNOWLEDGEMENT_STATE_PENDING`. Acknowledgment is
  independent of entitlement start time, including deferred replacements.
- Google verification binds each applicable `SubscriptionPurchaseV2` line item's
  authoritative product plus `offerDetails.basePlanId` and
  `offerDetails.offerId` to the exact purchase intent. Absent `offerId` matches
  only the regular-base `null` sentinel. A mismatched/unapproved offer never
  consumes the intent, grants access, or gets acknowledged; restore/RTDN still
  normalize the verified provider offer without inventing an intent.
- The backend developer-API call is the sole Android acknowledgement authority.
  A verified response is returned only after pending acknowledgement succeeds or
  authoritative state is already acknowledged; the native client never calls
  Play Billing acknowledgement for that purchase.
- `GooglePlayIntegrityVerifier.decode_and_verify(token, expected_request_hash) ->
  VerifiedReviewIntegrity` authenticates to
  `playintegrity.googleapis.com/v1/{package}:decodeIntegrityToken`, then requires
  exact request package/hash/fresh timestamp, `PLAY_RECOGNIZED`, approved signing
  certificate/version code, licensed review account, and configured device
  integrity verdict. It never trusts the encrypted token locally or persists it.
- `POST /api/v1/billing/google/purchases` requires `Idempotency-Key` and accepts
  supported product ID and raw purchase token once over authenticated HTTPS. It
  atomically stores the Google-submit key-ID/HMAC, canonical request hash, token-
  fingerprint pair, and sanitized verification/acknowledgement receipt. Exact
  replay returns the same result; conflicting key/body is 409; the same verified
  token under a new key resolves to the existing provider result without another
  grant or acknowledgement. A newly unbound purchase must
  consume the exact Task 5 purchase-intent ticket; already-bound restore,
  acknowledgement, RTDN, and reconciliation do not require or create an intent.
- `POST /api/v1/billing/google/purchases/claim` additionally consumes a
  `purchase_claim` step-up token and `Idempotency-Key` for a verified unbound
  out-of-app or deleted-lineage purchase; it never transfers an existing live
  binding.
- `POST /webhooks/google/play` requires a verified Pub/Sub push OIDC token with
  exact audience and service-account email, enforces a 256-KiB body limit,
  requires the exact configured subscription name from the push envelope,
  and performs a bounded decode with exactly one recognized RTDN payload branch.
  A mutually exclusive `testNotification` contains only its version: after OIDC,
  subscription, envelope message-ID, top-level package/version, and canonical-
  body validation, persist a durable idempotent delivery-test receipt keyed by
  subscription plus Pub/Sub message ID under
  `entitlement-provider-delivery-message` plus canonical-body SHA-256, then return
  204.
  That branch performs no Play Developer API call and writes no purchase token,
  event, credential, binding, grant, or entitlement; receipt-storage failure is
  non-2xx for redelivery. Token-bearing subscription lifecycle messages call
  `purchases.subscriptionsv2.get`; void/refund messages use their authoritative
  Voided Purchases/subscription lookup before any grant transition. After
  successful authentication/bounded parse, required authoritative lookup, and a
  durable idempotent mismatch-quarantine receipt, an opposite-environment or
  otherwise non-owning valid message returns 204 so the shared topic does not
  create a retry storm. It still writes no token, external account ID, provider
  credential/event, binding, or grant. Invalid OIDC/subscription, malformed/
  oversized input, transient or uncertain Google lookup, and quarantine/storage
  failure are not acknowledged; they return the bounded retryable/non-success
  class, feed the protected dead-letter policy, and alert without raw payloads.
- Staging accepts a new authoritative purchase containing Google’s `testPurchase`
  marker only while the staging billing lane is active and the approved product/
  base plan plus authoritative
  `externalAccountIdentifiers.obfuscatedExternalAccountId` match the opened
  candidate staging intent. RTDN-before-client-submit may settle that exact
  candidate. After lane closure, only lifecycle updates whose token fingerprint
  is already durably owned by staging are accepted. Unknown fingerprints,
  absent/random/mismatched external account IDs, unapproved products, and every
  unmarked production purchase are quarantined before any token, event,
  credential, binding, or grant write. Production rejects and quarantines test
  purchases except through its isolated review lane. Both environments use the
  same store identity/topic but never the same entitlement database. A mismatch
  quarantine stores only a provider-message HMAC, environment/reason, and time—
  never the purchase token, external account ID, or user.
- The only production exception is `GoogleReviewLane.verify_and_apply(...)`.
  Before accepting a `testPurchase`, it requires the authenticated synthetic
  reviewer generation HMAC, exact bounded purchase-test opening/expiry, approved
  product/base plan, and a
  server-verified Play Integrity token whose package, signing certificate,
  version code, app-recognition, and `LICENSED` verdict match the predeclared
  review build. The verdict proves store acquisition/licensing, not the Google
  account's identity or membership in the developer's License Testing list; no
  reviewer email or Play account is inferred from it. It writes only isolated
  review events/grants. RTDN may
  update an already-known review token fingerprint but cannot create a production
  grant; all other production `testPurchase` input remains quarantined.

- [ ] Add failing verifier tests for service-account/config failure, package
  mismatch, product/base-plan/offer mismatch, null regular-base sentinel versus
  named trial/intro offer, pending, active, grace, hold, paused,
  canceled with paid-through, expired, refund/revoke, linked purchase token,
  unspecified, pending-purchase-canceled, unknown state, transient 5xx/backoff,
  permanent 404/410, immediate/deferred replacement, multiple line items with
  different start times, Play-store resubscribe, and
  `outOfAppPurchaseContext` association.
- [ ] Add credential-boundary tests for separate Developer API, Integrity-decode,
  push-OIDC, and system-publisher identities; exact subject/package/scope;
  exact token URI/REST host with no redirect or caller override; protected-secret
  redaction; startup/health behavior; authorized subscription
  get/acknowledge and voided-purchase reads; and absence/rejection of refund,
  cancel, catalog, release, tester, permission, and publishing operations. A
  credential valid for Integrity or Pub/Sub must never satisfy Developer API
  readiness.
- [ ] Add Google purchase-intent tests for ticket/user/product/base-plan/offer/
  account-key/build binding, lost opened-response exact replay/conflict, lost
  purchase-submit response exact replay/conflict, same token under a new key,
  exact consume/replay, purchase after timeout, RTDN resolving
  a crashed `opened` intent, restore without a new intent, and rejection of a
  second-device/Apple intent while Google is active or uncertain.
- [ ] Add failing OIDC/RTDN tests for missing/invalid bearer, wrong audience,
  wrong publisher service account, wrong/missing subscription name, malformed/
  base64/oversized payload, test notification, replay, renewal, cancel, revoke,
  and out-of-order delivery. Assert `testNotification` is mutually exclusive,
  tokenless, never calls `subscriptionsv2.get`, and returns 204 only after one
  durable `(subscription,message_id,body_sha256)` receipt; duplicate replay is the
  same receipt and injected storage failure returns non-2xx. RTDN content alone
  must never grant access. Test
  first and duplicate opposite-environment quarantine return 204 only after the
  durable idempotent receipt and produce no token/event/credential/binding/grant
  write; transient verifier/Google/storage failure returns non-2xx for redelivery,
  then reaches the bounded dead-letter/PII-free alert path without a retry storm.
  Expire quarantine receipts under their bounded retention only after replay
  protection no longer depends on them.
- [ ] Add `voidedPurchaseNotification` and `pendingRefundReviewNotification`
  parsing, durable deduplication, and tests. Resolve a void through the Voided
  Purchases/Subscriptions APIs before changing a grant. Pending-refund review
  creates a bounded PII-free operator alert and refreshes provider state but does
  not revoke access until an authoritative void/refund state exists.
- [ ] Add staging/production isolation tests for direct purchase verification and
  every RTDN variant. On the same app topic/subscriptions, deliver a staging
  tester purchase to production and a production-review tester purchase to
  staging; neither may cross-bind. Cover RTDN-before-client-submit for an exact
  opened staging candidate, lane closed with an already-known staging token,
  lane closed with an unknown token, and random/absent/mismatched
  `obfuscatedExternalAccountId`. A mismatch is quarantined before any token,
  event, credential, binding, or entitlement write; only the owning database may
  process later lifecycle updates.
- [ ] Add controlled pre-submission Google review-purchase tests for the exact synthetic generation, verified Play
  Integrity package/certificate/version/licensing result, monthly/annual product,
  License Tester `testPurchase`, purchase-test opening/expiry boundaries, RTDN
  renewal/refund/revoke, auto-disable, drain, and bounded purchase-state purge
  while standing demo/auth stays active. Also prove final Play review has full demo
  capabilities but null context/no intent/sheet. Reject real/wrong user, header-only build
  spoof, invalid/replayed Integrity nonce, wrong version/certificate/package/
  licensing verdict, other product, production purchase in review namespace, and
  every attempt to bind review state to production grants. Arbitrary production
  users must gain nothing from a valid Google test purchase.
- [ ] Add Play Integrity decoder tests for Google auth/decode outage, request-hash
  mismatch, stale timestamp, replay-cleared/unevaluated verdict, wrong package/
  certificate/version, unlicensed/unrecognized/tampered app, insufficient device
  verdict, challenge expiry/account mismatch, oversize token, and no raw-token
  persistence. Lifecycle/restore remains available if review attestation fails;
  only review purchase admission is denied.
- [ ] Generate one random 128-bit base64url `obfuscatedAccountId` per live user,
  store/back up that provider-visible opaque value plus its versioned
  `entitlement-provider-account-key` HMAC pair, and return it only through
  the generated `GET /api/v1/billing/config`
  `GoogleNativeBillingConfig`. It never derives from user ID/email and does not
  change on HMAC rotation/restore. Verify Google’s `externalAccountIdentifiers`
  when present; deletion removes raw value and retains only the versioned lineage
  HMAC before any later account gets a fresh value.
- [ ] Fingerprint purchase tokens with the shared versioned
  `entitlement-purchase-token` HMAC domain for lookup/binding and
  encrypt the exact token with AES-256-GCM using a protected versioned key,
  random nonce, and stored `(source, hmac_key_id, fingerprint)` associated data.
  Candidate lookup spans active HMAC keys, while decryption reuses the stored AD
  so HMAC rotation never strands ciphertext. Back up ciphertext and both key
  versions; never back up either key. Decryption exists only inside the
  verifier/reconciler, never logs or exports plaintext, and zeroizes local byte
  buffers after use where the runtime permits.
- [ ] Map every current `SubscriptionPurchaseV2` line item by product/base plan/
  offer identity, start, and expiry. Future deferred replacements stay `confirming` until their
  start. When `linkedPurchaseToken` is present, verify both account bindings and
  retire the old token’s grant in the same transaction that activates the new
  one; never leave both active for one replacement.
- [ ] For `outOfAppPurchaseContext`, match verified expired account identifiers/
  token to the existing account. An unbound or ambiguous Play-store purchase
  remains `confirming` until authenticated restore plus step-up resolves it;
  acknowledge only after binding and the exact authoritative state/
  acknowledgement/line-item predicate above.
- [ ] For a deleted-lineage match, re-verify the presented Play token even when
  old ciphertext has expired, require the Task 1 email-HMAC/step-up/explicit-
  confirmation proof, and atomically bind only the current grant to the new
  account while retaining the deletion tombstone. Test same-email re-registration,
  renewal after deletion, old/new RTDN order, and no coaching-history resurrection.
- [ ] Acknowledge every verified, account-bound token whose authoritative
  `acknowledgementState` is `ACKNOWLEDGEMENT_STATE_PENDING`, state is neither
  unspecified, pending, expired, nor pending-purchase-canceled but is exactly
  `SUBSCRIPTION_STATE_ACTIVE`, `SUBSCRIPTION_STATE_PAUSED`,
  `SUBSCRIPTION_STATE_IN_GRACE_PERIOD`, `SUBSCRIPTION_STATE_ON_HOLD`, or
  `SUBSCRIPTION_STATE_CANCELED`, and present or future line-item ownership/
  product mapping is valid. This includes a
  deferred-replacement token before its entitlement begins. Acknowledge before
  returning success; a retryable failure remains `confirming`, persists a leased
  retry with the encrypted token, and is safe to replay without the client or a
  new RTDN. Already-acknowledged tokens create no work.
- [ ] Split Google lifecycle readiness from new-purchase/claim admission. Existing-
  binding restore, acknowledgement retries, RTDN, Voided Purchases checks, and
  reconciliation continue when admission is off. Once a Google event, grant,
  encrypted credential, or review record exists, missing lifecycle verifier/
  encryption config fails startup; test incident disablement preserves paid-
  through and still applies renewal, hold, expiry, refund, and revoke.
- [ ] Run `python -m pytest tests/test_google_billing.py tests/test_google_rtdn.py tests/test_entitlement_store.py tests/test_entitlement_api.py -q`; expect all pass.
- [ ] Regenerate OpenAPI, then run
  `npm --prefix mobile run api:generate && npm --prefix mobile run api:check &&
  npm --prefix mobile run typecheck`; commit both deterministic contract files:
  `git commit -m "feat: verify Google Play Pro subscriptions"`.

## Task 8: Add the native purchase, restore, and management experience

**Files:**

- Modify: `mobile/package.json`
- Modify: `mobile/package-lock.json`
- Modify: `mobile/app.config.ts`
- Create: `mobile/modules/caddie-integrity/package.json`
- Create: `mobile/modules/caddie-integrity/expo-module.config.json`
- Create: `mobile/modules/caddie-integrity/src/index.ts`
- Create: `mobile/modules/caddie-integrity/ios/CaddieIntegrity.podspec`
- Create: `mobile/modules/caddie-integrity/ios/CaddieIntegrityModule.swift`
- Create: `mobile/modules/caddie-integrity/ios/Tests/CaddieIntegrityModuleTests.swift`
- Create: `mobile/modules/caddie-integrity/android/build.gradle`
- Create: `mobile/modules/caddie-integrity/android/src/main/AndroidManifest.xml`
- Create: `mobile/modules/caddie-integrity/android/src/main/java/expo/modules/caddieintegrity/CaddieIntegrityModule.kt`
- Create: `mobile/modules/caddie-integrity/android/src/androidTest/java/expo/modules/caddieintegrity/CaddieIntegrityInstrumentedTest.kt`
- Create: `mobile/src/features/billing/types.ts`
- Create: `mobile/src/features/billing/iapAdapter.ts`
- Create: `mobile/src/features/billing/api.ts`
- Create: `mobile/src/features/billing/useIAPBridge.ts`
- Create: `mobile/src/features/billing/ProScreen.tsx`
- Create: `mobile/src/features/billing/SubscriptionDisclosure.tsx`
- Create: `mobile/src/features/billing/RestorePurchasesButton.tsx`
- Create: `mobile/src/features/billing/reviewIntegrity.ts`
- Modify: `mobile/app/more/pro.tsx`
- Modify: `mobile/src/features/more/MoreScreen.tsx`
- Create: `mobile/tests/billing/{iapAdapter,proScreen,restore}.test.tsx`
- Create: `mobile/tests/billing/subscriptionDisclosure.test.tsx`
- Create: `mobile/tests/billing/expoIapTypes.test-d.ts`
- Create: `mobile/tests/billing/reviewIntegrity.test.ts`

**Interfaces:**

- Pin `expo-iap@5.0.1`; use its Expo config plugin with no IAPKit/aggregator key.
  Do not install `react-native-iap` or Nitro because that bridge is not the Expo
  managed-workflow package.
- `IAPAdapter.connect/fetchProducts/purchase/restore/finishVerified/manage(target)/disconnect`
  is the only feature-facing native billing interface. `finishVerified` is
  platform-specific: on iOS it calls `expo-iap` `finishTransaction` only after
  backend verification; on Android a backend-verified/acknowledged result is
  terminal and this method is a deliberate no-op before snapshot refresh. A
  `confirming` or failed backend result is never finished on either platform.
  No Android path may call `finishTransaction`, because `expo-iap@5.0.1` would
  issue a second client-side acknowledgement after the server already did so.
- `useIAPBridge` is a root-session service, not Pro-screen state. It registers
  `purchaseUpdatedListener` and `purchaseErrorListener` before `initConnection`,
  remains active across Pro-screen unmount/remount, and removes listeners only at
  authenticated app-root teardown after durable intent reconciliation. In
  `expo-iap@5.0.1`, `requestPurchase(...)` is dispatch-only; no code awaits its
  return value as a transaction result. A purchase-update event must match the
  persisted opened intent’s provider/product/account/build before idempotent
  backend submission. Duplicate/replayed events reuse that submission; an
  unexpected/out-of-app event enters restore/current-purchase reconciliation and
  never opens or manufactures another intent/sheet. Only the pinned bridge’s typed
  `PurchaseError.code === ErrorCode.UserCancelled` (serialized by expo-iap as
  `user-cancelled`) maps to the app/server body literal `user_cancelled` and may
  call the cancel route; pending, network, timeout,
  listener/init loss, synchronous throw after opened, or any other/unknown error
  leaves the intent uncertain for restore/reconciliation.
- `IAPAdapter.manage(target)` first refetches the generated entitlement snapshot;
  it never calls purchase config. For Apple’s authoritative target it invokes
  `deepLinkToSubscriptions()` with no arguments. For Google’s authoritative
  target it reads the installed immutable application ID through
  `expo-application`, verifies it equals the generated build/package identity,
  then invokes exactly `deepLinkToSubscriptions({packageNameAndroid:
  Application.applicationId, skuAndroid: target.product_id})`. Missing,
  ambiguous, stale, cross-account, or unapproved targets never guess a SKU; they
  show the server-returned generic official management URL/support path. Existing
  subscribers retain Manage while purchase admission/config/account-key exposure
  is off.
- On Android, `IAPAdapter.purchase` requires the server-approved product/base
  plan/exact offer identity and the matching
  `ProductSubscriptionAndroid.subscriptionOffers[]` entry, comparing both
  `basePlanIdAndroid` and required string `id`. Canonical server
  `offer_id=null` maps only to `id === basePlanIdAndroid` (expo-iap's regular-base
  mapping); a named offer maps only to `id === offer_id`, and server config
  forbids a named offer equal to its base plan. Read that same entry's
  `offerTokenAndroid` and `pricingPhasesAndroid`, then call the pinned wrapper
  exactly as `requestPurchase({type:'subs', request:{google:{skus:[sku],
  subscriptionOffers:[{sku, offerToken}], obfuscatedAccountId}}})`. In
  particular, `skus`, `subscriptionOffers`, and `obfuscatedAccountId` belong
  under `request.google`, never at the top level. Multiple eligible
  trial/intro/regular offers may legitimately share a base plan; only duplicate
  full identities, duplicate tokens, missing configured identity, or mismatched
  token fail before Play Billing. If the configured trial/intro offer is absent
  because the account is ineligible, select the separately approved regular-base
  canonical-null/client-base-plan sentinel or show unavailable—never choose another same-base offer by
  array order. iOS uses its product ID only.
- On iOS, introductory metadata is not eligibility proof. Before rendering any
  free/intro conversion copy, narrow the exact product to `ProductSubscriptionIOS`,
  require its `subscriptionGroupIdIOS`, and call
  `isEligibleForIntroOfferIOS(subscriptionGroupIdIOS)` for the current App Store
  account. Show the product’s introductory fields/offer only on a fresh `true`.
  `false`, missing/mismatched group, query error, offline/stale result, or account
  switch renders recurring-price-only disclosure; it never promises a trial.
  Purchase still sends only the approved iOS product ID and `appAccountToken`.
- Store purchase tokens are sent only to the matching backend endpoint; client
  access is always the returned `/api/v1/entitlements` snapshot.
- `SubscriptionDisclosure` is rendered adjacent to the purchase CTA before any
  native sheet. For the selected monthly or annual plan it shows the plan
  duration, localized recurring price and billing period from the native store,
  every free/introductory `pricingPhasesAndroid` phase from the exact selected
  Android offer, or the exact iOS product’s intro phase only after the fresh
  subscription-group eligibility result above, followed by its localized
  recurring price/period,
  with explicit conversion wording such as “Free for X, then Y every period until
  canceled”; that the subscription auto-renews until canceled; how to cancel/
  manage it; and working Terms and Privacy links. It states that subscribing is
  optional: Free coaching remains available without a purchase and after
  cancellation/expiry, while Pro features end at the verified paid-through time.
  It never hard-codes a price, infers a period from the product ID, hides renewal
  text behind another interaction, or opens an external digital-purchase path.
- Provider lifecycle readiness and new-purchase admission are separate server
  capabilities. The client renders a sheet only when the fresh generated response
  has `purchase_allowed=true` and a non-null `purchase_context`. Ordinary
  admission off normally hides/disables the purchase sheet and guarded new claim;
  the only exceptions are exact server-returned `staging_test` or
  `production_review` contexts for their exact synthetic generation/build and
  separately active bounded purchase-test cycle. Restore/
  Manage for an existing binding and snapshot refresh remain available so paid
  lifecycle truth is never frozen by a sales rollback. The client never infers a
  context from environment or account data and closes the CTA when refetch removes
  it.
- `review_demo_active=true` is presentation context, not a subscription. Every
  feature screen still renders only the server-returned capability booleans; More/
  Pro and review/privacy account controls label it “Temporary store-review
  access” and never invent an active grant,
  paid-through date, renewal provider, or management target. When paired with
  `purchase_allowed=false`/null context—as required for standing Google Play app
  access—the client exposes all returned Pro features but creates no config/
  intent, CTA, native sheet, restore claim, or manage action. Apple may
  independently return `production_review` for its sandbox purchase window.
- `beginPurchase(source, product, purchaseContext) -> PurchaseIntent` acquires the server’s one-
  per-user cross-provider ticket before any native sheet. The client persists the
  intent plus separate 128-bit create, open, transaction-submit, and cancel keys
  before any corresponding request. It marks it `opened` with the stable open key,
  waits for the replay-safe opened receipt immediately before `requestPurchase`, submits it with
  the verified transaction, and calls
  `POST /api/v1/billing/purchase-intents/{id}/cancel` with its persisted
  operation-specific 128-bit cancel idempotency key only after matching raw
  `PurchaseError.code === ErrorCode.UserCancelled`; the adapter then sends the
  normalized server result `user_cancelled`. Generate/persist that key before opening the
  sheet and keep it through lost-response/restart replay; do not reuse the create,
  open, or transaction-submit key.
  Crash/timeout shows Restore/Resume or support and never opens a second sheet.
  For `production_review`, it rechecks the still-current review intent/capability
  immediately before `requestPurchase`; after native success it acquires a fresh
  exact review-integrity challenge and obtains the platform proof just in time for
  submission with that transaction against the isolated intent. Capability/window
  loss before opening closes the UI; loss or proof failure afterward leaves the
  verified store transaction recoverable through bounded review restore/retry and
  never falls back to an ordinary intent or finishes prematurely. `staging_test`
  uses its staging intent/provider-signed sandbox marker and never requests a
  production review challenge.
- Local Expo module `CaddieIntegrity` exposes iOS
  `getAppTransactionJWS() -> string` by executing `try await
  AppTransaction.shared`, accepting only `.verified`, and returning that
  `VerificationResult.jwsRepresentation`, not its decoded value. It throws and
  returns no proof for `.unverified` or StoreKit failure. On Android
  it wraps `com.google.android.play:integrity:1.6.0` Standard Integrity with
  `prepare(cloudProjectNumber)` and `request(requestHash) -> token`. It exposes no
  generic device identifiers/verdicts and is invoked only for the protected
  review lane after a server challenge.
- `getReviewIntegrityProof(challenge)` obtains exactly the platform proof, sends
  challenge ID plus raw proof only in the bounded billing request over HTTPS, and
  immediately drops it from memory. On Android it validates/parses only the
  challenge's server-issued `cloud_project_number`, passes that value to
  `prepare`, and exposes no caller override or build-time fallback. It never
  writes proof/JWS/token to
  SecureStore, cache, telemetry, error reporting, or logs.

- [ ] Run `npm install expo-iap@5.0.1`, configure the plugin, and run
  `npx expo prebuild --clean --no-install` in a disposable verification copy,
  not the tracked tree. Assert iOS IAP and Android billing configuration are
  generated and no third-party verification API key is embedded.
- [ ] Generate the functions-only local scaffold with pinned
  `npx --yes create-expo-module@57.0.0 --local`, names
  `CaddieIntegrity`/`caddie-integrity`, then retain/audit its package, module
  config, podspec, Gradle file, Android manifest, TypeScript bridge, and Swift/
  Kotlin sources; remove sample view/event/example/nested-Git output. Expo finds
  it only through the default `mobile/modules` nativeModulesDir, not a `file:`
  dependency. Define the podspec's CocoaPods `test_spec` for
  `ios/Tests/CaddieIntegrityModuleTests.swift` and the Gradle instrumented-test
  target/dependencies for `CaddieIntegrityInstrumentedTest.kt`; both compile the
  real bridge and inject only test-scoped StoreKit/Integrity adapters. In the disposable CNG
  prebuild, compile Swift StoreKit proof extraction and the pinned Play Integrity
  1.6.0 Standard API, then build/run both development clients. Assert the raw iOS
  JWS path is available only on iOS, Android warmup/request uses the server’s
  request hash and project number, and neither platform silently substitutes a
  decoded object/header when proof is unavailable. Test `.verified`,
  `.unverified`, thrown StoreKit error, empty/oversize JWS, and bridge exception
  redaction in the native suites; instrument Android project-number/request-hash
  propagation, unavailable/provider-error behavior, token size bounds, and
  redaction. JS bridge tests cannot substitute for either native gate.
- [ ] Run Expo autolinking `resolve --platform apple`, `resolve --platform
  android`, and `verify`; assert exactly one `CaddieIntegrity` pod/Swift module
  and one `caddie-integrity` Gradle project with the expected module class, plus
  the existing storage module, and no duplicates. Compile actual iOS and Android
  development and release configurations and invoke the integrity bridge on
  signed devices before billing/review evidence; both configurations must fail
  if the podspec, Gradle project, manifest, or native class is absent.
- [ ] Add failing adapter tests for connection, localized product metadata,
  unsupported/missing products, user cancel, pending, success, retryable server
  verification, invalid purchase, Android approved exact-offer selection,
  multiple eligible offers sharing one base plan, trial eligible/ineligible
  regular-base fallback, null versus named offer ID, duplicate full identity or
  token, missing/mismatched offer token, disclosure/purchase selected-object
  identity, iOS intro eligible/ineligible/query-error/missing-or-mismatched-group
  recurring-only fallback, iOS finish-after-verification
  ordering, Android verified-result no-op, confirming-result no-finish,
  reconnect, Apple no-argument manage, Android exact package/SKU manage, no
  binding, stale/mismatched product, admission-off existing subscriber, and
  listener-before-init ordering, screen unmount/remount persistence, root-teardown
  cleanup, dispatch-only `requestPurchase`, update/error correlation, and
  listener cleanup. Spy on the pinned native bridge and prove no
  Android purchase or restore path invokes `finishTransaction`/acknowledgement
  after the backend accepts it.
- [ ] Add a compile-time `expo-iap@5.0.1` fixture that narrows Android products to
  `ProductSubscriptionAndroid`, selects from `subscriptionOffers` by
  exact `(basePlanIdAndroid, id)`, maps canonical null to client
  `id === basePlanIdAndroid`, rejects named-offer/base-plan ambiguity, reads that
  object's `pricingPhasesAndroid`, maps its
  `offerTokenAndroid` to request `offerToken`, type-checks
  `RequestSubscriptionPropsByPlatforms['google']` with `request.google.skus`,
  `request.google.subscriptionOffers`, and `request.google.obfuscatedAccountId`.
  The complete wrapper must also satisfy
  `Extract<RequestPurchaseProps, {type:'subs'}>`. It fails if any field is placed
  at the wrapper top level or if stale
  `subscriptionOfferDetails`/un-suffixed native fields are referenced. The same
  fixture narrows `ProductSubscriptionIOS.subscriptionGroupIdIOS` and type-checks
  the exact `isEligibleForIntroOfferIOS(groupId)` call before reading iOS
  introductory fields. It also type-checks the exact callback parameters accepted
  by `purchaseUpdatedListener` and `purchaseErrorListener` and fails if a purchase
  result is read from the `requestPurchase` dispatch return. Pin and assert
  `ErrorCode.UserCancelled === 'user-cancelled'` plus its sole mapping to the
  normalized cancel body `user_cancelled`; underscore/hyphen string guessing must
  fail the fixture/runtime tests.
- [ ] Add client purchase-intent tests for simultaneous taps/devices/providers,
  write-before-open, lost open response/replay before sheet, four distinct durable
  operation keys, exact ticket/transaction submission, lost submit response,
  same provider transaction semantic replay, user cancel, purchase success,
  app termination before/after native sheet, expired/uncertain intent, restore
  resolution, exact cancel-route replay, typed user cancellation versus timeout/
  network/unknown/pending results, cancel/verified-submit race, account/epoch
  switch, and offline restart. Never call
  `requestPurchase` without a live server ticket or while another intent is
  active/uncertain; never call cancel for an ambiguous result. Cover update/error
  delivery before/after dispatch resolution, duplicate provider event, no matching
  intent, wrong product/account/build, screen remount, root restart/listener
  reattachment before connect, synchronous throw, and lost callback; only typed
  user cancel closes the intent.
- [ ] Add review-integrity tests for challenge-before-proof ordering, exact raw
  field transmission, iOS/Android platform separation, warmup retry bounds,
  cancellation, challenge expiry, proof replay, tampered build/account/product,
  missing/malformed/out-of-range project number, staging/production mismatch,
  attempted caller override,
  app termination, and scans of SecureStore/files/telemetry/Sentry/console proving
  no proof material persists. Review proof failure never blocks free coaching or
  existing restore/manage.
- [ ] Add generated `purchase_context` UI/adapter tests: `null` hides the sheet;
  `ordinary` uses ordinary verified purchase flow; `staging_test` is visible only
  to the server-admitted tester and never requests/uses a production review
  challenge; `production_review` requires the exact review challenge/proof before
  its isolated intent. The client never derives or overrides context from build
  environment, account email, or local allowlists, and context change closes any
  un-opened UI before refetch.
- [ ] Add generated `review_demo_active` tests with no canonical grant: traverse
  Today, Brief, Practice, Progress, and every Pro route using only returned
  capabilities. Require the temporary-review disclosure in More/Pro and review/
  privacy account controls, but no global banner or review/demo chrome on Today,
  Capture, Brief, Practice, or Progress; those five routes remain truthful feature
  surfaces suitable for public screenshots. Require no paid-through/renewal/
  management fiction. For the Google final-review shape (`true`, purchase false,
  null context, no management targets), assert zero billing-config/intent/native-
  sheet calls. For the independently enabled Apple review shape, assert only its
  isolated `production_review` sheet can open. Closing/rotating the scoped bearer
  must refetch and remove demo-only capabilities before any stale screen renders.
- [ ] Add failing Pro tests for free purchase allowed, confirming, active Apple,
  active Google, Stripe, Shopify/Founders/lifetime, overlapping providers,
  expired, offline, lifecycle unavailable, purchase admission disabled with
  existing restore/manage still available, review demo without a grant or
  purchase/management path, Apple review demo with its isolated sheet, and
  localized monthly/annual offers.
  Add disclosure tests for monthly/annual duration, full ordered free/intro then
  recurring pricing phases from the exact selected offer, Android trial-eligible/
  ineligible regular fallback, iOS eligible intro versus false/missing/error/group-
  mismatch recurring-only fallback, localized price/period, auto-renew/cancel
  and explicit trial-to-paid conversion language, optional-subscription/Free-
  remains/Pro-ends-at-paid-through copy, Terms/Privacy links, no hard-coded price,
  CTA adjacency before the native sheet, 200% text, and screen-reader order/labels.
- [ ] Add failing restore tests for zero/multiple purchases, iOS JWS, Android
  token, same-account replay, different-account conflict, partial provider
  outage, missing Apple account token, Google out-of-app claim required, guarded
  step-up claim/cancel, and final snapshot refresh.
- [ ] Add deleted-lineage restore UI tests: show that deletion did not cancel the
  store renewal, require the same verified email plus a fresh emailed step-up and
  explicit “restore subscription only” confirmation, never imply history returns,
  and send mismatch/ambiguous cases to protected support without access.
- [ ] Fetch the generated `NativeBillingConfigResponse` from
  `GET /api/v1/billing/config?platform=ios|android`, then fetch native localized
  products using only its exact product/base-plan/approved-offer identities.
  Require the matching non-null generated account-key field before purchase.
  Before `requestPurchase`, acquire the server purchase intent; if entitlement,
  global health, provider admission, or another active/uncertain intent rejects
  it, show provider management/restore and never open the purchase sheet. Persist
  the ticket, mark `opened`, then call native billing exactly once.
- [ ] Pass Apple `appAccountToken` or Google `obfuscatedAccountId` from that exact
  config response in the purchase request. For Android, select the native offer whose full base-plan/offer
  identity matches the backend-approved intent, render disclosure from that exact
  object's ordered pricing phases, and pass its SKU/offer token plus identity.
  Treat the call return as dispatch-only. On the matching root
  `purchaseUpdatedListener` event, send the
  purchase-intent ticket plus `purchase.purchaseToken` to the backend, wait for
  verified/confirming result, call iOS `finishVerified` only after verified server
  acceptance, and deliberately do no Android client finish/acknowledgement after
  the backend's authoritative acknowledgement. Then refetch `/me`, capabilities,
  and entitlement.
- [ ] Restore with `getAvailablePurchases`, reconcile supported transactions one
  by one through the same endpoints, finish verified iOS transactions, leave
  verified Android purchases server-acknowledged with no client finish, and show
  one combined result. Implement Manage only from the fresh generated
  `management_targets`: iOS uses no-argument `deepLinkToSubscriptions`; Android
  passes the immutable installed application ID and server-authoritative current
  Google product ID. If either is unavailable/mismatched, use only the snapshot's
  generic official management URL/support path and never infer from local purchase
  history or purchasable config.
- [ ] Display price/currency/trial/renewal only from `fetchProducts`; render the
  complete `SubscriptionDisclosure` beside the selected purchase CTA before
  acquiring/opening the native purchase sheet. Digital Pro has no web checkout
  or external-offer link. Gear remains Shopify browser flow.
- [ ] Run `npm test -- billing --runInBand && npm run lint && npm run typecheck && npm run expo:doctor`; expect all pass.
- [ ] Commit: `git commit -m "feat: add native Pro purchase and restore"`.

## Task 9: Reconciliation, failure matrix, and activation controls

**Files:**

- Create: `swinglab/entitlements/reconcile.py`
- Modify: `swinglab/entitlements/service.py`
- Modify: `swinglab/entitlements/cli.py`
- Modify: `swinglab/cli.py`
- Modify: `swinglab/integrations/apple/storekit.py`
- Modify: `swinglab/integrations/google/play_billing.py`
- Modify: `swinglab/web/billing.py`
- Modify: `swinglab/web/app.py`
- Modify: `swinglab/web/mobile_privacy.py`
- Modify: `swinglab/web/recovery_fence_ledger.py`
- Modify: `swinglab/config.py`
- Modify: `config.yaml`
- Modify: `swinglab/backups/core.py`
- Modify: `swinglab/kpis.py`
- Modify: `docs/deployment.md`
- Modify: `docs/operations/backup-recovery.md`
- Create: `docs/runbooks/native-billing.md`
- Create: `tests/test_entitlement_reconciliation.py`
- Create: `tests/test_native_billing_failure_matrix.py`
- Create: `tests/test_apple_notification_recovery.py`
- Create: `tests/test_google_ack_recovery.py`
- Modify: `tests/test_entitlement_privacy_extension.py`
- Modify: `tests/test_privacy_erasure_ledger.py`

**Interfaces:**

- `swinglab entitlements-audit --db <path>` reports PII-free counts for active,
  confirming, expired, source overlap, unmatched legacy, stale verification,
  replay conflicts, and binding conflicts.
- Apple and Google lifecycle-readiness and new-purchase-admission flags are
  independent and appear in `/healthz` as configuration/feature state only.
  Existing state makes the corresponding lifecycle readiness irreversible until
  bounded retention proves no provider state remains; purchase admission remains
  reversible.
- `swinglab entitlements-reconcile --source apple|google|stripe --db <path>
  --dry-run|--apply --limit <n>` uses expiring leases/cursors and applies only
  freshly verified provider events. Scheduled mode is default-off and never runs
  in an analysis worker.
- Entitlement/provider workers register the Task 1 erasure extension and cannot
  start until recovery-chain reconciliation has severed every restored
  `account_delete` match. Provider events for a deleted owner may advance only
  the bounded unbound lifecycle record/tombstone; binding/access recreation is a
  trigger/service error, never a fallback.

- [ ] Add a table-driven test matrix for purchase, restore, renewal,
  cancellation, expiration, billing retry/grace, refund, revocation, replay,
  out-of-order delivery, provider 4xx/5xx, webhook outage, account deletion,
  and every two-source overlap. Assert access and management source after each.
- [ ] Add duplicate-pressure tests proving the atomic per-user purchase-intent
  admits only one simultaneous iOS/Android/device sheet, while active, confirming,
  or uncertain state blocks all new intents across providers. Reconcile and
  display an externally created duplicate honestly without revoking either
  provider; provider outage/uncertainty keeps admission closed while restore and
  lifecycle processing continue.
- [ ] Add scratch backup/restore verification with all entitlement tables and
  compare effective snapshots before/after restore at one timestamp.
- [ ] Add pre-delete snapshot → account delete → newest-chain restore tests for
  Apple and Google encrypted credentials, Stripe, Shopify, legacy, and overlapping
  grants. Hold each provider worker at a barrier during live deletion and restore
  startup; prove extension drain/severing completes before 204/worker start and
  no effective access or user binding returns.
- [ ] Implement the local audit with no provider network calls by default. Add
  the explicit bounded reconciliation command/worker using Apple Notification
  History plus App Store Server API transaction history/current subscription
  status from the decrypted Apple `anyTransactionId`, and decrypted Google tokens plus
  `subscriptionsv2.get`/acknowledgement retries, and Stripe Subscription reads.
  Decryption occurs only inside the source adapter; plaintext buffers are
  zeroized where the runtime permits. It persists cursors/leases, applies
  recovered verified events idempotently, and resumes after provider outage or
  process crash.
- [ ] Test missed Apple notifications from an active-account credential,
  paginated history/status, missed Google RTDN,
  unacknowledged Google purchase across restart, missed Stripe webhook, provider
  rate limit, expired lease, key rotation, ciphertext authentication failure,
  missing AEAD/HMAC key, backup/scratch restore, and dry-run zero writes. Apple
  and Google credentials delete 30 days after known terminal state for a live
  account; after deletion they delete at the earlier deadline of 30 days after
  known terminal state or 90 days after account deletion.
  Account deletion severs the user binding immediately; renewals during
  retention never recreate access.
  Re-encrypt retained rows and their stored associated-data HMAC pair atomically
  during key rotation, destroy an old key only after no live row or retained
  backup references it, delete ciphertext at the deadline even if auto-renewing,
  and preserve only the versioned HMAC deletion tombstone afterward. Prove
  plaintext/ciphertext never enters logs or privacy export and test deletion-
  retention, purge, tombstone-only late notification, and authenticated-
  decryption failure.
- [ ] Document flags-off deploy, schema/backfill/shadow parity, Shopify/Stripe
  dual-write, pre-native reversible v2 read cutover, Apple sandbox, Google
  internal test, and independent provider lifecycle/admission activation. After
  the first native-lifecycle watermark, rollback keeps v2 reads and provider
  ingestion/reconciliation, disables only new purchase/claim admission/UI, and
  uses only schema-compatible binaries. Include provider incident response and
  tests for sales-off renewal/refund/revoke/expiry continuity.
- [ ] Run `python -m pytest -q`; expect the full Python suite to pass, including
  entitlement, native billing, Apple, Google, Stripe, Shopify, backup, privacy,
  and account tests.
- [ ] Run the full Python and mobile gates from Plans 1-2. Expected: all pass,
  deterministic OpenAPI/client types, and native billing flags off by default.
- [ ] Commit: `git commit -m "test: gate native entitlement rollout"`.

## Entitlement plan completion gate

- [ ] Prove shadow parity and backup/scratch-restore invariants before v2 reads.
- [ ] Prove zero active Stripe quarantine and Shopify/legacy ambiguous grants
  before v2 reads; unresolved evidence blocks cutover rather than inventing it.
- [ ] Prove each provider refund/revoke changes only its own grant.
- [ ] Prove purchase/restore/notification retries create neither duplicate events
  nor duplicate access projections and never bind a purchase to two accounts.
- [ ] Prove localized store metadata is used and no digital web payment path is
  reachable in the native app.
- [ ] Record code/fixture verification only. Do not claim sandbox lifecycle,
  store-product configuration, provider activation, or production billing until
  the approval-gated real-provider gates in Plan 4 are executed.
