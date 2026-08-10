# Theme-rebuild specimens (2026-08-10)

Rendered evidence for the PR D theme rebuild, produced per
`docs/quality/local-visual-verification.md`: there is no local Liquid
renderer, so these are **specimens** — the theme's real `base.css` plus the
real section stylesheet blocks, applied to static markup that uses the
theme's own class names, with the self-hosted woff2 faces embedded as data
URIs (`document.fonts` verified 3 loaded before capture; Chromium at
`/opt/pw-browsers/chromium`).

- `after-specimen-1440.png` — desktop: rebuilt header (CTA + nav + cart),
  night hero with the signal card, section-head, the Free-first plans band
  with the Pro/Coach/Founders ladder, the membership buy box with its terms
  rail, FAQ, and the auth card.
- `after-specimen-390.png` — the same at phone width: the header CTA is
  present (the old theme hid it below 981px), the signal card adapts
  instead of vanishing, cards stack on the spacing scale.

What a specimen cannot show — real Liquid rendering, live product data,
section ordering from `index.json` — is verified on the unpublished-theme
preview per `dist/UPLOAD.md`. A live "before" capture was attempted and is
impossible from this container (headless Chromium cannot traverse the
egress proxy; the same limitation the doc records for Google Fonts).
