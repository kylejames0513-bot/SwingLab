"""CaddieInsight Founders Pass — reproducible instrument-card drawing.

The live storefront ships photoreal Founders art
(`out/caddieinsight-founders-card-v2.png`) so Pro and Founders match as one
campaign series. This module keeps a flat instrument-card generator for
offline brand studies and print specimens: membership-card object on the
brand field, same 1536 x 1024 frame, one orange pendulum arc.
"""

from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw

from make_assets import (
    ARC_FAINT, BG, BORDER, CARD, GREEN, GREEN_INK, INK, INK_MUTED,
    ORANGE, archivo, canvas, finish, mono, rrect, swing_arc, tracked,
)

# quiet greens for marks printed on the deep-green card face
FACE_LINE = "#2e5c42"    # inner hairline + companion arc
FACE_TICK = "#3f7256"    # minor calibration ticks
FACE_MUTED = "#7fbf9a"   # secondary mono labels

W, H = 1536, 1024        # matches the v2 membership-card series
SS = 3                   # supersample factor


def founders_card():
    img, d = canvas(W, H, scale=SS)
    s = SS
    m = 96 * s

    # faint dashed brand arc across the field, per the series chrome
    swing_arc(d, W * s * 0.88, H * s * 0.10, 860 * s, 95, 175,
              ARC_FAINT, int(3 * s), dash=(2.2, 2.6))

    # ---- header chrome -----------------------------------------------------
    tracked(d, (m, 90 * s - 6 * s), "CADDIEINSIGHT",
            archivo(int(28 * s), 640, 104), GREEN, tracking=int(4 * s))
    tracked(d, (W * s - m, 90 * s - 4 * s), "MEMBERSHIP · FOUNDERS",
            mono(int(24 * s)), INK_MUTED, tracking=int(4 * s), anchor="r")
    d.line([m, 150 * s, W * s - m, 150 * s], fill=BORDER, width=int(2 * s))

    # ---- footer chrome -----------------------------------------------------
    d.line([m, 824 * s, W * s - m, 824 * s], fill=BORDER, width=int(2 * s))
    d.text((m, 842 * s), "Founders Pass",
           font=archivo(int(44 * s), 620, 102), fill=INK)
    tracked(d, (m, 908 * s), "ONE PAYMENT · FIRST 100 · NEVER RENEWS",
            mono(int(20 * s)), INK_MUTED, tracking=int(2 * s))
    tracked(d, (W * s - m, 908 * s), "SL-PRO-LIFE", mono(int(20 * s)),
            INK_MUTED, tracking=int(3 * s), anchor="r")

    # ---- the card object ---------------------------------------------------
    # credit-card proportions (1.586 : 1), centered between the rules
    cx0, cy0, cx1, cy1 = 308 * s, 194 * s, 1228 * s, 774 * s
    rrect(d, [cx0, cy0, cx1, cy1], 36 * s, fill=GREEN)
    # printed inner hairline, like the border of a specimen card
    rrect(d, [cx0 + 16 * s, cy0 + 16 * s, cx1 - 16 * s, cy1 - 16 * s],
          22 * s, outline=FACE_LINE, width=int(2 * s))

    pad = 60 * s
    lx = cx0 + pad           # left print margin on the card
    rx = cx1 - pad           # right print margin on the card

    # the arcs live on their own layer clipped to the card shape, so the
    # sweep enters cropped by the card's bottom edge instead of stopping
    # short of it — the hero's arc is cropped by its frame the same way
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    dl = ImageDraw.Draw(lay)
    pvx, pvy = 1188 * s, 828 * s
    # dashed companion arc first (texture), then the orange gesture over it
    swing_arc(dl, pvx, pvy, 512 * s, 184, 262, FACE_LINE, int(3 * s),
              dash=(2.0, 2.6))
    swing_arc(dl, pvx, pvy, 466 * s, 180, 270, ORANGE, int(12 * s))
    # ball at the sweep's terminus, mint
    bx, by = pvx, pvy - 466 * s
    dl.ellipse([bx - 15 * s, by - 15 * s, bx + 15 * s, by + 15 * s],
               fill=GREEN_INK)
    clip = Image.new("L", img.size, 0)
    ImageDraw.Draw(clip).rounded_rectangle([cx0, cy0, cx1, cy1], 36 * s,
                                           fill=255)
    lay.putalpha(ImageChops.multiply(lay.getchannel("A"), clip))
    img.paste(lay, (0, 0), lay)

    # card-face labels, top corners
    tracked(d, (lx, cy0 + 52 * s), "CADDIEINSIGHT PRO", mono(int(21 * s)),
            GREEN_INK, tracking=int(4 * s))
    tracked(d, (rx, cy0 + 52 * s), "LIFETIME", mono(int(21 * s)),
            FACE_MUTED, tracking=int(4 * s), anchor="r")

    # identity: two-line display setting
    f_disp = archivo(int(92 * s), 780, 104)
    d.text((lx - 4 * s, cy0 + 122 * s), "FOUNDERS", font=f_disp, fill=CARD)
    d.text((lx - 4 * s, cy0 + 224 * s), "PASS", font=f_disp, fill=CARD)

    # price line: display price, mono qualifier on the same optical baseline
    py = cy0 + 366 * s
    f_price = archivo(int(52 * s), 700, 104)
    d.text((lx, py), "$249", font=f_price, fill=CARD)
    pw = d.textlength("$149", font=f_price)
    tracked(d, (lx + pw + 26 * s, py + 22 * s), "· ONE PAYMENT",
            mono(int(23 * s)), GREEN_INK, tracking=int(3 * s))

    # calibration strip: one tick per membership, a major on every tenth
    # member (10, 20, … 100) so the count closes on a major
    base = cy1 - 104 * s
    span = rx - lx
    for i in range(100):
        x = lx + span * i / 99
        major = i % 10 == 9
        h = 20 * s if major else 11 * s
        col = GREEN_INK if major else FACE_TICK
        d.line([x, base - h, x, base], fill=col, width=int(2 * s))

    # specimen labels under the strip
    tracked(d, (lx, base + 18 * s), "FIRST 100 MEMBERS", mono(int(20 * s)),
            GREEN_INK, tracking=int(4 * s))
    tracked(d, (rx, base + 18 * s), "NEVER RENEWS", mono(int(20 * s)),
            FACE_MUTED, tracking=int(4 * s), anchor="r")

    # founders-card-instrument-study.png, NOT caddieinsight-founders-card-v2:
    # that exact filename is the shipped PHOTOGRAPH the storefront's webp
    # ladder is encoded from (plan_card_webp.py reads it). This module is an
    # offline brand study by its own docstring — writing to the shipped name
    # meant one careless `python founders_card.py && python plan_card_webp.py`
    # replaced the storefront's photography with a vector drawing.
    finish(img, "founders-card-instrument-study.png", W, H)


if __name__ == "__main__":
    founders_card()
