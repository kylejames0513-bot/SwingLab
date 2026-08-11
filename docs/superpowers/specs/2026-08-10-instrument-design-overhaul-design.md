# INSTRUMENT — a complete visual overhaul of both surfaces

Date: 2026-08-10
Status: approved (Kyle, 2026-08-10)

## The problem

CaddieInsight looks like a well-executed template. That is not the same as
looking like itself.

Four specific failures, each verified against the live sites rather than
asserted:

1. **One typeface does every job.** Archivo is display, interface, and
   heading. There is no typographic voice, and bold Archivo at tight tracking
   is the default landing-page look of the last five years.
2. **The live hero is not the hero.** `sl-hero__trace` — an SVG that draws the
   backswing across three beats and retraces it in one, at the engine's real
   `tempo_ratio` target — is a 280x88 box in the corner of a stock photograph.
   The most on-brand object in the repository is decoration.
3. **Ten sections, one shape.** Every homepage band is `eyebrow -> h2 -> lede
   -> card grid`. The page has no pulse.
4. **Amber on dark green is the fitness-app default.** It is the palette of
   every competitor the positioning doc says never to name.

And one real bug, found while capturing baselines: full-page screenshots of
`caddieinsight.com` render every light section **completely blank**.
`.sl-reveal` only receives `.is-in` from an IntersectionObserver, so any
client that does not run that observer — a screenshot, a crawler, a reader
with JS off — sees empty bands where the content should be.

## The direction

The product is a measuring instrument. It reads a swing from phone video and
returns one priority with a pass mark. The surfaces should look like the
readout, not like a brochure about the readout.

Dark-first on both surfaces. Strict grid. Hairline rules. Two signal colours
with exactly one job each. Small radii. The trace promoted from decoration to
identity.

## 1. Type

`store-assets/Archivo-var.ttf` carries `wght 100-900` **and `wdth 62-125`**.
The shipped web font discards the width axis.

The obvious move — ship the dual-axis file, drive width from CSS — is the
wrong one. Measured against Google's CDN, which is demonstrably where the
current file came from (the generator reproduces it byte-identically,
sha256 `8f704806dbed`):

| Shape | Bytes |
| --- | --- |
| `wght@400..800` (today) | 34,928 |
| `wdth,wght@100..125,400..800` — one dual-axis file | 90,104 |
| `wdth,wght@118,800` — static, *not* a named instance | 37,420 |
| **`wdth,wght@125,800` — static, IS a named instance** | **14,536** |

`wdth 125` is a named instance in Archivo's STAT table, so Google serves a
pre-built static for it. Arbitrary points do not get that treatment: 118 falls
back to a dynamic build at 2.5x the size, and `wdth 118 / wght 600..800`
returns the entire variable font again at 90,104.

So the display face is a second, tiny file — and it lands at `wdth 125`, where
the designer actually drew Expanded, rather than the 118 first proposed.

| Role | Face | Setting | Bytes |
| --- | --- | --- | --- |
| Display | Archivo Expanded | static `wdth 125` / `wght 800`, `-0.02em` | 14,536 |
| Interface | Archivo | variable `wght 400-800`, `wdth 100` | 34,928 |
| Data | DM Mono | `400` / `500`, uppercase, `+0.14em` | 29,808 |

One display weight, not two. A single expanded weight used at several sizes is
a more disciplined voice than a family of them, and it halves the cost.

DM Mono replaces IBM Plex Mono at ~15 KB/weight — a net cost of ~0.2 KB. It is
already vendored at `store-assets/DMMono-Regular.ttf`, and `brand_mark.py` and
`make_assets.py` already draw the instrument-sheet product artwork with it.
Today the generated photography and the live site use different monos. After
this change they use the same one.

Font budget: **64,524 -> 79,272 bytes, +14.7 KB.** All four faces are built by
`store-assets/make_fonts.py` and written byte-identically into both surfaces.

Fonts stay **self-hosted**. Never a Google Fonts URL at runtime: headless
Chromium in the verification container cannot reach it and fails silently, and
the theme must not depend on a third-party origin.

## 2. Colour

Every ratio below is computed, not estimated.

| Token | Value | On field | On raised |
| --- | --- | --- | --- |
| `--sl-field` | `#070F0B` | — | — |
| `--sl-field-raised` | `#0D1712` | — | — |
| `--sl-field-sunk` | `#040907` | — | — |
| `--sl-line` | `#1E2C25` | 1.33 | decorative only |
| `--sl-line-control` | `#5C6B62` | 3.45 | 3.25 |
| `--sl-ink` | `#EDEFE9` | 16.75 | 15.78 |
| `--sl-ink-soft` | `#A8B3AC` | 8.97 | 8.45 |
| `--sl-ink-muted` | `#78857D` | 5.04 | 4.75 |
| `--sl-signal` | `#F0A818` | 9.54 | 8.99 |
| `--sl-trace` | `#5FE3C0` | 12.23 | 11.52 |
| `--sl-paper` | `#F2EFE6` | — | — |
| `--sl-paper-ink` | `#10160F` | 15.97 on paper | — |
| `--sl-green` | `#0F3D28` | brand mark only | — |

