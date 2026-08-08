"""CaddieInsight brand assets — Fairway Modernism.

Flat catalog illustrations: warm off-white field, deep green ink, one orange
kinetic gesture per piece, systematic corner labels. Everything is drawn at
SS× and downscaled for clean edges.

The six product cards are drawn like instrument sheets: material texture in
ink (knurling, ribbing, stitch rows, screws), dimension lines with real
measurements, a cross-section or detail inset per object, and a two-line
specimen footer (title / spec + sku).
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

# palette (from the storefront theme's base.css)
BG = "#f7f5f0"
CARD = "#fffdf9"
INK = "#17201a"
INK_SOFT = "#4a544c"
INK_MUTED = "#7a8279"
GREEN = "#14472c"
GREEN_BTN = "#1a5c38"
GREEN_INK = "#e9f2ec"
ORANGE = "#e8720c"
BORDER = "#e3ded3"
ARC_FAINT = "#e9e4d6"

S = 2          # supersample factor for 1600px product cards
SIZE = 1600

# Fonts are not committed — see README.md for the two download commands.
MONO = str(HERE / "DMMono-Regular.ttf")
ARCHIVO = str(HERE / "Archivo-var.ttf")


def archivo(px: int, weight: int = 600, width: int = 100) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(ARCHIVO, px)
    f.set_variation_by_axes([weight, width])
    return f


def mono(px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(MONO, px)


def canvas(w=SIZE, h=SIZE, bg=BG, scale=S):
    img = Image.new("RGB", (w * scale, h * scale), bg)
    return img, ImageDraw.Draw(img)


def finish(img: Image.Image, name: str, w=SIZE, h=SIZE):
    img = img.resize((w, h), Image.LANCZOS)
    path = OUT / name
    img.save(path)
    print("wrote", path)


def tracked(draw, xy, text, font, fill, tracking=0, anchor=None):
    """Letter-spaced text (PIL has no tracking)."""
    x, y = xy
    widths = [draw.textlength(ch, font=font) for ch in text]
    total = sum(widths) + tracking * (len(text) - 1)
    if anchor == "m":      # center on x
        x -= total / 2
    elif anchor == "r":    # right-align on x
        x -= total
    for ch, wd in zip(text, widths):
        draw.text((x, y), ch, font=font, fill=fill)
        x += wd + tracking
    return total


def swing_arc(draw, cx, cy, r, a0, a1, fill, width, dash=None):
    """A circular arc. Solid arcs use PIL's native arc (clean edges);
    dashed arcs are stroked dot-by-dot (dash = (on, off) in degrees)."""
    if dash is None:
        box = [cx - r, cy - r, cx + r, cy + r]
        draw.arc(box, a0, a1, fill=fill, width=width)
        for ang in (a0, a1):  # round terminals
            x = cx + (r - width / 2) * math.cos(math.radians(ang))
            y = cy + (r - width / 2) * math.sin(math.radians(ang))
            draw.ellipse([x - width / 2, y - width / 2, x + width / 2, y + width / 2],
                         fill=fill)
        return
    step = 0.6
    a = a0
    on, off = dash
    while a < a1:
        phase = (a - a0) % (on + off)
        if phase > on:
            a += step
            continue
        x0 = cx + r * math.cos(math.radians(a))
        y0 = cy + r * math.sin(math.radians(a))
        x1 = cx + r * math.cos(math.radians(a + step))
        y1 = cy + r * math.sin(math.radians(a + step))
        draw.line([x0, y0, x1, y1], fill=fill, width=width)
        a += step


def rrect(draw, box, radius, **kw):
    draw.rounded_rectangle(box, radius=radius, **kw)


# ------------------------------------------------------- drafting helpers ----

def dashed_line(d, p0, p1, fill, width, dash=(26, 18)):
    """Straight dashed segment; dash lengths are in already-scaled pixels."""
    x0, y0 = p0
    x1, y1 = p1
    ln = math.hypot(x1 - x0, y1 - y0)
    if ln == 0:
        return
    ux, uy = (x1 - x0) / ln, (y1 - y0) / ln
    on, off = dash
    t = 0.0
    while t < ln:
        seg = min(on, ln - t)
        d.line([x0 + ux * t, y0 + uy * t, x0 + ux * (t + seg), y0 + uy * (t + seg)],
               fill=fill, width=width)
        t += on + off


def arrow_head(d, s, tip, ang_deg, size=26, fill=ORANGE):
    """Filled triangular arrowhead whose point sits at `tip`, aimed ang_deg."""
    a = math.radians(ang_deg)
    ux, uy = math.cos(a), math.sin(a)
    nx, ny = -uy, ux
    x, y = tip
    d.polygon([
        (x + ux * size * s * 0.62, y + uy * size * s * 0.62),
        (x - ux * size * s * 0.55 + nx * size * s * 0.52,
         y - uy * size * s * 0.55 + ny * size * s * 0.52),
        (x - ux * size * s * 0.55 - nx * size * s * 0.52,
         y - uy * size * s * 0.55 - ny * size * s * 0.52),
    ], fill=fill)


def dim_line(d, s, p0, p1, label=None, side=1, gap=40, color=INK_MUTED,
             font_px=20, tick=13, lw=2, tracking=2):
    """Engineering dimension line: hairline, perpendicular end ticks, and a
    centered mono label offset to one side (side=+1 along the normal)."""
    x0, y0 = p0
    x1, y1 = p1
    d.line([x0, y0, x1, y1], fill=color, width=int(lw * s))
    ln = math.hypot(x1 - x0, y1 - y0) or 1
    nx, ny = -(y1 - y0) / ln, (x1 - x0) / ln
    for px_, py_ in (p0, p1):
        d.line([px_ - nx * tick * s, py_ - ny * tick * s,
                px_ + nx * tick * s, py_ + ny * tick * s],
               fill=color, width=int(lw * s))
    if label:
        mx = (x0 + x1) / 2 + nx * gap * s * side
        my = (y0 + y1) / 2 + ny * gap * s * side
        tracked(d, (mx, my - font_px * s * 0.62), label, mono(int(font_px * s)),
                color, tracking=int(tracking * s), anchor="m")


def callout(d, s, pt, txt_xy, text, color=INK_SOFT, line_color=INK_MUTED,
            font_px=20, dot=7, align="l", tracking=2):
    """Specimen annotation: small ring on the feature, hairline leader, label."""
    x, y = pt
    tx, ty = txt_xy
    d.ellipse([x - dot * s, y - dot * s, x + dot * s, y + dot * s],
              outline=line_color, width=int(2.5 * s))
    ln = math.hypot(tx - x, ty - y) or 1
    ux, uy = (tx - x) / ln, (ty - y) / ln
    d.line([x + ux * dot * s, y + uy * dot * s, tx, ty],
           fill=line_color, width=int(2 * s))
    pad = 14 * s
    if align == "l":
        tracked(d, (tx + pad, ty - font_px * s * 0.62), text,
                mono(int(font_px * s)), color, tracking=int(tracking * s))
    else:
        tracked(d, (tx - pad, ty - font_px * s * 0.62), text,
                mono(int(font_px * s)), color, tracking=int(tracking * s), anchor="r")


def inset_panel(d, s, box, caption=None):
    """Rounded detail-study panel (cross-sections, magnified parts)."""
    rrect(d, box, 24 * s, fill=CARD, outline=BORDER, width=int(3 * s))
    if caption:
        tracked(d, (box[0] + 26 * s, box[1] + 22 * s), caption,
                mono(int(19 * s)), INK_MUTED, tracking=int(3 * s))


def screw(d, s, x, y, r=11, color=GREEN_INK, ang=45, lw=2.5):
    """Slotted screw head."""
    d.ellipse([x - r * s, y - r * s, x + r * s, y + r * s],
              outline=color, width=int(lw * s))
    a = math.radians(ang)
    rr = (r - 4) * s
    d.line([x - rr * math.cos(a), y - rr * math.sin(a),
            x + rr * math.cos(a), y + rr * math.sin(a)],
           fill=color, width=int(lw * s))


# ---------------------------------------------------------------- chrome ----

def card_chrome(draw, s, category: str, title: str, sku: str, spec: str | None = None):
    """The systematic frame every product card shares. With `spec` the footer
    becomes two lines: title, then spec (left) + sku (right) in mono."""
    m = 110 * s
    # faint brand arc across the field
    swing_arc(draw, 1600 * s * 0.86, 1600 * s * 0.16, 1120 * s, 95, 175,
              ARC_FAINT, int(3 * s), dash=(2.2, 2.6))
    # top-left wordmark — 13 letters, so tighter tracking and a step down in
    # size versus the old 8-letter mark; baseline stays optically level with
    # the mono category label on the right
    tracked(draw, (m, m - 6 * s), "CADDIEINSIGHT", archivo(int(28 * s), 640, 104),
            GREEN, tracking=int(4 * s))
    # top-right category, mono
    tracked(draw, (1600 * s - m, m - 4 * s), category.upper(), mono(int(24 * s)),
            INK_MUTED, tracking=int(4 * s), anchor="r")
    draw.line([m, 176 * s, 1600 * s - m, 176 * s], fill=BORDER, width=int(2 * s))
    if spec:
        draw.line([m, 1400 * s, 1600 * s - m, 1400 * s], fill=BORDER, width=int(2 * s))
        draw.text((m, 1418 * s), title, font=archivo(int(44 * s), 620, 102), fill=INK)
        tracked(draw, (m, 1484 * s), spec.upper(), mono(int(20 * s)), INK_MUTED,
                tracking=int(2 * s))
        tracked(draw, (1600 * s - m, 1484 * s), sku, mono(int(20 * s)), INK_MUTED,
                tracking=int(3 * s), anchor="r")
    else:
        draw.text((m, 1600 * s - m - 44 * s), title,
                  font=archivo(int(46 * s), 620, 102), fill=INK)
        tracked(draw, (1600 * s - m, 1600 * s - m - 26 * s), sku, mono(int(24 * s)),
                INK_MUTED, tracking=int(3 * s), anchor="r")
        draw.line([m, 1600 * s - 176 * s, 1600 * s - m, 1600 * s - 176 * s],
                  fill=BORDER, width=int(2 * s))


# ---------------------------------------------------------------- products ----

def _bezier(p0, p1, p2, n=100):
    pts = []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
        pts.append((x, y))
    return pts


def tempo_wand():
    img, d = canvas()
    s = S
    card_chrome(d, s, "Training aid · Tempo", "Tempo Trainer Swing Wand",
                "SL-TEMPO-WAND", spec="115 cm · 285 g head · R2 flex")
    # flexed shaft: quadratic bezier from grip (lower-left) to weighted head
    p0 = (430 * s, 1210 * s)
    p1 = (760 * s, 950 * s)
    p2 = (1120 * s, 430 * s)
    pts = _bezier(p0, p1, p2)
    # dashed trace of the head's path, pivoting around the grip
    pr = math.dist(p0, p2)
    pa = math.degrees(math.atan2(p2[1] - p0[1], p2[0] - p0[0]))
    swing_arc(d, p0[0], p0[1], pr, pa - 33, pa - 6, INK_MUTED, int(5 * s),
              dash=(1.6, 2.4))
    # full-length dimension, offset to the lower-right of the shaft
    ux, uy = (p2[0] - p0[0]) / pr, (p2[1] - p0[1]) / pr
    nx, ny = -uy, ux            # points down-right of the shaft
    off = 130 * s
    dim_line(d, s, (p0[0] + nx * off, p0[1] + ny * off),
             (p2[0] + nx * off, p2[1] + ny * off), "115 CM / 45 IN",
             side=1, gap=44)
    # orange whip section (upper 60%) — the kinetic gesture
    for i in range(40, 100):
        w = int((14 + (100 - i) * 0.16) * s)
        d.line([pts[i], pts[i + 1]], fill=ORANGE, width=w)
    # grip section (lower 40%) in deep green, thicker
    for i in range(0, 40):
        d.line([pts[i], pts[i + 1]], fill=GREEN, width=int(30 * s))
    # knurled grip: true crosshatch, ±45° ticks at each station
    for i in range(3, 38, 4):
        x, y = pts[i]
        tx_, ty_ = pts[i + 1][0] - x, pts[i + 1][1] - y
        base = math.atan2(ty_, tx_)
        for da in (math.pi / 4, -math.pi / 4):
            aa = base + da
            r = 15 * s
            d.line([x - r * math.cos(aa), y - r * math.sin(aa),
                    x + r * math.cos(aa), y + r * math.sin(aa)],
                   fill=GREEN_INK, width=int(2.5 * s))
    # ferrule where grip meets shaft: three collar rings
    for i in (40, 43, 46):
        x, y = pts[i]
        tx_, ty_ = pts[i + 1][0] - x, pts[i + 1][1] - y
        aa = math.atan2(ty_, tx_) + math.pi / 2
        r = 15 * s
        d.line([x - r * math.cos(aa), y - r * math.sin(aa),
                x + r * math.cos(aa), y + r * math.sin(aa)],
               fill=GREEN, width=int(5 * s))
    # butt cap with end screw
    d.ellipse([p0[0] - 24 * s, p0[1] - 24 * s, p0[0] + 24 * s, p0[1] + 24 * s],
              fill=GREEN)
    screw(d, s, p0[0], p0[1], r=10, ang=25)
    # weighted head ball with seam + dimples
    hx, hy = p2
    d.ellipse([hx - 62 * s, hy - 62 * s, hx + 62 * s, hy + 62 * s], fill=GREEN)
    d.arc([hx - 46 * s, hy - 58 * s, hx + 58 * s, hy + 46 * s], 300, 90,
          fill=GREEN_BTN, width=int(4 * s))
    for ang in (200, 235, 270):
        x = hx + 30 * s * math.cos(math.radians(ang))
        y = hy + 30 * s * math.sin(math.radians(ang))
        d.ellipse([x - 5 * s, y - 5 * s, x + 5 * s, y + 5 * s], fill=GREEN_INK)
    # head diameter
    dim_line(d, s, (hx + 114 * s, hy - 62 * s), (hx + 114 * s, hy + 62 * s),
             "Ø 63 MM", side=-1, gap=76)
    # section cut mark through the head
    dashed_line(d, (hx - 104 * s, hy), (hx + 96 * s, hy), INK_MUTED, int(2 * s),
                dash=(16 * s, 10 * s))
    for lx in (hx - 122 * s, hx + 106 * s):
        tracked(d, (lx, hy - 13 * s), "A", mono(int(19 * s)), INK_MUTED)
    # cross-section inset: shell + core
    inset_panel(d, s, (1020 * s, 1010 * s, 1424 * s, 1364 * s),
                caption="SECTION A–A · HEAD")
    ccx, ccy, cr = 1140 * s, 1200 * s, 94 * s
    d.ellipse([ccx - cr, ccy - cr, ccx + cr, ccy + cr],
              outline=GREEN, width=int(8 * s))
    for ang in range(0, 360, 15):        # shell hatch
        a = math.radians(ang)
        d.line([ccx + 62 * s * math.cos(a), ccy + 62 * s * math.sin(a),
                ccx + 88 * s * math.cos(a), ccy + 88 * s * math.sin(a)],
               fill=GREEN_BTN, width=int(2 * s))
    d.ellipse([ccx - 54 * s, ccy - 54 * s, ccx + 54 * s, ccy + 54 * s], fill=GREEN)
    tracked(d, (1268 * s, 1124 * s), "TPR SHELL", mono(int(19 * s)), INK_SOFT,
            tracking=int(2 * s))
    d.line([1260 * s, 1132 * s, ccx + 66 * s, ccy - 66 * s], fill=INK_MUTED,
           width=int(2 * s))
    tracked(d, (1268 * s, 1204 * s), "STEEL CORE", mono(int(19 * s)), INK_SOFT,
            tracking=int(2 * s))
    tracked(d, (1268 * s, 1244 * s), "285 G", mono(int(19 * s)), INK_MUTED,
            tracking=int(2 * s))
    d.line([1260 * s, 1212 * s, ccx + 34 * s, ccy + 8 * s], fill=INK_MUTED,
           width=int(2 * s))
    finish(img, "product-tempo-wand.png")


def metronome():
    img, d = canvas()
    s = S
    card_chrome(d, s, "Training aid · Tempo", "Clip-On Swing Metronome",
                "SL-METRONOME", spec="40–208 bpm · clip mount · 21 g")
    cx, cy = 780 * s, 800 * s
    bw, bh = 480 * s, 620 * s
    # spring clip behind body, with pivot pin and gripper teeth
    rrect(d, [cx - 130 * s, cy - bh / 2 - 84 * s, cx + 130 * s, cy - bh / 2 + 46 * s],
          40 * s, fill=GREEN_BTN)
    for tx in (-84, -28, 28, 84):
        d.line([cx + tx * s, cy - bh / 2 - 84 * s + 18 * s,
                cx + tx * s, cy - bh / 2 - 84 * s + 44 * s],
               fill=GREEN_INK, width=int(4 * s))
    screw(d, s, cx - 96 * s, cy - bh / 2 - 18 * s, r=10, ang=90)
    screw(d, s, cx + 96 * s, cy - bh / 2 - 18 * s, r=10, ang=90)
    # body
    rrect(d, [cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], 84 * s, fill=GREEN)
    # corner screws
    for sx in (-1, 1):
        for sy in (-1, 1):
            screw(d, s, cx + sx * (bw / 2 - 52 * s), cy + sy * (bh / 2 - 52 * s),
                  r=9, ang=45 if sx == sy else 135, color=GREEN_BTN)
    # screen
    rrect(d, [cx - bw / 2 + 56 * s, cy - bh / 2 + 76 * s,
              cx + bw / 2 - 56 * s, cy + 40 * s], 40 * s, fill=GREEN_INK)
    f = archivo(int(116 * s), 680, 104)
    d.text((cx, cy - 138 * s), "3 : 1", font=f, fill=GREEN, anchor="mm")
    tracked(d, (cx, cy - 52 * s), "TEMPO", mono(int(26 * s)), INK_MUTED,
            tracking=int(8 * s), anchor="m")
    # bpm scale etched along the screen foot
    d.line([cx - 140 * s, cy + 10 * s, cx + 140 * s, cy + 10 * s],
           fill=INK_MUTED, width=int(2 * s))
    for i in range(15):
        x = cx - 140 * s + i * 20 * s
        h = 10 * s if i % 7 == 0 else 6 * s
        d.line([x, cy + 10 * s - h, x, cy + 10 * s], fill=INK_MUTED, width=int(2 * s))
    d.polygon([(cx - 40 * s, cy + 24 * s), (cx - 48 * s, cy + 36 * s),
               (cx - 32 * s, cy + 36 * s)], fill=GREEN)
    # beat dots row on body (4th outlined — the "1" of 3:1)
    for i, bx in enumerate((-120, -40, 40, 120)):
        r = 20 * s
        box = [cx + bx * s - r, cy + 116 * s - r, cx + bx * s + r, cy + 116 * s + r]
        if i == 3:
            d.ellipse(box, outline=GREEN_INK, width=int(6 * s))
        else:
            d.ellipse(box, fill=GREEN_INK)
    # speaker grille
    for row in range(3):
        for col in range(7):
            gx = cx + (col - 3) * 26 * s
            gy = cy + 196 * s + row * 24 * s
            d.ellipse([gx - 4.5 * s, gy - 4.5 * s, gx + 4.5 * s, gy + 4.5 * s],
                      fill=GREEN_BTN)
    # knurled side dial
    rrect(d, [cx + bw / 2 - 8 * s, cy - 96 * s, cx + bw / 2 + 30 * s, cy + 16 * s],
          16 * s, fill=GREEN_BTN)
    for yy in range(-80, 8, 14):
        d.line([cx + bw / 2 + 2 * s, cy + yy * s, cx + bw / 2 + 22 * s, cy + yy * s],
               fill=GREEN_INK, width=int(2 * s))
    # rhythm arc with beat ticks (the orange gesture), counted 1-2-3-4
    acx, acy, ar = cx + 70 * s, cy + 30 * s, 560 * s
    swing_arc(d, acx, acy, ar, -80, -19, ORANGE, int(10 * s))
    for n, ang in enumerate((-76, -57, -38, -19), start=1):
        x = acx + ar * math.cos(math.radians(ang))
        y = acy + ar * math.sin(math.radians(ang))
        d.ellipse([x - 15 * s, y - 15 * s, x + 15 * s, y + 15 * s], fill=ORANGE)
        lx = acx + (ar + 58 * s) * math.cos(math.radians(ang))
        ly = acy + (ar + 58 * s) * math.sin(math.radians(ang))
        tracked(d, (lx, ly - 13 * s), str(n), mono(int(22 * s)), INK_MUTED,
                anchor="m")
    end = (acx + ar * math.cos(math.radians(-13.5)),
           acy + ar * math.sin(math.radians(-13.5)))
    arrow_head(d, s, end, -13.5 + 90, size=26)
    # dimensions
    dim_line(d, s, (cx - bw / 2, cy + bh / 2 + 74 * s),
             (cx + bw / 2, cy + bh / 2 + 74 * s), "58 MM", side=1)
    dim_line(d, s, (cx - bw / 2 - 74 * s, cy - bh / 2),
             (cx - bw / 2 - 74 * s, cy + bh / 2), "84 MM", side=1, gap=64)
    # clip cross-section inset
    inset_panel(d, s, (150 * s, 1060 * s, 520 * s, 1364 * s),
                caption="SECTION B–B · CLIP")
    rrect(d, [214 * s, 1130 * s, 282 * s, 1330 * s], 14 * s, fill=GREEN)
    d.line([282 * s, 1148 * s, 348 * s, 1160 * s], fill=GREEN_BTN, width=int(11 * s))
    d.line([348 * s, 1160 * s, 326 * s, 1316 * s], fill=GREEN_BTN, width=int(11 * s))
    screw(d, s, 296 * s, 1150 * s, r=11, ang=0, color=INK_MUTED)
    dim_line(d, s, (282 * s, 1338 * s), (326 * s, 1338 * s), "15 MM", side=1,
             gap=26, font_px=17, tick=8)
    tracked(d, (380 * s, 1224 * s), "SPRING", mono(int(17 * s)), INK_SOFT,
            tracking=int(2 * s))
    tracked(d, (380 * s, 1258 * s), "JAW", mono(int(17 * s)), INK_SOFT,
            tracking=int(2 * s))
    finish(img, "product-metronome.png")


def alignment_sticks():
    img, d = canvas()
    s = S
    card_chrome(d, s, "Training aid · Setup", "Alignment Stick Set",
                "SL-ALIGN-3PK", spec="3 × 122 cm · Ø 8 mm · rubber tips")

    def stick(x0, y0, x1, y1):
        x0, y0, x1, y1 = x0 * s, y0 * s, x1 * s, y1 * s
        ln = math.hypot(x1 - x0, y1 - y0)
        ux, uy = (x1 - x0) / ln, (y1 - y0) / ln
        nx, ny = -uy, ux
        d.line([x0, y0, x1, y1], fill=GREEN, width=int(24 * s))
        for tx, ty in ((x0, y0), (x1, y1)):
            d.ellipse([tx - 12 * s, ty - 12 * s, tx + 12 * s, ty + 12 * s], fill=GREEN)
        # rubber end caps
        for t in (0.0, 1.0):
            px_, py_ = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            qx, qy = x0 + (x1 - x0) * abs(t - 0.045), y0 + (y1 - y0) * abs(t - 0.045)
            d.line([px_, py_, qx, qy], fill=GREEN_BTN, width=int(30 * s))
        # 10 cm calibration ticks, long marks every 30 cm
        for k in range(1, 12):
            t = k * 10 / 122
            px_, py_ = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            r = (21 if k % 3 == 0 else 14) * s
            w = 4 * s if k % 3 == 0 else 3 * s
            d.line([px_ - nx * r, py_ - ny * r, px_ + nx * r, py_ + ny * r],
                   fill=GREEN_INK, width=int(w))

    # crossing stick first (ground line), then the parallel gate pair
    stick(390, 1010, 1250, 706)
    stick(450, 370, 720, 1230)
    stick(670, 350, 940, 1210)
    # 30 cm numerals along the crossing stick
    for k, cm in ((3, "30"), (6, "60"), (9, "90")):
        t = k * 10 / 122
        px_, py_ = (390 + 860 * t) * s, (1010 - 304 * t) * s
        tracked(d, (px_, py_ + 34 * s), cm, mono(int(17 * s)), INK_MUTED, anchor="m")
    # gate dimension beyond the top ends
    a_ext = ((450 - 0.07 * 270) * s, (370 - 0.07 * 860) * s)
    b_ext = ((670 - 0.07 * 270) * s, (350 - 0.07 * 860) * s)
    dim_line(d, s, a_ext, b_ext, "GATE 21 CM", side=-1, gap=42)
    # stick length dimension parallel to the right stick
    ln = math.hypot(270, 860)
    ux, uy = 270 / ln, 860 / ln
    nx, ny = uy, -ux            # offset to the upper-right
    off = 150 * s
    dim_line(d, s, ((670 * s) + nx * off, (350 * s) + ny * off),
             ((940 * s) + nx * off, (1210 * s) + ny * off),
             "122 CM / 48 IN", side=-1, gap=46)
    # orange ball-path gesture through the gate
    m0 = ((560 + 270 * 1.02) * s, (360 + 860 * 1.02) * s)
    m1 = ((560 + 270 * 0.22) * s, (360 + 860 * 0.22) * s)
    dashed_line(d, m0, m1, ORANGE, int(10 * s), dash=(30 * s, 22 * s))
    ang = math.degrees(math.atan2(m1[1] - m0[1], m1[0] - m0[0]))
    arrow_head(d, s, m1, ang, size=27)
    tracked(d, (582 * s, 408 * s), "START LINE",
            mono(int(19 * s)), INK_MUTED, tracking=int(3 * s), anchor="m")
    # ball at the start of the path
    bx, by = m0
    d.ellipse([bx - 26 * s, by - 26 * s, bx + 26 * s, by + 26 * s],
              fill=CARD, outline=GREEN, width=int(5 * s))
    for aa in (210, 250, 290):
        x = bx + 12 * s * math.cos(math.radians(aa))
        y = by + 12 * s * math.sin(math.radians(aa))
        d.ellipse([x - 2.5 * s, y - 2.5 * s, x + 2.5 * s, y + 2.5 * s], fill=GREEN)
    # ground dots
    for x in range(360, 1070, 60):
        d.ellipse([x * s - 4 * s, 1300 * s - 4 * s, x * s + 4 * s, 1300 * s + 4 * s],
                  fill=BORDER)
    # cross-section inset: glass-fiber core
    inset_panel(d, s, (1100 * s, 1050 * s, 1430 * s, 1330 * s),
                caption="SECTION C–C")
    ccx, ccy = 1190 * s, 1210 * s
    d.ellipse([ccx - 60 * s, ccy - 60 * s, ccx + 60 * s, ccy + 60 * s],
              outline=GREEN, width=int(7 * s))
    for off_ in range(-48, 52, 12):      # diagonal hatch inside the bore
        x0 = ccx + off_ * s - 34 * s
        x1 = ccx + off_ * s + 34 * s
        # clip hatch to the circle by shortening near edges
        d.line([max(x0, ccx - 52 * s), ccy + 34 * s - (x1 - x0) / 2 * 0,
                min(x1, ccx + 52 * s), ccy - 34 * s + 0], fill=GREEN_BTN,
               width=int(2 * s))
    tracked(d, (1272 * s, 1166 * s), "Ø 8 MM", mono(int(19 * s)), INK_SOFT,
            tracking=int(2 * s))
    tracked(d, (1272 * s, 1206 * s), "GLASS", mono(int(19 * s)), INK_MUTED,
            tracking=int(2 * s))
    tracked(d, (1272 * s, 1240 * s), "FIBER", mono(int(19 * s)), INK_MUTED,
            tracking=int(2 * s))
    finish(img, "product-alignment-sticks.png")


def hip_band():
    img, d = canvas()
    s = S
    card_chrome(d, s, "Training aid · Hips", "Anti-Sway Hip Resistance Band",
                "SL-HIP-BAND", spec="fits 76–120 cm · 50 mm web")
    cx, cy = 790 * s, 780 * s
    rx, ry = 430 * s, 245 * s
    th = 62 * s
    # webbing loop: solid ring
    d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=GREEN)
    d.ellipse([cx - rx + th, cy - ry + th, cx + rx - th, cy + ry - th], fill=BG)
    # rib-knit texture: radial ticks marching around the ring
    for ang in range(0, 360, 5):
        a = math.radians(ang)
        x0 = cx + (rx - th + 12 * s) * math.cos(a)
        y0 = cy + (ry - th + 12 * s) * math.sin(a)
        x1 = cx + (rx - 12 * s) * math.cos(a)
        y1 = cy + (ry - 12 * s) * math.sin(a)
        d.line([x0, y0, x1, y1], fill=GREEN_BTN, width=int(2.5 * s))
    # box-X stitched anchor patch on the left of the loop
    px0, py0, px1, py1 = cx - rx - 20 * s, cy - 48 * s, cx - rx + 88 * s, cy + 60 * s
    rrect(d, [px0, py0, px1, py1], 14 * s, fill=GREEN)
    for a_, b_ in (((px0, py0), (px1, py0)), ((px1, py0), (px1, py1)),
                   ((px1, py1), (px0, py1)), ((px0, py1), (px0, py0)),
                   ((px0, py0), (px1, py1)), ((px0, py1), (px1, py0))):
        dashed_line(d, (a_[0] + 8 * s * (1 if a_[0] <= b_[0] else -1), a_[1]),
                    b_, GREEN_INK, int(3 * s), dash=(10 * s, 8 * s))
    # cam adjuster on the right, with strap tail
    rrect(d, [cx + rx - 96 * s, cy - 118 * s, cx + rx + 44 * s, cy + 34 * s],
          28 * s, fill=GREEN)
    for yy in (-78, -40, -2):
        d.line([cx + rx - 70 * s, cy + yy * s, cx + rx + 18 * s, cy + yy * s],
               fill=GREEN_INK, width=int(5 * s))
    rrect(d, [cx + rx + 44 * s, cy - 70 * s, cx + rx + 152 * s, cy - 22 * s],
          12 * s, fill=GREEN_BTN)
    dashed_line(d, (cx + rx + 56 * s, cy - 46 * s), (cx + rx + 140 * s, cy - 46 * s),
                GREEN_INK, int(2.5 * s), dash=(9 * s, 7 * s))
    # rotation cue: dashed ORANGE arrow hugging the loop's outer edge
    prx, pry = rx + 95 * s, ry + 95 * s
    pts = []
    for i in range(0, 121):
        t = math.radians(200 + i)
        pts.append((cx + prx * math.cos(t), cy + pry * math.sin(t)))
    for i in range(0, 114, 6):
        if (i // 6) % 2 == 0:
            d.line(pts[i:i + 5], fill=ORANGE, width=int(10 * s), joint="curve")
    ex, ey = pts[-1]
    ang = math.degrees(math.atan2(ey - pts[-5][1], ex - pts[-5][0]))
    arrow_head(d, s, (ex, ey), ang, size=27)
    # specimen callouts
    callout(d, s, (cx + (rx - th / 2) * math.cos(math.radians(238)),
                   cy + (ry - th / 2) * math.sin(math.radians(238))),
            (430 * s, 470 * s), "50 MM KNIT WEB", align="r", font_px=19)
    callout(d, s, (cx + rx - 26 * s, cy + 34 * s), (1310 * s, 930 * s),
            "CAM ADJUSTER", align="r", font_px=19)
    callout(d, s, (cx - rx + 34 * s, cy + 60 * s), (330 * s, 980 * s),
            "BOX-X STITCH", align="l", font_px=19)
    # relaxed-diameter dimension under the loop
    dim_line(d, s, (cx - rx, cy + ry + 74 * s), (cx + rx, cy + ry + 74 * s),
             "Ø 27 CM RELAXED", side=1)
    # web cross-section inset
    inset_panel(d, s, (150 * s, 1130 * s, 520 * s, 1372 * s),
                caption="SECTION D–D · WEB")
    lx0, lx1 = 190 * s, 352 * s
    for yy, hh, core in ((1218, 14, False), (1240, 34, True), (1282, 14, False)):
        box = [lx0, yy * s, lx1, (yy + hh) * s]
        if core:
            rrect(d, box, 6 * s, fill=GREEN)
        else:
            rrect(d, box, 5 * s, outline=GREEN_BTN, width=int(2.5 * s))
            for xx in range(int(lx0) + int(8 * s), int(lx1) - int(4 * s),
                            int(14 * s)):
                d.line([xx, (yy + hh) * s, xx + 8 * s, yy * s],
                       fill=GREEN_BTN, width=int(2 * s))
    tracked(d, (376 * s, 1216 * s), "SHELL", mono(int(17 * s)), INK_MUTED,
            tracking=int(2 * s))
    tracked(d, (376 * s, 1248 * s), "LATEX", mono(int(17 * s)), INK_SOFT,
            tracking=int(2 * s))
    tracked(d, (376 * s, 1282 * s), "SHELL", mono(int(17 * s)), INK_MUTED,
            tracking=int(2 * s))
    finish(img, "product-hip-band.png")


def swing_mirror():
    img, d = canvas()
    s = S
    card_chrome(d, s, "Training aid · Positions", "Full-Length Swing Mirror",
                "SL-MIRROR", spec="90 × 30 cm · acrylic · folds flat")
    cx = 760 * s
    top, bot = 320 * s, 1250 * s
    w = 340 * s
    # kickstand behind, with hinge bracket and rubber foot
    leg_end = (cx + w / 2 + 250 * s, bot + 14 * s)
    d.line([cx + w / 2 - 16 * s, top + 130 * s, *leg_end],
           fill=GREEN_BTN, width=int(20 * s))
    d.ellipse([leg_end[0] - 16 * s, leg_end[1] - 16 * s,
               leg_end[0] + 16 * s, leg_end[1] + 16 * s], fill=GREEN)
    # frame + glass
    rrect(d, [cx - w / 2 - 22 * s, top - 22 * s, cx + w / 2 + 22 * s, bot + 22 * s],
          60 * s, fill=GREEN)
    rrect(d, [cx - w / 2, top, cx + w / 2, bot], 44 * s, fill=GREEN_INK)
    # hinge bracket straddling the frame's right edge
    hinge = (cx + w / 2 + 26 * s, top + 128 * s)
    rrect(d, [hinge[0] - 30 * s, hinge[1] - 28 * s,
              hinge[0] + 26 * s, hinge[1] + 28 * s], 10 * s, fill=GREEN_BTN)
    screw(d, s, hinge[0] - 2 * s, hinge[1], r=11, ang=30, color=GREEN_INK)
    # mitre joints at the frame corners
    for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
        x0 = cx + sx * (w / 2 + 20 * s)
        y0 = (top - 20 * s) if sy < 0 else (bot + 20 * s)
        d.line([x0, y0 - sy * 0, x0 - sx * 20 * s, y0 + sy * 20 * s],
               fill=GREEN_BTN, width=int(2.5 * s))
    # glass glints
    for gx in (0, 34):
        d.line([cx - 120 * s + gx * s, top + 44 * s, cx - 44 * s + gx * s,
                top + 128 * s], fill=CARD, width=int(6 * s))
    # ghost address figure reflected in the glass
    gcol = "#c5d8ca"
    gx0, gy0 = cx - 10 * s, top + 200 * s          # head
    d.ellipse([gx0 - 34 * s, gy0 - 34 * s, gx0 + 34 * s, gy0 + 34 * s],
              outline=gcol, width=int(7 * s))
    neck = (cx - 8 * s, top + 244 * s)
    hip = (cx - 14 * s, top + 560 * s)
    d.line([*neck, *hip], fill=gcol, width=int(7 * s))
    d.line([cx - 76 * s, top + 276 * s, cx + 62 * s, top + 276 * s],
           fill=gcol, width=int(7 * s))            # shoulders
    for kx in (-56, 36):                           # legs
        d.line([hip[0], hip[1], hip[0] + kx * s, top + 866 * s],
               fill=gcol, width=int(7 * s))
    # shoulder-turn protractor etched near the top of the glass
    pcx, pcy, prr = cx, top + 300 * s, 190 * s
    swing_arc(d, pcx, pcy, prr, 205, 335, GREEN_BTN, int(3 * s))
    for ang in range(205, 336, 13):
        a = math.radians(ang)
        r0 = prr - (16 * s if (ang - 205) % 26 == 0 else 9 * s)
        d.line([pcx + r0 * math.cos(a), pcy + r0 * math.sin(a),
                pcx + prr * math.cos(a), pcy + prr * math.sin(a)],
               fill=GREEN_BTN, width=int(2.5 * s))
    # centerline decal (the orange gesture) with calibration ticks
    d.line([cx, top + 40 * s, cx, bot - 40 * s], fill=ORANGE, width=int(10 * s))
    for i, y in enumerate(range(int(top + 80 * s), int(bot - 60 * s), int(90 * s))):
        d.line([cx - 26 * s, y, cx + 26 * s, y], fill=ORANGE, width=int(5 * s))
    for cm, yy in (("30", bot - 150 * s), ("60", bot - 420 * s),
                   ("90", bot - 690 * s)):
        tracked(d, (cx + 44 * s, yy - 12 * s), cm, mono(int(16 * s)), INK_MUTED)
    # base foot
    rrect(d, [cx - w / 2 - 60 * s, bot + 10 * s, cx + w / 2 + 60 * s, bot + 46 * s],
          20 * s, fill=GREEN)
    # dimensions
    dim_line(d, s, (cx - w / 2 - 96 * s, top - 22 * s),
             (cx - w / 2 - 96 * s, bot + 22 * s), "90 CM", side=1, gap=66)
    dim_line(d, s, (cx - w / 2 - 22 * s, bot + 92 * s),
             (cx + w / 2 + 22 * s, bot + 92 * s), "30 CM", side=1, gap=34)
    # kickstand angle
    swing_arc(d, hinge[0], hinge[1], 130 * s, 73, 90, INK_MUTED, int(2.5 * s))
    tracked(d, (hinge[0] + 60 * s, hinge[1] + 150 * s), "17°", mono(int(18 * s)),
            INK_MUTED)
    # hinge detail magnifier
    mcx, mcy, mr = 1240 * s, 330 * s, 116 * s
    d.line([hinge[0] + 20 * s, hinge[1] - 16 * s, mcx - mr * 0.7, mcy + mr * 0.7],
           fill=INK_MUTED, width=int(2 * s))
    d.ellipse([mcx - mr, mcy - mr, mcx + mr, mcy + mr], fill=CARD,
              outline=BORDER, width=int(3 * s))
    rrect(d, [mcx - 62 * s, mcy - 34 * s, mcx + 50 * s, mcy + 18 * s], 8 * s,
          fill=GREEN_BTN)
    d.line([mcx - 4 * s, mcy + 14 * s, mcx + 52 * s, mcy + 86 * s],
           fill=GREEN_BTN, width=int(13 * s))
    screw(d, s, mcx - 6 * s, mcy - 8 * s, r=17, ang=30, color=GREEN_INK, lw=3)
    tracked(d, (mcx, mcy + mr + 22 * s), "DETAIL E · HINGE", mono(int(17 * s)),
            INK_MUTED, tracking=int(2 * s), anchor="m")
    finish(img, "product-swing-mirror.png")


def performance_cap():
    img, d = canvas()
    s = S
    card_chrome(d, s, "Apparel", "CaddieInsight Performance Cap", "SL-CAP",
                spec="one size 58–62 cm · cotton twill")
    cx, cy = 830 * s, 780 * s
    # crown: half-ellipse
    d.pieslice([cx - 330 * s, cy - 330 * s, cx + 330 * s, cy + 330 * s],
               180, 360, fill=GREEN)
    # panel seams with running-stitch overlays
    for rx in (330, 208, 84):
        d.arc([cx - rx * s, cy - 330 * s, cx + rx * s, cy + 330 * s],
              182, 358, fill=GREEN_BTN, width=int(5 * s))
        for a in range(190, 352, 10):
            d.arc([cx - (rx - 12) * s, cy - 318 * s, cx + (rx - 12) * s,
                   cy + 318 * s], a, a + 4, fill=GREEN_INK, width=int(2.5 * s))
    # embroidered eyelets
    for ex, ey in ((cx - 148 * s, cy - 148 * s), (cx + 156 * s, cy - 138 * s)):
        d.ellipse([ex - 15 * s, ey - 15 * s, ex + 15 * s, ey + 15 * s],
                  outline=GREEN_INK, width=int(4 * s))
        for ang in range(0, 360, 45):
            a = math.radians(ang)
            d.line([ex + 17 * s * math.cos(a), ey + 17 * s * math.sin(a),
                    ex + 24 * s * math.cos(a), ey + 24 * s * math.sin(a)],
                   fill=GREEN_INK, width=int(2 * s))
    # button
    d.ellipse([cx - 16 * s, cy - 348 * s, cx + 16 * s, cy - 316 * s],
              fill=GREEN_BTN, outline=GREEN_INK, width=int(2.5 * s))
    # brim sweeping left
    d.pieslice([cx - 660 * s, cy - 60 * s, cx + 40 * s, cy + 150 * s],
               0, 180, fill=GREEN)
    d.pieslice([cx - 660 * s, cy - 100 * s, cx + 40 * s, cy + 110 * s],
               0, 180, fill=GREEN_BTN)
    # brim stitch rows
    for k in range(1, 5):
        d.arc([cx - 660 * s + k * 20 * s, cy - 100 * s + k * 8 * s,
               cx + 40 * s - k * 20 * s, cy + 110 * s - k * 8 * s],
              14, 166, fill=GREEN_INK, width=int(2.5 * s))
    # sweatband line with stitch dashes
    d.line([cx - 330 * s, cy, cx + 330 * s, cy], fill=GREEN_BTN, width=int(14 * s))
    dashed_line(d, (cx - 310 * s, cy - 16 * s), (cx + 310 * s, cy - 16 * s),
                GREEN_INK, int(2.5 * s), dash=(12 * s, 9 * s))
    # snapback adjuster at the back
    rrect(d, [cx + 316 * s, cy - 42 * s, cx + 468 * s, cy + 8 * s], 18 * s,
          fill=GREEN)
    rrect(d, [cx + 378 * s, cy - 54 * s, cx + 430 * s, cy + 20 * s], 10 * s,
          fill=GREEN_BTN)
    d.line([cx + 404 * s, cy - 46 * s, cx + 404 * s, cy + 12 * s],
           fill=GREEN_INK, width=int(3 * s))
    for hx in (438, 452):
        d.ellipse([cx + hx * s - 4 * s, cy - 21 * s, cx + hx * s + 4 * s,
                   cy - 13 * s], fill=GREEN_INK)
    # brand arc mark on the front panel (the orange gesture)
    swing_arc(d, cx - 80 * s, cy - 70 * s, 150 * s, 190, 262, ORANGE, int(14 * s))
    bx = cx - 80 * s + 150 * s * math.cos(math.radians(262))
    by = cy - 70 * s + 150 * s * math.sin(math.radians(262))
    d.ellipse([bx - 14 * s, by - 14 * s, bx + 14 * s, by + 14 * s], fill=GREEN_INK)
    # specimen callouts
    callout(d, s, (cx + 156 * s, cy - 138 * s), (1190 * s, 520 * s),
            "EYELET ×6", align="l", font_px=19)
    callout(d, s, (cx + 404 * s, cy + 16 * s), (1310 * s, 900 * s),
            "58–62 CM", align="l", font_px=19)
    callout(d, s, (cx - 500 * s, cy + 96 * s), (390 * s, 990 * s),
            "4-ROW STITCH", align="l", font_px=19)
    # brim projection dimension
    dim_line(d, s, (170 * s, 1120 * s), (500 * s, 1120 * s), "BRIM 70 MM",
             side=1, gap=38)
    # fabric swatch
    inset_panel(d, s, (1150 * s, 1080 * s, 1430 * s, 1330 * s),
                caption="FABRIC · TWILL")
    sx0, sy0, sx1, sy1 = 1184 * s, 1150 * s, 1304 * s, 1270 * s
    rrect(d, [sx0, sy0, sx1, sy1], 8 * s, fill=GREEN)
    for off in range(-110, 130, 16):
        x0 = max(sx0, sx0 + off * s)
        y0 = min(sy1, sy1 + off * s) if off < 0 else sy1
        # simple 45° twill lines clipped to the square
        a0 = (sx0 + max(0, off) * s, sy1 + min(0, off) * s)
        a1 = (sx1 + min(0, off) * s, sy0 + max(0, off) * s)
        d.line([a0, a1], fill=GREEN_BTN, width=int(2.5 * s))
    tracked(d, (1330 * s, 1176 * s), "2/1", mono(int(19 * s)), INK_SOFT,
            tracking=int(2 * s))
    tracked(d, (1330 * s, 1214 * s), "280", mono(int(19 * s)), INK_MUTED,
            tracking=int(2 * s))
    tracked(d, (1330 * s, 1248 * s), "GSM", mono(int(19 * s)), INK_MUTED,
            tracking=int(2 * s))
    finish(img, "product-cap.png")


def pro_membership():
    img, d = canvas()
    s = S
    # phone
    cx, cy = 800 * s, 790 * s
    pw, ph = 440 * s, 860 * s
    rrect(d, [cx - pw / 2 - 18 * s, cy - ph / 2 - 18 * s,
              cx + pw / 2 + 18 * s, cy + ph / 2 + 18 * s], 74 * s, fill=GREEN)
    rrect(d, [cx - pw / 2, cy - ph / 2, cx + pw / 2, cy + ph / 2], 58 * s, fill=CARD)
    # notch
    rrect(d, [cx - 70 * s, cy - ph / 2 + 18 * s, cx + 70 * s, cy - ph / 2 + 40 * s],
          12 * s, fill=GREEN)
    # screen: captured (orange) vs corrected (green) centerline overlay
    ax, ay = cx - 10 * s, cy + ph / 2 - 120 * s      # ankle pin
    # corrected: straight green line up
    d.line([ax, ay, ax, cy - ph / 2 + 150 * s], fill=GREEN_BTN, width=int(12 * s))
    d.ellipse([ax - 26 * s, cy - ph / 2 + 96 * s, ax + 26 * s, cy - ph / 2 + 148 * s],
              outline=GREEN_BTN, width=int(10 * s))
    # captured: sheared orange line
    tx = ax - 120 * s
    d.line([ax, ay, tx, cy - ph / 2 + 170 * s], fill=ORANGE, width=int(12 * s))
    d.ellipse([tx - 26 * s, cy - ph / 2 + 116 * s, tx + 26 * s, cy - ph / 2 + 168 * s],
              outline=ORANGE, width=int(10 * s))
    # ankle pin
    d.ellipse([ax - 16 * s, ay - 16 * s, ax + 16 * s, ay + 16 * s], fill=GREEN)
    # metric chips
    for i, label in enumerate(("TEMPO 3.1", "SWAY 0.2")):
        chy = cy - 60 * s + i * 90 * s
        rrect(d, [cx + 40 * s, chy, cx + pw / 2 - 34 * s, chy + 62 * s],
              31 * s, outline=BORDER, width=int(4 * s))
        tracked(d, (cx + 66 * s, chy + 16 * s), label, mono(int(21 * s)), INK_SOFT,
                tracking=int(2 * s))
    # PRO badge overlapping phone corner
    bx, by = cx + pw / 2 - 30 * s, cy - ph / 2 + 10 * s
    rrect(d, [bx - 110 * s, by - 56 * s, bx + 110 * s, by + 56 * s], 56 * s, fill=ORANGE)
    f = archivo(int(64 * s), 740, 108)
    d.text(((bx - 110 * s + bx + 110 * s) / 2, by), "PRO", font=f, fill=CARD, anchor="mm")
    card_chrome(d, s, "Membership · Digital", "CaddieInsight Pro", "SL-PRO")
    finish(img, "product-pro.png")


# ---------------------------------------------------------------- brand ----

def _pol(cx, cy, r, ang):
    a = math.radians(ang)
    return cx + r * math.cos(a), cy + r * math.sin(a)


def mark_protractor(d, cx, cy, R, ink=GREEN, sweep=ORANGE, tick_step=5,
                    bold=1.0):
    """The CaddieInsight mark: a protractor gauge caught mid-swing. Calibration
    tick fan in ink, the orange sweep inside it, the ball at the sweep's
    terminus, a needle from the pivot aimed at the ball.

    tick_step coarsens the fan and `bold` thickens strokes for tiny sizes
    (favicon); every third tick is a major regardless of step.
    """
    for ang in range(262, 353, tick_step):
        major = ((ang - 262) // tick_step) % 3 == 0
        r0 = R - (0.17 * R if major else 0.095 * R)
        w = max(int((0.028 if major else 0.019) * bold * R), 2)
        d.line([*_pol(cx, cy, r0, ang), *_pol(cx, cy, R, ang)], fill=ink,
               width=w)
    aw = int(0.115 * bold * R)
    swing_arc(d, cx, cy, 0.72 * R, 262, 333, sweep, aw)
    rmid = 0.72 * R - aw / 2
    bx, by = _pol(cx, cy, rmid, 348)
    br = 0.135 * R
    # needle: pivot aims at the ball, gauge-style. Kept sturdy — hairlines
    # are what wash out at favicon scale.
    d.line([*_pol(cx, cy, 0.10 * R, 348),
            *_pol(cx, cy, rmid - br - 0.03 * R, 348)],
           fill=ink, width=max(int(0.034 * bold * R), 2))
    d.ellipse([bx - br, by - br, bx + br, by + br], fill=ink)
    pr = 0.06 * R
    d.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], fill=ink)


def _wordmark(d, x, y, px, ink=GREEN):
    """Heavy Archivo 'CaddieInsight', tight tracking, single ink. The orange
    in the lockup stays reserved for the gauge's kinetic sweep — one orange
    gesture per composition, per the house rules. Returns total width."""
    f = archivo(int(px), 770, 102)
    t = int(-0.014 * px)
    w1 = tracked(d, (x, y), "Caddie", f, ink, tracking=t)
    w2 = tracked(d, (x + w1 + t, y), "Insight", f, ink, tracking=t)
    return w1 + t + w2


def logo(inverse=False):
    """Premium lockup: dual-arc precision gauge + heavy wordmark.

    The inverse is re-inked in mint for deep-green / near-black contexts —
    the orange sweep is the one kinetic color both versions share. Output
    filenames keep the historical swinglab- names so every CDN and theme
    reference keeps resolving.
    """
    sc = 4
    ink = GREEN_INK if inverse else GREEN
    img = Image.new("RGBA", (2000 * sc, 360 * sc), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy, R = 220 * sc, 250 * sc, 170 * sc
    for i, ang in enumerate(range(200, 341, 7)):
        rad = math.radians(ang)
        inner = R - (18 if i % 2 == 0 else 10) * sc
        outer = R + 2 * sc
        d.line(
            [
                cx + inner * math.cos(rad),
                cy + inner * math.sin(rad),
                cx + outer * math.cos(rad),
                cy + outer * math.sin(rad),
            ],
            fill=ink,
            width=max(2, int(2.2 * sc)),
        )
    box = [cx - R, cy - R, cx + R, cy + R]
    d.arc(box, 205, 335, fill=ink, width=int(10 * sc))
    r2 = R - 28 * sc
    box2 = [cx - r2, cy - r2, cx + r2, cy + r2]
    d.arc(box2, 215, 300, fill=ORANGE, width=int(16 * sc))
    a = math.radians(300)
    bx = cx + r2 * math.cos(a)
    by = cy + r2 * math.sin(a)
    d.ellipse([bx - 14 * sc, by - 14 * sc, bx + 14 * sc, by + 14 * sc], fill=ORANGE)
    d.ellipse([cx - 22 * sc, cy - 22 * sc, cx + 22 * sc, cy + 22 * sc], fill=ink)
    pivot_fill = GREEN_INK if inverse else ORANGE
    d.ellipse([cx - 10 * sc, cy - 10 * sc, cx + 10 * sc, cy + 10 * sc], fill=pivot_fill)
    f = archivo(int(148 * sc), 750, 100)
    x0, y0 = 440 * sc, 90 * sc
    tracking = int(-1.5 * sc)
    w1 = tracked(d, (x0, y0), "Caddie", f, ink, tracking=tracking)
    w2 = tracked(d, (x0 + w1 + 6 * sc, y0), "Insight", f, ink, tracking=tracking)
    d.line(
        [x0 + w1 + 6 * sc, y0 + 170 * sc, x0 + w1 + 6 * sc + w2, y0 + 170 * sc],
        fill=ORANGE,
        width=int(5 * sc),
    )
    img = img.crop(img.getbbox())
    k = min(1400 / img.width, 276 / img.height)
    img = img.resize((round(img.width * k), round(img.height * k)),
                     Image.LANCZOS)
    name = "swinglab-logo-inverse.png" if inverse else "swinglab-logo.png"
    img.save(OUT / name)
    print("wrote", OUT / name)


def favicon():
    """512 tile: deep-green rounded square, the gauge mark re-inked in mint.
    Coarser, bolder tick fan so the gauge still reads at 32 px."""
    w, sc = 512, 4
    img = Image.new("RGBA", (w * sc, w * sc), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rrect(d, [0, 0, w * sc, w * sc], 110 * sc, fill=GREEN)
    lay = Image.new("RGBA", (800 * sc, 800 * sc), (0, 0, 0, 0))
    dl = ImageDraw.Draw(lay)
    mark_protractor(dl, 110 * sc, 690 * sc, 560 * sc, ink=GREEN_INK,
                    tick_step=8, bold=1.45)
    lay = lay.crop(lay.getbbox())
    box = (w - 2 * 66) * sc
    k = min(box / lay.width, box / lay.height)
    lay = lay.resize((round(lay.width * k), round(lay.height * k)),
                     Image.LANCZOS)
    img.paste(lay, ((w * sc - lay.width) // 2, (w * sc - lay.height) // 2), lay)
    img = img.resize((w, w), Image.LANCZOS)
    img.save(OUT / "swinglab-favicon.png")
    print("wrote", OUT / "swinglab-favicon.png")


def collection_banner():
    wpx, hpx = 1600, 900
    img, d = canvas(wpx, hpx)
    s = S
    # swing arcs held to the right half, clear of the copy
    swing_arc(d, 1980 * s, 60 * s, 780 * s, 95, 195, ARC_FAINT, int(4 * s), dash=(2.2, 2.6))
    swing_arc(d, 1980 * s, 60 * s, 620 * s, 100, 190, ORANGE, int(12 * s))
    bx = 1980 * s + (620 - 6) * s * math.cos(math.radians(190))
    by = 60 * s + (620 - 6) * s * math.sin(math.radians(190))
    d.ellipse([bx - 24 * s, by - 24 * s, bx + 24 * s, by + 24 * s], fill=GREEN)
    tracked(d, (110 * s, 330 * s), "CADDIEINSIGHT", archivo(int(30 * s), 640, 104),
            GREEN, tracking=int(5 * s))
    d.text((104 * s, 400 * s), "Train what the\nreport flagged.",
           font=archivo(int(96 * s), 680, 104), fill=INK, spacing=int(18 * s))
    tracked(d, (110 * s, 700 * s), "TRAINING AIDS MATCHED TO YOUR SWING",
            mono(int(26 * s)), INK_MUTED, tracking=int(5 * s))
    finish(img, "collection-gear.png", wpx, hpx)


if __name__ == "__main__":
    tempo_wand()
    metronome()
    alignment_sticks()
    hip_band()
    swing_mirror()
    performance_cap()
    pro_membership()
    logo(False)
    logo(True)
    favicon()
    collection_banner()
