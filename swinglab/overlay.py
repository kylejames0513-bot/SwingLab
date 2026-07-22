"""Centerline overlay — the signature visual.

Three panels (address, top, impact). On top and impact, two skeletons: the
captured body and a corrected one produced by an ankle-pinned shear so the feet
stay planted and the correction grows with height. The address panel shows only
the captured skeleton plus a dashed vertical centerline through the head.

Ideal head position: at the top, the head is where it was at address; at
impact, 0.10 shoulder widths behind address (a touch behind the ball is
correct with driver). "Behind" means away from the target.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import pose
from .config import Config
from .drawing import (
    draw_dashed_vline,
    draw_gap_arrow,
    draw_skeleton,
    load_font,
    save_branded,
    sheared,
)

PAD = 12
HEADER_H = 56
LABEL_H = 40
LEGEND_H = 44
PANELS = ("Address", "Top", "Impact")


def _panel(
    frame_path: Path,
    lm: pose.Landmarks | None,
    dx: float | None,
    shoulder_width_px: float,
    cfg: Config,
) -> Image.Image:
    """One overlay panel. dx=None => address style (captured + centerline)."""
    img = Image.open(frame_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    if lm is None:
        return img  # pose failed here; ship the bare frame rather than nothing

    captured = cfg.overlay["captured_color"]
    corrected = cfg.overlay["corrected_color"]

    if dx is None:
        head = pose.head_center(lm)
        draw_dashed_vline(draw, head[0], 0, img.height, corrected)
        draw_skeleton(draw, lm, captured, shoulder_width_px)
        return img

    ideal_lm = sheared(lm, dx)
    # green (corrected) first, orange (captured) on top
    draw_skeleton(draw, ideal_lm, corrected, shoulder_width_px)
    draw_skeleton(draw, lm, captured, shoulder_width_px)

    if abs(dx) > cfg.overlay["arrow_min_px"]:
        cap_head = pose.head_center(lm)
        ideal_head = pose.head_center(ideal_lm)
        gap_sw = abs(dx) / shoulder_width_px
        draw_gap_arrow(
            draw,
            y=min(cap_head[1], ideal_head[1]),
            x_from=cap_head[0],
            x_to=ideal_head[0],
            label=f"{gap_sw:.2f} SW",
            color=captured,
            font=load_font(22),
        )
    return img


def make_overlay(
    frame_paths: dict[str, Path],  # keys: address, top, impact (full-res frames)
    landmarks: dict[str, pose.Landmarks | None],  # same keys, full-res pixel coords
    target_direction: int,
    out_path: str | Path,
    cfg: Config,
) -> Path:
    address_lm = landmarks.get("address")
    if address_lm is None:
        raise ValueError("Overlay needs a valid pose on the address frame.")
    sw = float(
        np.linalg.norm(
            address_lm[pose.LEFT_SHOULDER] - address_lm[pose.RIGHT_SHOULDER]
        )
    )
    address_head_x = pose.head_center(address_lm)[0]
    away = -target_direction

    def dx_for(key: str) -> float | None:
        lm = landmarks.get(key)
        if key == "address" or lm is None:
            return None
        ideal_x = address_head_x
        if key == "impact":
            ideal_x += cfg.analysis["impact_behind_sw"] * sw * away
        return float(pose.head_center(lm)[0] - ideal_x)

    panels = [
        _panel(frame_paths[key], landmarks.get(key), dx_for(key), sw, cfg)
        for key in ("address", "top", "impact")
    ]

    tile_h = min(p.height for p in panels)
    panels = [p.resize((round(p.width * tile_h / p.height), tile_h)) for p in panels]
    width = sum(p.width for p in panels) + PAD * (len(panels) + 1)
    height = HEADER_H + tile_h + LABEL_H + LEGEND_H
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)

    primary = cfg.brand["primary_color"]
    draw.rectangle([0, 0, width, HEADER_H - PAD], fill=primary)
    draw.text(
        (PAD, (HEADER_H - PAD) / 2 - 14),
        f"{cfg.brand['name']} — centerline overlay",
        fill="white",
        font=load_font(24),
    )

    label_font = load_font(20)
    x = PAD
    for panel, label in zip(panels, PANELS):
        canvas.paste(panel, (x, HEADER_H))
        bbox = draw.textbbox((0, 0), label, font=label_font)
        draw.text(
            (x + (panel.width - (bbox[2] - bbox[0])) / 2, HEADER_H + tile_h + 8),
            label,
            fill=primary,
            font=label_font,
        )
        x += panel.width + PAD

    # legend strip: orange = captured, green = where it should be
    ly = HEADER_H + tile_h + LABEL_H + 8
    legend_font = load_font(18)
    swatch = 18
    x = PAD
    for color, text in (
        (cfg.overlay["captured_color"], "Captured"),
        (cfg.overlay["corrected_color"], "Where it should be"),
    ):
        draw.rectangle([x, ly, x + swatch, ly + swatch], fill=color)
        draw.text((x + swatch + 8, ly - 2), text, fill="black", font=legend_font)
        x += swatch + 8 + draw.textbbox((0, 0), text, font=legend_font)[2] + 32

    return save_branded(canvas, out_path, cfg)
