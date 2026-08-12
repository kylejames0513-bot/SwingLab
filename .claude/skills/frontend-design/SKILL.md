---
name: frontend-design
description: CaddieInsight's two-surface design system. Use when building or restyling UI on the Shopify storefront theme (storefront-theme/) or the app's Jinja templates (swinglab/templates/), so both surfaces stay one product. Covers the INDUSTRY blueprint grammar, the paper ground and the one reversed field, the three colour roles and how contrast confines them, tokens, type, breakpoints, the design gates, and the Shopify/Liquid pitfalls this repo has already paid for.
---

# CaddieInsight frontend design

One product, two surfaces. The storefront (`storefront-theme/`, manual zip
deploy) and the app (`swinglab/templates/`, auto-deploys from `main`) must
look like the same hand built them. The parity contract is enforced by
`tests/test_app_storefront_parity.py` — design tokens are spelled
**byte-identically** in `storefront-theme/assets/base.css` and
`swinglab/templates/web_layout.html.j2`. Change a token in one place and the
test tells you where the other copy lives.

## INDUSTRY — a blueprint on paper

The product is a measuring instrument: it reads a swing from phone video and
returns one priority with a pass mark. Both surfaces are the spec sheet that
readout is printed on. Paper ground, ink type, Barlow Condensed over Barlow,
hairline rules, **square corners everywhere**, mono for every measured value.

Content sits inside **drawn objects**: square, transparent, hairline-bordered,
with `+` registration marks at the corners (`.sl-blueprint` plus four
`.sl-corner` children). Cards are line drawings, not filled panels — the
primary button is the one solid object on the board. A card that gains a fill,
a shadow or a radius stops being a drawing, which is most of what separates
this system from the last one.

### The one reversed ground

`.sl-field` is the deep green (`--sl-field: #070f0b`) and it is the **only**
dark surface. It carries video, evidence tiles, the hero, the priority panel,
the footer. Opting in via the class is the only way to make a surface dark,
which is what keeps the number of dark surfaces knowable.

Two textures, both belonging to objects rather than to the page:
`--sl-hatch` (115° steel screen-print grain, for field panels and evidence
tiles) and `--sl-rules` (wide vertical scan lines, for hero bands). **There is
no page-wide grid.** The 8px instrument grid was retired in 2026-08-11: it
existed to stop a near-black page reading as a dark theme, and under a light
ground it reads as graph paper the content is fighting.

`--sl-band: #1d2d3d` is the announcement strip above each header. It is
deliberately *not* the field — the field carries evidence, and a marketing bar
borrowing it would dilute what a dark surface means.

## Three colour roles, and contrast enforces two of them

| | | |
| --- | --- | --- |
| **steel** | `--sl-steel` `#5980a6` | STRUCTURE — kickers, active nav, rules, registration marks, segmented fill |
| **steel deep** | `--sl-orange` / `--sl-accent` `#416180` | THE SIGNAL: a value the engine MEASURED |
| **steel lit** | `--sl-trace` `#94bce3` | THE TRACE: the LIVE READOUT |

Neither signal is ever emphasis, decoration, a call to action, navigation
state, or hover feedback. **Buttons are the solid steel `--sl-green-btn`
(`#2c455d`)**; on the field they are `.sl-btn--light`, paper-edged.

Steel exists as a third colour *so that emphasis has somewhere to go*. Amber
was eroded three separate times under the old palette for exactly one reason:
it was the most attractive thing on the page and emphasis had nowhere else to
land. `tests/test_signal_colour_discipline.py` still enforces the two places
this demonstrably breaks — the primary CTA in both headers, `.sl-eyebrow` in
every section, `.sl-header__link.is-current`, and button hovers.

**The palette also confines each signal by contrast, which is new and worth
understanding.** The signal is 5.78 on paper and 3.00 on the field; the trace
is 9.76 on the field and 1.78 on paper. Each is legible on exactly one ground,
so putting one on the wrong surface is visible immediately rather than subtly.
That gives the rule a physical floor the old palette never had.

