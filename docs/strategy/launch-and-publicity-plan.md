# Launch and publicity plan — storefront + app

**Written 2026-08-09.** Audited against the live store through the Shopify
Admin API and a fetch of the live storefront, not against the repo. Where this
disagrees with `docs/quality/2026-08-09-launch-readiness-audit.md`, this
document is newer and the disagreement is called out.

Channel strategy is organic and founder-led with no paid spend until the money
path has passed a real test purchase — the shape
`docs/strategy/positioning-and-growth.md` §6 already argued for. Pricing copy
is written against the approved ladder in
`docs/superpowers/specs/2026-08-09-two-tier-membership-and-free-proof-cycle-design.md`
(Free / Pro $9.99 / Coach $19.99 / Founders $249).

---

## 1. The one thing blocking everything

**Every page of caddieinsight.com currently emits a Liquid error into
`<head>`.** Fetched live:

```
<meta property="og:image"    content="https:Liquid error (layout/theme line 59): invalid url input">
<meta name="twitter:image"   content="https:Liquid error (layout/theme line 65): invalid url input">
<link rel="icon"             href="Liquid error (layout/theme line 72): invalid url input">
<link rel="apple-touch-icon" href="Liquid error (layout/theme line 73): invalid url input">
"logo": Liquid error (layout/theme line 117): invalid url input
```

So: a default globe in the browser tab on 100% of pages, an image-less card
everywhere a link is pasted, and an Organization JSON-LD block that is invalid
JSON and therefore discarded by Google.

**Cause.** The live theme (`caddieinsight-theme-v4-logo`, MAIN) resolves the
three brand marks through `images['og-caddieinsight.png']` and friends. None of
those filenames exist in Shopify Files — confirmed by query. A Files lookup for
a missing name returns a **truthy** drop rather than nil, so the `{% if %}`
guard passes, `image_url` throws, and the `asset_url` fallback beside it is
unreachable dead code.

**Fix.** Already committed (`a694540`): unconditional `asset_url`, no Files
lookup, marks packaged with the theme. It has never been uploaded, because the
theme deploys by hand. `make theme-zip` builds the artifact;
`dist/UPLOAD.md` carries the procedure.

Do not publicize before this ships. The first thing anyone sees would be a
blank preview.

## 2. The second thing — fixed 2026-08-09

**Your supplier cost was printed on every gear product page.** All 17 `CI-*`
variants carried `compareAtPrice` byte-identical to `inventoryItem.unitCost`.
Shopify renders `compareAtPrice` as a struck-through "was" price, so the
Connection Ball read **"$12.99 — was $3.68"**: a struck price *lower* than the
selling price, announced by the seller. Also public as JSON at
`/products/*.js`.

Nulled on all 17 via the Admin API, and `taxable` set true at the same time
(they were `false` on tangible goods with TN nexus — a 2026-08-03 import
regression). Unit cost is untouched and stays private in `inventoryItem`.

## 3. Still open, and only you can do them

`docs/runbooks/pro-launch-checklist.md` is the authoritative list and is
accurate. The ones that gate publicity specifically:

| | Why it gates publicity |
|---|---|
| Upload the theme zip | §1 above. Nothing else matters until shared links preview. |
| `shopify app deploy` + **re-authorize the Bridge app** | `orders/paid` is the only thing that grants Pro. Until the scope is re-consented, a buyer is charged and receives nothing. |
| `SHOPIFY_STORE_DOMAIN` = `e0hbgh-ip.myshopify.com` | The custom domain is a valid-looking value that 400s every order. |
| Real $9.99 purchase, then refund | Nothing above is proven until this passes. |
| Publish Terms of Service | Confirmed absent: `shopPolicies` returns only CONTACT_INFORMATION, PRIVACY_POLICY, REFUND_POLICY, SUBSCRIPTION_POLICY — while two auto-renewing plans and a perpetual pass are on sale and the live privacy policy cites a Terms that 404s. |
| Store contact email → `inquiry@caddieinsight.com` | Confirmed still `kylejames0513@icloud.com`. Shopify renders it into the live privacy policy; no policy edit reaches it. |
| International market | Markets `International` and `United States` are both ACTIVE, but delivery zones cover only `Domestic` (US) and `Asia` (21 countries). A Canadian, UK, EU or Australian visitor reaches checkout and cannot complete it. Deactivate the market or add the zones. |
| Founders Pass cap | Variant `46839745282220` has qty 100 and policy DENY but `tracked: false`, so both are inert. ⚠️ Do **not** tick tracking on `SL-PRO-1MO` (qty 0) or `SL-PRO-12MO` (qty −1) — both are CONTINUE and become unbuyable instantly. |

`RANGE15` in the announcement bar **is** active and valid — no action.

## 4. Credibility, before reach

