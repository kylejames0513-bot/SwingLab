"""CaddieInsight Pro gallery + homepage art — Turf Instrument, phase 2.

Pose-skeleton golfers (the product's own visual language: joint dots +
limb segments, mediapipe style) rendered in the same palette and chrome
as the phase-1 product cards.
"""

from __future__ import annotations

import math

from make_assets import (
    ARC_FAINT, BG, BORDER, CARD, GREEN, GREEN_BTN, GREEN_INK, INK, INK_MUTED,
    INK_SOFT, ORANGE, S, archivo, canvas, card_chrome, finish, mono, rrect,
    swing_arc, tracked,
)

MINT = GREEN_INK
DARK = "#0f1712"


# ------------------------------------------------------------- skeleton ----

# Joint sets for a face-on golfer (target to the viewer's right).
# Coordinates are (x, y) in a 0..1 box, y down.
POSES = {
    "address": {
        "head": (0.50, 0.14),
        "neck": (0.50, 0.26), "hipC": (0.49, 0.52),
        "shL": (0.42, 0.28), "shR": (0.58, 0.28),
        "elL": (0.42, 0.40), "elR": (0.58, 0.40),
        "wr": (0.50, 0.52),
        "hipL": (0.43, 0.52), "hipR": (0.55, 0.52),
        "knL": (0.41, 0.70), "knR": (0.57, 0.70),
        "anL": (0.40, 0.88), "anR": (0.58, 0.88),
        "club": (0.62, 0.90),
    },
    "top": {
        "head": (0.52, 0.13),
        "neck": (0.51, 0.25), "hipC": (0.48, 0.52),
        "shL": (0.44, 0.28), "shR": (0.58, 0.25),
        "elL": (0.33, 0.24), "elR": (0.50, 0.16),
        "wr": (0.38, 0.12),
        "hipL": (0.42, 0.52), "hipR": (0.54, 0.52),
        "knL": (0.41, 0.70), "knR": (0.56, 0.70),
        "anL": (0.40, 0.88), "anR": (0.58, 0.88),
        "club": (0.62, 0.04),
    },
    "impact": {
        "head": (0.47, 0.14),
        "neck": (0.48, 0.26), "hipC": (0.47, 0.52),
        "shL": (0.40, 0.28), "shR": (0.56, 0.27),
        "elL": (0.44, 0.40), "elR": (0.57, 0.38),
        "wr": (0.53, 0.51),
        "hipL": (0.41, 0.52), "hipR": (0.53, 0.52),
        "knL": (0.39, 0.70), "knR": (0.55, 0.70),
        "anL": (0.38, 0.88), "anR": (0.57, 0.88),
        "club": (0.60, 0.90),
    },
    "finish": {
        "head": (0.44, 0.12),
        "neck": (0.45, 0.24), "hipC": (0.44, 0.50),
        "shL": (0.38, 0.26), "shR": (0.52, 0.25),
        "elL": (0.30, 0.18), "elR": (0.42, 0.13),
        "wr": (0.31, 0.09),
        "hipL": (0.39, 0.50), "hipR": (0.50, 0.50),
        "knL": (0.38, 0.69), "knR": (0.52, 0.68),
        "anL": (0.38, 0.88), "anR": (0.56, 0.86),
        "club": (0.18, 0.20),
    },
}

BONES = [
    ("neck", "shL"), ("neck", "shR"), ("neck", "hipC"),
    ("shL", "elL"), ("shR", "elR"), ("elL", "wr"), ("elR", "wr"),
    ("hipC", "hipL"), ("hipC", "hipR"),
    ("hipL", "knL"), ("hipR", "knR"), ("knL", "anL"), ("knR", "anR"),
]


