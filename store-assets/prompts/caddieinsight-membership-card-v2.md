# Membership card imagery v3 photoreal (filenames keep -v2)

These images are AI-generated campaign scenes. They are not customer photos,
testimonials, or analyzed swings. Use them as supporting membership-plan art
only, without representing the people or shown results as real customers.

Both Pro and Founders cards are 1536×1024. The homepage card's media area is
`aspect-ratio: 3 / 2` in `plans-band.liquid`, which matches them exactly, so
the full frame renders uncropped. Theme assets bind them via
`asset_img_url` in `plans-band.liquid`. Filenames keep the `-v2` suffix so
theme bindings and tests stay stable across this revamp.

## CaddieInsight Pro

- File: `out/caddieinsight-pro-card-v2.png` (also `storefront-theme/assets/`)
- Dimensions: 1536 x 1024
- SHA-256 prefix: `2ef55c5351f38fab`

Prompt summary: photoreal dawn covered-range editorial; adult golfer in
dark-green polo at iron follow-through; smartphone on tripod filming from
behind; cool mist fairway and pines; forest / charcoal / amber palette;
no text overlays, logos, or badges in the campaign frame.

## CaddieInsight Founders Pass

- File: `out/caddieinsight-founders-card-v2.png` (also `storefront-theme/assets/`)
- Dimensions: 1536 x 1024
- SHA-256 prefix: `10b68086ffc50919`

Prompt summary: photoreal private-club practice bay at blue-hour dusk;
adult golfer in charcoal knit at driver finish; warm clubhouse bokeh;
smartphone on tripod; exclusive lifetime-membership mood; same crop-safe
3:2 framing as Pro; no text overlays, logos, or badges.

The prior flat instrument-card Founders art (`founders_card.py`) remains as a
reproducible drawing utility, but the storefront ships the photoreal series so
Pro and Founders read as one premium campaign set with clearly different scenes.

## CaddieInsight Free

- File: `out/caddieinsight-free-card-v2.png`
- Remains the approachable photoreal free-tier companion from the earlier series.
