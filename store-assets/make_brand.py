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

from brand_mark import GREEN, MINT, ORANGE, draw_mark, mark_svg  # noqa: E402

OUT = HERE / "out"
STATIC = HERE.parent / "swinglab" / "web" / "static"
THEME = HERE.parent / "storefront-theme" / "assets"
MOBILE = HERE.parent / "mobile" / "assets"

ARCHIVO = str(HERE / "Archivo-var.ttf")
SS = 4  # supersample factor; everything downscales with LANCZOS


def archivo(px: int, weight: int = 600, width: int = 100):
    font = ImageFont.truetype(ARCHIVO, px)
    font.set_variation_by_axes([weight, width])
    return font


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


def icon_png(path: Path, size: int, *, radius_ratio: float, ticks: int,
             bold: float, scale: float = 0.315, opaque: bool = False,
             padding: float = 0.0, cup: bool = True) -> None:
    """A tile with the mark centred on it.

    `padding` shrinks the mark inside the tile for maskable icons, whose
    outer 10% on every side can be cropped away by the launcher.
    """
    img = rounded_tile(size, radius_ratio, GREEN, alpha=not opaque)
    d = ImageDraw.Draw(img)
    centre = size * SS / 2
    radius = size * SS * scale * (1 - padding)
    draw_mark(d, centre, centre, radius, ink=MINT, accent=ORANGE, bold=bold,
              ticks=ticks, cup=cup)
    img.resize((size, size), Image.LANCZOS).save(path, "PNG", optimize=True)
    print("wrote", path)


