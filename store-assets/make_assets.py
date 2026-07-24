"""SwingLab brand assets — Fairway Modernism.

Flat catalog illustrations: warm off-white field, deep green ink, one orange
kinetic gesture per piece, systematic corner labels. Everything is drawn at
SS× and downscaled for clean edges.
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


# ---------------------------------------------------------------- chrome ----

def card_chrome(draw, s, category: str, title: str, sku: str):
    """The systematic frame every product card shares."""
    m = 110 * s
    # faint brand arc across the field
    swing_arc(draw, 1600 * s * 0.86, 1600 * s * 0.16, 1120 * s, 95, 175,
              ARC_FAINT, int(3 * s), dash=(2.2, 2.6))
    # top-left wordmark
    tracked(draw, (m, m - 8 * s), "SWINGLAB", archivo(int(30 * s), 640, 104),
            GREEN, tracking=int(11 * s))
    # top-right category, mono
    tracked(draw, (1600 * s - m, m - 4 * s), category.upper(), mono(int(24 * s)),
            INK_MUTED, tracking=int(4 * s), anchor="r")
    # bottom-left title
    draw.text((m, 1600 * s - m - 44 * s), title, font=archivo(int(46 * s), 620, 102),
              fill=INK)
    # bottom-right sku
    tracked(draw, (1600 * s - m, 1600 * s - m - 26 * s), sku, mono(int(24 * s)),
            INK_MUTED, tracking=int(3 * s), anchor="r")
    # hairline separators under header / above footer
    draw.line([m, 176 * s, 1600 * s - m, 176 * s], fill=BORDER, width=int(2 * s))
    draw.line([m, 1600 * s - 176 * s, 1600 * s - m, 1600 * s - 176 * s],
              fill=BORDER, width=int(2 * s))


# ---------------------------------------------------------------- products ----

def tempo_wand():
    img, d = canvas()
    s = S
    # flexed shaft: quadratic bezier from grip (lower-left) to weighted head (upper-right)
    p0 = (430 * s, 1210 * s)
    p1 = (760 * s, 950 * s)      # control — bows the shaft
    p2 = (1130 * s, 430 * s)
    pts = []
    for i in range(101):
        t = i / 100
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
        pts.append((x, y))
    # orange whip section (upper 60%)
    for i in range(40, 100):
        w = int((14 + (100 - i) * 0.16) * s)
        d.line([pts[i], pts[i + 1] if i < 100 else pts[i]], fill=ORANGE, width=w)
    # grip section (lower 40%) in deep green, thicker
    for i in range(0, 41):
        w = int(30 * s)
        if i < 40:
            d.line([pts[i], pts[i + 1]], fill=GREEN, width=w)
    # grip stitching ticks
    for i in range(4, 38, 6):
        x, y = pts[i]
        nx, ny = pts[i + 1]
        ang = math.atan2(ny - y, nx - x) + math.pi / 2
        r = 20 * s
        d.line([x - r * math.cos(ang), y - r * math.sin(ang),
                x + r * math.cos(ang), y + r * math.sin(ang)],
               fill=GREEN_INK, width=int(3 * s))
    # butt cap
    d.ellipse([p0[0] - 24 * s, p0[1] - 24 * s, p0[0] + 24 * s, p0[1] + 24 * s], fill=GREEN)
    # weighted head ball
    hx, hy = p2
    d.ellipse([hx - 62 * s, hy - 62 * s, hx + 62 * s, hy + 62 * s], fill=GREEN)
    d.ellipse([hx - 62 * s, hy - 62 * s, hx + 62 * s, hy + 62 * s],
              outline=GREEN, width=int(6 * s))
    # dimple highlights on head
    for ang in (200, 235, 270):
        x = hx + 30 * s * math.cos(math.radians(ang))
        y = hy + 30 * s * math.sin(math.radians(ang))
        d.ellipse([x - 5 * s, y - 5 * s, x + 5 * s, y + 5 * s], fill=GREEN_INK)
    # dashed trace of the head's path, pivoting around the grip
    pr = math.dist(p0, p2)
    pa = math.degrees(math.atan2(p2[1] - p0[1], p2[0] - p0[0]))
    swing_arc(d, p0[0], p0[1], pr, pa - 34, pa - 6, INK_MUTED, int(5 * s), dash=(1.6, 2.4))
    card_chrome(d, s, "Training aid · Tempo", "Tempo Trainer Swing Wand", "SL-TEMPO-WAND")
    finish(img, "product-tempo-wand.png")


def metronome():
    img, d = canvas()
    s = S
    cx, cy = 800 * s, 810 * s
    bw, bh = 480 * s, 620 * s
    # clip behind body
    rrect(d, [cx - 120 * s, cy - bh / 2 - 74 * s, cx + 120 * s, cy - bh / 2 + 40 * s],
          40 * s, fill=GREEN_BTN)
    # body
    rrect(d, [cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], 84 * s, fill=GREEN)
    # screen
    rrect(d, [cx - bw / 2 + 56 * s, cy - bh / 2 + 76 * s,
              cx + bw / 2 - 56 * s, cy + 40 * s], 40 * s, fill=GREEN_INK)
    # 3 : 1 ratio on screen
    f = archivo(int(120 * s), 680, 104)
    d.text((cx, cy - 130 * s), "3 : 1", font=f, fill=GREEN, anchor="mm")
    tracked(d, (cx, cy - 40 * s), "TEMPO", mono(int(26 * s)), INK_MUTED,
            tracking=int(8 * s), anchor="m")
    # beat dots row on body
    for i, bx in enumerate((-120, -40, 40, 120)):
        r = 22 * s
        col = ORANGE if i == 3 else GREEN_INK
        d.ellipse([cx + bx * s - r, cy + 120 * s - r, cx + bx * s + r, cy + 120 * s + r],
                  fill=col)
    # side button
    rrect(d, [cx + bw / 2 - 8 * s, cy - 90 * s, cx + bw / 2 + 26 * s, cy + 10 * s],
          16 * s, fill=GREEN_BTN)
    # rhythm arc with beat ticks (the orange gesture)
    swing_arc(d, cx + 60 * s, cy + 40 * s, 560 * s, -78, -14, ORANGE, int(10 * s))
    for ang in (-72, -53, -34, -15):
        x = cx + 60 * s + 560 * s * math.cos(math.radians(ang))
        y = cy + 40 * s + 560 * s * math.sin(math.radians(ang))
        d.ellipse([x - 16 * s, y - 16 * s, x + 16 * s, y + 16 * s], fill=ORANGE)
    card_chrome(d, s, "Training aid · Tempo", "Clip-On Swing Metronome", "SL-METRONOME")
    finish(img, "product-metronome.png")


def alignment_sticks():
    img, d = canvas()
    s = S
    # three sticks: two parallel, one crossing (classic gate drill)
    def stick(x0, y0, x1, y1, color, band):
        d.line([x0, y0, x1, y1], fill=color, width=int(26 * s))
        for t in (0.0, 1.0):
            x = x0 + (x1 - x0) * t
            y = y0 + (y1 - y0) * t
            d.ellipse([x - 13 * s, y - 13 * s, x + 13 * s, y + 13 * s], fill=color)
        # grip-tape band near the top end, in a darker shade
        for t in (0.08, 0.14):
            x = x0 + (x1 - x0) * t
            y = y0 + (y1 - y0) * t
            ang = math.atan2(y1 - y0, x1 - x0) + math.pi / 2
            r = 14 * s
            d.line([x - r * math.cos(ang), y - r * math.sin(ang),
                    x + r * math.cos(ang), y + r * math.sin(ang)],
                   fill=band, width=int(10 * s))

    stick(430 * s, 380 * s, 700 * s, 1240 * s, ORANGE, "#b95c07")
    stick(650 * s, 360 * s, 920 * s, 1220 * s, ORANGE, "#b95c07")
    stick(380 * s, 1000 * s, 1240 * s, 700 * s, GREEN, GREEN_BTN)
    # ground line dots
    for x in range(360, 1280, 60):
        d.ellipse([x * s - 4 * s, 1300 * s - 4 * s, x * s + 4 * s, 1300 * s + 4 * s],
                  fill=BORDER)
    card_chrome(d, s, "Training aid · Setup", "Alignment Stick Set", "SL-ALIGN-3PK")
    finish(img, "product-alignment-sticks.png")


def hip_band():
    img, d = canvas()
    s = S
    cx, cy = 800 * s, 790 * s
    # looped band drawn as a fat ellipse ring, orange
    rx, ry = 430 * s, 250 * s
    for off in range(int(-27 * s), int(27 * s)):
        d.ellipse([cx - rx + off, cy - ry + abs(off) * 0.4,
                   cx + rx - off, cy + ry - abs(off) * 0.4],
                  outline=ORANGE, width=int(3 * s))
    # overlap seam highlights
    swing_arc(d, cx, cy - 30 * s, 300 * s, 240, 275, GREEN_INK, int(8 * s))
    # anchor strap wrapping the band at right
    rrect(d, [cx + rx - 90 * s, cy - 120 * s, cx + rx + 40 * s, cy + 30 * s],
          30 * s, fill=GREEN)
    for yy in (-80, -40, 0):
        d.line([cx + rx - 70 * s, cy + yy * s - 10 * s,
                cx + rx + 20 * s, cy + yy * s - 10 * s],
               fill=GREEN_INK, width=int(5 * s))
    # rotation cue: dashed green arrow hugging the loop's outer edge
    prx, pry = rx + 95 * s, ry + 95 * s
    pts = []
    for i in range(0, 121):
        t = math.radians(200 + i)          # 200° -> 320° around the ellipse
        pts.append((cx + prx * math.cos(t), cy + pry * math.sin(t)))
    for i in range(0, 120, 6):             # dashes of 3 segments on, 3 off
        if (i // 6) % 2 == 0 and i + 3 <= 120:
            d.line(pts[i:i + 4], fill=GREEN, width=int(10 * s), joint="curve")
    # arrowhead tangent to the path end
    ex, ey = pts[-1]
    tx_, ty_ = ex - pts[-4][0], ey - pts[-4][1]
    tl = math.hypot(tx_, ty_) or 1
    ux, uy = tx_ / tl, ty_ / tl
    nx, ny = -uy, ux
    d.polygon([(ex + ux * 52 * s, ey + uy * 52 * s),
               (ex + nx * 26 * s, ey + ny * 26 * s),
               (ex - nx * 26 * s, ey - ny * 26 * s)], fill=GREEN)
    card_chrome(d, s, "Training aid · Hips", "Anti-Sway Hip Resistance Band", "SL-HIP-BAND")
    finish(img, "product-hip-band.png")


def swing_mirror():
    img, d = canvas()
    s = S
    cx = 790 * s
    top, bot = 330 * s, 1260 * s
    w = 330 * s
    # kickstand behind
    d.line([cx + w / 2 - 20 * s, top + 120 * s, cx + w / 2 + 240 * s, bot],
           fill=GREEN_BTN, width=int(20 * s))
    # frame
    rrect(d, [cx - w / 2 - 22 * s, top - 22 * s, cx + w / 2 + 22 * s, bot + 22 * s],
          60 * s, fill=GREEN)
    # glass
    rrect(d, [cx - w / 2, top, cx + w / 2, bot], 44 * s, fill=GREEN_INK)
    # centerline decal (the orange gesture) with calibration ticks
    d.line([cx, top + 40 * s, cx, bot - 40 * s], fill=ORANGE, width=int(10 * s))
    for y in range(int(top + 80 * s), int(bot - 60 * s), int(90 * s)):
        d.line([cx - 26 * s, y, cx + 26 * s, y], fill=ORANGE, width=int(5 * s))
    # faint reflected arc in the glass
    swing_arc(d, cx - 60 * s, bot - 140 * s, 420 * s, 235, 300, "#d3e0d6", int(8 * s))
    # base foot
    rrect(d, [cx - w / 2 - 60 * s, bot + 10 * s, cx + w / 2 + 60 * s, bot + 46 * s],
          20 * s, fill=GREEN)
    card_chrome(d, s, "Training aid · Positions", "Full-Length Swing Mirror", "SL-MIRROR")
    finish(img, "product-swing-mirror.png")


def performance_cap():
    img, d = canvas()
    s = S
    cx, cy = 820 * s, 800 * s
    # crown: half-ellipse
    d.pieslice([cx - 330 * s, cy - 330 * s, cx + 330 * s, cy + 330 * s],
               180, 360, fill=GREEN)
    # panel seams
    for dx in (-160, 0, 160):
        d.arc([cx - 330 * s + abs(dx) * 1.1 * s, cy - 330 * s,
               cx + 330 * s - abs(dx) * 1.1 * s, cy + 330 * s],
              180, 360, fill=GREEN_BTN, width=int(5 * s))
        _ = dx
    # button
    d.ellipse([cx - 16 * s, cy - 346 * s, cx + 16 * s, cy - 314 * s], fill=ORANGE)
    # brim sweeping left
    d.pieslice([cx - 660 * s, cy - 60 * s, cx + 40 * s, cy + 150 * s],
               0, 180, fill=GREEN)
    d.pieslice([cx - 660 * s, cy - 100 * s, cx + 40 * s, cy + 110 * s],
               0, 180, fill=GREEN_BTN)
    # sweatband line
    d.line([cx - 330 * s, cy, cx + 330 * s, cy], fill=GREEN_BTN, width=int(14 * s))
    # brand arc mark on the front panel
    swing_arc(d, cx + 60 * s, cy - 60 * s, 150 * s, 190, 262, ORANGE, int(14 * s))
    bx = cx + 60 * s + 150 * s * math.cos(math.radians(262))
    by = cy - 60 * s + 150 * s * math.sin(math.radians(262))
    d.ellipse([bx - 14 * s, by - 14 * s, bx + 14 * s, by + 14 * s], fill=GREEN_INK)
    card_chrome(d, s, "Apparel", "SwingLab Performance Cap", "SL-CAP")
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
    card_chrome(d, s, "Membership · Digital", "SwingLab Pro", "SL-PRO")
    finish(img, "product-pro.png")


# ---------------------------------------------------------------- brand ----

def logo(inverse=False):
    w, h, sc = 1560, 400, 4
    img = Image.new("RGBA", (w * sc, h * sc), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ink = GREEN_INK if inverse else GREEN
    # mark: swing arc into ball
    mcx, mcy, mr = 150 * sc, 275 * sc, 195 * sc
    swing_arc(d, mcx, mcy, mr, 262, 348, ORANGE, int(30 * sc))
    bx = mcx + (mr - 15 * sc) * math.cos(math.radians(348))
    by = mcy + (mr - 15 * sc) * math.sin(math.radians(348))
    d.ellipse([bx - 34 * sc, by - 34 * sc, bx + 34 * sc, by + 34 * sc], fill=ink)
    f = archivo(int(210 * sc), 680, 106)
    d.text((470 * sc, 200 * sc), "SwingLab", font=f, fill=ink, anchor="lm")
    tracked(d, (478 * sc, 310 * sc), "SWING ANALYSIS · GEAR", mono(int(40 * sc)),
            ORANGE if inverse else INK_SOFT, tracking=int(14 * sc))
    img = img.resize((w, h), Image.LANCZOS)
    bbox = img.getbbox()
    img = img.crop(bbox)
    name = "swinglab-logo-inverse.png" if inverse else "swinglab-logo.png"
    img.save(OUT / name)
    print("wrote", OUT / name)


def favicon():
    w = 512
    sc = 4
    img = Image.new("RGBA", (w * sc, w * sc), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rrect(d, [0, 0, w * sc, w * sc], 110 * sc, fill=GREEN)
    cx, cy, r = 205 * sc, 300 * sc, 190 * sc
    swing_arc(d, cx, cy, r, 265, 355, ORANGE, int(34 * sc))
    bx = cx + r * math.cos(math.radians(355))
    by = cy + r * math.sin(math.radians(355))
    d.ellipse([bx - 40 * sc, by - 40 * sc, bx + 40 * sc, by + 40 * sc], fill=GREEN_INK)
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
    tracked(d, (110 * s, 330 * s), "SWINGLAB", archivo(int(34 * s), 640, 104), GREEN,
            tracking=int(12 * s))
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
