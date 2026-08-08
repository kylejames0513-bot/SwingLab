"""CaddieInsight campaign art — Turf Instrument, phase 3.

Instructional drill diagrams (each product's second gallery image), a cap
detail study, two wide page banners, and the social share card. Reuses the
palette, chrome, and drafting helpers from make_assets.py and the
pose-skeleton golfer from pro_home_assets.py.

Every piece keeps the movement's grammar: warm off-white field, one deep
green ink, exactly one orange kinetic gesture (the movement being trained),
mono specimen labels at the edges, dotted calibration rhythm.
"""

from __future__ import annotations

import math

from make_assets import (
    ARC_FAINT, BG, BORDER, CARD, GREEN, GREEN_BTN, GREEN_INK, INK, INK_MUTED,
    INK_SOFT, ORANGE, S, archivo, arrow_head, canvas, card_chrome, callout,
    dashed_line, dim_line, finish, mark_protractor, mono, rrect, swing_arc,
    tracked,
)
from pro_home_assets import POSES, skeleton

MINT = GREEN_INK


# ------------------------------------------------------------ shared bits ----

def chip(d, s, x, y, text, fill=GREEN, ink=MINT, font_px=24, pad=34, tracking=4):
    """Rounded pill with mono text; returns its right edge."""
    f = mono(int(font_px * s))
    wt = sum(d.textlength(ch, font=f) for ch in text) + tracking * s * (len(text) - 1)
    h = (font_px + 40) * s
    rrect(d, [x, y, x + wt + 2 * pad * s, y + h], h / 2, fill=fill)
    tracked(d, (x + pad * s, y + 19 * s), text, f, ink, tracking=int(tracking * s))
    return x + wt + 2 * pad * s


def drill_chrome(d, s, num, category, title, sku, spec):
    card_chrome(d, s, category, title, sku, spec=spec)
    chip(d, s, 110 * s, 212 * s, f"DRILL {num:02d}")


def protocol(d, s, x, y, lines):
    """Numbered setup list, mono, right column."""
    tracked(d, (x, y), "PROTOCOL", mono(int(20 * s)), INK_MUTED, tracking=int(6 * s))
    d.line([x, y + 44 * s, x + 344 * s, y + 44 * s], fill=BORDER, width=int(2 * s))
    yy = y + 66 * s
    for i, ln in enumerate(lines, 1):
        tracked(d, (x, yy), f"{i:02d}", mono(int(21 * s)), GREEN_BTN,
                tracking=int(1 * s))
        tracked(d, (x + 52 * s, yy), ln, mono(int(21 * s)), INK_SOFT,
                tracking=int(1 * s))
        yy += 52 * s
    return yy


def ground_dots(d, s, y, x0, x1, step=60):
    for x in range(x0, x1, step):
        d.ellipse([x * s - 4 * s, y * s - 4 * s, x * s + 4 * s, y * s + 4 * s],
                  fill=BORDER)


def golf_ball(d, s, x, y, r=16):
    d.ellipse([x - r * s, y - r * s, x + r * s, y + r * s],
              fill=CARD, outline=GREEN, width=int(4.5 * s))
    for aa in (210, 250, 290):
        dx = x + r * 0.45 * s * math.cos(math.radians(aa))
        dy = y + r * 0.45 * s * math.sin(math.radians(aa))
        d.ellipse([dx - 2.2 * s, dy - 2.2 * s, dx + 2.2 * s, dy + 2.2 * s],
                  fill=GREEN)


# ------------------------------------------------------------------ drills ----

