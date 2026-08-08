"""The CaddieInsight mark — one geometry, two renderers.

Tour Caddie v4: an instrument dial whose tick fan reads as measurement, a
contiguous amber run marking the reading the caddie actually calls, and a
fairway flagstick planted through the middle. Golf plus metrology, in a shape
that survives a 32px favicon.

Everything is defined once, in mark units on a unit circle centred at the
origin, so the raster (Pillow, for PNG icons and campaign art) and the vector
(SVG, for the favicon and PWA icon) can never drift apart. Both renderers
consume the same `tick_geometry()` / `flag_geometry()` output.

    from brand_mark import draw_mark, mark_svg
"""

from __future__ import annotations

import math

GREEN = "#0f3d28"
MINT = "#e6f2ea"
ORANGE = "#e8720c"

# -- dial ----------------------------------------------------------------
# 36 ticks, one every 10 degrees. Every third is a major (longer, heavier) so
# the bezel keeps a readable rhythm after it is downscaled.
TICK_COUNT = 36
TICK_STEP_DEG = 360 / TICK_COUNT
MAJOR_EVERY = 3

MINOR_LEN = 0.125
MAJOR_LEN = 0.205
MINOR_WIDTH = 0.030
MAJOR_WIDTH = 0.048

# The reading: a contiguous run through the lower right, where the downswing
# loads. Drawn at major weight in amber regardless of its position in the
# major/minor rhythm — it is the one kinetic gesture in the composition.
READING_START_DEG = 30.0
READING_SPAN_DEG = 50.0

# -- flagstick -----------------------------------------------------------
STAFF_TOP = -0.60          # negative is up (screen coordinates)
STAFF_BOTTOM = 0.42
STAFF_WIDTH = 0.052
PENNANT_REACH = 0.42       # how far right of the staff the flag flies
PENNANT_DROP = 0.30        # vertical extent of the pennant
CUP_RX = 0.205
CUP_RY = 0.075
CUP_WIDTH = 0.040


def _polar(cx: float, cy: float, r: float, deg: float) -> tuple[float, float]:
    a = math.radians(deg)
    return cx + r * math.cos(a), cy + r * math.sin(a)


def tick_geometry(cx: float, cy: float, r: float, bold: float = 1.0,
                  ticks: int = TICK_COUNT):
    """Yield (x0, y0, x1, y1, width, is_reading) for every bezel tick.

    Angles follow screen convention: 0 deg is 3 o'clock and they advance
    clockwise, matching both PIL's arc convention and SVG's y-down axes.

    `ticks` coarsens the fan for small renders. A 36-tick bezel turns into a
    grey ring below about 64px, so the favicon and the 192px PWA icon drop to
    24 and widen their strokes to compensate.
    """
    step = 360 / ticks
    # Keep strokes proportional to the gap between ticks, so a coarser fan
    # reads heavier instead of merely sparser.
    gap_scale = ticks / TICK_COUNT
    reading_end = READING_START_DEG + READING_SPAN_DEG
    for index in range(ticks):
        deg = index * step
        reading = READING_START_DEG <= deg <= reading_end
        major = reading or index % MAJOR_EVERY == 0
        length = MAJOR_LEN if major else MINOR_LEN
        width = (MAJOR_WIDTH if major else MINOR_WIDTH) * bold * r / gap_scale
        x0, y0 = _polar(cx, cy, r - length * r, deg)
        x1, y1 = _polar(cx, cy, r, deg)
        yield x0, y0, x1, y1, max(2.0, width), reading


