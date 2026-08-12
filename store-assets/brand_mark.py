"""The CaddieInsight mark — one geometry, two renderers.

Industry v1: a drawn iron. Grip, shaft, hosel and a blade with a sloped
topline and steel grooves. It replaces the Tour Caddie v4 instrument dial,
which read as a gauge with a flag through it — accurate about the metrology
and silent about the golf. The iron says both at once: it is the tool the
measurement is taken of, and it is unmistakably a club before you read the
letters beside it.

The mark's home is INSIDE the wordmark, standing in for the "I" of
CaddieInsight — "Caddie" + iron + "nsight" — so the club is read as a letter
at small sizes and as a club at large ones. It also stands alone as a favicon
and an app icon.

THE SIZE RULE IS PART OF THE GEOMETRY, not a rendering afterthought. Grip,
shaft and blade carry the silhouette all the way down to 16px; the grooves are
the first thing to turn to mud, so they drop out below ~19px and thin from
three to one on the way down. `groove_count()` encodes that, and both
renderers consume it, so a favicon and a 512px icon can never disagree about
what the mark is.

Everything is defined once, in mark units on a unit-HEIGHT box with the origin
at the box's top-left and y increasing downward (screen convention, matching
both Pillow and SVG), so the raster (Pillow, for PNG icons and campaign art)
and the vector (SVG, for the favicon and PWA icon) cannot drift apart. Both
renderers consume the same `iron_geometry()` output.

    from brand_mark import draw_mark, mark_svg
"""

from __future__ import annotations

# The palette the mark is cut from. GREEN is the reversed field, MINT the
# paper that reverses onto it, STEEL the grooves. These names are consumed by
# make_brand.py; ORANGE is kept as an alias because the amber signal is gone
# from the system but the parameter it fed is still called `accent`.
GREEN = "#070f0b"
MINT = "#f2f2f3"
STEEL = "#5980a6"
ORANGE = STEEL  # retired name, kept so no caller silently gets nothing
INK = "#1d1f20"

# -- the iron, in mark units (height = 1.0) ------------------------------
# Traced from mockup 6a/6b's Mark B at 60x128 and normalised by its height, so
# the proportions are the designer's rather than a re-derivation. 6a specifies
# the anatomy the flat rectangles only approximate: "a tapered grip with a
# rounded butt, a shaft that thins toward a flared hosel, and a blade with a
# sloped topline, toe radius and cambered sole", at
# shaft 12->8u, hosel flare 10->21u, sole camber R~180u, grooves 3u.
_U = 128.0                  # the drawing's own height, for legible ratios
IRON_W = 60 / _U            # box width as a fraction of height

# The club's axis. Grip, shaft and hosel are all centred on it, which is what
# keeps them fused when the taper widths change.
AXIS_X = 46 / _U

GRIP_Y0, GRIP_Y1 = 6 / _U, 40 / _U
GRIP_HALF_TOP = 6.5 / _U
GRIP_HALF_BOTTOM = 5.5 / _U
GRIP_BUTT_R = 3.0 / _U      # the rounded butt

# The shaft tapers 12u -> 8u over its length (6a). It starts under the grip so
# the two overlap rather than butt together.
SHAFT_Y0, SHAFT_Y1 = 38 / _U, 100 / _U
SHAFT_HALF_TOP = 6 / _U
SHAFT_HALF_BOTTOM = 4 / _U

# The hosel flares 10u -> 21u into the blade over the last stretch of shaft,
# and it flares ASYMMETRICALLY: its heel side stays flush with the blade's
# heel edge while the toe side spreads into the crown. A symmetric flare puts
# a spur out past the heel, which reads as a bracket bolted to the blade
# rather than as one forged piece.
HOSEL_Y0 = 88 / _U
HOSEL_HALF_TOP = 5 / _U
HOSEL_TOE_BOTTOM = 10.5 / _U   # left of the axis
HOSEL_HEEL_BOTTOM = 6 / _U     # right of the axis — flush with the heel

# Blade corners, before the toe radius and sole camber are applied.
#
# THE TOE IS THE LEFT END. The mockup's clip polygon keeps the right edge
# square and full height (that is the heel, under the hosel) and cuts the left
# away with a sloped topline and a clipped leading corner. Radiusing the wrong
# end rounds off the heel and leaves the toe a spike — which is what the first
# pass shipped.
BLADE_HEEL_X = 52 / _U
BLADE_TOP_Y = 100 / _U       # topline at the heel/hosel end
BLADE_SOLE_Y = 128 / _U
BLADE_TOE_SOLE_X = 8.5 / _U  # where the sole meets the toe
BLADE_LEAD_X = 2 / _U        # the leading corner, out at the toe
BLADE_LEAD_Y = 119.3 / _U
BLADE_TOPLINE_X = 13 / _U    # where the sloped topline meets the crown
BLADE_TOPLINE_Y = 104.2 / _U
TOE_R = 3.5 / _U             # toe radius, on the leading corner
SOLE_CAMBER = 1.6 / _U       # how far the sole bows below its chord

