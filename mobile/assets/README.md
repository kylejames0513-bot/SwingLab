# CaddieInsight brand assets

Repository-owned binary assets referenced by `app.config.ts`. Do not inherit Expo template artwork.

| File | Size | Notes |
|------|------|--------|
| `icon.png` | 1024×1024 | Opaque app icon (`#1A3D2E`) |
| `adaptive-icon.png` | 1024×1024 | Android adaptive foreground (opaque brand fill; keep critical mark in the center safe zone) |
| `monochrome-icon.png` | 432×432 | Single-color (white) glyph on transparent |
| `splash-icon.png` | 1024×1024 | Transparent splash mark |
| `notification-icon.png` | 96×96 | White glyph on transparent (Android notification) |

## Export rules

1. Source of truth is this directory; every path must be explicit in `app.config.ts`.
2. Replace solid placeholders with final brand artwork before store submission.
3. Icons must not match Expo template asset hashes.
4. Prefer PNG-24 with alpha where transparency is required; opaque for the primary app icon.
5. Regenerate placeholders with `python3 scripts/generate-brand-assets.py` if present.