`docs/strategy/positioning-and-growth.md` §4 is a golf-specific scam-pattern
checklist. The store trips several, and no amount of traffic survives them.

Still missing, confirmed against the live page list (contact,
data-sharing-opt-out, the-swinglab-method, how-swinglab-works, about, faq,
shipping-returns):

- **A founder page.** Named human, first person, 300–600 words, your own swing
  journey through the app, and a real phone-shot photo of you at the range.
  Founder stories convert 18–27% better on cold traffic. Being small is not the
  scam signal; pretending to be big is. `docs/runbooks/store-manual-actions.md`
  §6 has the opening paragraph.
- **An Accuracy & Limits page.** Paste-ready in `store-manual-actions.md` §7 and
  genuinely excellent. *No competitor publishes what phone video cannot
  measure.* This is the most differentiated content you can put on the internet
  and it costs one page.
- **Reviews.** Judge.me free tier. Never seed. Show the negatives — 4.2–4.7
  converts better than 5.0, and 53% of buyers read the bad ones first.
- **One** linked social profile. One active beats four dead.

Keep doing nothing about: countdown timers, "X people viewing", seeded reviews,
bought followers. The brand is built on the opposite and golf communities
detect it.

## 5. The asset kit — build once, use ninety days

Shot on your phone at a real range. The authenticity is the asset.

| Asset | Use |
|---|---|
| 8–10 stills of you at the range, phone on a bucket | founder page, About, social |
| 60–90s founder video: why you built it | founder page, Reddit, YouTube |
| **Your own completed before/after proof cycle** | the most important asset you own |
| 3–4 drill demo clips | product pages, reports, social |
| Screenshot: report with centreline overlay on **real** footage | Pro gallery |
| Screenshot: annotated coach replay from a real `replay_sN.mp4` | Pro gallery |
| Screenshot: progress dashboard across 3+ real sessions | Pro gallery |

Two traps. **Do not harvest from `/sample-report/`** — those `strip_s*.png` are
procedurally generated stick figures (`swinglab/sample.py:235`), cruder than
what is already on the page. And the Pro product gallery currently shows four
flat vector plates on the page that takes $249, while three genuinely premium
photoreal images sit on the homepage. Fixing that inversion is probably worth
more than the first month of every channel below combined.

## 6. Channels

**Reddit** — r/golf, r/golf_instruction, r/GolfSwing. Highest yield, highest
risk. 90/10: nine genuinely useful comments for every one that mentions the
product, and always disclose "I built this."

- Weeks 1–3: comment only. No links. Build recognisability.
- Week 4: the post that works is transparency, not promotion — *"I analyzed 50
  swing videos; here's the most common fault and the numbers behind it."*
  Include the real distribution, name the published thresholds (head sway
  beyond 0.35 shoulder widths, tempo under 2.4:1), and state plainly what phone
  video **cannot** measure. That last part is why the post survives.
- Ongoing: answer "what's wrong with my swing" posts with a real analysis and a
  screenshot. One good analysis reply out-reaches a month of posting.

**X** — reply-first. Follow 30–40 golf instruction accounts; reply with
screenshot-backed analysis that adds a measurement they did not make. 5
replies/day, 2 original posts/week, both proof cards.

**YouTube micro-creators (1k–50k subs)** — the Takomo model: free
Coach-for-life plus one training aid, **no script and no approval** over what
they say. 10 personalised emails, expect 2–3 to use it. A 5k-sub channel
converts better than a 500k one because the trust is real.

**Email** — `sections/email-capture.liquid` already exists. (1) welcome +
Accuracy & Limits, leading with the limits because it disarms; (2) day 3
founder story; (3) day 7 your own before/after; (4) day 14 a verified case
study.

**SEO** — Accuracy & Limits is the anchor. Then one piece a fortnight on
things people actually search: *"how to film your golf swing with a phone"*,
*"what is good golf swing tempo"*, *"3:1 tempo ratio explained"*, *"why does my
head sway in the backswing"*. Each ends with a real report screenshot.

**In person** — 15–20 real golfers through a full proof cycle in the first 30
days. The only channel here guaranteed to work, and the source of your first
honest reviews.

**GolfWRX** — no vendor posts. Editorial spotlight is the long-term route.

## 7. Ninety days

| Window | Focus |
|---|---|
| **Days 1–7** | Theme zip published, zero Liquid errors. Money path proven by a real purchase and refund. Founder + Accuracy pages live. Judge.me installed. Asset kit shot. |
| **Days 8–21** | Reddit/X presence built by contributing only. Recruit 15–20 golfers into free Coach. Your own proof cycle published. |
| **Days 22–45** | First transparency post. YouTube seeding emails out. Side-by-side matched re-film comparison UI. Drill demos. |
| **Days 46–60** | Ask each beta golfer for an honest review **at their pass-mark moment**; label free-access reviews. Publish 3–5 numeric before/after case studies. |
| **Days 61–90** | Content engine at cadence. Shareable verdict cards. Re-evaluate paid against the gate below. |