# Grooves run parallel to the sole, longest at the bottom, and all stop at the
# hosel. Each is (x0, x1, y) in mark units; groove_count() decides how many
# are drawn. The left ends step inward as they rise because the blade's
# leading edge slopes away — these are placed inside it, not clipped to it.
GROOVES = (
    (15 / _U, 43 / _U, 108 / _U),
    (12 / _U, 43 / _U, 114 / _U),
    (9 / _U, 43 / _U, 120 / _U),
)
GROOVE_WIDTH = 3 / _U


def _arc(cx, cy, r, a0, a1, steps=8):
    """Sampled arc, so both renderers draw the identical polygon.

    Pillow and SVG have incompatible arc APIs, and a mark defined partly by
    "the renderer's arc" is a mark that can drift between them. Sampling the
    curve into the shared point list keeps the one-geometry contract literal.
    """
    import math
    return [
        (cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / steps)),
         cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / steps)))
        for i in range(steps + 1)
    ]


def blade_points():
    """The blade outline in mark units, with the toe radius and sole camber.

    Clockwise from the heel crown: down the square heel edge, along the
    cambered sole toward the toe, round the leading corner, and back up the
    sloped topline.
    """
    pts = [
        (BLADE_HEEL_X, BLADE_TOP_Y),      # heel crown, under the hosel
        (BLADE_HEEL_X, BLADE_SOLE_Y),     # heel sole
    ]
    # Sole: a shallow arc bowing below the chord, heel to toe. 4t(1-t) peaks
    # at 1 mid-chord and is 0 at both ends, so the ends stay put.
    steps = 10
    for i in range(1, steps + 1):
        t = i / steps
        x = BLADE_HEEL_X + (BLADE_TOE_SOLE_X - BLADE_HEEL_X) * t
        pts.append((x, BLADE_SOLE_Y + SOLE_CAMBER * 4 * t * (1 - t)))
    # Toe: round the leading corner rather than leaving it a spike.
    pts += _arc(BLADE_LEAD_X + TOE_R, BLADE_LEAD_Y + TOE_R * 0.4, TOE_R, 110, 250)
    pts.append((BLADE_TOPLINE_X, BLADE_TOPLINE_Y))
    return pts


def groove_count(height_px: float) -> int:
    """How many grooves survive at this rendered height.

    Mockup 6b proves the mark at 46 / 30 / 18px and drops the grooves at 18.
    Below ~19px a 1.5-unit line is under a pixel and renders as a grey smear
    across the blade, which reads as a printing fault rather than as detail.
    """
    if height_px >= 44:
        return 3
    if height_px >= 26:
        return 2
    if height_px >= 19:
        return 1
    return 0