Every text pair clears WCAG AA for normal text. `--sl-line` is 1.33 and is
therefore **decorative only** — it may never be the sole boundary of an
interactive control. `--sl-line-control` exists so inputs, selects, and
buttons satisfy WCAG 1.4.11 non-text contrast. Two tokens because they have
two different jobs; collapsing them is how the control borders quietly stop
being visible.

**The discipline that makes this work:** amber marks a value the engine
measured. Cyan marks the live readout. Neither is ever used for emphasis,
decoration, or a call to action. A button is bone-on-field with a hairline. If
you see amber, it is a number the product produced.

Radii drop from `12/16/22px` to `2/4/8px`. Instruments have tight corners.
This single change does more perceptual work than the palette.

## 3. The live hero

Full-width canvas. A pose skeleton runs a real swing cycle — address, top,
impact, follow-through — at the engine's actual 3:1 tempo ratio. The trace
draws behind it. Landmark crosshairs resolve as they lock. The metric panel
counts up as each phase lands. Impact pulses. It loops.

The campaign photograph becomes a heavily desaturated ghost at low opacity:
atmosphere, not subject.

**Degradation is a requirement, not a nicety.** Under
`prefers-reduced-motion: reduce`, or with JS disabled, or in a screenshot, the
hero paints one complete still frame — never an empty box. This is the same
failure the `.sl-reveal` bug already ships, and the rebuild fixes both:
reveal animations become progressive enhancement over a visible default.

The existing "demonstration data" disclosure stays. Nothing in the hero may
imply a real customer result.

## 4. Layout

The homepage stops being ten identical bands. It gets a pulse: full-bleed
instrument panels alternating with narrow measured-column prose, a 12-column
grid with visible hairline gutters, section numbers set in mono in the margin
like a spec sheet, and asymmetry — content that starts at column 3 and runs to
11 rather than everything centred.

Breakpoints do not change. Four stops, mobile-first, exactly as today:
`560px`, `1000px`, `1280px`, with mobile-only blocks at `max-width: 749px` and
`max-width: 999px`. A new breakpoint is never the answer; retarget an existing
one.

## 5. Scope

- **Storefront** (`storefront-theme/`) — `base.css`, both layouts, header,
  footer, announcement bar, all 10 homepage sections, all 19 `main-*`
  sections, `related-products`, all 7 snippets.
- **App** (`swinglab/templates/`) — `web_layout.html.j2` and all 16 other
  templates, including `report.html.j2` and `report_guided.html.j2`.
- **Assets** — brand marks regenerated through `make_brand.py`; outputs are
  never hand-edited. New `caddieinsight-*` filenames only.

## 6. Constraints this must not violate

- **The parity contract.** `tests/test_app_storefront_parity.py` requires a
  set of tokens to be spelled **byte-identically** in
  `storefront-theme/assets/base.css` and `swinglab/templates/web_layout.html.j2`.
  The values all change; the contract does not. The test is updated to pin the
  new values on both ends, including leading zeros in `clamp()` expressions —
  a comparison that has to normalise before it can compare is one that will
  eventually normalise away a real divergence.
- **A Shopify Files entry beats a theme asset of the same name.** The store
  still holds `og-swinglab.png` and `swinglab-favicon.png` from the v3 brand.
  Never point at a retired filename expecting the theme copy to win, and never
  overwrite — an overwrite bypasses the preview and leaves no rollback.
- **Declare every block-setting id in `{% schema %}`.** Shopify silently drops
  undeclared settings; `templates/index.json` can carry a value while
  `block.settings.<id>` renders blank. This has already shipped once as an
  empty Coach column.
- `render` arguments take no filters — `assign` the filtered value first.
- iOS buttons do not inherit colour; icon buttons need `color: inherit`.
- **Two deploy paths.** Merging to `main` deploys the app via Railway
  immediately. The theme is a manual zip upload to a duplicate unpublished
  theme, previewed, then published (`docs/runbooks/rebrand-cutover.md`).
  Merging changes nothing on the store.

## 7. Verification

- The five design gates plus the parity and PWA suites, green.
- Contrast recomputed programmatically for every token pair, not eyeballed.
- Every page re-shot at 1440x900 and 390x844 and diffed against the baselines
  captured before any change.
- The no-JS case checked explicitly: content must be visible with the
  observer disabled.

## 8. Explicitly out of scope

- Copy rewrites. The positioning, claims, and honest-scope disclosures are
  settled and stay as they are.
- Pricing, plan structure, entitlements.
- The handle migration (`swinglab-pro` -> `caddieinsight-pro`), which remains
  deliberately deferred to a coordinated cutover.
- `mobile/`, the Expo scaffold.