def lockup(inverse: bool = False) -> Path:
    """Horizontal mark + wordmark, trimmed to its ink and scaled to fit the
    header's 38px logo slot at 3x for retina.

    The amber underscore under "Insight" is the same kinetic accent as the
    dial's reading run — one orange gesture per composition.
    """
    ink = MINT if inverse else GREEN
    img = Image.new("RGBA", (2400 * SS, 420 * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 24 ticks, not 36: the header renders this lockup at 38px tall, where a
    # finer fan silts up into a grey ring.
    cx, cy, r = 200 * SS, 210 * SS, 150 * SS
    draw_mark(d, cx, cy, r, ink=ink, accent=ORANGE, bold=1.1, ticks=24)

    font = archivo(int(168 * SS), 760, 100)
    x0, y0 = 430 * SS, 108 * SS
    tracking = int(-2.5 * SS)
    w1 = tracked(d, (x0, y0), "Caddie", font, ink, tracking=tracking)
    x1 = x0 + w1 + tracking
    w2 = tracked(d, (x1, y0), "Insight", font, ink, tracking=tracking)
    underline_y = y0 + 196 * SS
    d.rounded_rectangle(
        [x1, underline_y, x1 + w2, underline_y + 7 * SS],
        radius=4 * SS,
        fill=ORANGE,
    )

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
    img = Image.new("RGB", (W * SS, H * SS), GREEN)
    d = ImageDraw.Draw(img)

    # A quiet forest wash so the flat tile does not read as a placeholder.
    for step in range(H * SS):
        ratio = step / (H * SS)
        d.line(
            [(0, step), (W * SS, step)],
            fill=(
                int(6 + 9 * (1 - ratio)),
                int(17 + 44 * (1 - ratio)),
                int(12 + 28 * (1 - ratio)),
            ),
        )

    draw_mark(d, 218 * SS, 232 * SS, 104 * SS, ink=MINT, accent=ORANGE,
              bold=1.05, ticks=24)

    # Sized so the wordmark terminates inside the centre 1000x500 safe box —
    # X and iMessage both crop tighter than the declared 1.91:1.
    word = archivo(int(104 * SS), 780, 100)
    x0, y0 = 356 * SS, 178 * SS
    w1 = tracked(d, (x0, y0), "Caddie", word, MINT, tracking=int(-1.5 * SS))
    x1 = x0 + w1 - int(1.5 * SS)
    w2 = tracked(d, (x1, y0), "Insight", word, MINT, tracking=int(-1.5 * SS))
    d.rounded_rectangle(
        [x1, y0 + 132 * SS, x1 + w2, y0 + 138 * SS], radius=3 * SS, fill=ORANGE
    )

    line = archivo(int(46 * SS), 560, 100)
    d.text((218 * SS, 372 * SS),
           "One priority. One practice plan.", font=line, fill="#cfe0d5")
    d.text((218 * SS, 432 * SS),
           "Proof when you re-film.", font=line, fill="#cfe0d5")

    eyebrow = ImageFont.truetype(str(HERE / "DMMono-Regular.ttf"), int(26 * SS))
    tracked(d, (218 * SS, 520 * SS), "SWING ANALYSIS FROM ONE PHONE VIDEO",
            eyebrow, "#8fa89a", tracking=int(4 * SS))

    out = img.resize((W, H), Image.LANCZOS)
    path = OUT / "og-caddieinsight.png"
    out.save(path, "PNG", optimize=True)
    print("wrote", path)
    return path


def main() -> None:
    OUT.mkdir(exist_ok=True)

    # Vector marks. `caddie-mark.svg` is the transparent mark for in-page
    # chrome; `pwa-icon.svg` keeps its tile because it doubles as the tab
    # favicon and the installed-app icon, and browsers render it as small as
    # 16px — hence the coarse 16-tick fan and no cup.
    (OUT / "caddie-mark.svg").write_text(
        mark_svg(512, ink=GREEN, accent=ORANGE, tile=None, ticks=24, bold=1.1),
        encoding="utf-8",
    )
    print("wrote", OUT / "caddie-mark.svg")
    (OUT / "pwa-icon.svg").write_text(
        mark_svg(512, ink=MINT, accent=ORANGE, tile=GREEN, ticks=16, bold=1.2,
                 cup=False),
        encoding="utf-8",
    )
    print("wrote", OUT / "pwa-icon.svg")

    # Raster icons. Tick counts drop as the render shrinks so the bezel never
    # collapses into a solid ring.
    icon_png(OUT / "caddieinsight-favicon.png", 512, radius_ratio=0.215,
             ticks=24, bold=1.1)
    icon_png(OUT / "pwa-icon-192.png", 192, radius_ratio=0.215, ticks=24,
             bold=1.15)
    icon_png(OUT / "pwa-icon-512.png", 512, radius_ratio=0.215, ticks=36,
             bold=1.0)
    # Maskable: full-bleed tile, mark pulled inside the 80% safe circle.
    icon_png(OUT / "pwa-icon-maskable-512.png", 512, radius_ratio=0.0,
             ticks=24, bold=1.15, padding=0.22)
    # iOS rounds the corners itself and rejects alpha, so this one is opaque
    # and square.
    icon_png(OUT / "apple-touch-icon.png", 180, radius_ratio=0.0, ticks=24,
             bold=1.2, opaque=True)
    # The native app icon. Same rule as the touch icon — the platforms mask it
    # themselves and an alpha channel is rejected outright by App Store
    # submission — at the 1024 the stores require.
    icon_png(OUT / "app-icon-1024.png", 1024, radius_ratio=0.0, ticks=36,
             bold=1.0, opaque=True)

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
        "apple-touch-icon.png": (STATIC,),
        "pwa-icon.svg": (STATIC,),
        "pwa-icon-192.png": (STATIC,),
        "pwa-icon-512.png": (STATIC,),
        "pwa-icon-maskable-512.png": (STATIC,),
        "app-icon-1024.png": (MOBILE,),
        "caddieinsight-favicon.png": (STATIC, THEME),
        "og-caddieinsight.png": (THEME,),
    }
    for name, targets in ship.items():
        for target in targets:
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(OUT / name, target / name)
            print("shipped", target / name)


if __name__ == "__main__":
    main()