def drill_tempo_wand():
    img, d = canvas()
    s = S
    drill_chrome(d, s, 1, "Drill · Tempo", "Three-Count Wand Swings",
                 "SL-TEMPO-WAND", "3 sets × 10 swings · no ball")
    # golfer at the top of the backswing
    bx, by, bw, bh = 300 * s, 330 * s, 560 * s, 860 * s
    pose = POSES["top"]
    skeleton(d, pose, (bx, by, bw, bh), s, limb=GREEN, joint=GREEN_BTN, lw=9)
    # the wand overlaid on the club line: grip + shaft + weighted head
    wr = (bx + pose["wr"][0] * bw, by + pose["wr"][1] * bh)
    cl = (bx + pose["club"][0] * bw, by + pose["club"][1] * bh)
    hd = (cl[0] + (cl[0] - wr[0]) * 0.42, cl[1] + (cl[1] - wr[1]) * 0.42)
    d.line([*wr, *cl], fill=GREEN, width=int(16 * s))
    d.line([*cl, *hd], fill=GREEN, width=int(11 * s))
    d.ellipse([hd[0] - 30 * s, hd[1] - 30 * s, hd[0] + 30 * s, hd[1] + 30 * s],
              fill=GREEN)
    for aa in (200, 240, 280):
        dx = hd[0] + 14 * s * math.cos(math.radians(aa))
        dy = hd[1] + 14 * s * math.sin(math.radians(aa))
        d.ellipse([dx - 3 * s, dy - 3 * s, dx + 3 * s, dy + 3 * s], fill=MINT)
    # backswing path: patient dashed ink arc, counted 1-2-3
    swing_arc(d, 580 * s, 700 * s, 430 * s, -74, 78, INK_MUTED, int(4 * s),
              dash=(1.6, 2.2))
    tracked(d, (1006 * s, 862 * s), "1 · 2 · 3 BACK", mono(int(21 * s)),
            INK_MUTED, tracking=int(2 * s))
    # downswing: the orange gesture, one count
    swing_arc(d, 580 * s, 700 * s, 380 * s, -70, 70, ORANGE, int(10 * s))
    tip = (580 * s + 380 * s * math.cos(math.radians(73)),
           700 * s + 380 * s * math.sin(math.radians(73)))
    arrow_head(d, s, tip, 73 + 90, size=26)
    tracked(d, (712 * s, 1152 * s), "1 DOWN", mono(int(21 * s)), INK_MUTED,
            tracking=int(2 * s))
    # ground + stance measurement
    ground_dots(d, s, 1120, 210, 970)
    anl = (bx + pose["anL"][0] * bw, by + pose["anL"][1] * bh)
    anr = (bx + pose["anR"][0] * bw, by + pose["anR"][1] * bh)
    dim_line(d, s, (anl[0], 1196 * s), (anr[0], 1196 * s), "SHOULDER WIDTH",
             side=1, gap=34, font_px=17)
    # protocol + target chip
    protocol(d, s, 1080 * s, 360 * s,
             ["WAND ONLY — NO BALL", "COUNT 1-2-3 TO THE TOP",
              "ONE COUNT DOWN", "FEEL THE HEAD LAG"])
    chip(d, s, 1080 * s, 1136 * s, "TARGET TEMPO 3:1")
    finish(img, "drill-tempo-wand.png")


def drill_metronome():
    img, d = canvas()
    s = S
    drill_chrome(d, s, 2, "Drill · Tempo", "Beat-Synced Swings",
                 "SL-METRONOME", "start 76 bpm · 3 × 12 swings")
    bx, by, bw, bh = 300 * s, 340 * s, 560 * s, 860 * s
    pose = POSES["address"]
    skeleton(d, pose, (bx, by, bw, bh), s, limb=GREEN, joint=GREEN_BTN, lw=9)
    head = (bx + pose["head"][0] * bw, by + pose["head"][1] * bh)
    # the metronome clipped beside the cap, chirping
    mx, my = head[0] + 76 * s, head[1] - 32 * s
    rrect(d, [mx - 24 * s, my - 30 * s, mx + 24 * s, my + 30 * s], 10 * s,
          fill=GREEN)
    rrect(d, [mx - 16 * s, my - 20 * s, mx + 16 * s, my + 4 * s], 6 * s, fill=MINT)
    for rr in (44, 62):
        swing_arc(d, mx, my, rr * s, -38, 38, GREEN_BTN, int(3 * s))
    callout(d, s, (mx + 10 * s, my - 34 * s), (860 * s, 320 * s), "CLIP-ON",
            font_px=19)
    # pendulum beat arc through the ball — the orange gesture
    wr = (bx + pose["wr"][0] * bw, by + pose["wr"][1] * bh)
    ar = 352 * s
    swing_arc(d, wr[0], wr[1], ar, 26, 146, ORANGE, int(10 * s))
    for n, ang in ((1, 142), (2, 110), (3, 78)):
        x = wr[0] + ar * math.cos(math.radians(ang))
        y = wr[1] + ar * math.sin(math.radians(ang))
        d.ellipse([x - 14 * s, y - 14 * s, x + 14 * s, y + 14 * s], fill=ORANGE)
        if n < 3:
            lx = wr[0] + (ar - 52 * s) * math.cos(math.radians(ang))
            ly = wr[1] + (ar - 52 * s) * math.sin(math.radians(ang))
            tracked(d, (lx, ly - 13 * s), str(n), mono(int(21 * s)), INK_MUTED,
                    anchor="m")
    tracked(d, (706 * s, 1122 * s), "3", mono(int(21 * s)), INK_MUTED)
    tip = (wr[0] + ar * math.cos(math.radians(23)),
           wr[1] + ar * math.sin(math.radians(23)))
    arrow_head(d, s, tip, 23 - 90, size=26)
    # ball at beat three
    cl = (bx + pose["club"][0] * bw, by + pose["club"][1] * bh)
    golf_ball(d, s, cl[0] + 12 * s, cl[1] + 16 * s, r=15)
    ground_dots(d, s, 1156, 220, 1010)
    anl = (bx + pose["anL"][0] * bw, by + pose["anL"][1] * bh)
    anr = (bx + pose["anR"][0] * bw, by + pose["anR"][1] * bh)
    dim_line(d, s, (anl[0], 1216 * s), (anr[0], 1216 * s), "SHOULDER WIDTH",
             side=1, gap=34, font_px=17)
    protocol(d, s, 1080 * s, 360 * s,
             ["CLIP TO CAP BRIM", "BEAT 1 · TAKEAWAY", "BEAT 3 · IMPACT",
              "SAME COUNT, EVERY CLUB"])
    chip(d, s, 1080 * s, 1136 * s, "START AT 76 BPM")
    finish(img, "drill-metronome.png")


