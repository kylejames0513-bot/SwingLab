---
name: frontend-design
description: CaddieInsight's two-surface design system. Use when building or restyling UI on the Shopify storefront theme (storefront-theme/) or the app's Jinja templates (swinglab/templates/), so both surfaces stay one product. Covers tokens, breakpoints, fonts, the design-gate tests, and the Shopify/Liquid pitfalls this repo has already paid for.
---

# CaddieInsight frontend design

One product, two surfaces. The storefront (`storefront-theme/`, manual zip
deploy) and the app (`swinglab/templates/`, auto-deploys from `main`) must
look like the same hand built them. The parity contract is enforced by
`tests/test_app_storefront_parity.py` — design tokens are spelled
**byte-identical** in `storefront-theme/assets/base.css` and
`swinglab/templates/web_layout.html.j2`. Change a token in one place and the
test tells you where the other copy lives.

## Tokens and type

- All spacing, color, radius, and type come from `--sl-*` custom properties
  in `base.css`. Never hard-code a hex or px value that has a token.
- Spacing scale: `--sl-space-1` … `--sl-space-9`. Type scale: `--sl-text-*`.
- Fonts are **self-hosted**: Archivo (variable, 400–800) for UI, IBM Plex
  Mono (400/500) for labels/data. Loaded via `@font-face` on both surfaces,
  preloaded with the `preload_tag` filter (theme-check's AssetPreload rule
  rejects hand-written `<link rel="preload">`). Never reference Google
  Fonts — headless Chromium in this container can't reach it and fails
  silently, and the theme must not depend on a third-party origin.

## Breakpoints

Mobile-first, four stops, and the design gates count them:
`@media (min-width: 560px)`, `(min-width: 1000px)`, `(min-width: 1280px)`,
with mobile-only blocks at `(max-width: 749px)` and `(max-width: 999px)`.
Do not invent a new breakpoint; retarget an existing one.

## The gates

Run before calling any visual change done:

```bash
python3 -m pytest tests/test_storefront_design_system.py \
    tests/test_premium_storefront.py tests/test_storefront_mobile_regressions.py \
    tests/test_storefront_header.py tests/test_premium_accessibility.py -q
```

`test_pwa_shell.py` is excluded from `make test-fast` (browser-binary
failures locally) — its pins only surface in CI, so check it when touching
the app shell.

## Liquid / Shopify pitfalls already paid for

- **Declare every block-setting id in `{% schema %}`.** Shopify silently
  drops undeclared settings: `templates/index.json` can carry a value while
  `block.settings.<id>` renders blank. (This shipped once as an empty Coach
  column.)
- **A Shopify Files entry beats a theme asset of the same name.** Never
  reuse a retired filename (`og-swinglab.png`, `swinglab-favicon.png`);
  ship new `caddieinsight-*` names.
- `render` arguments take no filters — `assign` the filtered value first.
- iOS buttons don't inherit color (UA `ButtonText` blue); set
  `color: inherit` on icon buttons.
- Hero video uses the `video` setting type + `video_tag` with
  `autoplay/loop/muted/playsinline`; the photo stays poster/fallback and
  the Ken Burns drift (`sl-hero-drift`) covers the no-video case, gated by
  `prefers-reduced-motion: no-preference`.
- `country_option_tags` is an object, not a filter — use
  `all_country_option_tags`.

## Verifying visuals

Read `docs/quality/local-visual-verification.md` first. Screenshots in this
container render fallback fonts unless you embed the faces from
`store-assets/*.ttf` as data URIs; launch Chromium with
`executable_path="/opt/pw-browsers/chromium"`; two guided-report browser
tests fail locally on H.264 the container can't decode (they pass in CI).

## Shipping

`make theme-zip` builds `dist/caddieinsight-theme.zip` + `UPLOAD.md`.
Merging changes nothing on the store — the owner uploads the zip to a
duplicate unpublished theme, previews, then publishes
(`docs/runbooks/rebrand-cutover.md`). The app deploys itself from `main`.
