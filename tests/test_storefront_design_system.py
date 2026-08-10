"""The storefront design system, enforced — the scan's numbers as assertions.

The 2026-08-10 theme scan measured how the last system rotted: 142 raw px
font sizes against 37 token uses (34 of them half-pixel values, 33 sites
below 12px), 32 distinct breakpoints where four would do, three orphan
colours matching no token, and the dark-surface text colour hardcoded 66
times because it had no name. None of that happened in one commit; it
accumulated because nothing failed when a section nudged instead of using
the scale.

These gates make the system self-defending. Each one is a plain grep an
author can run and understand; each failure message says what to use
instead.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT / "storefront-theme"

STYLED = sorted(
    [
        *THEME.glob("sections/*.liquid"),
        *THEME.glob("snippets/*.liquid"),
        *THEME.glob("layout/*.liquid"),
        THEME / "assets" / "base.css",
    ]
)


def _sources() -> dict[str, str]:
    return {
        str(path.relative_to(THEME)): path.read_text(encoding="utf-8")
        for path in STYLED
    }


def _below_token_sheet(name: str, body: str) -> str:
    """base.css's :root IS the token sheet — literals are definitions there."""
    if name != "assets/base.css":
        return body
    cut = body.index("}", body.index(":root {")) + 1
    return body[cut:]


def test_font_sizes_come_from_the_scale():
    """`font-size` names a token; only the token sheet spells pixels.

    The old sheet's 8-rung scale was bypassed by 19 distinct off-scale
    sizes. A rung is a decision; a pixel is a nudge.
    """
    offenders = []
    for name, body in _sources().items():
        body = _below_token_sheet(name, body)
        for match in re.finditer(r"font-size:\s*([^;]+);", body):
            value = match.group(1).strip()
            if "px" in value and "var(--sl-" not in value:
                offenders.append(f"{name}: font-size: {value}")
    assert offenders == [], (
        "raw pixel font sizes — pick a --sl-text-* / --sl-heading-* rung:\n"
        + "\n".join(offenders)
    )


def test_no_half_pixel_type_or_layout_values():
    """34 half-pixel font sizes was the signature of nudging, not a scale.

    Sub-2px half values (1.5px hairline borders, 0.5px rules) are a
    deliberate idiom and stay; a 10.5px font or a 14.5px gap is a nudge.
    """
    offenders = []
    for name, body in _sources().items():
        body = _below_token_sheet(name, body)
        for match in re.finditer(r"\b(\d+\.5)px", body):
            if float(match.group(1)) >= 2:
                offenders.append(f"{name}: {match.group(0)}")
    assert offenders == [], "half-pixel values:\n" + "\n".join(offenders)


def test_type_never_renders_below_twelve_pixels():
    """The floor. 33 sites sat below it, down to 9.5px uppercase mono."""
    offenders = []
    for name, body in _sources().items():
        body = _below_token_sheet(name, body)
        for match in re.finditer(r"font-size:\s*(\d+(?:\.\d+)?)px", body):
            if float(match.group(1)) < 12:
                offenders.append(f"{name}: {match.group(0)}")
    assert offenders == [], "sub-12px type:\n" + "\n".join(offenders)


def test_exactly_four_breakpoints():
    """560 / 750 / 1000 / 1280 — and their max-width complements.

    The old theme had 32 distinct conditions; `how-it-works` broke at 767px
    while every neighbour used 749, so a 750-767px window rendered one
    section in mobile layout inside a desktop page.
    """
    allowed = {
        "min-width: 560px", "min-width: 750px",
        "min-width: 1000px", "min-width: 1280px",
        "max-width: 559px", "max-width: 749px",
        "max-width: 999px", "max-width: 1279px",
    }
    offenders = []
    for name, body in _sources().items():
        for prelude in re.findall(r"@media[^{]+", body):
            if "prefers-" in prelude or "@supports" in prelude:
                continue
            for cond in re.findall(r"(?:min|max)-width:\s*\d+px", prelude):
                normalized = re.sub(r"\s+", " ", cond)
                if normalized not in allowed:
                    offenders.append(f"{name}: @media ({normalized})")
    assert offenders == [], (
        "off-system breakpoints — the system is 560/750/1000/1280:\n"
        + "\n".join(offenders)
    )


