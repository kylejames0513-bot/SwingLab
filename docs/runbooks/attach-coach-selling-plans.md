# Attach the auto-renew selling plans to the Coach variants

**The one remaining step of the 2026-08-10 two-tier rollout**
(`pro-launch-checklist.md` §3). Until it is done, the two Coach variants sell
as one-time terms — the buy box renders that honestly (no plan radios on a
variant without plans), so nothing is broken, just not auto-renewing.

## Why this needs a specific actor

Shopify scopes selling-plan groups to **the app that created them**. The
MONTH/1 and YEAR/1 plans on the Pro variants (`SellingPlan/3547398316`,
`SellingPlan/3547431084`) are owned by whichever app created them — not by
whatever tool happens to be asking. An API client that is not the owner gets
zero groups back from discovery and an ownership error on the mutation. This
is a platform rule, not a tool limitation: the Claude session that created
the Coach variants could not attach the plans for exactly this reason.

**Never work around it by creating new groups from a different app.** A plan
only charges renewals if its owning app runs the billing cycles; a
lookalike group from an app with no billing engine produces a subscription
that checks out and silently never renews. Split ownership across two apps is
strictly worse than the admin UI.

## Path A — the admin UI (always works, ~1 minute)

Shopify admin → Apps → **Shopify Subscriptions** (or whichever app shows the
MONTH/1 and YEAR/1 plans) → edit each plan → add products/variants:

- MONTH/1 plan ← **Coach — 1 Month** (`SL-COACH-1MO`)
- YEAR/1 plan ← **Coach — 12 Months** (`SL-COACH-12MO`)

## Path B — a guarded script for an API-capable tool

Only useful if the tool's app **owns** the groups (e.g. the tool that
originally created them). Paste verbatim:

```
TASK: Attach the existing auto-renew selling plans to the two new Coach
variants on the CaddieInsight Pro product. Modify NOTHING else.

Store: e0hbgh-ip.myshopify.com (caddieinsight.com)
Product: gid://shopify/Product/8672414105772  (handle: swinglab-pro)

ADD these two variants (created 2026-08-10):
  gid://shopify/ProductVariant/46906759741612   SL-COACH-1MO   ($19.99, monthly terms)
  gid://shopify/ProductVariant/46906759774380   SL-COACH-12MO  ($139.99, yearly terms)

Reference variants that ALREADY carry the plans:
  gid://shopify/ProductVariant/46811170177196   SL-PRO-1MO   → MONTH/1 (SellingPlan/3547398316)
  gid://shopify/ProductVariant/46811170209964   SL-PRO-12MO  → YEAR/1  (SellingPlan/3547431084)

STEP 1 — Discover the groups and confirm ownership (read-only):

query {
  monthly: productVariant(id: "gid://shopify/ProductVariant/46811170177196") {
    sellingPlanGroups(first: 5) { nodes { id name appId sellingPlans(first: 5) { nodes { id name } } } }
  }
  yearly: productVariant(id: "gid://shopify/ProductVariant/46811170209964") {
    sellingPlanGroups(first: 5) { nodes { id name appId sellingPlans(first: 5) { nodes { id name } } } }
  }
}

If this returns ZERO groups, your app is not the owner. STOP and say so —
use Path A instead. Do not create new groups.

STEP 2 — Attach, using the group IDs from Step 1:

mutation Attach($groupId: ID!, $variantIds: [ID!]!) {
  sellingPlanGroupAddProductVariants(id: $groupId, productVariantIds: $variantIds) {
    sellingPlanGroup { id name }
    userErrors { field message }
  }
}

- ONE group containing both plans → run once with both Coach variant ids.
- TWO groups → run twice: MONTH/1 group gets ONLY 46906759741612,
  YEAR/1 group gets ONLY 46906759774380 (mirroring the Pro split).

STEP 3 — Verify (read-only): re-run Step 1 against the two COACH variant
ids and confirm each lists its group. Report the result.

GUARDRAILS — hard rules:
- Do NOT create new selling plan groups.
- Do NOT edit any plan's pricing policy, billing policy, or name.
- Do NOT touch inventory tracking on ANY variant.
- Do NOT modify any other product or variant.
- On any ownership error ("owned by another app"), STOP and report.
```

## After either path

- Confirm the Coach variants show the auto-renew option in the storefront
  buy box (the theme renders plan radios per variant automatically).
- Confirm the renewal-reminder question in
  `docs/strategy/launch-and-publicity-plan.md`'s appendix (the auto-renew
  lapse-warning defect) is scheduled before the first Coach renewal
  completes.
