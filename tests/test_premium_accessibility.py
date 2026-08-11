"""Contrast and control-boundary contracts for the premium primary journey."""

from __future__ import annotations

import re
from pathlib import Path


TEMPLATES = Path(__file__).resolve().parents[1] / "swinglab" / "templates"
LAYOUT = (TEMPLATES / "web_layout.html.j2").read_text(encoding="utf-8")


def _token(name: str) -> str:
    match = re.search(rf"--{re.escape(name)}:\s*(#[0-9a-fA-F]{{6}})\s*;", LAYOUT)
    assert match is not None, f"missing color token --{name}"
    return match.group(1)


def _relative_luminance(color: str) -> float:
    channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92
        if value <= 0.04045
        else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first: str, second: str) -> float:
    light, dark = sorted(
        (_relative_luminance(first), _relative_luminance(second)),
        reverse=True,
    )
    return (light + 0.05) / (dark + 0.05)


def test_small_text_tokens_meet_aa_on_primary_light_surfaces():
    backgrounds = (_token("sl-bg"), _token("sl-bg-card"))

    for foreground in (_token("sl-orange-text"), _token("sl-ink-muted")):
        assert min(_contrast(foreground, background) for background in backgrounds) >= 4.5


def _declaration(selector: str, prop: str) -> str:
    """The last value of `prop` inside the rule block for `selector`."""
    block = re.search(
        rf"{re.escape(selector)}\s*\{{(.*?)\}}", LAYOUT, re.S
    )
    assert block is not None, f"missing rule for {selector}"
    values = re.findall(rf"{re.escape(prop)}:\s*([^;]+);", block.group(1))
    assert values, f"{selector} declares no {prop}"
    return values[-1].strip()


def _resolve(value: str) -> str:
    """A colour declaration as a hex, following one var() indirection.

    This used to demand a literal hex so it could do the arithmetic, which
    put the contrast gate in direct conflict with the token sheet: the only
    way to satisfy it was to hardcode the colour the sheet exists to name.
    Resolving instead keeps the real guarantee — a computed ratio — without
    requiring a fork of the palette at the call site.
    """
    value = value.strip()
    reference = re.fullmatch(r"var\(\s*--([a-z0-9-]+)\s*\)", value)
    if reference:
        return _token(reference.group(1))
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), f"unresolvable colour: {value}"
    return value


def test_mobile_menu_cta_ink_sits_on_the_background_it_was_written_for():
    """This rule once set only the ink, leaving the base green background
    underneath: #06110c on #0f3d28 is 1.57:1, effectively unreadable. The
    bottom tab bar's More button routes into this menu, so it is a primary
    surface, not a corner.

    Both declarations still have to be PRESENT — that is the actual defect
    this guards, and it is unchanged by resolving tokens.
    """
    selector = ".sl-premium-chrome .sl-menu .sl-menu__cta"
    ink = _resolve(_declaration(selector, "color"))
    background = _resolve(_declaration(selector, "background"))

    assert _contrast(ink, background) >= 4.5, f"{ink} on {background}"


def test_control_border_token_has_three_to_one_non_text_contrast():
    border = _token("sl-control-border")

    assert _contrast(border, _token("sl-bg")) >= 3.0
    assert _contrast(border, _token("sl-bg-card")) >= 3.0
    # Against the paper panels, which are the only light surface left. This
    # used to check #ffffff — a colour the product no longer paints anywhere,
    # so the assertion was passing on a hypothetical.
    assert _contrast(border, _token("sl-paper")) >= 3.0


def test_primary_journey_uses_accessible_text_and_control_tokens():
    login = (TEMPLATES / "web_login.html.j2").read_text(encoding="utf-8")
    upload = (TEMPLATES / "web_upload.html.j2").read_text(encoding="utf-8")
    today = (TEMPLATES / "web_today.html.j2").read_text(encoding="utf-8")

    assert ".sl-eyebrow" in LAYOUT and "color: var(--sl-orange-text);" in LAYOUT
    assert "border: 1.5px solid var(--sl-control-border);" in login
    assert "color: var(--sl-orange-text);" in upload
    assert upload.count("var(--sl-control-border)") >= 2
    assert "color: var(--sl-orange-text);" in today
    assert "border: 1px solid var(--sl-control-border);" in today
