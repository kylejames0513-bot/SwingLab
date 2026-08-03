"""Static contracts for the Shopify storefront header hierarchy."""

from __future__ import annotations

import json
from pathlib import Path


THEME_ROOT = Path(__file__).resolve().parents[1] / "storefront-theme"
HEADER = (THEME_ROOT / "sections" / "header.liquid").read_text(encoding="utf-8")
LAYOUT = (THEME_ROOT / "layout" / "theme.liquid").read_text(encoding="utf-8")
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
    assert "app_login_url = app_url | append: '/login'" in HEADER
    assert "app_signup_url = app_url | append: '/signup'" in HEADER
    assert "app_session_url = app_url | append: '/auth/storefront/session'" in HEADER
    assert "'layout.navigation.analyze' | t" not in HEADER
    assert HEADER.count("'layout.navigation.analyze_a_swing' | t") == 2
    assert "assign app_action_url = app_signup_url" in HEADER
    assert HEADER.count(" data-app-primary-cta data-app-signed-out-label=") == 2
    assert "app_url | append: '/drills'" in HEADER


def test_storefront_account_menu_hydrates_from_the_app_session_safely():
    assert 'data-app-session-url="{{ app_session_url }}"' in HEADER
    assert HEADER.count("data-app-auth-signed-out>") == 2
    assert HEADER.count("data-app-auth-signed-in hidden") == 2
    assert HEADER.count('action="{{ app_session_url }}" method="post"') == 2
    assert HEADER.count("'layout.navigation.app_sign_in' | t") == 2
    assert HEADER.count("'layout.navigation.create_free_account' | t") == 2
    assert HEADER.count("'layout.navigation.log_out' | t") == 2
    assert "fetch(sessionUrl, {" in HEADER
    assert "credentials: 'include'" in HEADER
    assert "cache: 'no-store'" in HEADER
    assert "node.textContent = authenticated ? welcomeText : '';" in HEADER
    assert "innerHTML" not in HEADER
    assert "data-app-auth-summary" in HEADER
    assert "var nodes = document.querySelectorAll(selector);" in HEADER
    assert "node.querySelector('[data-app-cta-label]')" in HEADER
    assert "eachAppAuthNode('[data-app-pro-member-only]'" in HEADER
    assert "eachAppAuthNode('[data-app-upgrade-section]'" in HEADER
    assert "reapplyAppState: reapplyAppState" in HEADER
    assert (
        'body:has(.sl-header[data-app-authenticated="true"]) .sl-announcement'
        in HEADER
    )


def test_storefront_homepage_prominently_welcomes_signed_in_members():
    assert "request.page_type == 'index' and app_url != blank" in HEADER
    assert 'class="sl-member-rail" data-app-member-rail' in HEADER
    assert 'role="status"' in HEADER
    assert "'layout.navigation.membership_status' | t" in HEADER
    assert HEADER.count('aria-live="polite"') == 1
    assert "data-app-member-tier" in HEADER
    assert "data-app-member-greeting" in HEADER
    assert "data-app-member-action" in HEADER
    assert HEADER.count(" data-app-pro-sales-link") == 4
    assert "node.hidden = isPro;" in HEADER
    assert 'body:has(.sl-header[data-app-authenticated="true"]' in HEADER
    assert "announcement.hidden = authenticated;" not in HEADER
    assert "summary.textContent = authenticated ? welcomeText : accountLabel;" in HEADER
    assert "node.dataset.appPro = isPro ? 'true' : 'false';" in HEADER
    assert "isPro ? proActionLabel : analyzeLabel" in HEADER
    assert ".sl-member-rail__greeting" in HEADER
    assert "min-height: 44px" in HEADER


