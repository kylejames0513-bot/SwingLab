---
name: frontend-design
description: CaddieInsight's two-surface design system. Use when building or restyling UI on the Shopify storefront theme (storefront-theme/) or the app's Jinja templates (swinglab/templates/), so both surfaces stay one product. Covers the INSTRUMENT palette and its two colour rules, tokens, type, breakpoints, the design gates, and the Shopify/Liquid pitfalls this repo has already paid for.
---

# CaddieInsight frontend design

One product, two surfaces. The storefront (`storefront-theme/`, manual zip
deploy) and the app (`swinglab/templates/`, auto-deploys from `main`) must
look like the same hand built them. The parity contract is enforced by
`tests/test_app_storefront_parity.py` — design tokens are spelled
**byte-identically** in `storefront-theme/assets/base.css` and
`swinglab/templates/web_layout.html.j2`. Change a token in one place and the
test tells you where the other copy lives.

## INSTRUMENT — and its two colour rules

The product is a measuring instrument: it reads a swing from phone video and
returns one priority with a pass mark. Both surfaces look like the readout.
Near-black field under an 8px technical grid, bone type, hairline rules,
tight corners (2/4/8px), mono for every measured value.

The palette is near-monochrome plus **exactly two signals**, and its entire
value is that each one means one thing:

| | | |
| --- | --- | --- |
| **amber** | `--sl-orange` / `--sl-accent` `#f0a818` | a value the ENGINE MEASURED |
| **cyan** | `--sl-trace` `#5fe3c0` | the LIVE READOUT |

Neither is ever emphasis, decoration, a call to action, navigation state, or
hover feedback. **Buttons are bone** (`--sl-ink`) with a hairline; hover is
`--sl-ink-hi`. On a near-black field bone is louder than amber anyway, which
is what makes the rule affordable.

`tests/test_signal_colour_discipline.py` enforces the two places this
demonstrably breaks. It is not decoration on the rules — every case it checks
had already shipped once: the primary CTA in both headers, `.sl-eyebrow` in
every section, `.sl-header__link.is-current` on every page, and 25 button
hovers across 16 files. Each looked good alone; together they teach a reader
that amber means "important", and then it no longer means "measured".

## Tokens and type

- All spacing, colour, radius, and type come from `--sl-*` custom properties
  in `base.css`. Never hard-code a hex or px value that has a token.
- **The names survived the 2026-08 inversion; the values did not.**
  `--sl-green` is BONE now, not green (the forest survives as
  `--sl-brand-green`, for the mark only). `--sl-night` is the sunk well.
  Read the comments in `:root` before assuming a name means what it says.
- **Two border tokens, two jobs.** `--sl-border` is 1.33:1 and is
  **decorative only** — it may never be the sole boundary of an interactive
  control. `--sl-control-border` clears WCAG 1.4.11's 3:1. Collapsing them is
  how control edges quietly stop being visible; the parity test asserts they
  are not equal.
- Fonts are **self-hosted**, built by `store-assets/make_fonts.py`, which
  writes byte-identical copies into both surfaces:
  Archivo variable (400–800) for interface, **Archivo Expanded** (a static
  `wdth 125 / wght 800` instance) for display, DM Mono (400/500) for data.
  Display is a separate file, not a `font-stretch` on the variable font:
  `wdth 125` is a *named instance* in Archivo's STAT table so it ships
  pre-built at 14,536 bytes, where the dual-axis variable file is 90,104.
  Preloaded with `preload_tag` (theme-check's AssetPreload rule rejects a
  hand-written `<link rel="preload">`).
  **Never reference Google Fonts at runtime** — headless Chromium in the
  verification container cannot reach it and fails *silently*, so the page
  renders in a fallback while every screenshot of it looks deliberate.
- Display type is EXPANDED, so it eats line width far faster than a normal
  grotesk. The display rungs came down ~20% for this; if a headline runs long,
  reach for a smaller rung before you reach for a tighter `max-width`.

## Breakpoints

Mobile-first, four stops, and the design gates count them:
`@media (min-width: 560px)`, `(min-width: 750px)`, `(min-width: 1000px)`,
`(min-width: 1280px)`, with mobile-only blocks at `(max-width: 559px)`,
`(max-width: 749px)` and `(max-width: 999px)`.
Do not invent a new breakpoint; retarget an existing one.

