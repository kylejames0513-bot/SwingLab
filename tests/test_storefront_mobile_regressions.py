"""Regression contracts for the authenticated mobile storefront."""

from __future__ import annotations

import json
import re
from pathlib import Path


THEME = Path(__file__).resolve().parents[1] / "storefront-theme"
HEADER = (THEME / "sections" / "header.liquid").read_text(encoding="utf-8")
HERO = (THEME / "sections" / "hero.liquid").read_text(encoding="utf-8")
HOW = (THEME / "sections" / "how-it-works.liquid").read_text(encoding="utf-8")
MAIN_PAGE = (THEME / "sections" / "main-page.liquid").read_text(encoding="utf-8")
REPORT = (THEME / "sections" / "report-feature.liquid").read_text(encoding="utf-8")
FOOTER = (THEME / "sections" / "footer.liquid").read_text(encoding="utf-8")
ANNOUNCEMENT = (THEME / "sections" / "announcement-bar.liquid").read_text(
    encoding="utf-8"
)
BASE = (THEME / "assets" / "base.css").read_text(encoding="utf-8")
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


def phone_css(source: str, widths: set[int] | None = None) -> str:
    """Return max-width rules that participate in the phone layout."""

    allowed = widths or {480, 560, 649, 700, 749, 767}
    return "\n".join(
        body for width, body in max_width_media_blocks(source) if width in allowed
    )


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
    assert any("max-width:" in rule for rule in member_greeting_rules)
    assert "@media (min-width: 561px) and (max-width: 1100px)" in HEADER

    member_phone_css = "\n".join(body for width, body in media if width == 560)
    assert "padding: 10px var(--sl-pad-x)" in member_phone_css
    assert "calc(-64px - 72px)" in member_phone_css
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
    assert "align-content: end" in mobile_css
    assert "justify-items: stretch" in mobile_css
    assert "padding-inline: 18px" not in mobile_css
    assert "--sl-mobile-hero-height:" in mobile_css
    assert "svh" in mobile_css or "dvh" in mobile_css
    assert mobile_css.count("min-height: var(--sl-mobile-hero-height)") >= 2

    mobile_image_rules = declarations(mobile_css, ".sl-hero__image")
    assert mobile_image_rules
    assert all(
        "object-position: 58% center" not in rule for rule in mobile_image_rules
    )

    copy_rules = declarations(mobile_css, ".sl-hero__copy")
    assert copy_rules
    assert any("text-align: left" in rule for rule in copy_rules)
    body_rules = declarations(mobile_css, ".sl-hero__body")
    assert any("text-align: left" in rule for rule in body_rules)
    title_rules = declarations(HERO, ".sl-hero__title")
    assert any("text-align: left" in rule for rule in title_rules)
    action_rules = declarations(mobile_css, ".sl-hero__actions")
    proof_rules = declarations(mobile_css, ".sl-hero__proof")
    assert any("margin: 20px 0 0" in rule for rule in action_rules)
    assert any("margin: 18px 0 0" in rule for rule in proof_rules)


def test_mobile_method_section_is_compact_centered_and_semantic():
    assert '<ol class="sl-how__grid">' in HOW
    assert '<li class="sl-step"' in HOW
    assert "@media (min-width: 768px)" in HOW
    assert "@media (min-width: 640px)" not in HOW

    intro_rules = declarations(HOW, ".sl-how__intro")
    intro_copy_rules = declarations(HOW, ".sl-how__intro > p")
    step_rules = declarations(HOW, ".sl-step")
    caption_rules = declarations(HOW, ".sl-step__caption")
    assert any("grid-template-columns: minmax(0, 1fr)" in rule for rule in intro_rules)
    assert any("margin-inline: auto" in rule for rule in intro_rules)
    assert any("text-align: center" in rule for rule in intro_copy_rules)
    assert any("align-items: stretch" in rule for rule in step_rules)
    assert any("text-align: left" in rule for rule in step_rules)
    assert any("margin: auto 0 0" in rule for rule in caption_rules)

    mobile_css = phone_css(HOW, {749, 767})
    mobile_step_rules = declarations(mobile_css, ".sl-step")
    mobile_body_rules = declarations(mobile_css, ".sl-step__body")
    mobile_title_rules = declarations(mobile_css, ".sl-step__title")
    mobile_caption_rules = declarations(mobile_css, ".sl-step__caption")
    mobile_foot_rules = declarations(mobile_css, ".sl-how__foot-note")
    assert mobile_step_rules
    assert any("align-items: center" in rule for rule in mobile_step_rules)
    assert any("text-align: center" in rule for rule in mobile_step_rules)
    assert any("padding:" in rule for rule in mobile_step_rules)
    assert any("margin-inline: auto" in rule for rule in mobile_body_rules)
    assert any("text-align: center" in rule for rule in mobile_body_rules)
    assert any("text-align: center" in rule for rule in mobile_title_rules)
    assert any("text-align: center" in rule for rule in mobile_caption_rules)
    assert any("text-align: center" in rule for rule in mobile_foot_rules)