def test_premium_header_is_scoped_to_home_and_the_pro_product():
    chrome_scope = (
        "if request.page_type == 'index'\n"
        "    assign premium_chrome = true\n"
        "  elsif request.page_type == 'product' and product.handle == 'swinglab-pro'\n"
        "    assign premium_chrome = true"
    )

    assert "assign premium_header = false" in HEADER
    assert "assign overlay_header = false" in HEADER
    assert "if request.page_type == 'index'" in HEADER
    assert "assign overlay_header = true" in HEADER
    assert "elsif request.page_type == 'product' and product.handle == 'swinglab-pro'" in HEADER
    assert (
        'class="sl-header{% if premium_header %} sl-header--premium{% endif %}{% if overlay_header %} sl-header--overlay{% endif %}"'
        in HEADER
    )
    assert "assign premium_chrome = false" in LAYOUT
    assert chrome_scope in LAYOUT
    assert "{% if premium_chrome %} sl-premium-chrome{% endif %}" in LAYOUT

    desktop_nav = HEADER.split('<nav class="sl-header__desktop-nav"', 1)[1].split(
        "</nav>", 1
    )[0]
    premium_nav, standard_nav = desktop_nav.split("{%- else -%}", 1)
    for label in ("method", "sample_report", "plans", "gear"):
        assert f"'layout.navigation.{label}' | t" in premium_nav
    assert "'layout.navigation.pro' | t" not in premium_nav
    assert "'layout.navigation.my_game' | t" in standard_nav
    assert "'layout.navigation.pro' | t" in standard_nav
    assert "assign plans_link_url = plans_url" in HEADER
    assert "assign plans_link_url = product.url" in HEADER
    assert 'href="{{ plans_link_url }}"' in HEADER

    mobile_nav = HEADER.split('<nav class="sl-menu__nav"', 1)[1].split(
        "</nav>", 1
    )[0]
    assert "'layout.navigation.explore' | t" in mobile_nav
    for label in ("method", "sample_report", "plans", "gear"):
        assert f"'layout.navigation.{label}' | t" in mobile_nav
    assert "data-app-primary-cta" in mobile_nav
    assert "{{ app_action_label }}" in mobile_nav
    assert ".sl-premium-chrome .sl-menu" in HEADER


def test_mobile_header_uses_one_cart_link_and_an_accessible_dialog():
    assert HEADER.count('href="{{ routes.cart_url }}"') == 1
    assert '<dialog class="sl-menu"' in HEADER
    assert 'aria-haspopup="dialog"' in HEADER
    assert "data-menu-open" in HEADER and "data-menu-close" in HEADER
    assert "menu.showModal()" in HEADER and "menu.close()" in HEADER
    assert "document.documentElement.classList.add('sl-menu-open')" in HEADER
    assert "window.matchMedia('(min-width: 981px)')" in HEADER


def test_home_header_overlays_the_hero_then_gains_a_scroll_surface():
    assert "assign overlay_header = true" in HEADER
    assert ".shopify-section:has(> .sl-header--overlay) { margin-bottom: -76px; }" in HEADER
    overlay_css = HEADER.split(".sl-header--overlay {", 1)[1].split("}", 1)[0]
    assert "background: transparent" in overlay_css
    assert ".sl-header--overlay.is-scrolled" in HEADER
    assert "window.scrollY > 12" in HEADER
    assert "window.addEventListener('scroll', updateHeaderSurface" in HEADER


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
    assert 'aria-label="{{ cart_aria }}"' in HEADER
    tiny_mobile = HEADER.split("@media (max-width: 480px)", 1)[1].split(
        "@media (max-width: 360px)", 1
    )[0]
    assert ".sl-header__cart-label { display: none; }" in tiny_mobile
    assert "@media (max-width: 360px)" in HEADER
    assert "@media (min-width: 981px) and (max-width: 1099px)" in HEADER


def test_header_labels_describe_destinations():
    navigation = LOCALE["layout"]["navigation"]
    assert navigation["analyze_free"] == "Analyze free"
    assert navigation["analyze_a_swing"] == "Analyze a swing"
    assert navigation["drills"] == "Drills"
    assert navigation["explore"] == "Explore"
    assert navigation["free_plan"] == "Free plan"
    assert navigation["game_plan_ready"] == "Your game plan is ready."
    assert navigation["membership_status"] == "Membership status"
    assert navigation["my_game"] == "My Game"
    assert navigation["account"] == "Account"
    assert navigation["app_sign_in"] == "Sign in"
    assert navigation["create_free_account"] == "Create free account"
    assert navigation["log_out"] == "Log out"
    assert navigation["orders_subscriptions"] == "Orders and subscriptions"
    assert navigation["method"] == "Method"
    assert navigation["sample_report"] == "Sample report"
    assert navigation["plans"] == "Plans"
    assert navigation["pro_member"] == "Pro member"
    assert navigation["signed_in"] == "Signed in to CaddieInsight"
    assert navigation["welcome_back"] == "Welcome back"
    assert navigation["work_on_your_swing"] == "Let's work on your swing"
    assert navigation["close_menu"] == "Close menu"
