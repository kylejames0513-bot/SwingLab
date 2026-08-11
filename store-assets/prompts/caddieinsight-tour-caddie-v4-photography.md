# Tour Caddie v4 — campaign photography specs

Ready-to-run prompts for the five photographic surfaces. The mark, type, and
layout moved to Tour Caddie v4 in code; this is the matching photography, and
it is the only part of the revamp that still needs a human with an image tool.

**Why these five.** They are the images a visitor forms an impression from
before reading a word: the hero on both the app landing and the storefront,
the three membership cards in the plans band, and the card that renders when
someone pastes a link into Slack or iMessage.

## Ground rules

These are **AI-generated campaign scenes**. They are not customer photos,
testimonials, or analyzed swings, and nothing in them may be presented as a
real customer or a real result. Two rules follow from that and apply to every
prompt below:

- No text, numbers, logos, badges, watermarks, or UI overlays baked into the
  frame. Every label on the site is live HTML over the image — baked text
  cannot be translated, cannot meet contrast requirements, and reads as a
  fabricated screenshot.
- No scoreboard, launch monitor readout, or on-screen metric. Implying a
  measured result the product did not produce is the one thing this brand
  cannot do.

Keep faces either turned away, in profile, or motion-blurred at follow-through.
It suits the "one golfer, mid-move" framing and avoids a synthetic face
carrying the brand.

## Palette

Give the model the hexes; do not describe the colours in words alone.

| Role | Hex |
| --- | --- |
| Cool mist field | `#eef2ef` |
| Deep forest | `#0f3d28` |
| Forest mid | `#1a5c38` |
| Night | `#06110c` |
| Ink | `#101a14` |
| Amber accent (sparingly — one gesture per frame) | `#e8720c` |
| Warm highlight | `#ffad62` |

One amber gesture per composition: a sunrise rim, a range-light flare, a
jacket zip. It is the same kinetic accent as the dial's reading run. Two amber
sources in one frame reads as decoration rather than signal.

---

## 1. Hero — desktop

- **File:** `caddieinsight-range-hero-desktop.webp` (theme) and
  `caddieinsight-range-hero.webp` (app static) — same image, two names
- **Canonical size:** 1672 × 941 (16:9)
- **Where:** `storefront-theme/sections/hero.liquid`, and
  `swinglab/templates/web_login.html.j2` as the signed-out landing hero
- **Crop safety:** copy sits over the **left 55%**. Keep that region visually
  quiet — no subject, no high-frequency detail. The golfer belongs
  right-of-centre. The bottom 20% is overlaid by the CTA stack, so keep it
  dark and low-contrast.

