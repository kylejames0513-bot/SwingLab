"""AppIcon1024.png — the iOS App Store icon for the native app.

Same mark as make_apple_touch_icon.py (the dark green field, cream
ball-face circle, orange swing-arc gesture, and ball dot), rendered at
Apple's required 1024x1024 marketing size straight into the Xcode asset
catalog. App Store icons must be opaque with square corners — iOS masks
the rounding itself.

Writes into the iOS project:
    python store-assets/make_ios_app_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from make_apple_touch_icon import CANVAS, CREAM, NIGHT, ORANGE, swing_arc

HERE = Path(__file__).parent
APPICONSET = (
    HERE.parent / "ios" / "CaddieInsight" / "Assets.xcassets" / "AppIcon.appiconset"
)

SIZE = 1024
S = 2  # supersample factor


def main() -> None:
    scale = CANVAS * S / 512
    img = Image.new("RGB", (CANVAS * S, CANVAS * S), NIGHT)
    draw = ImageDraw.Draw(img)

    def xy(x: float, y: float) -> tuple[float, float]:
        return (x * scale, y * scale)

    draw.ellipse([xy(256 - 150, 256 - 150), xy(256 + 150, 256 + 150)], fill=CREAM)
    draw.polygon([xy(x, y) for x, y in swing_arc()], fill=ORANGE)
    draw.ellipse([xy(345 - 22, 192 - 22), xy(345 + 22, 192 + 22)], fill=NIGHT)

    APPICONSET.mkdir(parents=True, exist_ok=True)
    out = APPICONSET / "AppIcon1024.png"
    img.resize((SIZE, SIZE), Image.LANCZOS).save(out, format="PNG")
    print("wrote", out)


if __name__ == "__main__":
    main()
