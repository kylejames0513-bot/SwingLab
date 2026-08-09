# Two-tier membership and a completable free proof cycle

**Date:** 2026-08-09
**Status:** Design, approved in session (pricing structure, free-tier shape, PWA-as-beta)
**Supersedes the pricing decision recorded in** `docs/strategy/positioning-and-growth.md` §2 (2026-08-03)

## Why this changes now

Zero Pro units have ever sold. The store's entire order history is one $0.00
draft-order grant with an empty `transactions` array, so there is no customer to
grandfather and no public promise that has been kept or broken. Pricing is
cheaper to change today than it will ever be again.

Two facts drove the change:

1. **$9.99/mo is byte-identical to V1 Golf Plus**, a fifteen-year-old
   video-annotation tool that explicitly does not diagnose or prescribe. Pricing
   at the floor of the category communicates that we belong at the floor of the
   category.
2. **A free user cannot currently complete the proof cycle.** `billing.free_per_month: 1`
   means one analysis per calendar month, so Film → Practice → Re-film → Prove —
   the entire differentiator — is unreachable without paying. We are asking people
   to buy a method they have never experienced.

## The ladder

The insight that makes the split honest: **the lower tier is what competitors
sell, and the upper tier is what nobody else does.** That is not a marketing
frame layered on afterwards — it falls out of the two gate flags the code
already has.

| | Free | **Pro** $9.99/mo | **Coach** $19.99/mo |
|---|---|---|---|
| Full guided report, no fidelity cut | ✓ | ✓ | ✓ |
| Analyses | 3 in first 14 days, then 1/month | unlimited | unlimited |
| Slow motion | ✓ | ✓ | ✓ |
| Matched re-film | 1/month | ✓ | ✓ |
| Gear recommendations | ✓ | ✓ | ✓ |
| **Annotated coach replay** | — | — | ✓ |
| **Progress dashboard** | — | — | ✓ |
| **Proof cycle verification** | — | — | ✓ |
| **Verified Changes counter** | — | — | ✓ |

Positioning line: *"$9.99 gets you what other swing apps sell. $19.99 gets you
proof that the fix held."*

### Why this exact split

`config.yaml` already carries exactly two independent Pro gates —
`billing.replay_pro_only` and `billing.progress_pro_only` — and they already
partition cleanly along tool-versus-method lines. The annotated replay and the
progress/proof surfaces are the method; unlimited analysis is the tool. The
split therefore needs a new entitlement *level*, not new gate machinery.

Anything that would degrade the free report's fidelity is out of scope. "One
real analysis beats five scores" is load-bearing for trust, and a crippled free
report contradicts the honesty posture the whole brand rests on.

## Naming and SKUs

`Pro` is retained for the $9.99 tier. Renaming it would churn
`billing.shopify_pro_handle`, the `/products/swinglab-pro` handle, every SKU,
and a large body of tests and copy for no customer benefit. `Coach` is added
above it.

| Tier | SKU | Variant | Price | Selling plan |
|---|---|---|---|---|
| Pro monthly | `SL-PRO-1MO` | 46811170177196 (existing) | $9.99 | MONTH/1 (existing) |
| Pro yearly | `SL-PRO-12MO` | 46811170209964 (existing) | $69.99 | YEAR/1 (existing) |
| Coach monthly | `SL-COACH-1MO` | new | $19.99 | MONTH/1 (new) |
| Coach yearly | `SL-COACH-12MO` | new | $139.99 | YEAR/1 (new) |
| Founders Pass | `SL-PRO-LIFE` | 46839745282220 (existing) | **$249** | none |

**Founders Pass becomes Coach-for-life and rises from $149 to $249.** At $149
against a $19.99/mo Coach tier it is 7.5 months of revenue against perpetual
compute — the tier most likely to attract the heaviest users is the one that
stops paying first. $249 is 12.5 months, which is defensible to publish. The
100-member cap stays and becomes **actually enforced** (see below).

> This is the one number in this design that was publicly displayed before
> today. Nothing has sold, so no commitment is broken — but it is the change
> most worth vetoing if the $149 figure matters more than the margin.

`config.yaml billing.shopify_skus` gains the two Coach SKUs. The map's existing
shape (SKU → days) is retained; tier is resolved from the SKU prefix so a
replayed or mixed order cannot silently upgrade someone.

## Entitlement model

Today entitlement is a single expiry timestamp (`users.pro_until`). It becomes a
level plus an expiry.

- Add `users.tier` — `'free' | 'pro' | 'coach'` — defaulting to `'free'`.
- `pro_until` keeps its meaning and its column; it now governs whichever tier is
  recorded. One expiry, one level, no per-feature expiries.
- Grants take the **maximum** of the incoming and current tier, and **extend**
  the expiry. A Pro subscriber who buys Coach is upgraded for the remaining
  term; a Coach subscriber who buys Pro is not downgraded.
- On expiry the tier falls to `'free'`. No intermediate decay.

`is_pro` is retained as `tier in ('pro','coach')` so no existing call site
changes meaning. A new `has_coach` predicate gates the replay and progress
surfaces. This keeps the blast radius to the two gate checks and the grant path.

