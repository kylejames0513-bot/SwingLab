# CaddieInsight storefront theme

Source-controlled copy of the custom CaddieInsight Shopify theme. GitHub is
the source of truth for theme code; Shopify theme state, preview state, and
publication state must be verified separately during a release.

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

## Validation

Run both gates from the repository root before opening a pull request:

```text
shopify theme check --path storefront-theme --fail-level warning
python -m pytest tests/test_storefront_header.py tests/test_theme_selling_plans.py tests/test_premium_storefront.py -q
```

Theme Check also runs in GitHub Actions. A source PR is not a Shopify preview
or a live release.

## Release boundary

Do not copy a theme identifier from documentation or an earlier release.
At release time, discover the current theme inventory read-only, preserve the
current live theme for rollback, and upload the reviewed source to a duplicate
unpublished theme. Preview that duplicate across desktop and mobile before
requesting separate approval to publish it. Never upsert working-copy files
directly to the live theme.