def flag_geometry(cx: float, cy: float, r: float, bold: float = 1.0):
    """Staff line, pennant polygon, and cup ellipse box, in device units."""
    staff_w = max(2.0, STAFF_WIDTH * bold * r)
    top = cy + STAFF_TOP * r
    bottom = cy + STAFF_BOTTOM * r
    staff = (cx, top, cx, bottom)
    # The pennant hangs from the staff top and tapers to a point, with its
    # trailing edge cut back so it reads as fabric rather than a triangle
    # sign. The inset keeps it visually attached to the staff.
    inset = staff_w * 0.5
    pennant = [
        (cx + inset, top),
        (cx + PENNANT_REACH * r, top + PENNANT_DROP * r * 0.42),
        (cx + inset, top + PENNANT_DROP * r),
    ]
    cup = (
        cx - CUP_RX * r,
        bottom - CUP_RY * r,
        cx + CUP_RX * r,
        bottom + CUP_RY * r,
    )
    return staff, staff_w, pennant, cup, max(2.0, CUP_WIDTH * bold * r)


def draw_mark(d, cx, cy, r, ink=GREEN, accent=ORANGE, bold=1.0,
              ticks: int = TICK_COUNT, cup: bool = True) -> None:
    """Render the mark onto a Pillow ImageDraw at radius `r` about (cx, cy).

    `cup=False` drops the hole ellipse, which turns to mud below ~24px and
    costs nothing at that size — the dial and the flag already carry the mark.
    """
    for x0, y0, x1, y1, width, reading in tick_geometry(cx, cy, r, bold,
                                                        ticks):
        d.line([x0, y0, x1, y1], fill=accent if reading else ink,
               width=int(round(width)))

    staff, staff_w, pennant, cup_box, cup_w = flag_geometry(cx, cy, r, bold)
    d.line(list(staff), fill=ink, width=int(round(staff_w)))
    d.polygon(pennant, fill=ink)
    if cup:
        d.ellipse(list(cup_box), outline=ink, width=int(round(cup_w)))


def mark_svg(size: int = 512, ink: str = MINT, accent: str = ORANGE,
             tile: str | None = GREEN, radius_ratio: float = 0.115,
             bold: float = 1.0, label: str = "CaddieInsight",
             ticks: int = TICK_COUNT, cup: bool = True) -> str:
    """The same mark as standalone SVG.

    `tile` paints a rounded-square backdrop (favicon / PWA icon); pass None
    for a transparent mark that inherits the surrounding chrome.
    """
    cx = cy = size / 2
    # Leave a generous margin: maskable icons crop to a circle inscribed in
    # the middle 80%, so the mark has to survive that safe zone.
    r = size * 0.315
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'role="img" aria-label="{label}">'
    ]
    if tile:
        rx = size * radius_ratio
        parts.append(
            f'<rect width="{size}" height="{size}" rx="{rx:g}" fill="{tile}"/>'
        )

    for x0, y0, x1, y1, width, reading in tick_geometry(cx, cy, r, bold,
                                                        ticks):
        colour = accent if reading else ink
        parts.append(
            f'<line x1="{x0:.2f}" y1="{y0:.2f}" x2="{x1:.2f}" y2="{y1:.2f}" '
            f'stroke="{colour}" stroke-width="{width:.2f}" '
            'stroke-linecap="round"/>'
        )

    staff, staff_w, pennant, cup_box, cup_w = flag_geometry(cx, cy, r, bold)
    sx0, sy0, sx1, sy1 = staff
    parts.append(
        f'<line x1="{sx0:.2f}" y1="{sy0:.2f}" x2="{sx1:.2f}" y2="{sy1:.2f}" '
        f'stroke="{ink}" stroke-width="{staff_w:.2f}" stroke-linecap="round"/>'
    )
    points = " ".join(f"{px:.2f},{py:.2f}" for px, py in pennant)
    parts.append(f'<polygon points="{points}" fill="{ink}"/>')
    if cup:
        x0, y0, x1, y1 = cup_box
        parts.append(
            f'<ellipse cx="{(x0 + x1) / 2:.2f}" cy="{(y0 + y1) / 2:.2f}" '
            f'rx="{(x1 - x0) / 2:.2f}" ry="{(y1 - y0) / 2:.2f}" fill="none" '
            f'stroke="{ink}" stroke-width="{cup_w:.2f}"/>'
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"
