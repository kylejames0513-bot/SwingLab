# CaddieInsight store assets

Brand and product artwork for the Shopify storefront. Most catalog and
technical artwork is generated as code so it can be reproduced or retuned;
approved campaign photography keeps its generation prompt and content hash
beside the output. The visual system ("Turf Instrument", see `PHILOSOPHY.md`)
uses the same design tokens as the storefront theme: cool mist `#eef2ef`,
deep forest `#0f3d28` / `#1a5c38`, orange accent `#e8720c`, Archivo type —
and draws golfers as pose-estimation skeletons, the product's own visual
language.

## What's here

| File(s) in `out/` | Used as |
| --- | --- |
| `product-*.png` (6) | Featured images on the six gear products — instrument-sheet style: dimension lines, cross-section insets, spec footers |
| `drill-*.png` (5) | Second gallery image per training aid: the drill it trains, with setup measurements and protocol |
| `detail-cap.png` | Second gallery image on the cap: flat-lay construction study |
| `product-pro.png`, `pro-report-strip.png`, `pro-overlay-detail.png`, `pro-plans.png` | The CaddieInsight Pro product gallery |
| `caddieinsight-pro-card-v2.png`, `caddieinsight-founders-card-v2.png`, `caddieinsight-free-card-v2.png` | Photoreal membership cards bound in `storefront-theme/assets` via `plans-band.liquid` |
| `swinglab-logo.png`, `swinglab-logo-inverse.png`, `swinglab-favicon.png` | Theme logo (light + dark contexts) and favicon — Tour Caddie v3 flagstick lockup |
| `collection-gear.png` | CaddieInsight Gear collection image |
| `caddieinsight-premium-range-hero-0852e38d.png` | Premium desktop homepage hero campaign image |
| `caddieinsight-premium-range-hero-mobile-2e4ee946.png` | Purpose-built portrait companion for the mobile homepage hero |
| `swinglab-report-band.png` | Homepage "Numbers you can act on" band |
| `banner-method.png` | "The CaddieInsight Method" page banner — four-position swing frieze |
| `banner-about.png` | "About CaddieInsight" page banner — instrument-bench still life |
| `og-swinglab.png` | Social share card (og:image), 1200×630 |

Generator outputs intentionally keep their original `swinglab-`/`og-swinglab`
names so local builds stay reproducible. Shopify releases use new, immutable
CDN filenames and update the reviewed theme source to those release names.

The first batch is already uploaded to the store's Shopify CDN and wired into
products, the CaddieInsight Gear collection, and the Horizon theme's settings
(`config/settings_data.json`) and homepage (`templates/index.json`); the
drill/banner/og set is the second upload batch. The gear-product images are
placeholders by design — dropshipped listings will carry supplier photos once
a supplier app is connected.

Do not replace a Shopify File name referenced by the current live theme; that
would bypass the unpublished-theme preview and weaken rollback.

The premium range hero pair was created with the built-in image generation
workflow. Its approved prompts, dimensions, hashes, and disclosure boundary
are recorded in `prompts/caddieinsight-premium-range-hero-v2.md`.

## Regenerating

The two fonts are not committed; fetch them next to the scripts first:

```bash
curl -sSL -o Archivo-var.ttf \
  "https://raw.githubusercontent.com/google/fonts/main/ofl/archivo/Archivo%5Bwdth%2Cwght%5D.ttf"
curl -sSL -o DMMono-Regular.ttf \
  "https://raw.githubusercontent.com/google/fonts/main/ofl/dmmono/DMMono-Regular.ttf"
```

Then (needs Pillow, which the main package already depends on):

```bash
python3 make_assets.py       # gear products, logo, favicon, collection banner
python3 campaign_assets.py   # drill diagrams, cap study, page banners, og card
python3 pro_home_assets.py   # Pro gallery + homepage hero and report band
```

Everything renders supersampled and lands in `out/`. Palette, chrome, and
shared drawing/drafting helpers (dimension lines, callouts, insets, arrows)
live in `make_assets.py`; the pose-skeleton golfer (joint coordinates per
swing position) lives in `pro_home_assets.py`; `campaign_assets.py` imports
from both.
