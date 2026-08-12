# INDUSTRY — the blueprint overhaul

2026-08-11. Replaces the INSTRUMENT system of 2026-08-10
(`2026-08-10-instrument-design-overhaul-design.md`), which it inverts.

## Why

The Claude Design project *CaddieInsight Mockups* proposed a complete
replacement, not a retheme: it inverts the ground, changes both typefaces,
changes the accent, squares every corner and redraws the brand mark. The
direction is a **blueprint / spec-sheet grammar** — paper ground, ink type,
Barlow Condensed over Barlow, a steel accent, and every card, figure and
framed object drawn as a wireframe: square, hairline, with `+` registration
marks at the corners. CaddieInsight's deep green survives as **the one
reversed field**.

Three decisions were taken before any code moved:

1. **Paper on both surfaces.** Green is a field, not a theme.
2. **The two-signal rule survives, respent onto steel.** It is the most
   valuable thing in the previous system.
3. **The new mark is ported into `brand_mark.py`**, not imported as PNGs, so
   the Pillow and SVG renderers stay one geometry.

## The colour system, and why it is stronger than what it replaced

| Role | Value | Job |
| --- | --- | --- |
| steel | `#5980a6` | STRUCTURE — chrome, kickers, active nav, marks |
| steel deep | `#416180` | THE SIGNAL: a value the engine MEASURED |
| steel lit | `#94bce3` | THE TRACE: the LIVE READOUT |

Two changes from INSTRUMENT, both load-bearing.

**A third colour exists so emphasis has somewhere to go.** Amber was eroded
three separate times — the header CTA, `.sl-eyebrow` in every section, 25
button hovers across 16 files — for one reason: it was the most attractive
thing on a near-black page, and every time something needed emphasis it was
the obvious reach. Steel absorbs that pressure without being a signal.

**Each signal is confined to one ground by contrast, not only by discipline.**

| | on paper | on the field |
| --- | --- | --- |
| signal `#416180` | **5.78** | 3.00 |
| trace `#94bce3` | 1.78 | **9.76** |

Put either on the wrong surface and it stops being legible immediately. That
is a physical floor under the rule that the old palette never had — amber was
perfectly readable everywhere it was misused, which is exactly why misuse kept
happening.

The consequence is a rule worth stating explicitly: **on the field, a measured
value is set in `--sl-field-ink` at display or mono weight.** Loudness comes
from the ground rather than from a hue. The hero readout, the pass mark, the
sample art and the runtime overlay tints all follow it, and their tests pin
the field ink so a later pass cannot "restore the signal" onto green.

### One deviation from the source system

Industry's own readme makes the primary button a solid `--color-accent` fill
with `--color-bg` text. That pair is 3.71:1, so the mockups' own button labels
fail AA at 500 weight. The fill drops one ramp step to `#2c455d` (8.87) and
keeps the shape. `tests/test_premium_accessibility.py` exists for this.

## Technique: redefine, don't rename

The `--sl-*` tokens are named for the ROLE they fill, so redefining the values
inverts ~69 files at once. This is the second time it has worked, and the
reason is the same both times. Renaming would touch ~1,400 call sites to buy
nothing.

Roles that flipped and needed a hand, not just a value:

- `--sl-bg-card` stops being a fill — Industry's cards are line drawings.
- `--sl-night` inverts from a darker well to a *tinted recess*. Same job
  (inset, code, table stripe), opposite direction. Every surface that used it
  as "dark" — the announcement bar, the app banner, the hero readout panel,
  the trace tile — became a near-white box floating on a dark ground.
- `--sl-paper*` collapses into the ground; its four call sites keep working.
- `--sl-danger` returns to `#b3261e`, which the previous pass had to abandon
  at 2.97:1 and is 5.84 on paper.
- **`--sl-wash-rgb` is new and is the one that mattered.** A low-alpha wash
  means "the colour that contrasts with the ground". 104 hairlines were
  written as `rgba(--sl-cream-rgb, .03)` for a dark page; on paper they paint
  white on white. The 23 high-alpha uses are type on the FIELD and stay
  cream. **This cannot be done by threshold** — which ground a rule sits on is
  not something a script can determine — so it was done per file as each
  surface was converted.

New tokens: `--sl-field*` (the reversed ground finally has a name, because it
used to be the whole page), `--sl-band` (the announcement strip, deliberately
NOT the field — the field carries evidence and a marketing bar borrowing it
would dilute what a dark surface means), `--sl-hatch` / `--sl-rules` (the two
textures), `--sl-steel`, `--sl-weight-*`.

## Type: a variable font was doing more than anyone noticed

Barlow has **no variable font**. Google serves it static at v13 and the css2
endpoint rejects a range outright — `Barlow:wght@400..700` returns an HTML
error page, not a stylesheet. So a weight is a 22 KB file.

