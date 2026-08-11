"""The dusk-range hero, re-cropped for the shape a phone actually has.

    python store-assets/phone_hero_crop.py

The portrait companion shipped as the full 1122 x 1402 frame, and two thirds
of it is empty sky. That is not a framing preference, it is a sizing bug:
`object-fit: cover` in a 390 x 1019 hero is narrower than the image is, so it
crops the WIDTH and shows the FULL height — every row of dead sky is rendered,
and the golfer is squeezed into the 32% of the frame that is left. Measured on
the phone specimen he came out 330 px tall inside a 1019 px hero, with his
right side sliced off by the crop and his legs behind the readout card.

Cropping the sky out of the source is the only lever that moves him. No CSS
can: `object-position`'s Y component is inert whenever cover is cropping the
X axis, which is the case at every phone width the theme supports.

    1122 x 1402  golfer 455 px = 32% of frame  ->  330 px rendered
    1122 x  932  golfer 500 px = 54% of frame  ->  546 px rendered

Same recipe of record as every other photograph on both surfaces
(`plan_card_webp.py`, `prompts/caddieinsight-tour-caddie-v4-photography.md`):
quality=82, method=6. No resampling happens here — a crop is exact — so the
LANCZOS filter those scripts use has nothing to do.

The 1122 px width is kept whole rather than trimmed to the visible window.
Cropping width would not make the golfer any bigger (the rendered scale is
fixed by the hero's height), and it would spend the panning room that
`object-position` needs to keep him framed across phone aspect ratios.

Output is a NEW immutable name. The v1 frame stays on both surfaces and in the
archive: an overwrite would bypass the theme preview and leave no rollback,
which `CLAUDE.md` rules out for anything the store serves.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image

HERE = Path(__file__).parent
OUT = HERE / "out"
THEME = HERE.parent / "storefront-theme" / "assets"
APP = HERE.parent / "swinglab" / "web" / "static"

SOURCE = "caddieinsight-premium-range-hero-mobile-2e4ee946.png"
ARCHIVE = "caddieinsight-premium-range-hero-phone-v2.png"
SHIPPED = "caddieinsight-range-hero-mobile-v2.webp"

# Top edge of the keep. The golfer's club arc peaks at y=735 in the source and
# his feet land at y=1235, so this leaves 265 px of sky above the arc — enough
# for the frame to still read as dusk rather than as a tight sports crop.
SKY_CUT = 470

QUALITY = 82
METHOD = 6


def main() -> int:
    with Image.open(OUT / SOURCE) as source:
        frame = source.convert("RGB")
        crop = frame.crop((0, SKY_CUT, frame.width, frame.height))

    crop.save(OUT / ARCHIVE, format="PNG", optimize=True)
    crop.save(THEME / SHIPPED, format="WEBP", quality=QUALITY, method=METHOD)
    shutil.copyfile(THEME / SHIPPED, APP / SHIPPED)

    shipped = (THEME / SHIPPED).stat().st_size
    print(f"{SHIPPED}: {crop.width}x{crop.height}, {shipped:,} B")
    print(f"  archived {ARCHIVE}")
    print(f"  copied to {APP / SHIPPED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
