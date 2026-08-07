"""Shared Pillow drawing helpers: fonts, skeletons, the ankle-pinned shear,
and branding (watermark) applied to generated images."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import pose

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
)

LIMB_LINES = (
    (pose.LEFT_SHOULDER, pose.LEFT_ELBOW),
    (pose.LEFT_ELBOW, pose.LEFT_WRIST),
    (pose.RIGHT_SHOULDER, pose.RIGHT_ELBOW),
    (pose.RIGHT_ELBOW, pose.RIGHT_WRIST),
    (pose.LEFT_HIP, pose.LEFT_KNEE),
    (pose.LEFT_KNEE, pose.LEFT_ANKLE),
    (pose.RIGHT_HIP, pose.RIGHT_KNEE),
    (pose.RIGHT_KNEE, pose.RIGHT_ANKLE),
)


def load_font(size: int) -> ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def sheared(pts: pose.Landmarks, dx: float) -> pose.Landmarks:
    """Ankle-pinned shear: feet stay planted, the correction grows with height.

    ``dx`` is the horizontal error at head level (captured minus ideal); each
    point is shifted against it in proportion to its height above the ankles.
    """
    heady = pts[pose.NOSE][1]
    ankley = (pts[pose.LEFT_ANKLE][1] + pts[pose.RIGHT_ANKLE][1]) / 2
    out = {}
    for k, v in pts.items():
        f = np.clip((ankley - v[1]) / (ankley - heady), 0, 1)
        out[k] = v - np.array([dx * f, 0.0])
    return out


def head_radius(lm: pose.Landmarks, shoulder_width_px: float) -> float:
    ear_dist = max(
        np.linalg.norm(lm[pose.NOSE] - lm[pose.LEFT_EAR]),
        np.linalg.norm(lm[pose.NOSE] - lm[pose.RIGHT_EAR]),
    )
    return max(1.5 * ear_dist, 0.30 * shoulder_width_px)


def draw_skeleton(
    draw: ImageDraw.ImageDraw,
    lm: pose.Landmarks,
    color: str,
    shoulder_width_px: float,
    line_w: int = 4,
) -> None:
    """Limb lines, shoulder/hip lines, spine, neck, and a head circle."""

    def line(a: np.ndarray, b: np.ndarray) -> None:
        draw.line([tuple(a), tuple(b)], fill=color, width=line_w)

    for a, b in LIMB_LINES:
        line(lm[a], lm[b])
    line(lm[pose.LEFT_SHOULDER], lm[pose.RIGHT_SHOULDER])
    line(lm[pose.LEFT_HIP], lm[pose.RIGHT_HIP])
    shoulder_mid = pose.midpoint(lm, pose.LEFT_SHOULDER, pose.RIGHT_SHOULDER)
    hip_mid = pose.midpoint(lm, pose.LEFT_HIP, pose.RIGHT_HIP)
    head = pose.head_center(lm)
    line(hip_mid, shoulder_mid)  # spine
    line(shoulder_mid, head)  # neck
    r = head_radius(lm, shoulder_width_px)
    draw.ellipse(
        [head[0] - r, head[1] - r, head[0] + r, head[1] + r],
        outline=color,
        width=line_w,
    )


def draw_dashed_vline(
    draw: ImageDraw.ImageDraw,
    x: float,
    y0: float,
    y1: float,
    color: str,
    dash: int = 12,
    line_w: int = 3,
) -> None:
    y = y0
    while y < y1:
        draw.line([(x, y), (x, min(y + dash, y1))], fill=color, width=line_w)
        y += 2 * dash


def draw_marker(draw: ImageDraw.ImageDraw, point: tuple[float, float], color: str, radius: int = 8) -> None:
    """Draw one measured landmark marker; callers never imply an ideal pose."""
    x, y = point
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)


def draw_dashed_line(draw: ImageDraw.ImageDraw, start: tuple[float, float], end: tuple[float, float], color: str, dash: int = 12, line_w: int = 3) -> None:
    """Draw a configured, explicitly labelled boundary."""
    a, b = np.array(start, dtype=float), np.array(end, dtype=float)
    length = float(np.linalg.norm(b - a))
    if length == 0:
        return
    unit = (b - a) / length
    for offset in range(0, int(length), dash * 2):
        p0 = a + unit * offset
        p1 = a + unit * min(offset + dash, length)
        draw.line([tuple(p0), tuple(p1)], fill=color, width=line_w)


def draw_displacement_arrow(draw: ImageDraw.ImageDraw, start: tuple[float, float], end: tuple[float, float], color: str) -> None:
    draw.line([start, end], fill=color, width=3)
    draw.polygon([end, (end[0] - 8, end[1] - 5), (end[0] - 8, end[1] + 5)], fill=color)


def draw_angle_arc(draw: ImageDraw.ImageDraw, center: tuple[float, float], start: float, end: float, radius: float, color: str) -> None:
    draw.arc((center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius), start, end, fill=color, width=3)


def draw_labeled_timeline(draw: ImageDraw.ImageDraw, events: list[tuple[str, float]], *, y: int, color: str, font: ImageFont.ImageFont) -> None:
    """Timing-only primitive; it deliberately accepts no body landmarks."""
    if not events:
        return
    left, right = 50, 750
    draw.line((left, y, right, y), fill=color, width=3)
    first, last = events[0][1], events[-1][1]
    span = max(last - first, 1)
    for label, timestamp in events:
        x = left + (timestamp - first) / span * (right - left)
        draw.line((x, y - 14, x, y + 14), fill=color, width=3)
        draw.text((x - 25, y + 20), label, fill=color, font=font)


def draw_gap_arrow(
    draw: ImageDraw.ImageDraw,
    y: float,
    x_from: float,
    x_to: float,
    label: str,
    color: str,
    font: ImageFont.ImageFont,
) -> None:
    """Horizontal double-stemmed arrow between two head centers, labeled."""
    draw.line([(x_from, y), (x_to, y)], fill=color, width=3)
    head = 8 if x_to > x_from else -8
    draw.polygon(
        [(x_to, y), (x_to - head, y - 5), (x_to - head, y + 5)], fill=color
    )
    tx = (x_from + x_to) / 2
    bbox = draw.textbbox((0, 0), label, font=font)
    draw.text(
        (tx - (bbox[2] - bbox[0]) / 2, y - (bbox[3] - bbox[1]) - 10),
        label,
        fill=color,
        font=font,
    )


def apply_watermark(img: Image.Image, text: str) -> Image.Image:
    """Semi-transparent brand name in the bottom-right corner."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = load_font(max(16, img.height // 30))
    bbox = draw.textbbox((0, 0), text, font=font)
    pos = (img.width - (bbox[2] - bbox[0]) - 16, img.height - (bbox[3] - bbox[1]) - 16)
    # translucent white with a dark stroke so it reads on any background
    draw.text(
        pos,
        text,
        fill=(255, 255, 255, 110),
        font=font,
        stroke_width=2,
        stroke_fill=(40, 40, 40, 110),
    )
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def hex_to_rgba(color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    """'#rrggbb' -> (r, g, b, alpha); needed because brand colors are hex
    strings and Pillow RGBA layers want tuples."""
    c = color.lstrip("#")
    if len(c) != 6:
        raise ValueError(f"expected '#rrggbb' color, got {color!r}")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), alpha)


def draw_chip(
    layer: Image.Image,
    xy: tuple[int, int],
    text: str,
    bg_rgba: tuple[int, int, int, int],
    font: ImageFont.ImageFont,
    radius: int = 6,
    pad: tuple[int, int] = (10, 6),
) -> tuple[int, int]:
    """Rounded-rect text chip on an RGBA layer (the report-card look).

    Returns the rendered (width, height) so the caller can stack the next
    chip below this one.
    """
    draw = ImageDraw.Draw(layer)
    bbox = draw.textbbox((0, 0), text, font=font)
    w = (bbox[2] - bbox[0]) + 2 * pad[0]
    h = (bbox[3] - bbox[1]) + 2 * pad[1]
    x, y = xy
    draw.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=bg_rgba)
    # subtract the bbox origin so ascender/leading offsets don't skew centering
    draw.text(
        (x + pad[0] - bbox[0], y + pad[1] - bbox[1]),
        text,
        font=font,
        fill=(255, 255, 255, 255),
    )
    return w, h


def save_branded(img: Image.Image, out_path: str | Path, cfg) -> Path:
    if cfg.brand["watermark"]:
        img = apply_watermark(img, cfg.brand["name"])
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path
