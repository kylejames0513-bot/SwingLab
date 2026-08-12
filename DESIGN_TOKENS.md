# Design tokens

Every colour, face, size, space, radius and border the revamp is allowed to
use, where each one lives, and the three places CaddieInsight deliberately
departs from the Industry system it is built on.

This file plus `design-source/CaddieInsight Mockups.dc.html` are the source of
truth. The pre-revamp theme's colours and fonts are dead.

---

## 1. Where tokens live — and why twice

There is no shared stylesheet. Every token below is declared **twice**:

| Surface | File | Form |
| --- | --- | --- |
| Storefront | `storefront-theme/assets/base.css` | `:root` block |
| App | `swinglab/templates/web_layout.html.j2` | inline `<style>`, `:root` block |

The app does not load `base.css` — that was tried, the selectors matched
nothing, and the style layer now lives inline in the layout. Parity is held by
hand and by test, and the layout is explicit that shared names must be
"spelled EXACTLY as `base.css` spells them" because **the parity contract
compares text, not numbers**.

Practical consequence for every task in this revamp: **a token change is two
edits, and `make parity` is what catches you forgetting the second.**

A third place carries colour: `config.yaml`'s `primary_color` / `accent_color`
tint runtime overlays drawn onto *video*, not CSS. They are already correct
(`#94bce3` trace, `#f2f2f3` paper) and this revamp does not move them.

### The names are legacy on purpose

`--sl-green` means **ink**. `--sl-orange` means **steel-deep**. The names
survived two palette inversions because `--sl-green` has 131 references and
`--sl-border` 90 across the surfaces, and renaming would touch roughly 1,400
call sites to buy nothing. Each token is used for the role its *comment*
describes, not its name. **Do not rename them in this revamp.**

---

## 2. Colour

The entire mockup document — 188 KB, 17 screens — paints **twelve distinct
hex values**. That closure is the design, not an accident. Nothing outside
this table may be introduced.

### The grounds

| Role | Token | Hex | Notes |
| --- | --- | --- | --- |
| Paper — the ground | `--sl-bg`, `--sl-paper` | `#f2f2f3` | Both surfaces, everywhere |
| Sunk well | `--sl-night`, `--sl-surface-dark` | `#e9e9ea` | Inset, code, table stripe |
| **Field — the one reversed ground** | `--sl-field` | `#070f0b` | Video, evidence, hero, capture |
| Field lift | `--sl-field-lift` | `#0b1712` | Raised panel on the field |
| Band | `--sl-band` | `#1d2d3d` | Announcement bar only |

**Paper is the ground on both surfaces.** The field is not a theme, not a dark
mode, and not a second ground — it is where the product shows what it measured.
Video, evidence tiles, the home hero, the priority panel, the capture screen.
Nothing else goes on it.

### Type on those grounds

| Role | Token | Hex | On paper | On field |
| --- | --- | --- | --- | --- |
| Primary type | `--sl-ink`, `--sl-green` | `#1d1f20` | **14.79** | — |
| Secondary | `--sl-ink-soft` | `#5d5d60` | 5.87 | — |
| Fine print (storefront) | `--sl-ink-muted` | `#6a6a6d` | 4.82 | — |
| Fine print (app) | `--sl-ink-muted` | `#626265` | 5.43 | — |
| Type on the field | `--sl-field-ink`, `--sl-cream` | `#f2f2f3` | — | **17.34** |
| Secondary on field | `--sl-field-soft` | `#a8b3ac` | — | — |
| Muted on field | `--sl-field-muted` | `#8f9a93` | — | — |
| Rule on field | `--sl-field-rule` | `#243229` | — | — |
| Control edge on field | `--sl-field-control` | `#66756c` | — | — |

The app runs its small text one step darker than the storefront
(`#626265` vs `#6a6a6d`) because it sets interface text where the storefront
sets display prose. That asymmetry is intentional and has survived two
inversions.

### The three coloured roles, and what confines them

