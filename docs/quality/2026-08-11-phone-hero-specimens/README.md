# Phone hero specimens (2026-08-11)

Rendered evidence for the phone-hero fix, produced per
`docs/quality/local-visual-verification.md`: there is no local Liquid
renderer, so these are **specimens** — the real `sections/hero.liquid`
stylesheet block plus the real `base.css` tokens, applied to static markup
carrying the section's own class names, with settings substituted from
`templates/index.json` and strings from `locales/en.default.json`. The
self-hosted woff2 faces are embedded as data URIs (`document.fonts` verified
3 loaded before capture); Chromium at `/opt/pw-browsers/chromium`; iPhone-class
viewport 390 × 844 at DPR 2, downscaled to 1x here.

- `before-phone-390.png` — the hero as the 2026-08-10 theme shipped it. The
  photograph is not dim, it is **gone**: `grayscale(.72) brightness(.66)`
  under a `.72–.94` scrim composites to the flat field colour. The section
  was paying 104 KB for a layer with nothing visible in it.
- `after-phone-390.png` — same section, same copy, same readout.

What changed, and why each part was necessary:

| | before | after |
| --- | --- | --- |
| **live trace box** | 308 × 64 px, artwork drawn at **25%** | 308 × 246 px, drawn at **94%** |
| golfer in the photograph | 330 px tall in a 1019 px hero | 546 px |
| phone asset | 1122 × 1402, 104 KB | 1122 × 932, 51 KB |
| worst-case type contrast | 2.4:1 (fine print, had the scrim been lifted alone) | 4.89:1 against a 4.5 target |

The **live trace** is the headline fix here. It is the thing the section is
for — the generative read built in `048951f` — and on a phone it was a
thumbnail. The still is drawn `preserveAspectRatio="meet"` from a 320 × 260
viewBox, so a 308 × 64 strip scaled the artwork to 25% and stranded a ~79 px
figure in a field of empty grid; `swing-trace.js` measures the same box, so
the animation was drawn just as small. The 64 px was there to protect the
fold and was buying nothing: the readout starts ~552 px down the phone hero,
below the fold at any height, while the primary CTA that the fold actually
has to protect sits at 310 px and does not move.

The asset had to be re-cropped; CSS alone could not do it. Two thirds of the
v1 portrait frame is empty sky, and on a phone `object-fit: cover` is narrower
than the image, so it crops the **width** and renders the full height — every
dead row is drawn, and `object-position`'s Y component is inert because Y is
not the axis being cropped. `store-assets/phone_hero_crop.py` cuts 470 px of
sky off the top, which is also where the 53 KB saving comes from.

Contrast is measured, not judged: the probe hides `.sl-hero__copy`,
screenshots the backdrop, and takes the **brightest** pixel inside each text
box against that text's colour. That is what forced two of the changes here —
`--sl-ink-muted`'s AA guarantee is stated against `--sl-bg`, and below 749px
the background is a lit dusk sky, so the chips and fine print move one rung up
the ink ramp; and the scrim's stops sit at the legibility boundary rather than
anywhere darker, easing off across the button band where two opaque
full-width controls already cover the photograph.

What a specimen cannot show — real Liquid rendering, section ordering from
`index.json`, the live header over the hero — is verified on the unpublished
theme preview per `dist/UPLOAD.md`. The app's landing hero takes the same
treatment and *was* rendered for real (FastAPI + Jinja, not a specimen);
it is not captured here because the storefront is the surface that regressed.