## The gates

Run before calling any visual change done:

```bash
python -m pytest tests/test_storefront_design_system.py tests/test_premium_storefront.py tests/test_storefront_mobile_regressions.py tests/test_storefront_header.py tests/test_premium_accessibility.py tests/test_app_storefront_parity.py tests/test_signal_colour_discipline.py tests/test_theme_translations.py -q
```

`test_pwa_shell.py` is excluded from `make test-fast` (browser-binary
failures locally) — its pins only surface in CI, so check it when touching
the app shell.

**A pin can be green while the layout is broken.** The mobile readout once
satisfied the literal string `.sl-hero__trace { height: 64px; margin-top: 12px; }`
while rendering 80px wide, because the base rule's `aspect-ratio` was still
fixing the width. Pin behaviour; measure the rendered box.

## Liquid / Shopify pitfalls already paid for

- **`shopify theme check` runs at `--fail-level warning` in
  `scripts/package_theme.py`.** A *warning* — an unused `assign`, an orphaned
  snippet, a class used outside the file that defines it — fails the theme zip
  and errors all of `test_theme_package.py` at fixture time.
- **Declare every block-setting id in `{% schema %}`.** Shopify silently
  drops undeclared settings: `templates/index.json` can carry a value while
  `block.settings.<id>` renders blank. (This shipped once as an empty Coach
  column.)
- **A schema `default` is applied to newly added blocks.** A `"default": "01"`
  on a step number meant every block a merchant added arrived numbered `01`,
  so the auto-numbering fallback could never fire.
- **A missing translation key does not fail a render** — Shopify prints
  `Translation missing: en.some.key` into the page.
  `tests/test_theme_translations.py` catches it.
- **A Shopify Files entry beats a theme asset of the same name.** Never
  reuse a retired filename (`og-swinglab.png`, `swinglab-favicon.png`);
  ship new `caddieinsight-*` names.
- **Merchant settings override the token sheet.** `theme.liquid` writes
  `--sl-bg`, `--sl-green` and `--sl-orange` from `settings.*`, so a palette
  change is not finished until `config/settings_data.json` *and*
  `config/settings_schema.json` move with it.
- `render` arguments take no filters — `assign` the filtered value first.
- iOS buttons don't inherit color (UA `ButtonText` blue); set
  `color: inherit` on icon buttons.
- `<meta name="theme-color">` is parsed by the browser chrome, **not the
  CSSOM** — a `var()` there is discarded silently. Use a literal.
- `country_option_tags` is an object, not a filter — use
  `all_country_option_tags`.

## The live read

`assets/swing-trace.js` (synced to both surfaces by
`scripts/sync_shared_assets.py --check`) drives the hero canvas: a pose
skeleton runs a swing cycle and the clubhead draws its own arc, at the
engine's real 3:1 tempo ratio (backswing 0.42 of the cycle, downswing 0.14).

**Degradation is a contract, not a nicety.** The markup ships a complete SVG
still that the script hides only *after* the canvas initialises, so no-JS,
canvas-less, reduced-motion and screenshot clients all get a finished graphic.
Removing the `[hidden]` handshake means one of those cases renders an empty
box, which is invisible to anyone testing in a browser with JS on.

Reveal animations are progressive enhancement over a *visible* default. A
client that runs JS but never scrolls gets `.sl-js` (opacity 0) and then no
`.is-in` — measured on the live site, 11 of 13 blocks stayed invisible — so
`theme.liquid` carries a three-second safety net.

## Verifying visuals

Read `docs/quality/local-visual-verification.md` first. Screenshots in the
container render fallback fonts unless you embed the faces from
`store-assets/*.ttf` as data URIs; launch Chromium with
`executable_path="/opt/pw-browsers/chromium"`; two guided-report browser
tests fail locally on H.264 the container can't decode (they pass in CI).

## Shipping

`make theme-zip` builds `dist/caddieinsight-theme.zip` + `UPLOAD.md`.
Merging changes nothing on the store — the owner uploads the zip to a
duplicate unpublished theme, previews, then publishes
(`docs/runbooks/rebrand-cutover.md`). The app deploys itself from `main`.