This is the part that erodes if nobody guards it. Each colour is pinned to one
job **and to one ground**, and its own contrast is what enforces the pinning.

| Role | Token | Hex | Job | Confined to |
| --- | --- | --- | --- | --- |
| **Structure** | `--sl-steel` | `#5980a6` | Kickers, active nav, rules, registration marks, segmented fill | Chrome only. **3.71 on paper — not body-copy safe** |
| **The signal** | `--sl-orange`, `--sl-accent` | `#416180` | A value the engine measured. Never emphasis, never decoration, never a CTA | **Paper** (5.78). On the field it drops to 3.00 |
| **The trace** | `--sl-trace` | `#94bce3` | The live readout — scrub line, framing guides, audio peaks, canvas trace | **Field** (9.76). On paper it is 1.78 |

Read that table twice before adding a colour anywhere. The signal is
structurally a paper-side colour and the trace is structurally a field-side one;
each becomes illegible on the other ground, which is the rule enforcing itself.

The app additionally carries `--sl-orange-text: #375169` (7.38) for signal text
at interface size, and `--sl-trace-dim: #5980a6` for the trace at rest.

### Borders — two tokens, two jobs

| Token | Hex | Contrast | Rule |
| --- | --- | --- | --- |
| `--sl-border` | `#d0d1d1` | 1.37 | **Decorative only.** May never be the sole boundary of an interactive control |
| `--sl-border-strong` | `#b7b7ba` | — | Structural rule |
| `--sl-control-border` | `#7a7a7d` | 3.82 | Interactive edges — clears WCAG 1.4.11 |

Collapsing the first and third is how control borders quietly stop being
visible. Keep them separate.

### Support

| Token | Hex | Role |
| --- | --- | --- |
| `--sl-orange-soft`, `--sl-surface-soft` | `#eef6ff` | Tinted wash for callouts (ink on it: 14.3) |
| `--sl-focus` | `#416180` | Focus ring on paper |
| `--sl-focus-dark` | `#94bce3` | Focus ring on the field |
| `--sl-danger` | `#b3261e` | Error text — 5.84 on paper |
| `--sl-ink-hi` | `#0b0c0c` | Hover for an ink control |
| `--sl-arc` | `rgba(89,128,166,.28)` | The quiet-arc decoration, one place |

### One token to retire

`--sl-brand-green: #0f3d28` is described as "for the mark and only the mark."
The mockups' marks are ink, paper and steel — that forest green appears nowhere
in the design source. **Phase 5 should remove it** once its call sites are
checked, or the palette has a thirteenth colour with no job.

---

## 3. The deliberate deviation: the primary button

The one place the mockups and the shipped code disagree, and the disagreement
is on purpose.

Industry's readme fills the primary button with the base accent, and the
mockups follow it — `<span class="btn btn-primary">` renders `#5980a6` with a
paper label at industry.css's `.btn` size of **14px / 600**.

Computed:

| Combination | Ratio | Verdict at 14px/600 |
| --- | --- | --- |
| Paper label on `#5980a6` (mockup) | **3.71** | **Fails AA.** Needs 4.5 |
| Ink label on `#5980a6` | 3.99 | Also fails |
| Paper label on `#2c455d` (shipped) | **8.87** | Passes comfortably |

Barlow Condensed 600 is not bold enough to qualify for the AA-large exemption,
which needs 18.66px at weight 700+, or 24px. So the mockup's button cannot be
reproduced literally at its own type size without failing the accessibility
pass the brief asks for in Phase 6.

**Decision: keep `--sl-green-btn: #2c455d`** — one step down the accent ramp.
It preserves everything that makes the button the mockups' "one solid object on
the board": the solid fill, the square corners, the registration marks, the
single-filled-element grammar. Only the value moves, by one ramp step.

This is already what ships, and `base.css` already documents the reasoning. I
am recording it here so the next person comparing the preview against the
mockups sees a known, computed deviation rather than a bug.

