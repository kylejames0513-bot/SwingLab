"""Build the self-hosted web fonts both surfaces ship.

The theme must never reference a third-party font origin at runtime: headless
Chromium in the verification container cannot reach Google Fonts and fails
silently, so a page that depends on it renders in a system fallback without
telling anyone. The faces are therefore fetched once, here, and committed as
assets.

Three faces, three voices:

    Barlow            400, 500       interface, static        44,204 B
    Barlow Condensed  600            display, static          22,308 B
    DM Mono           400, 500       every measured value     29,808 B

**Barlow has no variable font.** Google serves it at v13 as a static family, and
the css2 endpoint rejects a range outright — `Barlow:wght@400..700` returns an
HTML error page, not a stylesheet. So every weight is a separate 22 KB file and
the weight palette is a budget rather than a free axis.

That is why the interface collapsed from seven weights to two. Archivo's
variable file made 400/500/600/650/700/750/800 cost the same as one, and 157
declarations duly accumulated across the two surfaces. Under Industry the
display voice is a different *family* (Barlow Condensed), not a heavier grade of
the body face, so the interface only ever needs regular and medium:

    600 · Barlow Condensed   every heading and display number
    500 · Barlow             interface emphasis, buttons, labels
    400 · Barlow             body copy

Weights this build does not ship are synthesised by the browser, which is what
`tests/test_storefront_design_system.py` polices.

The cost is 66,512 bytes against Archivo's 49,464 — 17 KB more for two extra
files, paid because Condensed is a real family rather than an instance of the
body face. Three weights of one static family (66 KB) would have been the
alternative and buys nothing the condensed face does not do better.

Google's CDN is the source rather than the raw `ofl/` TTF because its
production build is dramatically better optimised — a locally subset TTF of
the same coverage lands at ~81 KB against Google's 34.9 KB for identical
axes. The existing `archivo-latin-var.woff2` is byte-identical to Google's
`wght@400..800` latin file, so this is also how it was originally made.

Run from the repository root:

    python store-assets/make_fonts.py

Writes byte-identical copies into both surfaces. They must stay identical:
the guided report asks for Archivo and relies on the app shell having loaded
the same face the storefront did.
"""

from __future__ import annotations

import hashlib
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTS = (
    ROOT / "storefront-theme" / "assets",
    ROOT / "swinglab" / "web" / "static",
)

# A modern Chrome UA is required: the css2 endpoint serves woff2 only to
# browsers it recognises, and falls back to ttf otherwise.
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
}

# The latin subset both surfaces already declare. Kept verbatim so the
# @font-face unicode-range in base.css and web_layout.html.j2 stays truthful.
LATIN = (
    "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,"
    "U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,"
    "U+2212,U+2215,U+FEFF,U+FFFD"
)

FACES = [
    (
        "barlow-latin-400.woff2",
        "https://fonts.googleapis.com/css2?family=Barlow:wght@400&display=swap",
        "400",
    ),
    (
        "barlow-latin-500.woff2",
        "https://fonts.googleapis.com/css2?family=Barlow:wght@500&display=swap",
        "500",
    ),
    (
        "barlow-condensed-latin-600.woff2",
        "https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600&display=swap",
        "600",
    ),
    (
        "dm-mono-latin-400.woff2",
        "https://fonts.googleapis.com/css2?family=DM+Mono:wght@400&display=swap",
        "400",
    ),
    (
        "dm-mono-latin-500.woff2",
        "https://fonts.googleapis.com/css2?family=DM+Mono:wght@500&display=swap",
        "500",
    ),
]


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _latin_url(css: str, weight: str | None) -> str:
    """Pick the latin @font-face block's woff2 URL.

    `latin-ext` also contains U+0000-00FF in its comment header, so the block
    is identified by its unicode-range declaration rather than by a substring
    search over the whole block.
    """
    blocks = css.split("@font-face")
    for block in blocks:
        ranges = re.search(r"unicode-range:\s*([^;]+);", block)
        if not ranges or "U+0000-00FF" not in ranges.group(1):
            continue
        # latin-ext's range starts at U+0100; latin's at U+0000.
        if not ranges.group(1).strip().startswith("U+0000-00FF"):
            continue
        if weight is not None:
            declared = re.search(r"font-weight:\s*([^;]+);", block)
            if declared and declared.group(1).strip() != weight:
                continue
        url = re.search(r"url\((https://[^)]+)\)", block)
        if url:
            return url.group(1)
    raise SystemExit(f"no latin woff2 block found (weight={weight})")


# Faces this build replaced. A theme zip ships every file in assets/, so a
# leftover font is dead weight in every release and an invitation for a later
# @font-face to resurrect it. Deleting them here rather than by hand keeps one
# source of truth for what the surfaces are allowed to serve.
RETIRED = (
    "archivo-latin-var.woff2",
    "archivo-expanded-latin-800.woff2",
    "plex-mono-latin-400.woff2",
    "plex-mono-latin-500.woff2",
)


def main() -> int:
    for destination in DESTS:
        destination.mkdir(parents=True, exist_ok=True)

    total = 0
    for name, css_url, weight in FACES:
        css = _get(css_url).decode("utf-8")
        payload = _get(_latin_url(css, weight))
        digest = hashlib.sha256(payload).hexdigest()[:12]
        for destination in DESTS:
            (destination / name).write_bytes(payload)
        total += len(payload)
        print(f"{name:32s} {len(payload):7,d} B  sha256:{digest}")

    dropped = 0
    for name in RETIRED:
        for destination in DESTS:
            stale = destination / name
            if stale.exists():
                stale.unlink()
                dropped += 1
                print(f"{name:32s} retired")

    print(
        f"\nwrote {len(FACES)} faces ({total:,d} B) into {len(DESTS)} surfaces"
        f"; removed {dropped} retired file(s)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