def skeleton(d, pose, box, s, limb=MINT, joint=ORANGE, lw=9):
    """Draw a pose skeleton inside box=(x, y, w, h)."""
    bx, by, bw, bh = box

    def pt(name):
        x, y = pose[name]
        return (bx + x * bw, by + y * bh)

    # club shaft from wrists
    d.line([pt("wr"), pt("club")], fill=limb, width=int(lw * 0.6 * s))
    cx, cy = pt("club")
    d.ellipse([cx - 7 * s, cy - 7 * s, cx + 7 * s, cy + 7 * s], fill=limb)
    for a, b in BONES:
        d.line([pt(a), pt(b)], fill=limb, width=int(lw * s))
    hx, hy = pt("head")
    r = 0.055 * bh
    d.ellipse([hx - r, hy - r, hx + r, hy + r], outline=limb, width=int(lw * 0.8 * s))
    for name in ("neck", "shL", "shR", "elL", "elR", "wr",
                 "hipL", "hipR", "knL", "knR", "anL", "anR"):
        x, y = pt(name)
        jr = 5.5 * s
        d.ellipse([x - jr, y - jr, x + jr, y + jr], fill=joint)


# ------------------------------------------------------------- gallery ----

def pro_report_strip():
    img, d = canvas()
    s = S
    labels = ["ADDRESS", "TOP", "IMPACT", "FINISH"]
    keys = ["address", "top", "impact", "finish"]
    gx, gy = 150 * s, 212 * s
    cw, ch = 630 * s, 528 * s
    gap = 40 * s
    for i, (key, label) in enumerate(zip(keys, labels)):
        col, row = i % 2, i // 2
        x = gx + col * (cw + gap)
        y = gy + row * (ch + gap + 26 * s)
        rrect(d, [x, y, x + cw, y + ch], 24 * s, fill=GREEN)
        # ground line
        d.line([x + 50 * s, y + ch - 60 * s, x + cw - 50 * s, y + ch - 60 * s],
               fill=GREEN_BTN, width=int(3 * s))
        skeleton(d, POSES[key], (x + 90 * s, y + 40 * s, cw - 180 * s, ch - 110 * s), s)
        # frame number + label
        tracked(d, (x + 24 * s, y + ch + 14 * s), f"0{i+1}  {label}",
                mono(int(24 * s)), INK_SOFT, tracking=int(4 * s))
    card_chrome(d, s, "Pro · Swing report", "Every swing, four positions", "REPORT / STRIP")
    finish(img, "pro-report-strip.png")