*Flagging it plainly: the button will read slightly deeper and less saturated
than the mockups. If you would rather have literal parity and accept 3.71:1,
say so and I will change it — it is a one-line move on each surface.*

For context, the steel fill against the field it sits on is **4.68**, so the
button is unambiguous as an object either way. The problem is only the label.

---

## 4. Type

| Token | Value |
| --- | --- |
| `--font-heading` | `"Barlow Condensed", system-ui, sans-serif` |
| `--font-heading-weight` | `600` |
| `--font-body` | `"Barlow", system-ui, sans-serif` |
| Mono | **DM Mono** 400/500 — see below |

Self-hosted on both surfaces, latin subset, no third-party origin at runtime:
`barlow-latin-400/500.woff2`, `barlow-condensed-latin-600.woff2`,
`dm-mono-latin-400/500.woff2`. Roughly 79 KB total.

**Do not use industry.css's `@import` of Google Fonts.** Its first line pulls
Barlow from `fonts.googleapis.com`; both surfaces already self-host. Importing
it would add a third-party origin, and on the storefront it is the documented
failure this repo has already paid for once.

### The mono face is a CaddieInsight extension

The mockups use `ui-monospace, Menlo, monospace` — a system stack — in **207
places**: every measured value, every spec label, every kicker, every
registration caption. Monospace is load-bearing in this design.

But Industry declares only Barlow and Barlow Condensed, and its lint rule
(`_adherence.oxlintrc.json`) **rejects any other family by name**. So the
mockups' `ui-monospace` is a placeholder for whatever mono the surface ships,
and both surfaces already ship DM Mono.

**DM Mono is the answer to the mockups' `ui-monospace`.** It is a documented
extension to Industry, in the same way the green field is.

### Scale

industry.css sets the base scale — h1 42 / h2 32 / h3 25 / h4 20 / h5 16 /
h6 13, body 15/1.55, `-0.015em` tracking on headings, `1.12` heading
line-height.

The mockups then set **display sizes above it inline**, per screen: 62, 60, 58,
46, 44, 38, 36, 34, 32, 27, 26. The home hero is 58px at `1.02` line-height
and `-0.025em`. Treat these as a display tier the section owns, not as a change
to the base scale.

Interface text clusters tightly and should be taken as the real small scale:

| Size | Uses | Typical job |
| --- | --- | --- |
| 13px | 88 | Nav links, body in cards, table cells |
| 14px | 47 | Buttons, inputs, list rows |
| 12px | 41 | Field labels, captions |
| 11px | 42 | Tags, meta rows, spec captions |
| 10px | 23 | Kickers, registration marks |

`h6` carries `0.08em` tracking and uppercase; kickers carry `0.1em`; the
monospace captions run `0.1`–`0.12em`.

---

## 5. Spacing, radius, elevation

Industry's density is 0.85×, already baked into the scale. Use the variables,
never raw numbers.

| Token | Value | | Token | Value |
| --- | --- | --- | --- | --- |
| `--space-1` | 3.4px | | `--radius-sm` | 2px |
| `--space-2` | 6.8px | | `--radius-md` | 4px |
| `--space-3` | 10.2px | | `--radius-lg` | 7px |
| `--space-4` | 13.6px | | | |
| `--space-6` | 20.4px | | `--shadow-sm` | `0 1px 2px` ink 14% |
| `--space-8` | 27.2px | | `--shadow-md` | `0 3px 10px` ink 16% |
| | | | `--shadow-lg` | `0 12px 32px` ink 22% |

**The radius tokens exist but are overridden to zero for every blueprint
object.** industry.css ends by resetting `.card, .btn, .input, .tag, .seg,
.dialog` to `border-radius: 0`. Square corners are the grammar; the radius
scale survives only for the few things outside it.

---

## 6. Component rules

