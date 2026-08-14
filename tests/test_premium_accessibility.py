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


def test_menu_cta_ink_sits_on_the_background_it_was_written_for():
    """This rule once set only the ink, leaving the base green background
    underneath: #06110c on #0f3d28 is 1.57:1, effectively unreadable. The
    bottom tab bar's More button routes into this CTA, so it is a primary
    surface, not a corner.

    Both declarations still have to be PRESENT — that is the actual defect
    this guards, and it is unchanged by resolving tokens.

    It used to be checked at `.sl-premium-chrome .sl-menu .sl-menu__cta`,
    the dark-chrome override. Industry has one paper header and that whole
    parallel treatment is gone, so the check moved to `.sl-header__cta` —
    the rule that actually carries the pair now, and the one the mobile menu
    shares (the element takes both classes). Deleting the test instead would
    have retired a live guarantee along with a dead selector.
    """
    selector = ".sl-header__cta"
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


def _rgb_token(name: str) -> str:
    """A `--x-rgb: r, g, b` triple, as a hex."""
    match = re.search(
        rf"--{re.escape(name)}:\s*(\d{{1,3}})\s*,\s*(\d{{1,3}})\s*,\s*(\d{{1,3}})\s*;",
        LAYOUT,
    )
    assert match is not None, f"missing colour token --{name}"
    return "#%02x%02x%02x" % tuple(int(part) for part in match.groups())


def _flatten(top: str, alpha: float, behind: str) -> str:
    over = [int(top[index:index + 2], 16) for index in (1, 3, 5)]
    under = [int(behind[index:index + 2], 16) for index in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(
        round(one * alpha + other * (1 - alpha))
        for one, other in zip(over, under)
    )


def _resolve_over(value: str, behind: str) -> str:
    """A colour declaration as a hex, composited onto what it is painted on.

    `rgba(var(--x-rgb), a)` is how the sheet spells a wash, and it is the
    shape that hides this class of defect: the alpha makes a wrong base
    colour look deliberate right up until it is flattened onto the ground.
    """
    wash = re.fullmatch(
        r"rgba\(\s*var\(\s*--([a-z0-9-]+)\s*\)\s*,\s*([0-9.]+)\s*\)",
        value.strip(),
    )
    if wash:
        return _flatten(_rgb_token(wash.group(1)), float(wash.group(2)), behind)
    return _resolve(value)


# Jinja comments are stripped before any brace matching: `#}` ends with the
# same character a rule block does, so one comment inside a rule truncates it
# and the reader reports a property that is plainly declared as missing.
LAYOUT_CSS = re.sub(r"\{#.*?#\}", "", LAYOUT, flags=re.S)


def _rule_blocks(selector: str) -> list[str]:
    """Every rule block for `selector`, not just the first.

    `.sl-tabbar` is declared twice — `display: none` at the top level and the
    real bar inside its media query — so a first-match reader finds the rule
    that carries no ground at all.
    """
    return re.findall(rf"{re.escape(selector)}\s*\{{(.*?)\}}", LAYOUT_CSS, re.S)


def _last_declaration(selector: str, prop: str) -> str:
    """The winning value of `prop` across every block for `selector`.

    The property is anchored: unanchored, `color` also matches the tail of
    `-webkit-tap-highlight-color`, and the tab bar sets that one to
    `transparent` — so the reader answered with a value no text is ever
    painted in.
    """
    values: list[str] = []
    for block in _rule_blocks(selector):
        values += re.findall(
            rf"(?:^|[;{{\s]){re.escape(prop)}:\s*([^;]+);", block, re.M
        )
    assert values, f"{selector} declares no {prop}"
    return values[-1].strip()


# (the selector carrying the text, the selector painting the ground under it)
# Hover states are in here on purpose: a hover that drops out of contrast is
# the same defect wearing a state, and it is invisible to any sweep of the
# page at rest. The footer's link hover was --sl-focus-dark, which is the
# trace — 9.76 on the field it was written for, 1.78 on the paper it moved to.
APP_CHROME_ON_ITS_OWN_GROUND = (
    (".sl-app-footer__logo", ".sl-app-footer"),
    (".sl-app-footer__logo:hover", ".sl-app-footer"),
    (".sl-app-footer__nav strong", ".sl-app-footer"),
    (".sl-app-footer__nav a", ".sl-app-footer"),
    (".sl-app-footer__nav a:hover", ".sl-app-footer"),
    (".sl-tabbar__item", ".sl-tabbar"),
    (".sl-tabbar__item:hover", ".sl-tabbar"),
    (".sl-tabbar__item.is-current", ".sl-tabbar"),
)


def test_app_chrome_text_is_legible_on_the_ground_it_is_painted_on():
    """Chrome text, checked against its OWN ground rather than a token pair.

    Every one of these rules was correct under the pre-inversion palette,
    where `--sl-cream` was bone on a dark bar. The 2026-08 flip made
    `--sl-cream` #f2f2f3 and `--sl-night` a *light* recess without moving the
    call sites, so the app footer wordmark and both nav headings painted
    #f2f2f3 on #f2f2f3 — 1.00:1 — and all four tab-bar items landed at
    1.05:1 on the bar. The tab bar is the PWA's primary navigation and its
    icons are `stroke="currentColor"`, so the icons went with the labels.

    Nothing pinned as a token was wrong, which is why 70 green design gates
    said the shell was fine. This gate resolves the ground instead: it is the
    same `--sl-wash-rgb` vs `--sl-cream-rgb` trap that left 104 hairlines
    invisible, and it stays wrong-by-construction rather than wrong-by-value.
    """
    page = _token("sl-bg")

    for text_selector, ground_selector in APP_CHROME_ON_ITS_OWN_GROUND:
        ground = _resolve_over(
            _last_declaration(ground_selector, "background"), page
        )
        ink = _resolve_over(_last_declaration(text_selector, "color"), ground)

        assert _contrast(ink, ground) >= 4.5, (
            f"{text_selector}: {ink} on {ground} (from {ground_selector})"
        )


def test_primary_journey_uses_accessible_text_and_control_tokens():
    login = (TEMPLATES / "web_login.html.j2").read_text(encoding="utf-8")
    upload = (TEMPLATES / "web_upload.html.j2").read_text(encoding="utf-8")
    today = (TEMPLATES / "web_today.html.j2").read_text(encoding="utf-8")

    # The eyebrow used to be pinned as amber. It is chrome, not a measured
    # value, so it is --sl-ink-soft now and the gate checks the tokens that
    # small text actually depends on instead.
    assert ".sl-eyebrow" in LAYOUT
    assert "color: var(--sl-ink-soft);" in LAYOUT
    assert "border: 1.5px solid var(--sl-control-border);" in login
    assert "color: var(--sl-orange-text);" in upload
    assert upload.count("var(--sl-control-border)") >= 2
    assert "color: var(--sl-orange-text);" in today
    assert "border: 1px solid var(--sl-control-border);" in today