def pro_overlay_detail():
    img, d = canvas()
    s = S
    # one large dark panel
    px, py, pw, ph = 150 * s, 240 * s, 800 * s, 1130 * s
    rrect(d, [px, py, px + pw, py + ph], 28 * s, fill=GREEN)
    ax, ay = px + pw * 0.52, py + ph - 130 * s          # ankle pin
    top_y = py + 110 * s
    # corrected: vertical green-mint dashed centerline
    swing_arc  # (unused here, quiet the linter)
    d.line([ax, ay, ax, top_y], fill="#7fbf9a", width=int(8 * s))
    # captured: sheared orange line to head
    hx = ax - 150 * s
    d.line([ax, ay, hx, top_y + 26 * s], fill=ORANGE, width=int(8 * s))
    # head circles at each line top
    for cx_, cy_, col in ((ax, top_y - 6 * s, "#7fbf9a"), (hx, top_y + 20 * s, ORANGE)):
        r = 34 * s
        d.ellipse([cx_ - r, cy_ - r, cx_ + r, cy_ + r], outline=col, width=int(8 * s))
    # gap arrow between heads
    yline = top_y - 46 * s
    d.line([hx, yline, ax, yline], fill=MINT, width=int(4 * s))
    for tx, sgn in ((hx, 1), (ax, -1)):
        d.polygon([(tx, yline), (tx + sgn * 22 * s, yline - 12 * s),
                   (tx + sgn * 22 * s, yline + 12 * s)], fill=MINT)
    tracked(d, ((hx + ax) / 2, yline - 42 * s), "0.42 SW", mono(int(24 * s)),
            MINT, tracking=int(3 * s), anchor="m")
    # skeleton ghost at address behind lines
    skeleton(d, POSES["impact"], (px + 90 * s, py + 130 * s, pw - 180 * s, ph - 300 * s),
             s, limb="#2e5c42", joint="#3f7256", lw=8)
    # ankle pin
    d.ellipse([ax - 14 * s, ay - 14 * s, ax + 14 * s, ay + 14 * s], fill=MINT)
    tracked(d, (px + 40 * s, py + ph - 66 * s), "CAPTURED", mono(int(22 * s)),
            ORANGE, tracking=int(4 * s))
    tracked(d, (px + 280 * s, py + ph - 66 * s), "CORRECTED", mono(int(22 * s)),
            "#7fbf9a", tracking=int(4 * s))
    # metric chips column
    chips = [("TEMPO", "2.6 : 1", ORANGE), ("HEAD SWAY", "0.42 SW", ORANGE),
             ("HIP SLIDE", "0.14 SW", GREEN_BTN)]
    cx0 = px + pw + 60 * s
    for i, (k, v, accent) in enumerate(chips):
        cy0 = py + 60 * s + i * 200 * s
        rrect(d, [cx0, cy0, 1450 * s, cy0 + 150 * s], 20 * s,
              fill=CARD, outline=BORDER, width=int(3 * s))
        d.rectangle([cx0, cy0 + 30 * s, cx0 + 10 * s, cy0 + 120 * s], fill=accent)
        tracked(d, (cx0 + 40 * s, cy0 + 28 * s), k, mono(int(22 * s)), INK_MUTED,
                tracking=int(3 * s))
        d.text((cx0 + 40 * s, cy0 + 62 * s), v, font=archivo(int(52 * s), 680, 104),
               fill=INK)
    # coaching note under chips
    note_y = py + 60 * s + 3 * 200 * s + 20 * s
    rrect(d, [cx0, note_y, 1450 * s, note_y + 330 * s], 20 * s, fill="#efe9dc")
    tracked(d, (cx0 + 40 * s, note_y + 30 * s), "COACHING NOTE", mono(int(22 * s)),
            INK_MUTED, tracking=int(3 * s))
    d.multiline_text((cx0 + 40 * s, note_y + 80 * s),
                     "Head drifts off the line\nin the backswing — see\nthe anti-sway drills.",
                     font=archivo(int(40 * s), 560, 102), fill=INK_SOFT,
                     spacing=int(14 * s))
    card_chrome(d, s, "Pro · Overlay", "Your body vs. the corrected line", "REPORT / OVERLAY")
    finish(img, "pro-overlay-detail.png")


def pro_plans():
    img, d = canvas()
    s = S
    cards = [
        ("FREE", "$0", ["1 full analysis every month", "Complete report each time",
                        "Coaching notes per swing"], False),
        ("PRO", "Unlimited", ["Unlimited swing analyses", "Annotated coach replay",
                              "Progress dashboard"], True),
    ]
    cw, ch = 620 * s, 980 * s
    gx, gy = 150 * s, 280 * s
    for i, (name, price, feats, pro) in enumerate(cards):
        x = gx + i * (cw + 60 * s)
        fill = GREEN if pro else CARD
        rrect(d, [x, gy, x + cw, gy + ch], 30 * s, fill=fill,
              outline=None if pro else BORDER, width=int(3 * s))
        tcol = MINT if pro else INK_SOFT
        hcol = CARD if pro else GREEN
        tracked(d, (x + 60 * s, gy + 70 * s), name, mono(int(30 * s)),
                ORANGE if pro else INK_MUTED, tracking=int(8 * s))
        d.text((x + 60 * s, gy + 130 * s), price,
               font=archivo(int(92 * s), 700, 104), fill=hcol)
        if pro:
            rrect(d, [x + cw - 200 * s, gy + 64 * s, x + cw - 60 * s, gy + 132 * s],
                  34 * s, fill=ORANGE)
            d.text((x + cw - 130 * s, gy + 98 * s), "PRO",
                   font=archivo(int(40 * s), 740, 108), fill=CARD, anchor="mm")
        y = gy + 330 * s
        for feat in feats:
            # check mark
            d.line([x + 60 * s, y + 16 * s, x + 82 * s, y + 38 * s],
                   fill=ORANGE, width=int(8 * s))
            d.line([x + 82 * s, y + 38 * s, x + 122 * s, y - 6 * s],
                   fill=ORANGE, width=int(8 * s))
            d.text((x + 150 * s, y - 8 * s), feat,
                   font=archivo(int(34 * s), 540, 102), fill=tcol)
            y += 110 * s
        # footer line inside card
        note = "Yours the moment you check out" if pro else "No card required"
        tracked(d, (x + 60 * s, gy + ch - 90 * s), note.upper(), mono(int(20 * s)),
                MINT if pro else INK_MUTED, tracking=int(2 * s))
    card_chrome(d, s, "Pro · Plans", "Free gets you started. Pro removes the limit.",
                "SL-PRO")
    finish(img, "pro-plans.png")


