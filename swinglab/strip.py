"""Key position strip: address, top, impact, finish tiled on white with a
small branded header."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from .config import Config
from .drawing import load_font, save_branded

LABELS = ("Address", "Top", "Impact", "Finish")
PAD = 12
HEADER_H = 56
LABEL_H = 40


def make_strip(
    frame_paths: list[Path], swing_no: int, out_path: str | Path, cfg: Config
) -> Path:
    """Tile the four full-res key frames horizontally, labeled."""
    images = [Image.open(p).convert("RGB") for p in frame_paths]
    tile_h = min(im.height for im in images)
    images = [
        im.resize((round(im.width * tile_h / im.height), tile_h)) for im in images
    ]

    width = sum(im.width for im in images) + PAD * (len(images) + 1)
    height = HEADER_H + tile_h + LABEL_H + PAD
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)

    primary = cfg.brand["primary_color"]
    draw.rectangle([0, 0, width, HEADER_H - PAD], fill=primary)
    header_font = load_font(24)
    draw.text(
        (PAD, (HEADER_H - PAD) / 2 - 14),
        f"{cfg.brand['name']} — Swing {swing_no}: key positions",
        fill="white",
        font=header_font,
    )

    label_font = load_font(20)
    x = PAD
    for im, label in zip(images, LABELS):
        canvas.paste(im, (x, HEADER_H))
        bbox = draw.textbbox((0, 0), label, font=label_font)
        draw.text(
            (x + (im.width - (bbox[2] - bbox[0])) / 2, HEADER_H + tile_h + 8),
            label,
            fill=primary,
            font=label_font,
        )
        x += im.width + PAD

    return save_branded(canvas, out_path, cfg)