## 8. The gate for paid spend

All of: ≥10 verified proof cycles from real users, ≥8 unseeded reviews, ≥3
published numeric case studies, and a measured free→paid rate from the ledger.
Separately and independently, `docs/first-sale-launch.md` forbids paid traffic
to any gear SKU without a supplier record, a sample order and a measured
delivery SLA.

## 9. Measurement

`/admin/kpis` — once `SWINGLAB_ADMIN_TOKEN` is set — reads the PII-minimised
ledger: `landing_view`, `account_verified`, `upload_started`,
`upload_completed`, `brief_viewed`, `pro_clicked`, `cart_started`,
`checkout_started`, `paid_order`, `repeat_analysis`.

The one number that matters is **`brief_viewed` → `pro_clicked`**. Everything
in §4 exists to move it. The moment to instrument specifically is the verdict
that reads *"change held — one more confirmation to count"* — the emotional
peak of the product and the natural paywall trigger.

---

## Appendix — audit items that were already fixed

Roughly half of `docs/quality/2026-08-09-launch-readiness-audit.md` §4 was
resolved by PR #98 on the same day it was written, so a plan built from the
audit alone re-does finished work. Verified fixed:

| Audit item | Actual state |
|---|---|
| `/pricing` cannot sell to anonymous visitors | Fixed — `web_pricing.html.j2` has the `{% elif pro_store_url %}` branch. |
| No og/twitter/canonical in the app | Fixed — `web_layout.html.j2` has a `{% block meta %}`. |
| App header renders brand as text | Fixed — `brand.logo_url` set; logos in `swinglab/web/static/`. |
| Report paywall has no CTA, renders "· 0" | Fixed — `/pricing` links present; `item_count` suppressed when locked. |
| Sample-report cache serves a stale stylesheet | Fixed — `_report_is_current` is content-addressed. |
| `/docs`, `/redoc`, `/openapi.json` public | Fixed — gated behind `docs_enabled()`; Railway injects `PORT`, so off in production. |
| Zero security headers | Fixed — `SECURITY_HEADERS` + HSTS. |
| `/shop` ships zero products | Fixed — `first_sale_catalog_only: false`, plus a loud warning when the filter empties the catalogue. |
| Plans band serves 6.1 MB of PNG at `sizes="148px"` | Fixed — the webp ladder ships; no PNG is served. |
| v3 brand files in Shopify Files | Already deleted. |

And two the audit called defects that are deliberate:

- **`--sl-ink-muted` differs between surfaces** (`#5a655e` app, `#626a63`
  theme). Not drift — the app sets small interface text, the storefront sets
  prose, and both clear AA on both backgrounds (5.37:1 and 4.94:1 against
  `--sl-bg`). `tests/test_app_storefront_parity.py` documents it; unifying them
  would trade contrast for symmetry. Both ends are now pinned.
- **`brand.accent_color: #e8720c`** is not a third stray orange. It is the
  brand amber — the mark's own colour, used in translucent washes on *both*
  surfaces and in every generated report artifact. `#9a4b0a` and `#f07a18` are
  its accessible-text and dark-surface derivatives (both `#e8720c` and
  `#f07a18` fail AA as text on light, which is exactly why the derivatives
  exist). Changing it would desynchronise the artwork from the mark and break
  four test suites.

## Appendix — known defect, deliberately not fixed here

**Shopify auto-renew subscribers will receive a lapse warning every cycle.**
`users.pro_expiring_between` (`swinglab/web/users.py:7994`) excludes only
Stripe-managed subscriptions:

```sql
AND NOT (plan = ? AND subscription_status IN (?, ?, ?))
```

Nothing in `shopify_billing.py` reads `selling_plan_allocation`, so a Shopify
subscription order is indistinguishable from a one-time purchase: it lands as a
31- or 365-day grant. Between renewals that grant enters the reminder window
and `digest.py:551` sends *"CaddieInsight Pro ends in N days — extend it on the
store"* to somebody whose card is about to be charged automatically. That
contradicts the auto-renewal terms being shipped.

**Why it is not fixed in this pass.** The fix needs a new column recording that
an order was subscription-backed, threaded through `record_order`, the claim
path and `pro_expiring_between` — a schema migration on
`/data/sessions/swinglab.db`, which is the file holding every entitlement and
which still has **no backup configured** (checklist §1.5). Migrating it in a
hurry, untested against production data, is a worse risk than the bug.

**It also cannot fire yet:** no Shopify subscription has ever sold, so there is
no affected customer today. Fix it after backups are proven and before the
first auto-renewal cycle completes — roughly 31 days after the first monthly
subscription sale.