def drill_alignment_sticks():
    img, d = canvas()
    s = S
    drill_chrome(d, s, 3, "Drill · Setup", "Railroad Tracks Alignment",
                 "SL-ALIGN-3PK", "every session · first 10 balls")
    # faint plan grid (kept below the chip row)
    for gx in range(250, 1460, 150):
        d.line([gx * s, 320 * s, gx * s, 1330 * s], fill=ARC_FAINT, width=int(2 * s))
    for gy in range(320, 1340, 200):
        d.line([180 * s, gy * s, 1440 * s, gy * s], fill=ARC_FAINT, width=int(2 * s))

    def stick(x0, y0, x1, y1):
        x0, y0, x1, y1 = x0 * s, y0 * s, x1 * s, y1 * s
        ln = math.hypot(x1 - x0, y1 - y0)
        ux, uy = (x1 - x0) / ln, (y1 - y0) / ln
        nx, ny = -uy, ux
        d.line([x0, y0, x1, y1], fill=GREEN, width=int(22 * s))
        for t in (0.0, 1.0):
            px_, py_ = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            qx = x0 + (x1 - x0) * abs(t - 0.045)
            qy = y0 + (y1 - y0) * abs(t - 0.045)
            d.line([px_, py_, qx, qy], fill=GREEN_BTN, width=int(28 * s))
        for k in range(1, 12):
            t = k * 10 / 122
            px_, py_ = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            r = (18 if k % 3 == 0 else 12) * s
            d.line([px_ - nx * r, py_ - ny * r, px_ + nx * r, py_ + ny * r],
                   fill=MINT, width=int(3 * s))

    # ball-position stick (vertical), then the two rails
    stick(900, 500, 900, 1354)
    stick(300, 560, 1154, 560)
    stick(330, 1010, 1184, 1010)
    tracked(d, (318 * s, 512 * s), "TARGET LINE", mono(int(18 * s)), INK_MUTED,
            tracking=int(3 * s))
    tracked(d, (336 * s, 1044 * s), "TOE LINE", mono(int(18 * s)), INK_MUTED,
            tracking=int(3 * s))
    # golfer from above: shoes, shoulders, arms to the grip, club to the ball
    for sx0, sx1 in ((585, 665), (795, 875)):
        rrect(d, [sx0 * s, 1032 * s, sx1 * s, 1188 * s], 34 * s,
              outline=GREEN, width=int(8 * s))
        d.line([(sx0 + 12) * s, 1122 * s, (sx1 - 12) * s, 1122 * s],
               fill=GREEN, width=int(5 * s))
    d.ellipse([646 * s, 1054 * s, 814 * s, 1166 * s], outline=GREEN,
              width=int(8 * s))                       # shoulders
    d.ellipse([692 * s, 1072 * s, 768 * s, 1148 * s], fill=GREEN)   # head
    for ax_ in (668, 792):
        d.line([ax_ * s, 1076 * s, 762 * s, 902 * s], fill=GREEN, width=int(7 * s))
    d.line([762 * s, 902 * s, 886 * s, 652 * s], fill=GREEN, width=int(8 * s))
    d.line([862 * s, 636 * s, 912 * s, 662 * s], fill=GREEN, width=int(13 * s))
    golf_ball(d, s, 900 * s, 612 * s, r=15)
    # start-line gesture
    dashed_line(d, (946 * s, 606 * s), (1392 * s, 606 * s), ORANGE, int(10 * s),
                dash=(30 * s, 22 * s))
    arrow_head(d, s, (1400 * s, 606 * s), 0, size=27)
    tracked(d, (1160 * s, 646 * s), "TO TARGET", mono(int(19 * s)), INK_MUTED,
            tracking=int(3 * s))
    # setup measurements
    dim_line(d, s, (250 * s, 560 * s), (250 * s, 1010 * s), "60 CM", side=1,
             gap=44)
    d.line([730 * s, 1196 * s, 730 * s, 1262 * s], fill=INK_MUTED, width=int(2 * s))
    dim_line(d, s, (730 * s, 1262 * s), (900 * s, 1262 * s), "24 CM FWD",
             side=1, gap=32, font_px=17)
    protocol(d, s, 1080 * s, 268 * s,
             ["RAILS PARALLEL", "CLUBFACE DOWN THE LINE", "TOES ON THE NEAR RAIL",
              "BALL OFF LEAD HEEL"])
    finish(img, "drill-alignment-sticks.png")


