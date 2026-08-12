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
| Redirects | 8 created — 2 dormant, 6 fixing live 404s |
| Handle-rename groundwork | Theme accepts old and new page handles |

Home (`2a`) was already built before this revamp began — every string from the
mockup was already in the theme.

---

## 2. Left to build

Roughly in value order.

1. **Policy shell** (`3b`) — sidebar, `LAST UPDATED` stamp, summary spec row,
   numbered clauses. One shell for six documents.
2. **Contact page** (`3c`) — subject router, conditional order-number field,
   direct table, FAQ deflection. **Use `inquiry@caddieinsight.com` only.**
3. **Sticky mobile buy bar** (`2c`) — the one part of the product mockups not
   yet built.
4. **Collection / cart / search / 404** — templates exist and are styled;
   they need the mockups' copy from `COPY_DECK.md` §8.
5. **Nav and menus** — the live `swinglab-main` menu is `Home · Gear · The
   Method · Pro Membership · FAQ`; the mockups want `Method · Sample report ·
   Plans · Gear`. Add the Founders Pass link.
6. **`/collections/swinglab-gear` rename** — see §6.
7. **App (Phase 5)** — the full `1a`–`1f` build you asked for.
8. **SEO and a11y pass (Phase 6)** — per-page titles and descriptions,
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
- [ ] `python -m pytest -q` green.
- [ ] `shopify theme check --path storefront-theme --fail-level warning` clean.

**Storefront**

- [ ] Rename pages: `the-swinglab-method` → `method`,
      `how-swinglab-works` → `how-it-works`. Redirects already exist and
      activate on rename. The theme handles either handle, so order does not
      matter.
- [ ] Update the `swinglab-main` and `swinglab-footer` menus.
- [ ] `shopify theme push --path storefront-theme --store e0hbgh-ip.myshopify.com --theme 154863632556`
- [ ] Review the preview at 375 / 768 / 1440.
- [ ] **Publish the theme.** Yours to do, not mine.

**The Founders Pass**

- [ ] Set the `founders-pass` product to **Active**. It is a draft with
      inventory tracked at 100. The moment it is active the claim button
      appears and the counter goes live; until then the button correctly hides
      rather than pointing somewhere broken.
- [ ] Confirm it is on the Online Store sales channel.
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

**The `swinglab-gear` collection rename.** 61 references across 24 files,
including `swinglab/drills.py`'s `GEAR_COLLECTION_PATH` — the URL live swing
reports point at for "Matched training aids". Renaming it finishes the rebrand;
leaving it means `caddieinsight.com/collections/swinglab-gear` stays the
canonical gear URL. It is mechanical but cross-surface, and it wants its own
pass with a full test run. My recommendation is to do it, in one commit, before
the app work.

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

**`--sl-brand-green: #0f3d28`** is documented as "for the mark and only the
mark" and appears nowhere in the design source. Retire it once its call sites
are checked.
