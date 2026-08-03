"""Regression contracts for the authenticated mobile storefront."""

from __future__ import annotations

import json
import re
from pathlib import Path


THEME = Path(__file__).resolve().parents[1] / "storefront-theme"
HEADER = (THEME / "sections" / "header.liquid").read_text(encoding="utf-8")
HERO = (THEME / "sections" / "hero.liquid").read_text(encoding="utf-8")
LOCALE = json.loads(
    (THEME / "locales" / "en.default.json").read_text(encoding="utf-8")
)


def max_width_media_blocks(source: str) -> list[tuple[int, str]]:
    blocks: list[tuple[int, str]] = []
    pattern = re.compile(r"@media\s*\(max-width:\s*(\d+)px\)\s*\{")
    for match in pattern.finditer(source):
        depth = 1
        cursor = match.end()
        while cursor < len(source) and depth:
            if source[cursor] == "{":
                depth += 1
            elif source[cursor] == "}":
                depth -= 1
            cursor += 1
        assert depth == 0, f"Unclosed media query starting at {match.start()}"
        blocks.append((int(match.group(1)), source[match.end() : cursor - 1]))
    return blocks


def declarations(source: str, selector: str) -> list[str]:
    return re.findall(rf"{re.escape(selector)}\s*\{{([^{{}}]*)\}}", source)


def test_pro_cta_apostrophe_is_not_double_escaped():
    label = LOCALE["layout"]["navigation"]["work_on_your_swing"]
    assert label == "Let's work on your swing"
    assert "&amp;#39;" not in label

    match = re.search(
        r'data-app-pro-action-label="\{\{(?P<expression>[^}]*)\}\}"', HEADER
    )
    assert match is not None
    filters = [
        part.strip().split(":", 1)[0]
        for part in match["expression"].split("|")[1:]
    ]
    assert filters[-2:] == ["t", "escape_once"]
    assert "escape" not in filters

    member_match = re.search(
        r'data-app-member-action-label="\{\{(?P<expression>[^}]*)\}\}"', HEADER
    )
    assert member_match is not None
    assert member_match["expression"] != match["expression"]
    assert "layout.navigation.open_game_plan" in member_match["expression"]


def test_translation_derived_app_data_labels_are_escaped_once():
    attributes = re.findall(
        r'(data-app-[\w-]*label)="\{\{(?P<expression>[^}]*)\}\}"', HEADER
    )
    assert attributes

    translation_derived = [
        (name, expression)
        for name, expression in attributes
        if "| t" in expression or "app_action_label" in expression
    ]
    assert len(translation_derived) >= 10

    unsafe = []
    for name, expression in translation_derived:
        filters = [
            part.strip().split(":", 1)[0]
            for part in expression.split("|")[1:]
        ]
        if filters.count("escape_once") != 1 or "escape" in filters:
            unsafe.append((name, expression.strip()))
    assert unsafe == []


def test_pro_member_rail_and_primary_cta_use_distinct_actions():
    navigation = LOCALE["layout"]["navigation"]
    assert navigation["open_game_plan"] == "Open game plan"
    assert navigation["work_on_your_swing"] == "Let's work on your swing"
    assert navigation["open_game_plan"] != navigation["work_on_your_swing"]

    assert "data-app-member-action-label=" in HEADER
    assert "data-app-pro-action-label=" in HEADER
    assert "var memberActionLabel = header.getAttribute" in HEADER
    assert "isPro ? memberActionLabel : analyzeLabel" in HEADER
    assert "isPro ? proActionLabel : analyzeLabel" in HEADER


def test_authenticated_header_centers_member_content_across_modern_iphone_widths():
    media = max_width_media_blocks(HEADER)
    modern_phone_css = "\n".join(
        body for width, body in media if width == 480
    )
    assert modern_phone_css
    assert ".sl-header__logo-img" in modern_phone_css
    assert ".sl-header__cart" in modern_phone_css
    assert "--sl-pad-x: 20px" in modern_phone_css
    assert "--sl-pad-x: 16px" not in modern_phone_css

    member_inner_rules = declarations(HEADER, ".sl-member-rail__inner")
    member_greeting_rules = declarations(HEADER, ".sl-member-rail__greeting")
    assert member_inner_rules
    assert member_greeting_rules
    assert any("flex-wrap: wrap" in rule for rule in member_inner_rules)
    assert any("justify-content: center" in rule for rule in member_inner_rules)
    assert any("text-align: center" in rule for rule in member_inner_rules)
    assert any("order: 4" in rule for rule in member_greeting_rules)
    assert any("flex: 1 0 100%" in rule for rule in member_greeting_rules)
    assert "text-overflow: ellipsis" in "\n".join(member_greeting_rules)
    assert "white-space: nowrap" in "\n".join(member_greeting_rules)

    member_phone_css = "\n".join(body for width, body in media if width == 560)
    assert "padding: 10px var(--sl-pad-x)" in member_phone_css
    assert "calc(100% + 28px)" not in member_phone_css
    assert "margin-inline: -14px" not in member_phone_css


def test_mobile_hero_is_fluid_through_modern_iphone_widths():
    media = max_width_media_blocks(HERO)
    mobile_css = "\n".join(body for width, body in media if 440 <= width <= 749)
    compact_phone_css = "\n".join(
        body for width, body in media if 440 <= width <= 560
    )

    assert mobile_css
    assert compact_phone_css
    assert ".sl-hero__title" in compact_phone_css
    assert "min-height: 720px" not in mobile_css
    assert "align-content: center" in mobile_css
    assert "justify-items: center" in mobile_css
    assert "padding-inline: 18px" not in mobile_css
    assert "--sl-mobile-hero-height:" in mobile_css
    assert "svh" in mobile_css or "dvh" in mobile_css
    assert mobile_css.count("min-height: var(--sl-mobile-hero-height)") >= 2

    mobile_image_rules = declarations(mobile_css, ".sl-hero__image")
    assert mobile_image_rules
    assert all(
        "object-position: 58% center" not in rule for rule in mobile_image_rules
    )

    copy_rules = declarations(HERO, ".sl-hero__copy")
    assert copy_rules
    assert any("align-items: center" in rule for rule in copy_rules)
    assert any("text-align: center" in rule for rule in copy_rules)
    assert "margin: 20px auto 0" in mobile_css
    assert "margin: 18px auto 0" in mobile_css