> Photoreal editorial photograph of a covered practice range at first light.
> A single adult golfer, seen from three-quarters behind at the top of an iron
> follow-through, stands right of centre. A smartphone on a low tripod films
> them from behind, clearly a phone and not a broadcast camera. Cool mist
> hangs over the fairway; dark pines recede into fog. Deep forest greens
> (#0f3d28, #1a5c38) dominate; a single warm amber sunrise flare (#e8720c)
> breaks through the pines on the right edge. The left 55% of the frame is
> open, quiet, atmospheric fog with no subject. Shot on a 35mm lens at f/2,
> natural light, fine grain, muted contrast, editorial colour grade.
> Photographic, not illustrated.
>
> **Negative:** text, numbers, logos, watermarks, scoreboards, launch monitor
> screens, UI overlays, crowds, multiple golfers, mid-day sun, saturated
> greens, HDR, lens flare across the left third.

**Alt text:** `A golfer filming an iron swing on a misty practice range at
dawn, phone mounted on a low tripod behind them.`

## 2. Hero — mobile

- **File:** `caddieinsight-range-hero-mobile-v2.webp` (both theme assets and
  app static). The `-v2` suffix is the 1122 × 932 crop that both surfaces
  bind; the uncropped 1122 × 1402 `caddieinsight-range-hero-mobile.webp`
  remains the source of record and the rollback frame.
- **Canonical size:** 1122 × 932 shipped, from a 1122 × 1402 (4:5) source
- **Crop safety:** this is a **purpose-built portrait companion**, not a crop
  of the desktop frame — the subject must be re-composed for vertical. Copy
  sits over the **bottom 60%**, so put the golfer in the **upper third** and
  keep everything below the waistline dark and quiet.
- **The delivered frame did not meet that brief, and it cost the section.**
  The golfer landed at 55–87% down the frame with two thirds sky above him.
  On a phone `object-fit: cover` is narrower than the image, so it crops the
  WIDTH and renders the full height: every dead row was paid for, the golfer
  rendered 330 px tall inside a 1019 px hero, and his legs sat behind the
  readout card. No CSS can correct this — `object-position`'s Y component is
  inert whenever cover is cropping X, which is every phone width the theme
  supports. `store-assets/phone_hero_crop.py` cuts 470 px of sky off the top
  and takes him to 54% of the frame. **Re-composing to the brief above is
  still the better fix; the crop is what the delivered art allows.**

> Photoreal vertical editorial photograph of a covered practice range at first
> light. A single adult golfer at the top of an iron follow-through occupies
> the upper third of the frame, seen from three-quarters behind. Below them the
> frame falls away into deep shadow and cool mist over the fairway — quiet,
> unoccupied, low contrast. Dark pines in fog behind. Deep forest greens
> (#0f3d28, #1a5c38); one warm amber sunrise flare (#e8720c) at the upper
> right. Shot on a 35mm lens at f/2, natural light, fine grain, muted contrast,
> editorial colour grade. Photographic, not illustrated.
>
> **Negative:** same as the desktop hero, plus: subject centred, subject in the
> lower half, busy foreground.

**Alt text:** same as desktop.

## 3. Pro membership card

- **File:** `caddieinsight-pro-card-v2.png` — keep the `-v2` suffix; the theme
  binds it by name in `plans-band.liquid` and tests assert on it
- **Canonical size:** 1536 × 1024 (3:2)
- **Crop safety:** none needed vertically — `.sl-plans__media` is
  `aspect-ratio: 3 / 2` in `plans-band.liquid`, which matches the source
  exactly, so the full frame is shown. Still keep the subject off the outer 5%
  so the rounded card corners do not clip anything that matters.

Pro and Founders must read as **clearly different scenes** — different time of
day, different location. If they look like two frames from one shoot, the
plans band reads as filler.

> Photoreal editorial photograph of a public covered driving range at dawn. An
> adult golfer in a dark forest-green technical polo at the finish of an iron
> swing, weight through the front foot, seen from three-quarters front with the
> face turned away from camera. A smartphone on a tripod films from the
> face-on position. Cool morning mist, wet matting, distant yardage flags out
> of focus. Deep forest and charcoal palette (#0f3d28, #101a14) with one amber
> sunrise rim light (#e8720c) along the golfer's shoulder. Shot on an 85mm lens
> at f/2, natural light, fine grain, editorial colour grade.
>
> **Negative:** text, numbers, logos, badges, watermarks, launch monitor
> readouts, scoreboards, UI overlays, direct eye contact, studio lighting,
> saturated colour.

**Alt text:** `A golfer at the finish of an iron swing on a misty dawn range,
filmed face-on by a phone on a tripod.`

## 4. Founders Pass membership card

- **File:** `caddieinsight-founders-card-v2.png` — keep the `-v2` suffix
- **Canonical size:** 1536 × 1024 (3:2), same full-frame fit as Pro

Deliberately the opposite end of the day and a private setting, so the tier
difference is legible at a glance rather than stated.

> Photoreal editorial photograph of a private golf club practice bay at
> blue-hour dusk. An adult golfer in a charcoal merino quarter-zip at the
> finish of a driver swing, seen in profile. Warm clubhouse windows glow out of
> focus behind them (#ffad62 bokeh). A smartphone on a tripod films from the
> side. Manicured turf, timber bay dividers, deep blue evening sky fading to
> night (#06110c). Restrained, exclusive, quiet — a members-only hour. Shot on
> an 85mm lens at f/1.8, available light, fine grain, editorial colour grade.
>
> **Negative:** text, numbers, logos, badges, watermarks, launch monitor
> readouts, luxury-brand cues, gold, crowds, direct eye contact, HDR.

**Alt text:** `A golfer at the finish of a driver swing in a private club
practice bay at dusk, filmed from the side by a phone on a tripod.`

## 5. Free membership card

- **File:** `caddieinsight-free-card-v2.png` — keep the `-v2` suffix
- **Canonical size:** 1536 × 1024 (3:2), same full-frame fit as Pro

Approachable and daylit. It should not look like a lesser version of Pro — it
is a different, earlier moment in the same golfer's week.

> Photoreal editorial photograph of an open municipal driving range on an
> overcast afternoon. An adult golfer in a light grey t-shirt mid-backswing
> with a mid-iron, seen from three-quarters behind. A smartphone leans against
> a small range bucket, filming — casual, improvised, no equipment. Flat soft
> daylight, cool mist-grey sky (#eef2ef), green turf (#1a5c38), a single amber
> range marker (#e8720c) in the middle distance. Shot on a 50mm lens at f/2.8,
> natural light, fine grain, muted editorial grade.
>
> **Negative:** text, numbers, logos, badges, watermarks, launch monitor
> readouts, premium/luxury cues, tripods, sunshine, saturated colour.

**Alt text:** `A golfer mid-backswing at an open range on an overcast day,
filmed by a phone propped against a range bucket.`

## 6. Social share card (og:image)

- **File:** `og-swinglab.png` — the name is historical; keep it, the theme
  binds `images['og-swinglab.png']` in `theme.liquid`
- **Canonical size:** 1200 × 630
- **Crop safety:** Slack, iMessage, and X each crop this differently. Keep the
  subject inside the **centre 1000 × 500**.

The current file is a drawn card, not a photograph. A photographic OG image is
the single highest-leverage upgrade here — it is what people see before they
have seen anything else.

> Photoreal editorial photograph, wide 1.91:1 crop, of a covered practice range
> at dawn with a single adult golfer at the top of the backswing, centred, seen
> from three-quarters behind, and a smartphone on a low tripod filming them.
> Cool mist, dark pines, deep forest palette (#0f3d28, #06110c) with one amber
> sunrise flare (#e8720c). Generous quiet space above and below the subject.
> Shot on a 35mm lens at f/2, natural light, fine grain, muted editorial grade.
>
> **Negative:** text, numbers, logos, watermarks, UI overlays, borders, crowds,
> centre-cropped subject touching the frame edges.

**Alt text:** `A golfer at the top of the backswing on a misty dawn range, a
phone on a tripod filming from behind.`

---

## Dropping them in

Heroes ship as WebP, cards and the OG image as PNG. Generate at or above the
canonical size, then convert and place:

```bash
# from the repository root, per image
python3 - <<'PY'
from PIL import Image
src, dst, size = "new-hero.png", "swinglab/web/static/caddieinsight-range-hero.webp", (1672, 941)
im = Image.open(src).convert("RGB").resize(size, Image.LANCZOS)
im.save(dst, "WEBP", quality=82, method=6)
print("wrote", dst, im.size)
PY
```

Both heroes and all three cards live in **two** places and must stay
byte-identical:

| Image | Copies |
| --- | --- |
| `caddieinsight-range-hero.webp` | `swinglab/web/static/` |
| `caddieinsight-range-hero-desktop.webp` | `storefront-theme/assets/` (same image as above) |
| `caddieinsight-range-hero-mobile-v2.webp` | `swinglab/web/static/` **and** `storefront-theme/assets/` — the crop both surfaces bind |
| `caddieinsight-range-hero-mobile.webp` | `swinglab/web/static/` **and** `storefront-theme/assets/` — uncropped source, no longer bound |
| `caddieinsight-*-card-v2.png` | `store-assets/out/` **and** `storefront-theme/assets/` |
| `og-swinglab.png` | `store-assets/out/` |

Then confirm every binding still resolves and nothing changed shape:

```bash
python3 -m pytest tests/test_premium_storefront.py tests/test_premium_landing.py \
                  tests/test_storefront_mobile_regressions.py -q
```

Record the new SHA-256 prefixes in
`caddieinsight-membership-card-v2.md` alongside the existing ones, so a future
reader can tell which generation shipped.

## Not covered here

The six gear product images (`product-*.png`, `drill-*.png`, `detail-cap.png`)
are deliberately drawn, not photographed — they are placeholders until a
supplier app supplies real product photography. Do not replace them with
generated photos; a generated photo of a physical product someone can buy is a
different and worse problem than an obvious illustration.
