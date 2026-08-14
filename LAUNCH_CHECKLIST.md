# Launch checklist

Where the revamp stands, what is left, how to go live, and how to get back if
it goes wrong.

**Preview:** https://caddieinsight.com/pages/founders?_ab=0&_fd=0&_sc=1&preview_theme_id=154863632556
**Theme:** `caddieinsight-revamp-20260812` (#154863632556) — unpublished
**Branch:** `site-revamp`

The `_ab=0&_fd=0&_sc=1` params matter. Without them Shopify's domain redirect
drops `preview_theme_id` and you are quietly looking at the live theme — which
is exactly how I first "verified" a page that had not rendered at all.

---

## 1. Done

| | |
| --- | --- |
| Design source imported | `design-source/`, 22 files, offline-safe |
| Mockups inventoried | `MOCKUP_INVENTORY.md` — 17 screens, 7 turns |
| Both surfaces audited | `SITE_AUDIT.md` — repo and live theme in sync |
| Tokens pinned | `DESIGN_TOKENS.md` |
| Site map and copy | `COPY_DECK.md` |
| Brand assets | Already Mark B; verified byte-identical on regeneration |
| Favicon set | 16/32/64/512 now ship to both surfaces; apple-touch fixed |
| Founders Pass page | Built, inventory-driven count, verified in browser |
| Product specification | Metafield-driven, all six products populated |
| Contact page | Subject router, conditional order field, support aside |
| Policy shell | `page.policy` template, applied to the two theme-owned pages |
| Sticky mobile buy bar | Submits the real form; no duplicated state |
| Redirects | 8 created — 2 dormant, 6 fixing live 404s |
| Handle-rename groundwork | Theme accepts old and new page handles |

Home (`2a`) was already built before this revamp began — every string from the
mockup was already in the theme.

---

## 2. Left to build

Roughly in value order.

1. **Collection / cart / search / 404** — templates exist and are styled;
   they need the mockups' copy from `COPY_DECK.md` §8.
2. **Nav and menus** — the live `swinglab-main` menu is `Home · Gear · The
   Method · Pro Membership · FAQ`; the mockups want `Method · Sample report ·
   Plans · Gear`. Add the Founders Pass link.
3. **`/collections/swinglab-gear` rename** — see §6.
4. **App (Phase 5)** — the full `1a`–`1f` build you asked for.
5. **SEO and a11y pass (Phase 6)** — per-page titles and descriptions,
   Lighthouse, contrast, keyboard nav, broken-link crawl.

---

## 3. Go-live

Do these in order. Nothing here is reversible by itself except the theme
publish, which is why it goes last.

**Before**

- [ ] Read the refund policy in `COPY_DECK.md` §7. It is real legal copy with a
      `LAST UPDATED 04 AUG 2026` stamp, not placeholder — 14-day membership
      refunds, 30-day gear returns, a named non-refundable list.
- [ ] Confirm prices: Pro $9.99/mo · Coach $19.99/mo · $69.99/yr · Founders
      $249 · `RANGE15` 15% off gear.
- [ ] Confirm `inquiry@caddieinsight.com` is monitored.
- [ ] **Write a Terms of Service.** The store has privacy, refund,
      cancellation and contact-information policies but no terms — so the
      policy index renders five entries where the mockup shows six. The
      shell picks it up automatically the moment it exists.
- [ ] `python -m pytest -q` green.
- [ ] `shopify theme check --path storefront-theme --fail-level warning` clean.

**Storefront**

- [ ] **Rename the collection `swinglab-gear` → `gear`.** The code side is
      done and every lookup resolves either handle, but nothing else in this
      list creates the new handle — and `swinglab/drills.py` may only move to
      it once it exists. Shopify writes the old→new 301 automatically.
- [ ] After that rename: set `GEAR_COLLECTION_PATH` in `swinglab/drills.py`
      to `/collections/gear`, update `tests/test_drills.py` to match, and
      retarget the six archived-product redirects, which still point at
      `/collections/swinglab-gear`.
- [ ] Point `header-group.json`'s announcement link at
      `shopify://collections/gear`.
- [ ] Rename pages: `the-swinglab-method` → `method`,
      `how-swinglab-works` → `how-it-works`. Redirects already exist and
      activate on rename. The theme handles either handle, so order does not
      matter.
- [ ] Update the `swinglab-main` and `swinglab-footer` menus.
- [ ] `shopify theme push --path storefront-theme --store e0hbgh-ip.myshopify.com --theme 154863632556`
- [ ] Review the preview at 375 / 768 / 1440.
- [ ] **Publish the theme.** Yours to do, not mine.

**Memberships**

- [x] **`templateSuffix` on CaddieInsight Pro set to `membership`** (done
      2026-08-12). It was null, so the product rendered through
      `main-product.liquid`, which has ZERO selling-plan handling — all four
      subscription variants had plans attached and no shopper could reach them.
      Every membership sale was a **one-time charge** on a product advertised
      at $9.99/mo. The live page now offers "One-time purchase" and
      "Auto-renew monthly".
- [x] Subscription is now the DEFAULT selected option rather than one-time.
      **This half only reaches customers when the theme is published** — the
      live theme still opens on "One-time purchase". Until then the options
      exist but the default is the cheaper one.
- [ ] Watch the first subscription order end to end. `shopify_billing.py`
      grants on SKU, and a recurring charge arrives as a NEW order against the
      same SKU, so renewals should extend the term automatically — but nothing
      here has ever processed one.

**The Founders Pass**

Nothing to activate — it is variant `SL-PRO-LIFE` of CaddieInsight Pro, already
live at $249 with inventory tracked at 100 and policy DENY, so the cap is
already enforced by Shopify.

- [ ] Buy one yourself and refund it. The count should drop to 99 and come
      back to 100 — this tests the counter, the cap and the refund path in one
      go.

**App**

- [ ] Merge `site-revamp` after CI is green. Railway deploys from `main`
      automatically and **does not wait for checks** (`checkSuites: false`), so
      a merge is a deploy whatever the tests say.

---

## 4. After

- [ ] `/pages/the-swinglab-method` and `/pages/how-swinglab-works` 301 to the
      new handles.
- [ ] The six archived-product URLs land on the gear collection.
- [ ] Favicon: check a 16px tab. The grooves should be gone and the silhouette
      should still read as a club.
- [ ] iOS: add to home screen. The tile must be opaque with no white or black
      box behind the mark.
- [ ] `/pages/founders` counter matches the product's real inventory.
- [ ] Sample report link from the storefront still resolves.
- [ ] Re-run `python scripts/refresh_store_readiness.py` — the fixture in
      `tests/fixtures/store_readiness.json` still records the old page handles.
- [ ] Re-crawl for broken links.

---

## 5. Rollback

**Storefront** — republish the previous theme. It is
`caddieinsight-industry-20260812` (#154836009132) and it is untouched: this
work went to a new unpublished theme throughout, and the repo is byte-identical
to it apart from one empty `blocks: {}` Shopify normalises away. Rollback is
one click and loses nothing.

**Redirects** — harmless if the rename is rolled back. A redirect whose target
does not exist 404s exactly as the un-renamed URL would have.

**Founders Pass** — set the product back to draft. The page then hides its own
buy button.

**App** — revert the merge commit; Railway redeploys from `main`.

**The one thing that is not cleanly reversible** is a Founders Pass sale. It is
a lifetime commitment at a capped price, which is why the product is a draft
and why activating it is a deliberate, separate step.

---

## 6. Open decisions

**The `swinglab-gear` collection rename is code-complete, store-pending.**
Every lookup resolves the new handle first and falls back to the old, and
every constructed URL goes through `snippets/gear-url.liquid`, which resolves
against what the store actually has. So the theme is correct on both sides of
the rename. What is NOT done is the rename itself, and `drills.py` still
names the old handle deliberately — a report bakes its URL in permanently, so
it may only ever name an address the store answers at render time.

**The docs now agree with the code** (reconciled 2026-08-12). The cutover
runbook was rewritten around the real targets — it had prescribed
`caddieinsight-gear`, which satisfies neither fallback arm — and the README,
deploy/README and gear-coverage runbook now describe the two-handle window
instead of naming the old handle as if permanent. The runbook carries the
step-by-step; this checklist stays the top-level sequence.

**`swinglab-pro` keeps its handle.** `shopify.app.toml` is explicit that the
`orders/paid` webhook is the only thing that grants Pro, and `config.yaml`'s
`first_sale_catalog_only` allowlist matches on product handles. Renaming it
risks the money path to fix a URL nobody reads.

**The Rotation Trainer is not tagged `swinglab:hip-slide`.** It carries
`swinglab:consistency` and `swinglab:general`, so the app will not recommend it
for the hip-slide priority the mockups pair it with. Adding the tag would
change what the app recommends to users mid-session, which is a merchandising
call rather than a theme one — flagging rather than doing.

**The primary button is `#2c455d`, not the mockups' `#5980a6`.** A paper label
on the base accent is 3.71:1 at industry.css's own 14px/600 — below AA, and
Barlow Condensed 600 is not bold enough for the large-text exemption. One ramp
step down gives 8.87 and keeps the square corners, the solid fill and the
registration marks. It reads slightly deeper than the mockups. One line per
surface to revert if you want literal parity.

**Railway does not wait for CI.** `checkSuites: false`. Cheap to change and
worth doing before this work starts landing on `main`.

**Four of the six policies cannot be styled.** Privacy, refund,
cancellations and contact information are Shopify shop policies, served
from `checkout.shopify.com` in Shopify's own bare template. The shell
dresses the two pages the theme owns and links out to the rest. Moving
them into ordinary pages would bring them inside the shell but duplicates
legal text into a second place that can drift from the one checkout links
to — not doing that without a decision.

**`--sl-brand-green: #0f3d28`** is documented as "for the mark and only the
mark" and appears nowhere in the design source. Retire it once its call sites
are checked.