Archivo's variable file had made seven weights cost the same as one, and 157
declarations duly accumulated across the two surfaces. Under Industry the
display voice is a different *family*, so the interface only needs two:

    600 · Barlow Condensed   every heading and display number
    500 · Barlow             interface emphasis, buttons, labels
    400 · Barlow             body copy

124 declarations at 600/650/700/750/800 were resolved to 500 or the display
face, classified by whether their enclosing rule named `--sl-font-display`.

96,320 bytes against a 100 KB gate. That 3.7 KB of headroom is deliberate: a
fourth weight is a whole extra file somebody has to argue for, and the answer
is nearly always the display face, which is already loaded.

The display rungs went back **up** ~20%. They had been cut because Archivo
Expanded ate line width; condensed does the opposite.

## The mark

A drawn iron whose home is INSIDE the wordmark, standing in for the "I" —
"Caddie" + iron + "nsight". The club is the letter, not an ornament beside it,
which also fixes a failure mode the old lockup had: a mark next to text
degrades the moment the two wrap onto different lines.

Geometry is traced from mockup 6a/6b's Mark B at 60×128 and normalised by its
height. Two rules live in the geometry rather than in the callers:

- `groove_count()` drops 3 → 1 → 0 grooves as the render shrinks, and takes
  the FINAL pixel height. `icon_png` therefore draws the mark at 1× over a
  supersampled tile — supersampling the mark would ask the size rule about a
  2048px club and then shrink three hairlines into one grey smear.
- Minimum stroke widths hold the grip, shaft and hosel open at favicon size.
  Mockup 7 asks for these by name; at 16px the shaft computes to 0.6px.

Arcs (the toe radius, the cambered sole) are sampled into the shared point
list rather than handed to each renderer's arc call, because Pillow and SVG
have incompatible arc APIs and a mark defined partly by "whatever the renderer
draws" can drift between them.

Two errors were caught by rendering it rather than reading it: the toe is the
LEFT end (radiusing the wrong one rounded off the heel and left the toe a
spike), and a symmetric hosel flare puts a spur past the heel edge.

## What got deleted

- **The 8px technical grid**, page-wide on both surfaces plus six local
  repaints. It existed to stop a near-black page reading as a dark theme;
  paper has no such problem, and a grid under a light ground reads as graph
  paper the content is fighting. Texture belongs to objects now.
- **The header overlay.** Four things had to stay in sync — a Liquid flag, a
  pull-up margin per breakpoint, a signed-in variant adding the member rail's
  52px, and a scroll listener — and the pull-up had to be re-derived every
  time the bar's height changed. Mockup 2a stacks them instead.
- **The dark "premium" chrome**, 31 rules in the app and 24 in the theme. The
  app applied it unconditionally, so the premium variant *was* the app header
  and the base rule underneath had been unreachable long enough to still carry
  a pre-inversion background. `premium_header` survives on the storefront,
  where it selects which navigation a page shows — product logic that merely
  used to also carry a colour.
- **Inset lit top edges** and most drop shadows. Line drawings don't cast.

Two things were rescued on the way out rather than deleted with the treatment
they happened to live under: the compact 64px phone header, and the
mobile-menu CTA contrast gate (retargeted to `.sl-header__cta`, which carries
the pair now).

## Generated imagery is part of the palette

This is where a rebrand rots. `report.html.j2` — the plain report — had never
been inverted *at all*: it was still the warm cream document from two brands
ago, with an orange signal and a page-wide orange wash, and its own test had
been re-pinning that palette on every run.

Everything drawn moved with the system: `make_assets.py`'s palette (which
`campaign_assets.py` and `pro_home_assets.py` import), the runtime overlay
tints in `config.yaml`, `swing-trace.js`, and the sample art in `sample.py`.

The overlay tints are the interesting case. They land on *phone video*, which
is dark far more often than not, so they follow the FIELD's rule rather than
the page's: measured reads as paper, the live read as the lit trace. The steel
signal is deliberately absent — it is 3.00 on the field and would sink into
the footage.

**Alt text that names a colour is part of the palette.** `sample.py`'s
illustration alt text said "an orange head marker... the green starting zone".
Leaving that after the marks became paper and steel describes a picture that
is not there, for the readers who depend on it most, and silently — the
rendered page still looks right to everyone else.

## What this cost, and the one lesson worth keeping

A page-wide wash of the signal colour turned out to exist on three surfaces
(both reports and the old body rule). It was teaching readers that the signal
meant "background", on the documents where it has to mean "measured".

And the lesson: **all 70 design-gate tests passed while the app's hero
rendered ink-on-near-black and its nav was invisible.** The tokens were
correct; the *grounds* were not, and no pin was watching a ground. The gates
caught real regressions later in the same work — a 404 behind the password
wall, an og card shipping to one surface only, a mobile header height about to
be deleted — so they earn their keep. They just cannot see a page. Run the app
and look at it.