# ------------------------------------------------------------ homepage ----

def hero_image():
    w, h = 2560, 1440
    img, d = canvas(w, h, bg=GREEN)
    s = S
    # deep vignette panels for depth
    d.rectangle([0, 0, w * s, h * s], fill=GREEN)
    rrect(d, [-400 * s, h * s * 0.55, w * s + 400 * s, h * s + 400 * s], 0,
          fill="#123f27")
    # faint dotted arc field
    swing_arc(d, w * s * 0.72, h * s * 1.7, 1500 * s, 235, 305, "#1d5535",
              int(4 * s), dash=(1.8, 2.6))
    swing_arc(d, w * s * 0.72, h * s * 1.7, 1300 * s, 238, 302, "#1d5535",
              int(4 * s), dash=(1.8, 2.6))
    # measurement centerline through the golfer, the report's reference
    gx0, gy0, gw, gh = w * s * 0.50, 140 * s, 1040 * s, 1200 * s
    cxm = gx0 + 0.47 * gw
    a = 200 * s
    while a < 1204 * s:
        d.line([cxm, a, cxm, a + 26 * s], fill="#2a6343", width=int(5 * s))
        a += 46 * s
    # big orange swing arc sweeping up behind the golfer
    swing_arc(d, w * s * 0.58, h * s * 1.12, 940 * s, 205, 322, ORANGE, int(16 * s))
    bx = w * s * 0.58 + (940 - 8) * s * math.cos(math.radians(322))
    by = h * s * 1.12 + (940 - 8) * s * math.sin(math.radians(322))
    d.ellipse([bx - 28 * s, by - 28 * s, bx + 28 * s, by + 28 * s], fill=MINT)
    # golfer skeleton, finish pose, right side — feet on the ground line
    skeleton(d, POSES["finish"], (gx0, gy0, gw, gh), s,
             limb=MINT, joint=ORANGE, lw=12)
    d.line([w * s * 0.47, 1222 * s, w * s * 0.96, 1222 * s],
           fill="#1d5535", width=int(5 * s))
    # Product-loop caption, intentionally free of invented analysis values.
    tracked(d, (w * s * 0.505, 1284 * s),
            "CHOOSE CLUB · FILM THE VIEW · WORK ONE PLAN · RE-FILM",
            mono(int(26 * s)), MINT, tracking=int(3 * s))
    finish(img, "swinglab-hero.png", w, h)


