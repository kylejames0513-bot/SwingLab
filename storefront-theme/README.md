# CaddieInsight storefront theme

Source of the custom Shopify theme running on the store's draft theme slot
(`swinglab-storefront-theme`, unpublished). This directory is the working
copy: every file here has been applied byte-for-byte to the draft theme via
the Admin API (`themeFilesUpsert`), and future edits should be made here
first, then re-upserted.

Built on the same "Fairway Modernism" design system as `../store-assets/`
(see `PHILOSOPHY.md` there): warm off-white field, deep green ink, one
orange kinetic accent, Archivo display type with DM Mono specimen labels.

## Layout

| Path | What it is |
| --- | --- |
| `layout/theme.liquid` | Document shell: fonts, favicon, og:image, scroll-reveal script |
| `assets/base.css` | Design tokens + shared classes (`.sl-chip`, `.sl-drill-card`, `.sl-note`, …) |
| `sections/` | Homepage sections (hero, stats band, how-it-works, report feature, gear showcase, Free-vs-Pro comparison, coach notes, FAQ, email capture, CTA banner) plus main page/product/collection/cart/search/404 and header/footer groups |
| `snippets/` | Product card and media placeholder |
| `templates/` | JSON templates wiring sections; `index.json` carries the full homepage content |
| `config/` | Theme settings schema + data |

## Conventions

- Shopify template/group JSON is pure JSON (no comment banners).
- Section-specific styles live in that section's <code>&#123;% stylesheet %&#125;</code> block
  with `sl-<section>__` prefixed classes; shared patterns live in
  `assets/base.css` only.
- `url`-type settings carry no `default` (this store's validator rejects
  relative-path defaults); templates set URLs explicitly, and sections fall
  back sensibly when a setting is blank.
- Product pages branch on `product.type == 'Membership'`: the Pro page keeps
  its benefits/unlock experience (locale keys in `locales/`, managed in
  Shopify), gear pages get flag chips, compare-at pricing, trust strip, and
  the drill-protocol description written by `store-assets` product copy.

## Applying changes

Upsert changed files to the draft theme (never the live one) with the Admin
API:

```graphql
mutation UpsertThemeFiles($themeId: ID!, $files: [OnlineStoreThemeFilesUpsertFileInput!]!) {
  themeFilesUpsert(themeId: $themeId, files: $files) {
    upsertedThemeFiles { filename }
    userErrors { field message }
  }
}
```

with `files: [{ filename, body: { type: TEXT, value } }]`, draft theme id
`gid://shopify/OnlineStoreTheme/154368999596`. Preview
`caddieinsight-storefront-theme` in Shopify admin under
Online Store → Themes before publishing it manually. Never upsert these
working-copy changes directly to the live MAIN theme
(`gid://shopify/OnlineStoreTheme/154372636844`).
