"""apple-touch-icon.png — the iOS home-screen mark.

Reproduces swinglab/web/static/pwa-icon.svg with Pillow: the night turf
field, the cool-mist ball-face circle, one orange swing-arc gesture, and
the ball dot. iOS ignores SVG touch icons and rounds the corners itself,
so this renders the mark full-bleed on an OPAQUE 180x180 canvas (no alpha,
no pre-rounded corners). Drawn at supersample scale and downscaled for
clean edges, like the other generators here.

Writes straight into the app's static directory:
    python store-assets/make_apple_touch_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent
STATIC = HERE.parent / "swinglab" / "web" / "static"

# palette (from pwa-icon.svg / Turf Instrument)
NIGHT = "#06110c"
MIST = "#eef2ef"
ORANGE = "#e8720c"

SIZE = 180     # Apple's documented touch-icon size
CANVAS = 512   # draw in the SVG's own 512-unit coordinate space
S = 2          # supersample factor


def _cubic(p0, p1, p2, p3, steps=48):
    """Sample one cubic bezier as a point list (endpoint excluded)."""
    points = []
    for i in range(steps):
        t = i / steps
        u = 1 - t
        x = u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0]
        y = u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1]
        points.append((x, y))
    return points


def swing_arc() -> list[tuple[float, float]]:
    """The SVG swoosh: M157 286c60-123 132-118 198-61-61-26-115-11-155 50z."""
    top = _cubic((157, 286), (217, 163), (289, 168), (355, 225))
    belly = _cubic((355, 225), (294, 199), (240, 214), (200, 275))
    return top + belly + [(200, 275)]


def main() -> None:
    scale = CANVAS * S / 512
    img = Image.new("RGB", (CANVAS * S, CANVAS * S), NIGHT)
    draw = ImageDraw.Draw(img)

    def xy(x: float, y: float) -> tuple[float, float]:
        return (x * scale, y * scale)

    draw.ellipse([xy(256 - 150, 256 - 150), xy(256 + 150, 256 + 150)], fill=MIST)
    draw.polygon([xy(x, y) for x, y in swing_arc()], fill=ORANGE)
    draw.ellipse([xy(345 - 22, 192 - 22), xy(345 + 22, 192 + 22)], fill=NIGHT)

    out = STATIC / "apple-touch-icon.png"
    img.resize((SIZE, SIZE), Image.LANCZOS).save(out, format="PNG")
    print("wrote", out)


if __name__ == "__main__":
    main()