| Rule | Detail |
| --- | --- |
| **The blueprint frame** | `.blueprint` + four `<i class="corner tl/tr/bl/br">`. 1px `--sl-border`, square, plus 11px `+` registration marks offset −6px outside the box. Every card, figure and primary button wears it |
| **Never drop the marks** | A framed element without its corners is not in the system |
| **Cards are line drawings** | Transparent, hairline-bordered. No surface fill. The solid primary button is the one deliberate exception |
| **Duotone** | Every content photograph goes through `.duotone` — desaturated and washed in the accent via `mix-blend-mode: color`. `.blueprint.duotone` must keep `overflow: visible` so the marks survive |
| **Icons** | Lucide at stroke-width **1.5**. Never thicker |
| **Focus** | `:focus-visible { outline: 2px solid; outline-offset: 2px }` — `--sl-focus` on paper, `--sl-focus-dark` on the field. Never the browser default |
| **Hover / active** | One ramp step past base. Never a signal colour — interaction feedback and measurement are different languages |
| **Disabled** | 45% opacity |
| **Selection** | Accent at 30% |

---

## 7. Assets

Settled at the Phase 0 checkpoint. Source files in `design-source/`.

| Asset | Ground | Where it goes |
| --- | --- | --- |
| `favicon-512/64/32/16.png` | Club, paper on field | **The favicon.** Shopify favicon slot, app `<link rel="icon">`, PWA manifest |
| `ci-mark-ink-512.png` | CI lockup, ink, transparent | OG images, print, email — light grounds |
| `ci-mark-ink-128.png` | Same, grooves dropped | Small light-ground uses |
| `ci-mark-paper-512.png` | CI lockup, paper, transparent | Reversed, on the field |
| `ci-favicon-64/32.png` | CI monogram, ink | Monogram favicon variant, where a monogram is wanted |
| `ci-favicon-paper-32.png` | CI monogram, paper | The same, on the field |
| `club-ink-512.png` / `club-paper-512.png` | Club alone | Marks that stand without the wordmark |
| `ci-favicon-16.png` | — | **Not shipped.** `7b`: at 16px use the club-only tile |

**The lockup itself is CSS, not an image.** No mockup screen loads a PNG; the
wordmark is one inline-block containing five absolutely-positioned spans —
grip, shaft, blade (a `clip-path` polygon), and two groove lines:

```
Caddie[ grip · shaft · blade · groove · groove ]nsight
```

Grip, shaft and blade take `currentColor`; the grooves take steel. Because the
silhouette inherits colour, **one snippet renders ink-on-paper and
paper-on-field with no variant** — which is why the reversed lockup needs no
second asset. Build it once per surface as a shared snippet.

At 18px and below, the grooves drop and grip/shaft/blade carry the silhouette.

### Retired filenames

`swinglab/web/static/` still holds `swinglab-favicon.png`, `swinglab-logo.png`
and `swinglab-logo-inverse.png`. A Shopify **Files** entry always beats a theme
asset of the same name, and the store still holds the v3 `swinglab-*` marks —
so never point at a retired filename expecting the theme copy to win.
`tests/test_theme_brand_filenames.py` holds the retired list; keep it current.

---

## 8. What this revamp actually changes

Most of the palette is already right. Worth stating plainly so effort lands
where it is needed:

**Already correct, do not touch** — the grounds, the three coloured roles, the
border split, the type families, the self-hosting, the spacing/radius/shadow
scales, `config.yaml`'s overlay colours, the field sub-palette.

**Changes in this revamp**

1. Add the **display type tier** (26–62px) the mockups introduce.
2. Add **DM Mono** to the documented token set, with its 207-use role.
3. Name `#070f0b` and its sub-palette as first-class tokens (already present
   as values; this is bookkeeping).
4. Install the **`favicon-*` set** in both favicon slots and the PWA manifest.
5. Build the **CSS lockup** as a shared snippet on each surface.
6. Retire `--sl-brand-green`.
7. Correct the app layout's font comment, which describes Archivo at length
   while the surface loads Barlow.

**Watch for** — `tests/test_theme_brand_filenames.py` and `make parity` will
both push back on any of this. That is what they are for.