def iron_geometry(cx: float, cy: float, height: float, bold: float = 1.0):
    """Device-space shapes for the mark, centred on (cx, cy).

    Returns (shapes, grip_round, grooves, groove_width) where `shapes` is a
    list of polygons as [(x, y), ...], `grip_round` is
    (box, radius) for the rounded butt, and `grooves` is a list of
    (x0, y0, x1, y1) segments already filtered by the size rule.
    """
    w = IRON_W * height
    x0 = cx - w / 2
    y0 = cy - height / 2

    def px(u: float) -> float:
        return x0 + u * height

    def py(v: float) -> float:
        return y0 + v * height

    # MINIMUM STROKE WIDTHS, in device pixels. Mockup 7 requires them by name:
    # "grip, shaft and blade only, with minimum stroke widths so the
    # silhouette holds at 16px". Without them the shaft is 0.6px at favicon
    # size and the mark renders as a blade with nothing holding it — the club
    # stops being a club exactly where it most needs to still be one.
    def half(units: float, min_px: float) -> float:
        return max(units * height * bold, min_px / 2) / height

    def taper(half_top, ay, by, toe_bottom, heel_bottom=None):
        """A trapezoid about the club's axis; asymmetric if heel differs."""
        if heel_bottom is None:
            heel_bottom = toe_bottom
        return [
            (px(AXIS_X - half_top), py(ay)), (px(AXIS_X + half_top), py(ay)),
            (px(AXIS_X + heel_bottom), py(by)), (px(AXIS_X - toe_bottom), py(by)),
        ]

    grip = taper(half(GRIP_HALF_TOP, 3.4), GRIP_Y0, GRIP_Y1,
                 half(GRIP_HALF_BOTTOM, 3.0))
    shaft = taper(half(SHAFT_HALF_TOP, 2.2), SHAFT_Y0, SHAFT_Y1,
                  half(SHAFT_HALF_BOTTOM, 1.8))
    hosel = taper(half(HOSEL_HALF_TOP, 2.2), HOSEL_Y0, BLADE_TOP_Y,
                  half(HOSEL_TOE_BOTTOM, 4.0), half(HOSEL_HEEL_BOTTOM, 2.6))
    blade = [(px(u), py(v)) for u, v in blade_points()]

    # The butt cap, as a rounded rect over the grip's top so the corners read
    # as turned rubber rather than as a cut-off stick.
    grip_round = (
        [px(AXIS_X - GRIP_HALF_TOP), py(GRIP_Y0),
         px(AXIS_X + GRIP_HALF_TOP), py(GRIP_Y0 + 2 * GRIP_BUTT_R)],
        GRIP_BUTT_R * height,
    )

    count = groove_count(height)
    # Draw the LAST n grooves: the long ones near the sole are the ones that
    # still read when there is only room for a couple.
    grooves = [
        (px(gx0), py(gy), px(gx1), py(gy))
        for gx0, gx1, gy in GROOVES[len(GROOVES) - count:]
    ]
    return ([grip, shaft, hosel, blade], grip_round, grooves,
            max(1.0, GROOVE_WIDTH * height * bold))


def draw_mark(d, cx, cy, height, ink=INK, accent=STEEL, bold: float = 1.0) -> None:
    """Render the mark onto a Pillow ImageDraw at `height` about (cx, cy).

    `height` is the mark's full height, not a radius — the iron is a tall
    shape and sizing it by a radius is how the old dial's call sites all had
    to carry a fudge factor.
    """
    shapes, (butt_box, butt_r), grooves, gw = iron_geometry(cx, cy, height, bold)
    for shape in shapes:
        d.polygon(shape, fill=ink)
    d.rounded_rectangle(butt_box, radius=butt_r, fill=ink)
    for gx0, gy0, gx1, gy1 in grooves:
        d.line([gx0, gy0, gx1, gy1], fill=accent, width=max(1, int(round(gw))))


def mark_svg(size: int = 512, ink: str = MINT, accent: str = STEEL,
             tile: str | None = GREEN, radius_ratio: float = 0.0,
             bold: float = 1.0, label: str = "CaddieInsight",
             padding: float = 0.19) -> str:
    """The same mark as standalone SVG.

    `tile` paints a square backdrop (favicon / PWA icon); pass None for a
    transparent mark that inherits the surrounding chrome. `radius_ratio`
    defaults to 0 because Industry is square — a rounded tile is now the
    exception (the iOS mask) rather than the house style.
    """
    cx = cy = size / 2
    # Maskable icons crop to a circle inscribed in the middle 80%, so the mark
    # has to survive that safe zone. The iron is tall and narrow, so the
    # binding constraint is its height rather than its width.
    height = size * (1 - 2 * padding)
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'role="img" aria-label="{label}">'
    ]
    if tile:
        rx = size * radius_ratio
        rect = f'<rect width="{size}" height="{size}" fill="{tile}"'
        parts.append(rect + (f' rx="{rx:g}"/>' if rx else "/>"))

    shapes, (butt_box, butt_r), grooves, gw = iron_geometry(cx, cy, height, bold)
    for shape in shapes:
        points = " ".join(f"{x:.2f},{y:.2f}" for x, y in shape)
        parts.append(f'<polygon points="{points}" fill="{ink}"/>')
    bx0, by0, bx1, by1 = butt_box
    parts.append(
        f'<rect x="{bx0:.2f}" y="{by0:.2f}" width="{bx1 - bx0:.2f}" '
        f'height="{by1 - by0:.2f}" rx="{butt_r:.2f}" fill="{ink}"/>'
    )
    for gx0, gy0, gx1, gy1 in grooves:
        parts.append(
            f'<line x1="{gx0:.2f}" y1="{gy0:.2f}" x2="{gx1:.2f}" y2="{gy1:.2f}" '
            f'stroke="{accent}" stroke-width="{gw:.2f}"/>'
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"
