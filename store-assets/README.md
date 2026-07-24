# SwingLab store assets

Brand and product artwork for the Shopify storefront, generated as code so
every image can be reproduced or retuned. The visual system ("Fairway
Modernism", see `PHILOSOPHY.md`) uses the same design tokens as the
storefront theme: warm off-white `#f7f5f0`, deep green `#14472c` /
`#1a5c38`, orange accent `#e8720c`, Archivo type — and draws golfers as
pose-estimation skeletons, the product's own visual language.

## What's here

| File(s) in `out/` | Used as |
| --- | --- |
| `product-*.png` (6) | Featured images on the six gear products |
| `product-pro.png`, `pro-report-strip.png`, `pro-overlay-detail.png`, `pro-plans.png` | The SwingLab Pro product gallery |
| `swinglab-logo.png`, `swinglab-logo-inverse.png`, `swinglab-favicon.png` | Theme logo (light + dark contexts) and favicon |
| `collection-gear.png` | SwingLab Gear collection image |
| `swinglab-hero.png` | Homepage hero background (Horizon theme) |
| `swinglab-report-band.png` | Homepage "Numbers you can act on" band |

All of these are already uploaded to the store's Shopify CDN and wired into
products, the SwingLab Gear collection, and the Horizon theme's settings
(`config/settings_data.json`) and homepage (`templates/index.json`). The
gear-product images are placeholders by design — dropshipped listings will
carry supplier photos once a supplier app is connected.

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
python3 pro_home_assets.py   # Pro gallery + homepage hero and report band
```

Everything renders supersampled and lands in `out/`. Palette, chrome, and
shared drawing helpers live in `make_assets.py`; the pose-skeleton golfer
(joint coordinates per swing position) lives in `pro_home_assets.py`.
