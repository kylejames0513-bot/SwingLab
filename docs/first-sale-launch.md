# First-sale launch runbook

## Product loop

CaddieInsight’s customer promise is intentionally narrow:

> **Film → Caddie Brief → one drill → re-film target → visible progress → optional matched gear**

The mobile/PWA experience now provides:

- a short golfer setup: Start, Improve, or Compete explanation mode; optional
  handicap range; goal; 10/20/45-minute practice preference; handedness;
  camera angle; club; reduced motion; and a separate unchecked marketing
  opt-in;
- a `/today` screen that answers what to do next before exposing a full metric
  wall;
- one evidence-backed Caddie Brief, a pass mark, and three time-boxed versions
  of the same prescribed drill;
- a re-film checkpoint and private practice check-in;
- stable `/api/v1` resources for identity, golfer profile, Today, sessions,
  Caddie Briefs, and practice check-ins; and
- an installable PWA shell. It caches only public drill/offline content, not
  reports, accounts, videos, or uploads. Offline video upload is not offered.

Existing upload/session/report routes remain unchanged for compatibility.
The annual Pro offer remains first in pricing; gear stays optional and appears
only after a relevant measured result. The app must never say that a product
fixes a swing issue—only that it can support the prescribed drill.

## First-party funnel measurement

The local, PII-minimized event ledger supports:

`landing_view`, `account_verified`, `upload_started`, `upload_completed`,
`brief_viewed`, `pro_clicked`, `gear_match_clicked`, `cart_started`,
`checkout_started`, `paid_order`, `fulfillment_updated`, and
`repeat_analysis`.

It stores no IP address, email, request body, video label, arbitrary client
properties, or raw Shopify order ID. Signed same-store `orders/paid` webhooks
record `paid_order`, while `fulfillments/create` and `fulfillments/update`
record `fulfillment_updated` only if the paid-order ledger proves one linked
app identity. They are telemetry only: the app never changes Shopify order,
inventory, or fulfillment state. Cart/checkout events still require a
same-origin storefront integration. The operator endpoint is protected by
`SWINGLAB_ADMIN_TOKEN`; it is a measurement surface, not a checkout or
fulfillment system.

`fulfillments/create` and `fulfillments/update` need the current Shopify
fulfillment-read scope before an operator subscribes them. Do not add that
scope or webhook subscription until the bridge and supplier release gates are
approved; the code remains inert without the incoming signed delivery.

## Catalog gate: no paid traffic before proof

Only consider three US candidate practice aids at launch:

1. Clip-on swing metronome.
2. Anti-sway hip resistance band.
3. Alignment-stick set.

Do not run paid acquisition to the mirror, wand, cap, or any other product
until real fulfillment evidence says it belongs. Shopify Collective may be a
good supplier path if the store qualifies, but eligibility and product
availability must be verified at the time of activation.

Before any SKU appears in a paid campaign, retain all of the following in an
operator-controlled supplier record:

- supplier agreement and permitted marketing claims;
- landed cost, target margin, and US sample order;
- actual inventory behavior and an order-routing test;
- branded tracking, return address, return acceptance, and packaging proof;
- measured delivery SLA; and
- accurate storefront shipping/refund copy with a delay/cancel/refund path.

Do not promise shipping dates that the supplier has not demonstrated. A first
sale counts only when a real paid order reaches the supplier, produces valid
tracking, ships inside the stated promise, and appears in the customer’s
linked CaddieInsight/Shopify record.

## Release gates

Before paid traffic:

- run keyboard/mobile-device/reduced-motion checks;
- prove email delivery;
- verify policies and checkout trust copy;
- order the three supplier samples;
- run a controlled checkout-to-fulfillment test; and
- verify conversion telemetry end to end.

For every change, use a feature branch and focused PR; run CI, security,
container, health, representative upload/report, and Shopify storefront
checks. Preserve the single Railway replica, SQLite database, local session
artifacts, root Dockerfile, injected `PORT`, and `/data/sessions/swinglab.db`
contract until durable jobs/artifacts are externally coordinated.

This runbook creates no supplier contract, inventory, Shopify product edit,
advertising campaign, production deployment, or external configuration change.