The consequence: **on the field, a measured value is set in `--sl-field-ink`
at display or mono weight**, not in the signal. Loudness comes from the ground.
The hero readout, the pass mark and the sample art all do this, and their
tests pin the field ink so a later pass cannot "restore the signal" onto a
ground it cannot be read on.

## Tokens and type

- All spacing, colour, radius, and type come from `--sl-*` custom properties
  in `base.css`. Never hard-code a hex or px value that has a token.
- **The names survived two inversions; the values did not.** `--sl-green` is
  INK now. `--sl-night` is a *tinted recess* (the sunk well), not a darker
  one. `--sl-bg-card` is the ground, because cards are line drawings.
  `--sl-paper*` collapsed into the ground. Read the comments in `:root`
  before assuming a name means what it says.
- **`--sl-wash-rgb` vs `--sl-cream-rgb`, and getting it wrong is invisible.**
  A low-alpha wash means "the colour that contrasts with the ground". On paper
  that is ink (`--sl-wash-rgb`); on the field it is bone (`--sl-cream-rgb`).
  Pick by ground. A bone wash on paper paints white on white and simply
  vanishes — 104 hairlines were in that state after the token flip.
- **Two border tokens, two jobs.** `--sl-border` is 1.37:1 and is
  **decorative only** — it may never be the sole boundary of an interactive
  control. `--sl-control-border` clears WCAG 1.4.11's 3:1. Collapsing them is
  how control edges quietly stop being visible; the parity test asserts they
  are not equal.
- Fonts are **self-hosted**, built by `store-assets/make_fonts.py`, which
  writes byte-identical copies into both surfaces: Barlow 400/500 for
  interface, **Barlow Condensed 600** for display, DM Mono 400/500 for data.
  Preloaded with `preload_tag` (theme-check's AssetPreload rule rejects a
  hand-written `<link rel="preload">`).
  **Never reference Google Fonts at runtime** — headless Chromium in the
  verification container cannot reach it and fails *silently*.
- **Barlow has NO variable font.** Google serves it static at v13 and the css2
  endpoint rejects a range outright, so a weight is a 22 KB file and the
  palette is a budget: **400, 500, 600 only.** Anything else is synthesised.
  A rule that wants more weight reaches for `--sl-font-display`, not a heavier
  number — display is a separate *family*, which is what lets the interface
  ship two weights. The parity test caps the whole type system at 100 KB
  against 96,320 actual, so a fourth weight is a decision somebody has to
  argue for.
- Display type is CONDENSED, so it holds more per line than the old expanded
  face. The display rungs went back **up** ~20% for this.

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

**A pin can be green while the layout is broken**, and the 2026-08-11 overhaul
is the reference case: all 70 design-gate tests passed while the app's hero
rendered ink-on-near-black and the nav was invisible, because the tokens were
correct and the *grounds* were not. Pin behaviour; then look at the page.

## Verifying visuals

Read `docs/quality/local-visual-verification.md` first. Launch Chromium with
`executable_path="/opt/pw-browsers/chromium"` in the container. Two
`tests/test_guided_report_browser.py` tests fail locally on H.264 the
container cannot decode; they pass in CI — confirm with `canPlayType` before
calling a failure real.

The fonts are self-hosted now, so the Google Fonts fallback trap no longer
applies — but **check anyway**, because the failure is silent either way:

```js
await page.evaluate(() => [...document.fonts].filter(f => f.status === "loaded").length)
```

Five faces should load. Fewer means you are looking at a lie.

**The storefront cannot be rendered locally at all** — there is no local
Liquid render, so theme changes are verifiable only through the pinned tests
and `make theme-zip`. The app can be run (`uvicorn --factory
swinglab.web.app:create_app`) and should be, because it is the only place the
shared system can actually be seen.

## The live read