def test_method_page_removes_double_top_spacing_and_centers_phone_actions():
    assert "page.handle == 'the-swinglab-method'" in MAIN_PAGE
    assert "sl-page--method" in MAIN_PAGE
    assert "sl-method-page" in MAIN_PAGE

    method_page_rules = declarations(MAIN_PAGE, ".sl-page--method")
    method_content_rules = declarations(
        MAIN_PAGE, ".sl-page--method .sl-method-page"
    )
    method_hero_rules = declarations(MAIN_PAGE, ".sl-method-page .sl-page-hero")
    assert method_page_rules
    assert any("padding-top: 0" in rule for rule in method_page_rules)
    assert any("margin-top: 0" in rule for rule in method_content_rules)
    assert any("text-align: center" in rule for rule in method_hero_rules)

    mobile_css = phone_css(MAIN_PAGE, {749})
    chip_rules = declarations(mobile_css, ".sl-method-page .sl-chip")
    action_group_rules = declarations(MAIN_PAGE, ".sl-method-page > div:has(> .sl-btn)")
    action_rules = declarations(
        mobile_css, ".sl-method-page > div:has(> .sl-btn) .sl-btn"
    )
    assert any("white-space: normal" in rule for rule in chip_rules)
    assert any("max-width: 100%" in rule for rule in chip_rules)
    assert any("justify-content: center" in rule for rule in action_group_rules)
    assert any("width: min(100%, 320px)" in rule for rule in action_rules)


def test_mobile_report_and_footer_center_primary_interactions():
    report_mobile = phone_css(REPORT, {749})
    report_copy_rules = declarations(report_mobile, ".sl-report__copy")
    report_body_rules = declarations(report_mobile, ".sl-report__body")
    report_cta_rules = declarations(report_mobile, ".sl-report__cta")
    assert any("text-align: center" in rule for rule in report_copy_rules)
    assert any("text-align: center" in rule for rule in report_body_rules)
    assert any("margin:" in rule and "auto" in rule for rule in report_body_rules)
    assert any("width:" in rule and "100%" in rule for rule in report_cta_rules)
    assert any("margin: 20px auto 0" in rule for rule in report_cta_rules)
    assert any(
        "text-align: center" in rule
        for rule in declarations(report_mobile, ".sl-report__note")
    )
    caption_group = report_mobile.split(
        ".sl-report__caption,\n  .sl-report__disclosure {", 1
    )[1].split("}", 1)[0]
    assert "margin-inline: auto" in caption_group
    assert "text-align: center" in caption_group

    footer_mobile = phone_css(FOOTER, {749})
    assert any(
        "text-align: center" in rule
        for rule in declarations(footer_mobile, ".sl-footer__grid")
    )
    assert any(
        "align-items: center" in rule
        for rule in declarations(footer_mobile, ".sl-footer__col")
    )
    assert any(
        "justify-content: center" in rule
        for rule in declarations(footer_mobile, ".sl-footer__form")
    )
    assert any(
        "justify-content: center" in rule
        for rule in declarations(footer_mobile, ".sl-footer__policies-list")
    )
    assert any(
        "text-align: center" in rule
        for rule in declarations(footer_mobile, ".sl-footer__fine")
    )


def test_mobile_chrome_and_compact_buttons_have_44px_touch_targets():
    base_mobile = phone_css(BASE, {749})
    small_button_rules = declarations(base_mobile, ".sl-btn--sm")
    assert any("min-height: 44px" in rule for rule in small_button_rules)

    announcement_mobile = phone_css(ANNOUNCEMENT, {649})
    announcement_rules = declarations(announcement_mobile, ".sl-announcement")
    assert any("min-height: 44px" in rule for rule in announcement_rules)

    member_mobile = phone_css(HEADER, {560})
    member_action_rules = declarations(member_mobile, ".sl-member-rail__action")
    assert any("min-height: 44px" in rule for rule in member_action_rules)

    header_mobile = phone_css(HEADER, {480})
    cart_rules = declarations(header_mobile, ".sl-header__cart")
    toggle_rules = declarations(header_mobile, ".sl-header__toggle")
    assert any("min-height: 44px" in rule for rule in cart_rules)
    assert any(
        "width: 44px" in rule and "height: 44px" in rule
        for rule in toggle_rules
    )
