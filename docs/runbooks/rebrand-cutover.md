# SwingLab → CaddieInsight handle cutover runbook

Goal: remove every customer-visible "swinglab" string. Today they appear in
all shareable URLs and metadata:

- `/products/swinglab-pro` (the flagship product URL)
- `/collections/swinglab-gear`
- `/pages/the-swinglab-method`, `/pages/how-swinglab-works`
- `og-swinglab.png` (every social link preview), `swinglab-favicon.png`,
  `swinglab-logo.png`
- Menu handles `swinglab-main`, `swinglab-footer` (admin-only, low priority)

This is a coordinated change: the theme (19 files, 53 occurrences), the app
(`config.yaml`, `swinglab/web/shop.py` collection handle, billing product
references), and Shopify admin must move together. Shopify creates automatic
URL redirects when a handle changes, so old links keep working — the risk is
internal references, not inbound links.

## Landmines (why this is not a find-and-replace)

1. **Premium chrome trigger**: `layout/theme.liquid:5` and
   `sections/header.liquid` key the Pro page's premium styling off
   `product.handle == 'swinglab-pro'`. Renaming the handle without updating
   these silently kills the premium Pro-page presentation.
2. **App billing/config**: `config.yaml` references the Pro product handle and
   the `swinglab-gear` collection handle (`shop.py`). The app deploys from
   main on Railway; the theme deploys manually. Sequence so neither window
   breaks (redirects cover storefront URLs; config lookups by handle do NOT
   follow redirects — Storefront API queries by handle return null for the
   old handle once renamed).
3. **Asset filenames are immutable by convention** (storefront README):
   upload new `caddieinsight-*` named files, then update references — never
   overwrite a filename referenced by the live theme.
4. **Report-matcher tags**: product tags `swinglab:*` (tempo, sway, etc.) are
   an internal vocabulary shared by the app's gear matcher and the theme's
   flag chips. Renaming the tag prefix is a separate, optional migration —
   tags are not customer-visible URLs; defer.

## Cutover sequence (one sitting, ~1 hour)

1. **Prepare the PR** (no handle changes yet): a branch that updates every
   handle reference in `storefront-theme/**` and `config.yaml`/`shop.py` to
   the new handles (`caddieinsight-pro`, `caddieinsight-gear`,
   `the-caddieinsight-method`, `how-caddieinsight-works`), updates the
   premium-chrome conditionals, and references new asset names
   (`og-caddieinsight.png`, `caddieinsight-favicon.png`,
   `caddieinsight-logo.png`).
2. **Upload new-name assets** to Shopify Files (og image, favicon, logo) —
   same artwork, new names.
3. **Rename handles in admin** (Products, Collection, Pages). Confirm Shopify
   created the URL redirects (Online Store → Navigation → URL redirects).
4. **Merge the PR** → Railway deploys the app with new config handles.
5. **Upload the theme** from the merged source to a duplicate unpublished
   theme, preview (Pro page premium chrome, gear collection, method pages,
   footer links), then publish per the release boundary in
   `storefront-theme/README.md`.
6. **Verify**: old URLs 301 to new; Pro page premium header intact; gear
   showcase populates; app /shop renders gear; a test checkout email still
   grants Pro (billing matches by product/variant IDs, not handles — confirm
   in `swinglab/web/shopify_billing.py` before assuming).
7. Optional cleanup later: menu handles, archived SwingLab-vendor products,
   `SL-*` SKUs on the Pro variants (`CI-PRO-1MO` etc.), internal tag
   vocabulary.

## Explicitly out of scope

The Python package/database/CLI name `swinglab` stays (ADR 0001) — it is not
customer-visible and renaming it is a codebase migration, not a brand fix.
