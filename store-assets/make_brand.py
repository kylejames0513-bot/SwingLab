"""Generate the shipped CaddieInsight brand and PWA icon set.

One command regenerates every mark the app and the storefront theme serve, so
the favicon, the installed-app icon, the iOS home-screen tile, and the header
lockup can never drift out of sync:

    python store-assets/make_brand.py

Outputs land in `store-assets/out/` (the reviewable source of truth) and are
copied into `swinglab/web/static/` and `storefront-theme/assets/` under the
caddieinsight-* names both surfaces actually serve. (They used to ship under
the retired swinglab-* names, which meant regenerating the brand updated
files nothing referenced while the served copies went stale.)

Requires the two fonts fetched by the commands in README.md.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from brand_mark import (  # noqa: E402
    GREEN, INK, IRON_W, MINT, STEEL, draw_mark, mark_svg,
)

OUT = HERE / "out"
STATIC = HERE.parent / "swinglab" / "web" / "static"
THEME = HERE.parent / "storefront-theme" / "assets"
MOBILE = HERE.parent / "mobile" / "assets"

DISPLAY_TTF = str(HERE / "BarlowCondensed-SemiBold.ttf")
BODY_TTF = str(HERE / "Barlow-Regular.ttf")
MONO_TTF = str(HERE / "DMMono-Regular.ttf")
SS = 4  # supersample factor; everything downscales with LANCZOS


def display(px: int):
    """Barlow Condensed SemiBold — the wordmark's own face.

    Barlow ships no variable font, so there is no set_variation_by_axes() to
    call here the way Archivo allowed: a weight is a file. That is the same
    constraint the web faces run under, which is convenient — the raster
    lockup and the CSS display voice are now the identical cut rather than
    two points on an axis that happened to be nearby.
    """
    return ImageFont.truetype(DISPLAY_TTF, px)


def tracked(draw, xy, text, font, fill, tracking=0):
    """Letter-spaced text (PIL has no tracking). Returns the drawn width."""
    x, y = xy
    for char in text:
        draw.text((x, y), char, font=font, fill=fill)
        x += draw.textlength(char, font=font) + tracking
    return x - xy[0] - tracking


def rounded_tile(size: int, radius_ratio: float, fill: str,
                 alpha: bool = True) -> Image.Image:
    mode = "RGBA" if alpha else "RGB"
    background = (0, 0, 0, 0) if alpha else fill
    img = Image.new(mode, (size * SS, size * SS), background)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(
        [0, 0, size * SS - 1, size * SS - 1],
        radius=radius_ratio * size * SS,
        fill=fill,
    )
    return img


def icon_png(path: Path, size: int, *, radius_ratio: float = 0.0,
             bold: float = 1.0, scale: float = 0.74, opaque: bool = False,
             padding: float = 0.0) -> None:
    """A tile with the mark centred on it.

    `padding` shrinks the mark inside the tile for maskable icons, whose
    outer 10% on every side can be cropped away by the launcher.

    The mark is sized by HEIGHT (`scale` is a fraction of the tile) rather
    than by a radius. The iron is a tall, narrow shape and a radius made every
    call site carry its own fudge factor.

    THE SIZE RULE IS APPLIED AT THE FINAL SIZE, NOT THE SUPERSAMPLED ONE. This
    is the whole reason groove_count() takes pixels: drawing at 4x and asking
    the geometry how many grooves fit would answer for a 2048px mark and then
    shrink three hairlines into one grey smear. So the mark is drawn on a 1x
    overlay, and only the tile is supersampled.
    """
    img = rounded_tile(size, radius_ratio, GREEN, alpha=not opaque)
    img = img.resize((size, size), Image.LANCZOS)
    d = ImageDraw.Draw(img)
    centre = size / 2
    draw_mark(d, centre, centre, size * scale * (1 - padding),
              ink=MINT, accent=STEEL, bold=bold)
    img.save(path, "PNG", optimize=True)
    print("wrote", path)


def wordmark(d, xy, height: int, ink: str, accent: str) -> float:
    """Draw "Caddie" + the iron + "nsight", and return the drawn width.

    The mark stands in for the "I": it is a letterform at reading size and a
    club at display size, which is the entire idea of the lockup. Its height
    is matched to the cap height rather than the em box, so it sits on the
    baseline with the letters instead of floating above them.
    """
    x, y = xy
    font = display(height)
    cap = font.getbbox("H")
    cap_top, cap_h = cap[1], cap[3] - cap[1]
    baseline = y + cap[3]

    d.text((x, y), "Caddie", font=font, fill=ink)
    x += d.textlength("Caddie", font=font)

    # 1.42x the cap height, standing ON the baseline — the proportion mockup
    # 6a/6b draws, where the club rises well above the cap line like an
    # ascender and its sole sits with the feet of the letters. The sole
    # cambers ~1.25% below the geometry box, so the box is lifted by that much
    # or the blade dips through the baseline the other letters sit on.
    mark_h = cap_h * 1.42
    gap = height * 0.05
    mark_w = mark_h * IRON_W
    x += gap
    draw_mark(d, x + mark_w / 2, baseline - mark_h / 2 - mark_h * 0.0125,
              mark_h, ink=ink, accent=accent)
    x += mark_w + gap

    d.text((x, y), "nsight", font=font, fill=ink)
    x += d.textlength("nsight", font=font)
    return x - xy[0]


def lockup(inverse: bool = False) -> Path:
    """The wordmark with the iron set into it, trimmed to its ink.

    There is no separate mark-then-words arrangement any more, and no amber
    underscore: the club IS the "I", and the grooves are the one accent the
    composition gets. That is a stronger lockup at header size because it
    cannot be split — the previous one degraded into a dial sitting next to
    some text whenever the two ended up on different lines.
    """
    ink = MINT if inverse else INK
    img = Image.new("RGBA", (2400, 420), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    wordmark(d, (40, 40), 300, ink, STEEL)

    img = img.crop(img.getbbox())
    k = min(1400 / img.width, 300 / img.height)
    img = img.resize((round(img.width * k), round(img.height * k)),
                     Image.LANCZOS)
    # The lockup draws "CaddieInsight" — it saves under the caddieinsight-*
    # names the app and theme serve. It used to save under the retired
    # swinglab-* names, so `make brand` regenerated a file nothing shipped
    # while the live caddieinsight-logo*.png (a one-time hand copy) went
    # stale. The generator and the served filename agree now.
    name = (
        "caddieinsight-logo-inverse.png" if inverse else "caddieinsight-logo.png"
    )
    path = OUT / name
    img.save(path, "PNG", optimize=True)
    print("wrote", path)
    return path


def og_card() -> Path:
    """The 1200x630 social share card.

    Rendered rather than photographed for now: it must stay legible as a
    ~300px thumbnail in a Slack unfurl, where a photo of a golfer becomes an
    unreadable smudge. Everything sits inside the centre 1000x500, because
    Slack, iMessage and X each crop this differently.
    """
    W, H = 1200, 630
    img = Image.new("RGB", (W, H), GREEN)
    d = ImageDraw.Draw(img)

    # The field's own hatch rather than a gradient wash: it is the texture the
    # reversed ground carries everywhere else, so the share card is recognisably
    # the same surface as the hero it links to.
    for step in range(-H, W + H, 11):
        d.line([(step, 0), (step + H, H)], fill="#0d1a13", width=2)

    # Sized so the wordmark terminates inside the centre 1000x500 safe box —
    # X and iMessage both crop tighter than the declared 1.91:1.
    wordmark(d, (100, 150), 120, MINT, STEEL)

    line = ImageFont.truetype(BODY_TTF, 40)
    d.text((100, 330), "One priority. One practice plan.", font=line, fill="#a8b3ac")
    d.text((100, 384), "Proof when you re-film.", font=line, fill="#a8b3ac")

    eyebrow = ImageFont.truetype(MONO_TTF, 22)
    tracked(d, (100, 476), "SWING ANALYSIS FROM ONE PHONE VIDEO",
            eyebrow, "#8f9a93", tracking=4)

    path = OUT / "og-caddieinsight.png"
    img.save(path, "PNG", optimize=True)
    print("wrote", path)
    return path


def main() -> None:
    OUT.mkdir(exist_ok=True)

    # Vector marks. `caddie-mark.svg` is the transparent mark for in-page
    # chrome; `pwa-icon.svg` keeps its tile because it doubles as the tab
    # favicon and the installed-app icon.
    #
    # The tiles are SQUARE now. Industry's whole geometry is square, and a
    # rounded app tile was the one place the old system still rounded — every
    # platform that wants a rounded icon (iOS, and Android via the maskable
    # variant) applies its own mask anyway, so the radius was being drawn
    # underneath a mask that discarded it.
    (OUT / "caddie-mark.svg").write_text(
        mark_svg(512, ink=INK, accent=STEEL, tile=None),
        encoding="utf-8",
    )
    print("wrote", OUT / "caddie-mark.svg")
    (OUT / "pwa-icon.svg").write_text(
        mark_svg(512, ink=MINT, accent=STEEL, tile=GREEN),
        encoding="utf-8",
    )
    print("wrote", OUT / "pwa-icon.svg")

    # Raster icons. groove_count() drops detail as the render shrinks, so the
    # blade never silts up — the size rule lives in the geometry, not here.
    icon_png(OUT / "caddieinsight-favicon.png", 512)
    icon_png(OUT / "pwa-icon-192.png", 192, bold=1.1)
    icon_png(OUT / "pwa-icon-512.png", 512)
    # Maskable: full-bleed tile, mark pulled inside the 80% safe circle.
    icon_png(OUT / "pwa-icon-maskable-512.png", 512, padding=0.22, bold=1.1)
    # iOS rounds the corners itself and rejects alpha, so this one is opaque.
    icon_png(OUT / "apple-touch-icon.png", 180, bold=1.15, opaque=True)
    # The native app icon. Same rule as the touch icon — the platforms mask it
    # themselves and an alpha channel is rejected outright by App Store
    # submission — at the 1024 the stores require.
    icon_png(OUT / "app-icon-1024.png", 1024, opaque=True)
    # The favicon sizes the mockups prove the mark at. 16 and 32 are where the
    # grooves are gone and only the silhouette is left, so they are generated
    # rather than left to the browser's downscale of the 512.
    for px in (16, 32, 64):
        icon_png(OUT / f"caddieinsight-favicon-{px}.png", px, bold=1.2)

    lockup(inverse=False)
    lockup(inverse=True)
    og_card()

    # Retired swinglab-* names are deliberately absent: shipping them kept
    # resurrecting a brand the product had left, and the theme packager
    # excludes them from the upload anyway.
    ship = {
        "caddieinsight-logo.png": (STATIC, THEME),
        # The inverse lockup is an app asset (service-worker precache); the
        # theme's night surfaces invert the standard mark with a CSS filter,
        # so nothing in the theme references this file.
        "caddieinsight-logo-inverse.png": (STATIC,),
        # The theme needs this too. It was pointing its apple-touch-icon at the
        # 512 favicon master, which carries an alpha channel iOS rejects — the
        # home-screen tile came out with a black or white box behind the mark
        # depending on the OS version. This one is 180 and opaque by
        # construction, which is the whole reason it is generated separately.
        "apple-touch-icon.png": (STATIC, THEME),
        "pwa-icon.svg": (STATIC,),
        "pwa-icon-192.png": (STATIC,),
        "pwa-icon-512.png": (STATIC,),
        "pwa-icon-maskable-512.png": (STATIC,),
        "app-icon-1024.png": (MOBILE,),
        "caddieinsight-favicon.png": (STATIC, THEME),
        # The sized set from mockup 7a, which asks for real exports at
        # 512/64/32/16 rather than one master the browser downscales. They were
        # generated above and shipped nowhere, so both surfaces served the 512
        # into a 16px tab slot — the exact case groove_count() exists to handle,
        # decided correctly at generation time and then thrown away.
        "caddieinsight-favicon-16.png": (STATIC, THEME),
        "caddieinsight-favicon-32.png": (STATIC, THEME),
        "caddieinsight-favicon-64.png": (STATIC, THEME),
        # Both surfaces, not just the theme. The share card is one of the
        # three images tests/test_app_storefront_parity.py holds to identical
        # bytes across the two surfaces, and shipping it to the theme alone
        # left the app serving the previous brand's card — invisible until
        # somebody shared an app link.
        "og-caddieinsight.png": (STATIC, THEME),
    }
    for name, targets in ship.items():
        for target in targets:
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(OUT / name, target / name)
            print("shipped", target / name)


if __name__ == "__main__":
    main()