`assets/swing-trace.js` (synced to both surfaces by
`scripts/sync_shared_assets.py --check`) drives the hero canvas at the
engine's real 3:1 tempo ratio (backswing 0.42 of the cycle, downswing 0.14).
It only ever runs on the field, so all three of its colours are field-side.
Its `signal` parameter is a misleading name kept for its call sites: it draws
landmark crosshairs and the impact pulse, which are LIVE events, not measured
values.

**Degradation is a contract, not a nicety.** The markup ships a complete SVG
still that the script hides only *after* the canvas initialises, so no-JS,
canvas-less, reduced-motion and screenshot clients all get a finished graphic.
Removing the `[hidden]` handshake means one of those cases renders an empty
box, which is invisible to anyone testing in a browser with JS on.

Reveal animations are progressive enhancement over a *visible* default. A
client that runs JS but never scrolls gets `.sl-js` (opacity 0) and then no
`.is-in` — measured on the live site, 11 of 13 blocks stayed invisible — so
`theme.liquid` carries a three-second safety net.

## Generated imagery is part of the palette

Drawn art carries the brand too, and it is the thing most often left behind:
`report.html.j2` was still rendering a warm cream document from two brands ago
because nothing pointed at it.

- `store-assets/make_assets.py` holds the palette for every generated
  illustration; `campaign_assets.py` and `pro_home_assets.py` import it.
- `store-assets/brand_mark.py` is the mark — one geometry, two renderers. Do
  not hand-edit outputs; regenerate with `make_brand.py`.
- `config.yaml`'s `primary_color` / `accent_color` tint runtime-drawn overlays
  (skeletons, chips, drill diagrams). They land on *video*, so they follow the
  FIELD's rule, not the page's.
- **Alt text that names a colour is part of the palette.** `sample.py`'s
  illustration alt text says which marks are which colour; leaving it saying
  "orange" after the mark became paper describes a picture that is not there,
  for the readers who depend on it most.

## Liquid / Shopify pitfalls already paid for

- **`shopify theme check` runs at `--fail-level warning` in
  `scripts/package_theme.py`.** A *warning* — an unused `assign`, an orphaned
  snippet, a class used outside the file that defines it — fails the theme zip
  and errors all of `test_theme_package.py` at fixture time. Anything two
  sections share must live in `base.css`.
- **Declare every block-setting id in `{% schema %}`.** Shopify silently
  drops undeclared settings: `templates/index.json` can carry a value while
  `block.settings.<id>` renders blank.
- **A schema `default` is applied to newly added blocks.**
- **A missing translation key does not fail a render** — Shopify prints
  `Translation missing: en.some.key` into the page.
  `tests/test_theme_translations.py` catches it.
- **A Shopify Files entry beats a theme asset of the same name.** Files holds
  `swinglab-logo.png`, `swinglab-logo-inverse.png`, `swinglab-favicon.png` and
  `og-swinglab.png`; it holds NO `caddieinsight-*` mark, so those theme assets
  resolve from the theme and can be regenerated in place. Verify before
  assuming — `tests/test_theme_brand_filenames.py` pins the retired list.
- **Merchant settings override the token sheet.** `theme.liquid` writes
  `--sl-bg`, `--sl-green` and `--sl-orange` from `settings.*`, so a palette
  change is not finished until `config/settings_data.json` *and*
  `config/settings_schema.json` move with it.
- `render` arguments take no filters — `assign` the filtered value first.
- iOS buttons don't inherit color (UA `ButtonText` blue); set
  `color: inherit` on icon buttons.
- `<meta name="theme-color">` is parsed by the browser chrome, **not the
  CSSOM** — a `var()` there is discarded silently. Use a literal, and
  remember `password.liquid` has its own copy of the font and colour setup.
- `country_option_tags` is an object, not a filter — use
  `all_country_option_tags`.

## Shipping

`make theme-zip` builds `dist/caddieinsight-theme.zip` + `UPLOAD.md`.
Merging changes nothing on the store — the owner uploads the zip to a
duplicate unpublished theme, previews, then publishes
(`docs/runbooks/rebrand-cutover.md`). The app deploys itself from `main`,
which is why a half-restyled app must never land there.
