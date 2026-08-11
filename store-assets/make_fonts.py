"""Build the self-hosted web fonts both surfaces ship.

The theme must never reference a third-party font origin at runtime: headless
Chromium in the verification container cannot reach Google Fonts and fails
silently, so a page that depends on it renders in a system fallback without
telling anyone. The faces are therefore fetched once, here, and committed as
assets.

Two faces, three voices:

    Archivo  wght@400..800           interface, variable      34,928 B
    Archivo  wdth 125 / wght 800     display, static          14,536 B
    DM Mono  400, 500                every measured value     29,808 B

Archivo's variable font carries a width axis the previous build discarded, and
the obvious move — ship the dual-axis file and drive width from CSS — is the
wrong one. `wdth,wght@100..125,400..800` costs **90,104** bytes for the latin
subset, against 34,928 for the weight-only file. A separate static instance
costs 14,536, because `wdth 125` is a *named* instance in Archivo's STAT table
and Google therefore serves a pre-built static for it. Points that are not
named instances do not get that treatment: `wdth 118` falls back to a dynamic
build at 37,420, and `wdth 118 / wght 600..800` at 90,104 — the whole variable
font again.

So: two files, 49,464 bytes total, against 90,104 for the single dual-axis
file. The display voice is also *better* at 125 than at the 118 first
proposed, because 125 is where the designer drew Expanded.

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
        "archivo-latin-var.woff2",
        "https://fonts.googleapis.com/css2?family=Archivo:wght@400..800&display=swap",
        None,
    ),
    (
        "archivo-expanded-latin-800.woff2",
        "https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@125,800&display=swap",
        None,
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


def main() -> int:
    for destination in DESTS:
        destination.mkdir(parents=True, exist_ok=True)

    for name, css_url, weight in FACES:
        css = _get(css_url).decode("utf-8")
        payload = _get(_latin_url(css, weight))
        digest = hashlib.sha256(payload).hexdigest()[:12]
        for destination in DESTS:
            (destination / name).write_bytes(payload)
        print(f"{name:32s} {len(payload):7,d} B  sha256:{digest}")

    print(f"\nwrote {len(FACES)} faces into {len(DESTS)} surfaces")
    return 0


if __name__ == "__main__":
    sys.exit(main())