def test_the_orphan_colours_stay_dead():
    """Three colours matched no token: #e8720c (a ghost of an older
    palette, 10 uses), #14472c (14), #0f2a1b (4). Each now has exactly one
    home — the accent, --sl-arc, and the night shadow triplet."""
    orphan = re.compile(
        r"#e8720c|#14472c|#0f2a1b"
        r"|rgba\(\s*232\s*,\s*114\s*,\s*12"
        r"|rgba\(\s*20\s*,\s*71\s*,\s*44"
        r"|rgba\(\s*15\s*,\s*42\s*,\s*27",
        re.I,
    )
    offenders = []
    for name, body in _sources().items():
        body = _below_token_sheet(name, body)
        for match in orphan.finditer(body):
            offenders.append(f"{name}: {match.group(0)}")
    assert offenders == [], "orphan colours:\n" + "\n".join(offenders)


def test_the_named_colours_are_used_as_tokens():
    """The palette's solid hexes appear only in the token sheet.

    #f5f2e9 — the text colour on every dark surface — had 66 hardcoded uses
    and no name. Alpha-derivations via the *-rgb triplets are fine; a bare
    hex that duplicates a token is a fork waiting to drift.
    """
    named = re.compile(
        r"#f5f2e9|#06110c|#f07a18|#ff9a42|#ffad62|#0f3d28|#1a5c38|#9a4b0a"
        r"|#eef2ef|#f8fbf9|#101a14|#445049|#626a63|#d4ddd6|#e6f2ea|#e8f0ea",
        re.I,
    )
    offenders = []
    for name, body in _sources().items():
        body = _below_token_sheet(name, body)
        # theme.liquid's settings bridge writes token values from settings —
        # that block is the one legitimate non-base.css definition site.
        if name == "layout/theme.liquid":
            body = re.sub(r"\{% style %\}.*?\{% endstyle %\}", "", body, flags=re.S)
            body = re.sub(r'content="#[0-9a-f]{6}"', "", body)  # theme-color meta
        for match in named.finditer(body):
            offenders.append(f"{name}: {match.group(0)}")
    assert offenders == [], (
        "hardcoded palette hexes — use the token:\n" + "\n".join(offenders)
    )


def test_no_weight_the_face_does_not_load():
    """Archivo ships 400-800; 900 renders as synthetic bold."""
    for name, body in _sources().items():
        assert not re.search(r"font-weight:\s*900\b", body), name


def test_fonts_are_self_hosted_and_preloaded():
    """Google Fonts was a render-blocking third-party sheet loaded AFTER
    base.css. The three latin woff2 files ship in assets/ and preload."""
    layout = (THEME / "layout" / "theme.liquid").read_text(encoding="utf-8")
    assert "fonts.googleapis.com" not in layout
    assert "fonts.gstatic.com" not in layout
    for asset in (
        "archivo-latin-var.woff2",
        "plex-mono-latin-400.woff2",
        "plex-mono-latin-500.woff2",
    ):
        assert (THEME / "assets" / asset).is_file(), asset
        assert asset in layout, asset
    assert layout.count('rel="preload"') >= 2
    assert 'font-weight: 400 800' in layout  # the variable-font range


def test_the_spacing_scale_exists():
    """The old sheet had no spacing tokens at all — 21 distinct gap values
    accumulated. New and refactored rules compose from --sl-space-*."""
    base = (THEME / "assets" / "base.css").read_text(encoding="utf-8")
    for step in range(1, 10):
        assert f"--sl-space-{step}:" in base, f"--sl-space-{step}"