**The parked-grant defect is fixed as part of this work, not after it.**
`users.py:7215-7235` currently refuses to grant to an email-matched account when
the payload carries `customer.id`, parking days in `pro_grants`;
`claim_pending_grant` only runs at signup/login. An opportunistic claim is added
to `current_user` and to `apply_customer` so a purchase lands without the buyer
logging out and back in. `tests/test_shopify_billing.py` fixtures gain a
customer object, because every existing fixture omits one and that is precisely
why the suite is green on a broken path.

## Free tier: burst then throttle

**3 analyses within 14 days of account creation, then 1 per calendar month.**

The 14-day window starts at signup, not at first upload, so the clock is legible
and cannot be gamed by registering early and waiting. Three is the minimum that
completes the cycle with one spare: baseline → re-film → confirmation. The
existing `allowances.free_matched_refilm` credit is retained on top and unchanged.

New config under `allowances`:

```yaml
free_onboarding_analyses: 3      # total during the window (0 = disabled)
free_onboarding_window_days: 14  # measured from account creation
```

The bare-code default is `0` — white-label installs stay on the old behavior
until they opt in, the same deliberate difference already used by
`replay_pro_only` and `free_matched_refilm`.

The upgrade prompt fires at the verdict that says *"change held — one more
confirmation to count."* That is the emotional peak of the product and the only
moment a golfer has felt the differentiator rather than read about it. It is the
natural paywall trigger and it should be instrumented.

## Surfaces that change

**`/pricing` (app).** Three cards become four states (Free, Pro, Coach,
Founders). Coach is the featured card. The anonymous-visitor defect is fixed in
the same pass: all three CTAs currently render `<a href="/login">Log in to
upgrade</a>` because every store-link branch is gated on `user`
(`web_pricing.html.j2:252-256`), so cold traffic converts at zero. An
`{% elif pro_store_url %}` branch emits the `?variant=` deep link already
computed at `app.py:3348-3354`. `claim_pending_pro` already exists to attach a
purchase made before an account did.

**Storefront plans band and comparison table.** `plans-band.liquid` and
`comparison.liquid` gain the Coach column. The comparison table is the clearest
statement of the ladder and should lead with the three Coach-only rows.

**The report paywall.** A free user's locked state currently renders
`<summary>Coach replay · 0</summary>` and six sections reading "This section is
locked." with no price, no link, and no CTA anywhere in the file. That reads as
a bug, not an offer. The working upsell already exists at
`report.html.j2:1178-1189` and is ported to the guided template. With two paid
tiers the locked copy must name **Coach** specifically, not "Pro".

**The public sample report.** 32,558 bytes containing zero occurrences of
"pricing", "upgrade" or "unlock". It is the demo that is supposed to sell the
membership and it offers no route to buy one.

## Founders Pass cap

The storefront promises "first 100 members" three times and frames it as a
solvency ethic. Variant 46839745282220 has `inventoryQuantity: 100` and
`inventoryPolicy: DENY`, but `inventoryItem.tracked: false` makes both inert —
nothing stops sale 101, or 5000, each at $249 against perpetual compute.

Enforcement is one checkbox in Shopify admin (Inventory → Track quantity). The
theme already renders the disabled option, the "Sold out" label and schema.org
`OutOfStock` off `variant.available`, so no theme deploy is needed.

**Do not enable tracking on `SL-PRO-1MO` (qty 0) or `SL-PRO-12MO` (qty -1)** —
both are `CONTINUE` and would immediately become unbuyable. This is the single
most dangerous adjacent click in the whole launch.

## Testing

- Grant path: a paid order **carrying a customer object** grants without
  re-login, for each of the five SKUs. This is the test that does not exist today.
- Tier maximum: Pro→Coach upgrades, Coach→Pro does not downgrade, both extend.
- Expiry falls to free; `has_coach` false, `is_pro` false.
- Free allowance: 3 within the window, 4th refused; after day 14, 1/month;
  matched re-film unaffected.
- Anonymous `/pricing` emits store links with the correct `?variant=` per card.
- Locked report renders a price and a `/pricing` link, and names Coach.

Per `CLAUDE.md`: break each new guard, watch the specific test go red, restore.
`tests/test_guided_report_html.py:679` currently asserts the old locked string
and will need updating with the paywall port.

## Sequencing

The tier model gates the pricing surfaces, so it lands before any copy or theme
work on `/pricing`, `plans-band.liquid` or `comparison.liquid`. Everything in
the premium pass that does not mention a price — the site-wide Liquid error, the
app logo, the sample-report stylesheet, meta tags, the Evidence Theater gate,
the traceback leak — is independent and proceeds in parallel.

Shopify variant creation, the app scope re-consent, Railway variables and the
live test purchase are owner actions and are tracked separately in
`docs/quality/2026-08-09-launch-readiness-audit.md` §3.

## Out of scope

- Renaming the `swinglab` package or the `/products/swinglab-pro` handle.
- Any LLM between measurement and recommendation.
- Per-feature expiries, seat sharing, or coach/team accounts.
- Degrading the free report's fidelity.
