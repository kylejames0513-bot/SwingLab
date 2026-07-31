"""Static contracts for the Shopify storefront header hierarchy."""

from __future__ import annotations

import json
from pathlib import Path


THEME_ROOT = Path(__file__).resolve().parents[1] / "storefront-theme"
HEADER = (THEME_ROOT / "sections" / "header.liquid").read_text(encoding="utf-8")
APP_LAYOUT = (
    Path(__file__).resolve().parents[1]
    / "swinglab"
    / "templates"
    / "web_layout.html.j2"
).read_text(encoding="utf-8")
LOCALE = json.loads(
    (THEME_ROOT / "locales" / "en.default.json").read_text(encoding="utf-8")
)


def test_storefront_header_has_one_state_aware_app_action():
    assert "app_login_url" not in HEADER
    assert "app_signup_url" not in HEADER
    assert "'layout.navigation.analyze' | t" not in HEADER
    assert HEADER.count("'layout.navigation.analyze_a_swing' | t") == 2
    assert "app_url | append: '/drills'" in HEADER


def test_mobile_header_uses_one_cart_link_and_an_accessible_dialog():
    assert HEADER.count('href="{{ routes.cart_url }}"') == 1
    assert '<dialog class="sl-menu"' in HEADER
    assert 'aria-haspopup="dialog"' in HEADER
    assert "data-menu-open" in HEADER and "data-menu-close" in HEADER
    assert "menu.showModal()" in HEADER and "menu.close()" in HEADER
    assert "document.documentElement.classList.add('sl-menu-open')" in HEADER
    assert "window.matchMedia('(min-width: 981px)')" in HEADER


def test_storefront_and_app_share_the_responsive_header_contract():
    for source in (HEADER, APP_LAYOUT):
        assert "max-width: 1280px" in source
        assert "@media (max-width: 980px)" in source
        assert "width: min(88vw, 360px)" in source
        assert "data-header-dropdown" in source
        assert "data-sl-menu" in source
        assert "data-menu-link" in source
        assert '<nav class="sl-menu__nav"' in source
        assert "closeOnBreakpointChange" in source
        assert "addEventListener('focusout'" in source


def test_storefront_header_reinitializes_cleanly_in_the_theme_editor():
    assert "window.CaddieInsightHeader" in HEADER
    assert "shopify:section:load" in HEADER
    assert "shopify:section:unload" in HEADER
    assert "new AbortController()" in HEADER
    assert "slHeaderCleanup" in HEADER


def test_storefront_keeps_mobile_actions_readable():
    assert 'class="sl-header__cta sl-header__app"' in HEADER
    assert 'class="sl-header__cta sl-menu__cta"' in HEADER
    assert ".sl-header__cart-label { display: none; }" not in HEADER


def test_header_labels_describe_destinations():
    navigation = LOCALE["layout"]["navigation"]
    assert navigation["analyze_a_swing"] == "Analyze a swing"
    assert navigation["drills"] == "Drills"
    assert navigation["my_game"] == "My Game"
    assert navigation["account"] == "Account"
    assert navigation["orders_subscriptions"] == "Orders & subscriptions"
    assert navigation["close_menu"] == "Close menu"
