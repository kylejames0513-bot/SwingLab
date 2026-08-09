"""Plan-card photography → the webp ladder the plans band actually serves.

    python store-assets/plan_card_webp.py

The three membership cards are photographs, and they shipped as
uncompressed PNG: 6.0 MB of theme asset sitting on the one section where a
visitor decides to pay. The dusk-range hero already proved what a photograph
costs when it is encoded as one — 1672 x 941 of webp in 95.6 KB — so this is
the same recipe (`quality=82, method=6`, LANCZOS downscale) recorded in
`prompts/caddieinsight-tour-caddie-v4-photography.md` for the heroes, applied
to the cards.

Why a ladder rather than one file. Theme-packaged webps have to be served
whole with `asset_url`: `asset_img_url` is the legacy sizing filter, it does
not process webp, and it emits the no-image placeholder instead — the same
trap `sections/hero.liquid` documents. So the responsive candidates the
browser chooses between cannot be generated on the CDN; they are pre-encoded
here. The three rungs cover every render size the plans band asks for:

    480   the free band's thumbnail (200 CSS px) at 2x
    960   a paid card (396 CSS px) at 2x, the free band at 3x
   1536   a paid card at 3x, and the tablet breakpoint's 560 CSS px at 2x;
          also the native size, so nothing is ever upscaled

All three plans get the same ladder on purpose. The free band renders smaller
than the paid cards, but `sizes` is what decides which rung is fetched — an
identical ladder costs nothing at runtime and stops the free card from being
the one that cannot keep up if the band is ever widened.

The PNGs stay where they are. They are the source of record, they remain the
input to this script, and `store-assets/out/` keeps the archive copy. The webp
rungs are pure derivatives — regenerable by re-running this — so they are
written straight to the theme rather than archived a second time.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

HERE = Path(__file__).parent
OUT = HERE / "out"
THEME = HERE.parent / "storefront-theme" / "assets"

# The approved campaign photographs, by the names the theme binds.
CARDS = (
    "caddieinsight-pro-card-v2.png",
    "caddieinsight-founders-card-v2.png",
    "caddieinsight-free-card-v2.png",
)

WIDTHS = (480, 960, 1536)
QUALITY = 82   # the hero recipe; visually lossless at these render sizes
METHOD = 6     # slowest/densest encode — this runs once, the page runs forever


def rung_name(png_name: str, width: int) -> str:
    """`caddieinsight-pro-card-v2.png` + 960 → `…-card-v2-960.webp`.

    A new filename per rung, never an overwrite: a Shopify Files entry beats
    a theme asset of the same name, so replacing art in place would bypass
    the unpublished-theme preview and leave nothing to roll back to.
    """
    return f"{Path(png_name).stem}-{width}.webp"


def convert(png_name: str) -> list[tuple[str, int, int]]:
    source = OUT / png_name
    original = Image.open(source).convert("RGB")
    written = []
    for width in WIDTHS:
        height = round(original.height * width / original.width)
        rung = (
            original
            if width == original.width
            else original.resize((width, height), Image.LANCZOS)
        )
        target = THEME / rung_name(png_name, width)
        rung.save(target, "WEBP", quality=QUALITY, method=METHOD)
        written.append((target.name, width, target.stat().st_size))
        print("wrote", target, f"{rung.size[0]}x{rung.size[1]}",
              f"{target.stat().st_size:,} B")
    return written


def main() -> None:
    THEME.mkdir(parents=True, exist_ok=True)
    for png_name in CARDS:
        before = (OUT / png_name).stat().st_size
        rungs = convert(png_name)
        largest = max(size for _, _, size in rungs)
        ladder = sum(size for _, _, size in rungs)
        print(
            f"{png_name}: {before:,} B PNG → {largest:,} B at full width "
            f"({100 - round(largest * 100 / before)}% smaller), "
            f"{ladder:,} B for the whole ladder"
        )


if __name__ == "__main__":
    main()