def drill_hip_band():
    img, d = canvas()
    s = S
    drill_chrome(d, s, 4, "Drill · Hip slide", "Banded Hip-Wall Turns",
                 "SL-HIP-BAND", "hold 5 s × 10 reps")
    bx, by, bw, bh = 320 * s, 330 * s, 560 * s, 880 * s
    pose = POSES["address"]
    skeleton(d, pose, (bx, by, bw, bh), s, limb=GREEN, joint=GREEN_BTN, lw=9)
    hipc = (bx + pose["hipC"][0] * bw, by + pose["hipC"][1] * bh)
    # the band above the knees
    bnd = [518 * s, 862 * s, 671 * s, 906 * s]
    d.ellipse(bnd, outline=GREEN, width=int(15 * s))
    d.ellipse([bnd[0] + 10 * s, bnd[1] + 10 * s, bnd[2] - 10 * s, bnd[3] - 10 * s],
              outline=GREEN_BTN, width=int(3 * s))
    rrect(d, [666 * s, 866 * s, 690 * s, 902 * s], 8 * s, fill=GREEN_BTN)
    callout(d, s, (671 * s, 902 * s), (846 * s, 986 * s), "50 MM BAND",
            font_px=19)
    # the hip wall: a line the pelvis must not cross
    dashed_line(d, (720 * s, 640 * s), (720 * s, 1020 * s), INK_MUTED, int(4 * s),
                dash=(22 * s, 16 * s))
    tracked(d, (744 * s, 998 * s), "HIP WALL", mono(int(18 * s)), INK_MUTED,
            tracking=int(3 * s))
    # allowed slide, measured the way the report measures it
    dim_line(d, s, (hipc[0], 668 * s), (720 * s, 668 * s), "MAX 0.25 SW",
             side=-1, gap=36, font_px=17)
    # rotation ring around the pelvis — the orange gesture
    rrx, rry = 120 * s, 48 * s
    pts = []
    for i in range(0, 234, 2):
        t = math.radians(152 + i)
        pts.append((hipc[0] + rrx * math.cos(t), hipc[1] + rry * math.sin(t)))
    for i in range(0, len(pts) - 6, 10):
        if (i // 10) % 2 == 0:
            d.line(pts[i:i + 7], fill=ORANGE, width=int(10 * s), joint="curve")
    ex, ey = pts[-1]
    ang = math.degrees(math.atan2(ey - pts[-7][1], ex - pts[-7][0]))
    arrow_head(d, s, (ex, ey), ang, size=25)
    tracked(d, (438 * s, 762 * s), "TURN", mono(int(18 * s)), INK_MUTED,
            tracking=int(3 * s), anchor="m")
    # ball + ground + stance
    cl = (bx + pose["club"][0] * bw, by + pose["club"][1] * bh)
    golf_ball(d, s, cl[0] + 12 * s, cl[1] + 14 * s, r=14)
    ground_dots(d, s, 1150, 230, 1000)
    anl = (bx + pose["anL"][0] * bw, by + pose["anL"][1] * bh)
    anr = (bx + pose["anR"][0] * bw, by + pose["anR"][1] * bh)
    dim_line(d, s, (anl[0], 1210 * s), (anr[0], 1210 * s), "SHOULDER WIDTH",
             side=1, gap=34, font_px=17)
    protocol(d, s, 1080 * s, 360 * s,
             ["BAND ABOVE KNEES", "SET A HIP WALL", "TURN, DON'T SLIDE",
              "HOLD 5 S AT THE TOP"])
    chip(d, s, 1080 * s, 1136 * s, "TARGET SLIDE < 0.25 SW")
    finish(img, "drill-hip-band.png")


def drill_swing_mirror():
    img, d = canvas()
    s = S
    drill_chrome(d, s, 5, "Drill · Positions", "Mirror Checkpoints",
                 "SL-MIRROR", "10 slow reps · daily")
    bx, by, bw, bh = 280 * s, 340 * s, 520 * s, 840 * s
    pose = POSES["top"]
    # rehearsal arc behind everything — the orange gesture
    swing_arc(d, 530 * s, 800 * s, 432 * s, -85, -27, ORANGE, int(9 * s))
    tip = (530 * s + 432 * s * math.cos(math.radians(-24)),
           800 * s + 432 * s * math.sin(math.radians(-24)))
    arrow_head(d, s, tip, -24 + 90, size=24)
    skeleton(d, pose, (bx, by, bw, bh), s, limb=GREEN, joint=GREEN_BTN, lw=9)
    head = (bx + pose["head"][0] * bw, by + pose["head"][1] * bh)
    # the mirror, in profile at the right
    rrect(d, [940 * s, 300 * s, 1180 * s, 1120 * s], 30 * s, fill=GREEN)
    rrect(d, [962 * s, 322 * s, 1158 * s, 1098 * s], 22 * s, fill=MINT)
    d.line([1060 * s, 360 * s, 1060 * s, 1062 * s], fill=GREEN_BTN, width=int(6 * s))
    for yy in (460, 700, 940):
        d.line([1038 * s, yy * s, 1082 * s, yy * s], fill=GREEN_BTN, width=int(4 * s))
    # the reflection: same pose, mirrored, faint
    mirrored = {k: (1 - x, y) for k, (x, y) in pose.items()}
    skeleton(d, mirrored, (968 * s, 420 * s, 180 * s, 600 * s), s,
             limb="#b9d2c2", joint="#b9d2c2", lw=5)
    tracked(d, (1060 * s, 1146 * s), "HEAD · HIP · KNEE", mono(int(17 * s)),
            INK_MUTED, tracking=int(2 * s), anchor="m")
    # sight line to the glass
    dashed_line(d, (head[0] + 40 * s, head[1] - 6 * s), (1052 * s, 430 * s),
                INK_MUTED, int(3 * s), dash=(16 * s, 12 * s))
    # ground + distance to the mirror
    ground_dots(d, s, 1112, 210, 920)
    anr = (bx + pose["anR"][0] * bw, by + pose["anR"][1] * bh)
    dim_line(d, s, (anr[0] + 40 * s, 1168 * s), (940 * s, 1168 * s), "1.5 M",
             side=1, gap=34, font_px=17)
    tracked(d, (110 * s, 1258 * s),
            "01 REHEARSE SLOW · 02 PAUSE AT THE TOP · 03 MATCH THE CENTERLINE",
            mono(int(20 * s)), INK_SOFT, tracking=int(2 * s))
    finish(img, "drill-swing-mirror.png")


# --------------------------------------------------------------- cap study ----

def detail_cap():
    img, d = canvas()
    s = S
    card_chrome(d, s, "Apparel · Detail", "Performance Cap, Construction",
                "SL-CAP", spec="6 panel · cotton twill · one size")
    ccx, ccy, cr = 500 * s, 700 * s, 300 * s
    # crown from above
    d.ellipse([ccx - cr, ccy - cr, ccx + cr, ccy + cr], fill=GREEN)
    for ang in range(0, 360, 60):        # six panel seams + running stitches
        a = math.radians(ang)
        ex, ey = ccx + cr * math.cos(a), ccy + cr * math.sin(a)
        d.line([ccx, ccy, ex, ey], fill=GREEN_BTN, width=int(5 * s))
        nx, ny = -math.sin(a), math.cos(a)
        for side in (-1, 1):
            dashed_line(d, (ccx + nx * 12 * s * side + 40 * s * math.cos(a),
                            ccy + ny * 12 * s * side + 40 * s * math.sin(a)),
                        (ex + nx * 12 * s * side - 16 * s * math.cos(a),
                         ey + ny * 12 * s * side - 16 * s * math.sin(a)),
                        MINT, int(2.5 * s), dash=(11 * s, 9 * s))
    # eyelets at the panel centers
    for ang in range(30, 390, 60):
        a = math.radians(ang)
        ex, ey = ccx + 165 * s * math.cos(a), ccy + 165 * s * math.sin(a)
        d.ellipse([ex - 15 * s, ey - 15 * s, ex + 15 * s, ey + 15 * s],
                  outline=MINT, width=int(4 * s))
        for ra in range(0, 360, 45):
            rr = math.radians(ra)
            d.line([ex + 17 * s * math.cos(rr), ey + 17 * s * math.sin(rr),
                    ex + 23 * s * math.cos(rr), ey + 23 * s * math.sin(rr)],
                   fill=MINT, width=int(2 * s))
    # crown button
    d.ellipse([ccx - 22 * s, ccy - 22 * s, ccx + 22 * s, ccy + 22 * s],
              fill=GREEN_BTN, outline=MINT, width=int(3 * s))
    # brim fan below, with stitch rows — and the orange arc mark
    d.pieslice([240 * s, 780 * s, 760 * s, 1260 * s], 30, 150, fill=GREEN_BTN)
    for k in range(1, 5):
        box = [240 * s + k * 26 * s, 780 * s + k * 26 * s,
               760 * s - k * 26 * s, 1260 * s - k * 30 * s]
        for a in range(34, 146, 9):
            d.arc(box, a, a + 4, fill=MINT, width=int(3 * s))
    swing_arc(d, 612 * s, 1156 * s, 54 * s, 168, 256, ORANGE, int(10 * s))
    obx = 612 * s + 54 * s * math.cos(math.radians(256))
    oby = 1156 * s + 54 * s * math.sin(math.radians(256))
    d.ellipse([obx - 8 * s, oby - 8 * s, obx + 8 * s, oby + 8 * s], fill=MINT)
    # dimensions
    dim_line(d, s, (ccx - cr, 350 * s), (ccx + cr, 350 * s), "Ø 18 CM CROWN",
             side=-1, gap=40)
    dim_line(d, s, (806 * s, 1020 * s), (806 * s, 1244 * s), "70 MM", side=-1,
             gap=52, font_px=18)
    # right column: components
    tracked(d, (1020 * s, 316 * s), "ADJUSTER · 58–62 CM", mono(int(20 * s)),
            INK_MUTED, tracking=int(3 * s))
    rrect(d, [1020 * s, 370 * s, 1400 * s, 420 * s], 20 * s, fill=GREEN)
    rrect(d, [1180 * s, 350 * s, 1260 * s, 440 * s], 12 * s, fill=GREEN_BTN)
    d.line([1220 * s, 358 * s, 1220 * s, 432 * s], fill=MINT, width=int(3 * s))
    for hx in (1310, 1348, 1386):
        d.ellipse([hx * s - 5 * s, 390 * s, hx * s + 5 * s, 400 * s], fill=MINT)
    dashed_line(d, (1034 * s, 382 * s), (1160 * s, 382 * s), MINT, int(2.5 * s),
                dash=(10 * s, 8 * s))
    dashed_line(d, (1034 * s, 408 * s), (1160 * s, 408 * s), MINT, int(2.5 * s),
                dash=(10 * s, 8 * s))
    # eyelet magnifier
    mgx, mgy, mgr = 1120 * s, 660 * s, 100 * s
    d.line([ccx + 165 * s * math.cos(math.radians(-30)) + 12 * s,
            ccy + 165 * s * math.sin(math.radians(-30)) - 8 * s,
            mgx - mgr + 8 * s, mgy - 30 * s], fill=INK_MUTED, width=int(2 * s))
    d.ellipse([mgx - mgr, mgy - mgr, mgx + mgr, mgy + mgr], fill=CARD,
              outline=BORDER, width=int(3 * s))
    d.ellipse([mgx - 40 * s, mgy - 40 * s, mgx + 40 * s, mgy + 40 * s],
              outline=GREEN, width=int(8 * s))
    d.ellipse([mgx - 18 * s, mgy - 18 * s, mgx + 18 * s, mgy + 18 * s],
              outline=GREEN_BTN, width=int(4 * s))
    for ra in range(0, 360, 30):
        rr = math.radians(ra)
        d.line([mgx + 44 * s * math.cos(rr), mgy + 44 * s * math.sin(rr),
                mgx + 58 * s * math.cos(rr), mgy + 58 * s * math.sin(rr)],
               fill=GREEN, width=int(3 * s))
    tracked(d, (1250 * s, 626 * s), "EMB. EYELET", mono(int(20 * s)), INK_SOFT,
            tracking=int(2 * s))
    tracked(d, (1250 * s, 668 * s), "Ø 4 MM × 6", mono(int(20 * s)), INK_MUTED,
            tracking=int(2 * s))
    # sweatband strip
    tracked(d, (1020 * s, 848 * s), "WICKING SWEATBAND", mono(int(20 * s)),
            INK_MUTED, tracking=int(3 * s))
    rrect(d, [1020 * s, 898 * s, 1400 * s, 966 * s], 16 * s, fill=GREEN)
    dashed_line(d, (1040 * s, 916 * s), (1380 * s, 916 * s), MINT, int(2.5 * s),
                dash=(12 * s, 9 * s))
    dashed_line(d, (1040 * s, 948 * s), (1380 * s, 948 * s), MINT, int(2.5 * s),
                dash=(12 * s, 9 * s))
    # stitch density swatch
    tracked(d, (1020 * s, 1046 * s), "SEAM · 8 ST/CM", mono(int(20 * s)),
            INK_MUTED, tracking=int(3 * s))
    rrect(d, [1020 * s, 1096 * s, 1180 * s, 1256 * s], 12 * s, fill=GREEN)
    for k in range(4):
        dashed_line(d, (1040 * s, (1124 + k * 36) * s),
                    (1160 * s, (1124 + k * 36) * s), MINT, int(3 * s),
                    dash=(12 * s, 8 * s))
    tracked(d, (1210 * s, 1108 * s), "LOCK", mono(int(19 * s)), INK_MUTED,
            tracking=int(2 * s))
    tracked(d, (1210 * s, 1146 * s), "STITCH", mono(int(19 * s)), INK_MUTED,
            tracking=int(2 * s))
    finish(img, "detail-cap.png")


# ------------------------------------------------------------------ banners ----

def banner_method():
    w, h = 2000, 800
    img, d = canvas(w, h)
    s = S
    m = 90 * s
    tracked(d, (m, 54 * s), "CADDIEINSIGHT", archivo(int(24 * s), 640, 104), GREEN,
            tracking=int(5 * s))
    tracked(d, (w * s - m, 56 * s), "THE CADDIEINSIGHT METHOD · 4 POSITIONS",
            mono(int(20 * s)), INK_MUTED, tracking=int(3 * s), anchor="r")
    d.line([m, 120 * s, w * s - m, 120 * s], fill=BORDER, width=int(2 * s))
    # one continuous faint arc over the sequence
    swing_arc(d, 1000 * s, 2697 * s, 2527 * s, -108.4, -71.6, BORDER,
              int(5 * s), dash=(0.55, 0.75))
    # the orange downswing gesture: top of figure 02 into the ball at figure 03
    swing_arc(d, 914 * s, 600 * s, 374 * s, -115, -4, ORANGE, int(9 * s))
    tip = (914 * s + 374 * s * math.cos(math.radians(-2)),
           600 * s + 374 * s * math.sin(math.radians(-2)))
    arrow_head(d, s, tip, -2 + 90, size=22)
    golf_ball(d, s, 1310 * s, 618 * s, r=12)
    # four key positions on one ground line
    centers = (330, 790, 1250, 1710)
    keys = ("address", "top", "impact", "finish")
    labels = ("01 · ADDRESS", "02 · TOP", "03 · IMPACT", "04 · FINISH")
    for cxx, key, label in zip(centers, keys, labels):
        skeleton(d, POSES[key], ((cxx - 160) * s, 210 * s, 320 * s, 430 * s), s,
                 limb=GREEN, joint=GREEN_BTN, lw=8)
        tracked(d, (cxx * s, 664 * s), label, mono(int(21 * s)), INK_SOFT,
                tracking=int(3 * s), anchor="m")
    ground_dots(d, s, 640, 140, 1880, step=34)
    # tempo count ticks between positions: 1-2-3 back, 1 down
    for x, n in ((445, "1"), (560, "2"), (675, "3"), (1020, "1")):
        d.line([x * s, 620 * s, x * s, 640 * s], fill=GREEN_BTN, width=int(3 * s))
        tracked(d, (x * s, 588 * s), n, mono(int(16 * s)), INK_MUTED, anchor="m")
    d.line([m, 716 * s, w * s - m, 716 * s], fill=BORDER, width=int(2 * s))
    tracked(d, (m, 740 * s), "ONE SWING · FOUR CHECKPOINTS", mono(int(20 * s)),
            INK_MUTED, tracking=int(4 * s))
    tracked(d, (w * s - m, 740 * s), "TEMPO BENCHMARK 3.0", mono(int(20 * s)),
            INK_MUTED, tracking=int(4 * s), anchor="r")
    finish(img, "banner-method.png", w, h)


def banner_about():
    w, h = 2000, 800
    img, d = canvas(w, h)
    s = S
    m = 90 * s
    tracked(d, (m, 54 * s), "CADDIEINSIGHT", archivo(int(24 * s), 640, 104), GREEN,
            tracking=int(5 * s))
    tracked(d, (w * s - m, 56 * s), "INSTRUMENT BENCH · 06 PIECES",
            mono(int(20 * s)), INK_MUTED, tracking=int(3 * s), anchor="r")
    d.line([m, 120 * s, w * s - m, 120 * s], fill=BORDER, width=int(2 * s))
    # measured grid + bench line
    for gx in range(166, 1980, 152):
        d.line([gx * s, 160 * s, gx * s, 620 * s], fill=ARC_FAINT, width=int(2 * s))
    d.line([m, 620 * s, w * s - m, 620 * s], fill=BORDER, width=int(3 * s))
    for gx in range(120, 1900, 38):
        d.line([gx * s, 620 * s, gx * s, 628 * s], fill=BORDER, width=int(2 * s))
    base = 620

    # 01 tempo wand (leaning, whip in orange — the bench's one gesture)
    pts = []
    for i in range(41):
        t = i / 40
        x = 246 + (292 - 246) * t + 24 * math.sin(math.pi * t)
        y = base - 8 - (base - 8 - 208) * t
        pts.append((x * s, y * s))
    for i in range(16):
        d.line([pts[i], pts[i + 1]], fill=GREEN, width=int(13 * s))
    for i in range(16, 40):
        d.line([pts[i], pts[i + 1]], fill=ORANGE, width=int(9 * s))
    d.ellipse([pts[-1][0] - 17 * s, pts[-1][1] - 17 * s,
               pts[-1][0] + 17 * s, pts[-1][1] + 17 * s], fill=GREEN)

    # 02 metronome (drawn ×3)
    rrect(d, [521 * s, (base - 84) * s, 589 * s, base * s], 12 * s, fill=GREEN)
    rrect(d, [531 * s, (base - 72) * s, 579 * s, (base - 40) * s], 8 * s, fill=MINT)
    rrect(d, [539 * s, (base - 96) * s, 571 * s, (base - 84) * s], 5 * s,
          fill=GREEN_BTN)
    for i, bxx in enumerate((541, 553, 565)):
        d.ellipse([bxx * s, (base - 28) * s, (bxx + 8) * s, (base - 20) * s],
                  fill=MINT)

    # 03 alignment sticks (tripod lean)
    for x0, x1 in ((806, 878), (846, 880), (888, 884)):
        d.line([x0 * s, base * s, x1 * s, 196 * s], fill=GREEN, width=int(9 * s))
        d.line([x0 * s, base * s, (x0 + (x1 - x0) * 0.06) * s,
                (base - (base - 196) * 0.06) * s], fill=GREEN_BTN, width=int(13 * s))

    # 04 hip band (standing loop)
    d.ellipse([1087 * s, (base - 96) * s, 1183 * s, base * s],
              outline=GREEN, width=int(13 * s))
    rrect(d, [1170 * s, (base - 62) * s, 1188 * s, (base - 34) * s], 6 * s,
          fill=GREEN_BTN)

    # 05 swing mirror
    rrect(d, [1345 * s, (base - 320) * s, 1505 * s, base * s], 18 * s, fill=GREEN)
    rrect(d, [1359 * s, (base - 306) * s, 1491 * s, (base - 14) * s], 12 * s,
          fill=MINT)
    d.line([1425 * s, (base - 286) * s, 1425 * s, (base - 34) * s],
           fill=GREEN_BTN, width=int(4 * s))
    for yy in range(base - 266, base - 40, 42):
        d.line([1415 * s, yy * s, 1435 * s, yy * s], fill=GREEN_BTN, width=int(3 * s))

    # 06 performance cap (drawn ×2)
    d.pieslice([1639 * s, (base - 88) * s, 1811 * s, (base + 88) * s], 180, 360,
               fill=GREEN)
    for rx in (86, 40):
        d.arc([(1725 - rx) * s, (base - 88) * s, (1725 + rx) * s,
               (base + 88) * s], 182, 358, fill=GREEN_BTN, width=int(3 * s))
    d.pieslice([1567 * s, (base - 24) * s, 1737 * s, (base + 16) * s], 0, 180,
               fill=GREEN_BTN)
    d.ellipse([1719 * s, (base - 96) * s, 1731 * s, (base - 84) * s],
              fill=GREEN_BTN)

    # specimen labels: name + measure, centered per slot
    entries = (
        (265, "01 TEMPO WAND", "115 CM"),
        (555, "02 METRONOME", "SCALE 3:1"),
        (845, "03 ALIGN STICKS", "122 CM"),
        (1135, "04 HIP BAND", "Ø 27 CM"),
        (1425, "05 SWING MIRROR", "90 CM"),
        (1725, "06 CAP", "SCALE 2:1"),
    )
    for cxx, name, measure in entries:
        tracked(d, (cxx * s, 652 * s), name, mono(int(19 * s)), INK_SOFT,
                tracking=int(2 * s), anchor="m")
        tracked(d, (cxx * s, 686 * s), measure, mono(int(17 * s)), INK_MUTED,
                tracking=int(2 * s), anchor="m")
    d.line([m, 730 * s, w * s - m, 730 * s], fill=BORDER, width=int(2 * s))
    tracked(d, (m, 750 * s), "EVERY FLAG HAS A DRILL · EVERY DRILL HAS A TOOL",
            mono(int(20 * s)), INK_MUTED, tracking=int(4 * s))
    tracked(d, (w * s - m, 750 * s), "SL-GEAR / 06", mono(int(20 * s)),
            INK_MUTED, tracking=int(4 * s), anchor="r")
    finish(img, "banner-about.png", w, h)


# -------------------------------------------------------- homepage report ----

def report_keyframes():
    """Key-position strip for the homepage report card (1600×480): four
    equal frames — ADDRESS / TOP / IMPACT / FINISH — on deep-green fields,
    with mono labels beneath. This is a position-sequence illustration, not
    a club-path measurement."""
    w, h = 1600, 480
    img, d = canvas(w, h, bg=CARD)
    s = S
    gap = 20 * s
    fw = (w * s - 3 * gap) / 4
    fy, fh = 0, 396 * s
    keys = ("address", "top", "impact", "finish")
    labels = ("01 · ADDRESS", "02 · TOP", "03 · IMPACT", "04 · FINISH")
    for i, (key, label) in enumerate(zip(keys, labels)):
        x = i * (fw + gap)
        rrect(d, [x, fy, x + fw, fy + fh], 18 * s, fill=GREEN)
        # ground line
        gy = fy + fh - 42 * s
        d.line([x + 40 * s, gy, x + fw - 40 * s, gy], fill=GREEN_BTN,
               width=int(3 * s))
        # ankles (y = 0.88 of the box) land on the ground line
        box = (x + 62 * s, fy + 36 * s, fw - 124 * s, (gy - fy - 36 * s) / 0.88)
        skeleton(d, POSES[key], box, s, limb=MINT, joint="#7fbf9a", lw=7)
        tracked(d, (x + fw / 2, fy + fh + 24 * s), label, mono(int(28 * s)),
                INK_SOFT, tracking=int(4 * s), anchor="m")
    finish(img, "report-keyframes.png", w, h)


# ------------------------------------------------------------------- social ----

def og_card():
    w, h = 1200, 630
    img, d = canvas(w, h, bg=GREEN)
    s = S
    # deep field with a darker floor band
    d.rectangle([0, 512 * s, w * s, h * s], fill="#123f27")
    for r in (560, 470):
        swing_arc(d, 1030 * s, 700 * s, r * s, 195, 320, "#1d5535", int(3 * s),
                  dash=(1.6, 2.2))
    # copy block
    tracked(d, (80 * s, 74 * s), "CADDIEINSIGHT", archivo(int(28 * s), 660, 104),
            MINT, tracking=int(5 * s))
    d.text((76 * s, 168 * s), "Know your\nswing.",
           font=archivo(int(92 * s), 700, 103), fill=CARD, spacing=int(14 * s))
    tracked(d, (80 * s, 452 * s), "TEMPO · SWAY · SLIDE — FROM ONE PHONE VIDEO",
            mono(int(21 * s)), "#7fbf9a", tracking=int(2 * s))
    tracked(d, (80 * s, 540 * s), "app.caddieinsight.com",
            mono(int(21 * s)), MINT, tracking=int(2 * s))
    # the gauge mark, large, its pivot resting on the floor band — the
    # card's one orange gesture is the mark's sweep
    mark_protractor(d, 764 * s, 486 * s, 330 * s, ink=MINT)
    d.line([700 * s, 560 * s, 1140 * s, 560 * s], fill="#1d5535",
           width=int(5 * s))
    finish(img, "og-swinglab.png", w, h)


if __name__ == "__main__":
    drill_tempo_wand()
    drill_metronome()
    drill_alignment_sticks()
    drill_hip_band()
    drill_swing_mirror()
    detail_cap()
    banner_method()
    banner_about()
    report_keyframes()
    og_card()
