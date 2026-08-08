# Premium AI Golfer Revamp

**Status:** Cloud-agent implementation design (user requested full revamp; proceeding without interactive approval gates)

**Date:** 2026-08-08

## Summary

Elevate CaddieInsight’s storefront, app shell, Shopify hub bridge, and guided
report to one premium “AI tour caddie” instrument — photoreal membership art,
cleaner signed-in chrome, tighter hub sync surfacing, and a report that feels
like a private coaching session rather than a lab dump.

## Goals

1. **Storefront feel** — cool mist Turf Instrument, brand-first hero, no
   first-viewport clutter; membership cards look like real campaign photography.
2. **Legit logos / Pro / Founders art** — regenerate logo lockup + photoreal
   Pro and Founders Pass cards; ship in theme assets and `store-assets/out`.
3. **Shopify hub sync** — account page shows outbound sync state, store orders
   deep-link, and storefront session hydration stays the single signed-in bridge.
4. **Signed-in header / boxes** — member rail no longer breaks overlay pull-up;
   account triggers stay short; Pro chip / banner / plans boxes do not overlap
   or wrap into each other on mid and phone widths.
5. **Guided report 100x** — premium presentation (type, media, training), richer
   coaching language, video/training prominence, without inventing LLM claims or
   breaking the guided-report-v1 contract.

## Non-goals

- Replacing pose / metrics / coaching engines with generative AI
- Renaming the `swinglab` package or Shopify product handles
- Live Shopify Admin theme push from this agent (source + assets only)

## Approach

Single cohesive pass across theme + app + report + assets (not a parallel
rebrand). Keep shared CSS tokens; raise craft density where the user feels
“cheap” today (flat Founders card, system-ui report, crowded signed-in chrome).

## Component plan

| Area | Change |
| --- | --- |
| Assets | New Pro/Founders photoreal cards; refined logo + favicon; copy into theme |
| Hero | Brand-first; relocate live-signal panel out of first viewport |
| Header | Auth-aware overlay offset; short account summary; rail compact rules |
| App shell | Banner/chip density fix; hub links; Shopify sync card |
| Report | Turf Instrument styling; media theater; richer brief/insight copy |
| Tests | Update storefront/header/parity/report contracts |

## Success criteria

- Signed-in homepage: member rail visible, hero not clipped oddly, no overlapping
  header actions
- Plans show photoreal Pro + Founders art from theme assets
- Account shows sync status + store hub actions when Shopify is linked
- Guided report uses brand fonts/tokens and leads with priority + video evidence
  + one training block before optional depth
- Focused pytest suite for touched contracts passes
