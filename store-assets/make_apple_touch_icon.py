"""apple-touch-icon.png — the iOS home-screen mark.

Tour Caddie v3: deep-forest tile with the flagstick + precision-arc mark in
mint and one amber kinetic segment. iOS ignores SVG touch icons and rounds
the corners itself, so this renders the mark full-bleed on an OPAQUE 180x180
canvas (no alpha, no pre-rounded corners). Drawn at supersample scale and
downscaled for clean edges.

Writes straight into the app's static directory:
    python store-assets/make_apple_touch_icon.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent
STATIC = HERE.parent / "swinglab" / "web" / "static"

GREEN = "#0f3d28"
MINT = "#e6f2ea"
ORANGE = "#e8720c"

SIZE = 180
S = 4


def _pol(cx: float, cy: float, r: float, ang: float) -> tuple[float, float]:
    a = math.radians(ang)
    return cx + r * math.cos(a), cy + r * math.sin(a)


def main() -> None:
    canvas = SIZE * S
    img = Image.new("RGB", (canvas, canvas), GREEN)
    d = ImageDraw.Draw(img)
    cx = cy = canvas / 2
    R = canvas * 0.32
    stroke = max(2, int(0.055 * R))

    for start, end, fill in (
        (200, 290, MINT),
        (295, 340, ORANGE),
        (345, 430, MINT),
    ):
        d.arc([cx - R, cy - R, cx + R, cy + R], start, end, fill=fill, width=stroke)

    stick_w = max(2, int(0.05 * R))
    top = cy - 0.55 * R
    bottom = cy + 0.42 * R
    d.line([(cx, top), (cx, bottom)], fill=MINT, width=stick_w)
    flag_h = 0.28 * R
    flag_w = 0.42 * R
    d.polygon(
        [
            (cx + stick_w * 0.5, top),
            (cx + flag_w, top + flag_h * 0.45),
            (cx + stick_w * 0.5, top + flag_h),
        ],
        fill=MINT,
    )
    cup_rx, cup_ry = 0.22 * R, 0.09 * R
    d.ellipse(
        [cx - cup_rx, bottom - cup_ry, cx + cup_rx, bottom + cup_ry],
        outline=MINT,
        width=max(2, int(0.04 * R)),
    )

    out = img.resize((SIZE, SIZE), Image.LANCZOS)
    path = STATIC / "apple-touch-icon.png"
    out.save(path, "PNG", optimize=True)
    print("wrote", path)


if __name__ == "__main__":
    main()
