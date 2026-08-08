#!/usr/bin/env python3
"""Generate solid-color placeholder brand PNGs for CaddieInsight."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


def write_png(width: int, height: int, pixels: list[tuple[int, int, int, int]], path: Path) -> None:
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        row = y * width
        for x in range(width):
            r, g, b, a = pixels[row + x]
            raw.extend((r, g, b, a))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    blob = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(blob)
    print(f"wrote {path} ({width}x{height}, {len(blob)} bytes)")


def solid(w: int, h: int, rgba: tuple[int, int, int, int]) -> list[tuple[int, int, int, int]]:
    return [rgba] * (w * h)


def main() -> None:
    out = Path(__file__).resolve().parents[1] / "assets"
    out.mkdir(parents=True, exist_ok=True)

    write_png(1024, 1024, solid(1024, 1024, (26, 61, 46, 255)), out / "icon.png")
    write_png(1024, 1024, solid(1024, 1024, (34, 85, 64, 255)), out / "adaptive-icon.png")

    mono = [(0, 0, 0, 0)] * (432 * 432)
    for y in range(432):
        for x in range(432):
            if 96 <= x < 336 and 96 <= y < 336:
                mono[y * 432 + x] = (255, 255, 255, 255)
    write_png(432, 432, mono, out / "monochrome-icon.png")

    splash = [(0, 0, 0, 0)] * (1024 * 1024)
    for y in range(1024):
        for x in range(1024):
            if 312 <= x < 712 and 312 <= y < 712:
                splash[y * 1024 + x] = (26, 61, 46, 255)
    write_png(1024, 1024, splash, out / "splash-icon.png")

    note = [(0, 0, 0, 0)] * (96 * 96)
    for y in range(96):
        for x in range(96):
            if 20 <= x < 76 and 20 <= y < 76:
                note[y * 96 + x] = (255, 255, 255, 255)
    write_png(96, 96, note, out / "notification-icon.png")


if __name__ == "__main__":
    main()
