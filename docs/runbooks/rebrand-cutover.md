# SwingLab → CaddieInsight handle cutover runbook

> **Revised 2026-08-12, during the site-revamp.** The original version of this
> runbook prescribed target handles the project has since decided differently
> (`caddieinsight-gear`, `caddieinsight-pro`), and following it after the
> revamp landed would have broken both arms of the theme's handle fallbacks —
> `shop.py` queries `gear` and `swinglab-gear`, so a rename to
> `caddieinsight-gear` satisfies neither, empties `/shop`, and silences every
> in-report gear recommendation. The targets below are the ones the shipped
> code actually resolves. `LAUNCH_CHECKLIST.md` sequences this into the wider
> go-live; this file carries the detail.

Goal: remove the customer-visible "swinglab" strings that remain. The agreed
targets:

| Today | Target | Status |
| --- | --- | --- |
| `/collections/swinglab-gear` | `/collections/gear` | Code-complete; rename pending |
| `/pages/the-swinglab-method` | `/pages/method` | Theme + redirect ready; rename pending |
| `/pages/how-swinglab-works` | `/pages/how-it-works` | Theme + redirect ready; rename pending |
| `/products/swinglab-pro` | **stays** | Deliberate — see landmine 2 |
| Menu handles `swinglab-main`, `swinglab-footer` | optional | Admin-only, low priority |

Already done during the revamp (no cutover step needed): the `og-swinglab.png`
/ `swinglab-favicon.png` / `swinglab-logo.png` asset migration — the theme
resolves every brand mark through `asset_url` on `caddieinsight-*` names, and
`tests/test_theme_brand_filenames.py` holds the retired list.

## Landmines (why this is not a find-and-replace)

1. **Premium chrome trigger**: `layout/theme.liquid` and
   `sections/header.liquid` key the Pro page's premium styling off
   `product.handle == 'swinglab-pro'`. This is one of the reasons the handle
   stays.
2. **`swinglab-pro` keeps its handle, permanently.** `shopify.app.toml`
   records that the `orders/paid` webhook is the only thing that grants Pro,
   and `config.yaml`'s catalog allowlist matches on product handles. The SKUs
   (`SL-PRO-LIFE` etc.) are "the durable key the order webhook matches on"
   (config.yaml). Renaming any of it risks the money path to fix a URL nobody
   reads. Renaming the SKUs is explicitly deferred for the same reason.
3. **Handle lookups do not follow redirects.** Shopify's automatic 301 covers
   storefront *URLs* only. A Storefront API `collection(handle:)` query
   returns null for the old handle the moment it is renamed — which is why
   `swinglab/web/shop.py` queries both handles in one request and takes
   whichever answers with products, and why the theme resolves the collection
   through `snippets/gear-url.liquid` and per-section fallbacks rather than
   naming a handle. **Both fallbacks assume the new handle is exactly
   `gear`.**
4. **Reports bake their URL in at render time.** `swinglab/drills.py`'s
   `GEAR_COLLECTION_PATH` still points at `/collections/swinglab-gear` on
   purpose: it resolves today, and after the rename the automatic 301 carries
   it. Move it to `/collections/gear` (and update `tests/test_drills.py`)
   only AFTER the collection is renamed — the app deploys from `main`
   automatically, so pointing it forward early puts a 404 inside every newly
   rendered report for as long as the rename lags the deploy.
5. **Report-matcher tags**: product tags `swinglab:*` (tempo, sway, etc.) are
   an internal vocabulary shared by the app's gear matcher, `drills.py`'s
   `gear_tag` fields, and the theme's flag chips. Not customer-visible;
   renaming them is a separate, optional migration — defer.

## Cutover sequence

1. **Rename the collection** in admin: `swinglab-gear` → `gear`. Confirm
   Shopify created the URL redirect (Online Store → Navigation → URL
   redirects). The theme and `/shop` keep working through both sides of this
   — the fallbacks exist so the order cannot break them.
2. **Rename the pages**: `the-swinglab-method` → `method`,
   `how-swinglab-works` → `how-it-works`. The explicit redirects for both
   were created 2026-08-12 and sit dormant until the rename activates them;
   the theme resolves either handle.
3. **Follow-up PR** (after step 1 is confirmed): move
   `drills.py:GEAR_COLLECTION_PATH` to `/collections/gear`, update
   `tests/test_drills.py`, point `header-group.json`'s announcement link at
   `shopify://collections/gear`, and retarget the six archived-product
   redirects that currently point at `/collections/swinglab-gear`. Merge →
   Railway deploys.
4. **Regenerate the readiness fixture**:
   `python scripts/refresh_store_readiness.py` — the stored snapshot still
   records the old handles, truthfully, until the live store changes.
5. **Verify**: old URLs 301; `/shop` renders gear; the gear showcase and
   footer links resolve; a report rendered before step 1 still reaches the
   collection through the 301; one rendered after step 3 links `/collections/
   gear` directly.
6. **Later, optional cleanup**: delete the legacy arms — the `legacy:` alias
   in `shop.py`, the old-handle fallbacks in `gear-url.liquid`,
   `gear-showcase`, `related-products`, `main-collection`, `footer`, and
   `main-page` — once the redirects have been live long enough that no cached
   page still points at the old handle. Each carries a comment saying it is
   deletable.

## Explicitly out of scope

The Python package/database/CLI name `swinglab` stays (ADR 0001) — it is not
customer-visible and renaming it is a codebase migration, not a brand fix.