def report_band():
    w, h = 2400, 1200
    img, d = canvas(w, h)
    s = S
    # right-side collage: overlay panel + two mini frames
    px, py, pw, ph = w * s * 0.52, 140 * s, 620 * s, 900 * s
    rrect(d, [px, py, px + pw, py + ph], 28 * s, fill=GREEN)
    ax, ay = px + pw * 0.5, py + ph - 110 * s
    top_y = py + 110 * s
    d.line([ax, ay, ax, top_y], fill="#7fbf9a", width=int(8 * s))
    hx = ax - 120 * s
    d.line([ax, ay, hx, top_y + 20 * s], fill=ORANGE, width=int(8 * s))
    for cx_, cy_, col in ((ax, top_y - 4 * s, "#7fbf9a"), (hx, top_y + 16 * s, ORANGE)):
        r = 30 * s
        d.ellipse([cx_ - r, cy_ - r, cx_ + r, cy_ + r], outline=col, width=int(7 * s))
    skeleton(d, POSES["impact"], (px + 70 * s, py + 120 * s, pw - 140 * s, ph - 280 * s),
             s, limb="#2e5c42", joint="#3f7256", lw=8)
    d.ellipse([ax - 12 * s, ay - 12 * s, ax + 12 * s, ay + 12 * s], fill=MINT)
    # sway gap arrow between the two head circles
    yline = top_y - 52 * s
    d.line([hx, yline, ax, yline], fill=MINT, width=int(4 * s))
    for tx_, sgn in ((hx, 1), (ax, -1)):
        d.polygon([(tx_, yline), (tx_ + sgn * 20 * s, yline - 11 * s),
                   (tx_ + sgn * 20 * s, yline + 11 * s)], fill=MINT)
    tracked(d, ((hx + ax) / 2, yline - 40 * s), "0.42 SW", mono(int(20 * s)),
            MINT, tracking=int(2 * s), anchor="m")
    # captured / corrected legend at the panel foot
    tracked(d, (px + 40 * s, py + ph - 58 * s), "CAPTURED", mono(int(18 * s)),
            ORANGE, tracking=int(3 * s))
    tracked(d, (px + 240 * s, py + ph - 58 * s), "CORRECTED", mono(int(18 * s)),
            "#7fbf9a", tracking=int(3 * s))
    # two mini position frames stacked right of panel, with position labels
    fx = px + pw + 50 * s
    for i, (key, lab) in enumerate((("top", "02 · TOP"), ("finish", "04 · FINISH"))):
        fy = py + i * (ph / 2 + 10 * s)
        fh = ph / 2 - 60 * s
        rrect(d, [fx, fy, fx + 420 * s, fy + fh], 24 * s, fill="#123f27")
        skeleton(d, POSES[key], (fx + 60 * s, fy + 26 * s, 300 * s, fh - 60 * s),
                 s, lw=7)
        tracked(d, (fx + 8 * s, fy + fh + 14 * s), lab, mono(int(16 * s)),
                INK_MUTED, tracking=int(2 * s))
    # metric chips below the panel, card-style with accent bars
    chips = (("TEMPO", "2.6 : 1", ORANGE), ("HEAD SWAY", "0.42 SW", ORANGE),
             ("HIP SLIDE", "0.14 SW", GREEN_BTN))
    for i, (k, v, accent) in enumerate(chips):
        cx0 = px + i * 214 * s
        cy0 = py + ph + 24 * s
        rrect(d, [cx0, cy0, cx0 + 198 * s, cy0 + 96 * s], 16 * s,
              fill=CARD, outline=BORDER, width=int(3 * s))
        d.rectangle([cx0, cy0 + 20 * s, cx0 + 7 * s, cy0 + 76 * s], fill=accent)
        tracked(d, (cx0 + 24 * s, cy0 + 16 * s), k, mono(int(15 * s)), INK_MUTED,
                tracking=int(2 * s))
        d.text((cx0 + 24 * s, cy0 + 40 * s), v, font=archivo(int(34 * s), 660, 104),
               fill=INK)
    # faint arc into the left (text) half, staying subtle
    swing_arc(d, w * s * 0.1, h * s * 1.9, 1400 * s, 270, 320, ARC_FAINT,
              int(4 * s), dash=(2, 2.6))
    finish(img, "swinglab-report-band.png", w, h)


if __name__ == "__main__":
    pro_report_strip()
    pro_overlay_detail()
    pro_plans()
    hero_image()
    report_band()
