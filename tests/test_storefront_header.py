"""Static contracts for the Shopify storefront header hierarchy."""

from __future__ import annotations

import json
import re
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


def test_storefront_header_prefers_theme_packaged_logo_asset():
    assert "section.settings.logo != blank" in HEADER
    # The name moved to caddieinsight-logo.png: Files still holds a v3
    # swinglab-logo.png, so the old name could never be trusted through an
    # images[] lookup (see tests/test_theme_brand_filenames.py).
    assert "images['swinglab-logo.png']" not in HEADER
    assert "caddieinsight-logo.png' | asset_url" in HEADER
    assert 'class="sl-header__logo-img"' in HEADER
    theme_logo = THEME_ROOT / "assets" / "caddieinsight-logo.png"
    assert theme_logo.is_file()


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
    assert "var summaryText = accountLabel || signedInLabel;" in HEADER
    assert "summary.textContent = summaryText;" in HEADER
    assert "summary.textContent = authenticated ? welcomeText : accountLabel;" not in HEADER
    assert "node.dataset.appPro = isPro ? 'true' : 'false';" in HEADER
    assert "authenticated ? analyzeLabel : signedOutLabel" in HEADER
    assert "isPro ? proActionLabel : analyzeLabel" not in HEADER
    assert ".sl-member-rail__greeting" in HEADER
    assert "min-height: 44px" in HEADER
    assert ".sl-header__account > summary" in HEADER
    assert "text-overflow: ellipsis" in HEADER
    assert (
        '.sl-header[data-app-authenticated="true"] .sl-header__search { display: none; }'
        in HEADER
    )
    assert (
        '.sl-header[data-app-authenticated="true"] .sl-header__account > summary'
        in HEADER
    )
    assert "[data-app-auth-summary]" in HEADER
    assert "flex: 1 1 auto" in HEADER.split("[data-app-auth-summary]", 1)[1]


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
    # The drawer closes exactly where the CSS retires the toggle — the 1000px
    # system stop. The old sheet's 981px JS/CSS pair drifted apart once.
    assert "window.matchMedia('(min-width: 1000px)')" in HEADER


def test_home_header_overlays_the_hero_then_gains_a_scroll_surface():
    assert "assign overlay_header = true" in HEADER
    assert ".shopify-section:has(> .sl-header--overlay) { margin-bottom: -76px; }" in HEADER
    assert (
        '.shopify-section:has(> .sl-header--overlay[data-app-authenticated="true"])'
        in HEADER
    )
    assert "margin-bottom: calc(-76px - 52px);" in HEADER
    # Below the 1000px stop the bar is 64px tall; the pull-up (and its
    # signed-in variant, +52px of member rail) matches inside that media
    # block so the hero sits flush at every width.
    below_desktop = HEADER.split("@media (max-width: 999px)", 1)[1].split("@media", 1)[0]
    assert ".shopify-section:has(> .sl-header--overlay) { margin-bottom: -64px; }" in below_desktop
    assert "margin-bottom: calc(-64px - 52px);" in below_desktop
    assert (
        'body:has(.sl-header[data-app-authenticated="true"]) .sl-hero__inner'
        in HEADER
    )
    overlay_css = HEADER.split(".sl-header--overlay {", 1)[1].split("}", 1)[0]
    assert "background: transparent" in overlay_css
    assert ".sl-header--overlay.is-scrolled" in HEADER
    assert "window.scrollY > 12" in HEADER
    assert "window.addEventListener('scroll', updateHeaderSurface" in HEADER


def test_storefront_and_app_share_the_responsive_header_contract():
    # The mechanics are shared: both surfaces run the same drawer/dropdown
    # script contract. The breakpoint values diverge on purpose until the
    # app shell restyle lands: the storefront sits on the four-stop system
    # (560/750/1000/1280) while the app still carries 980/1280 — pinned
    # per-surface below so the divergence stays visible, never silent.
    for source in (HEADER, APP_LAYOUT):
        assert "data-header-dropdown" in source
        assert "data-sl-menu" in source
        assert "data-menu-link" in source
        assert '<nav class="sl-menu__nav"' in source
        assert "closeOnBreakpointChange" in source
        assert "addEventListener('focusout'" in source
    assert "max-width: var(--sl-maxw)" in HEADER  # 1280px, by token
    assert "@media (min-width: 1000px)" in HEADER
    assert "max-width: 1280px" in APP_LAYOUT
    assert "@media (max-width: 980px)" in APP_LAYOUT
    assert "width: min(88vw, 360px)" in APP_LAYOUT


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
    # The primary CTA is present at EVERY width. The old sheet display:none'd
    # it below 981px — the one action the storefront exists to produce,
    # hidden from every phone and tablet. Compact on the smallest phones;
    # never removed.
    stylesheet = HEADER.split("{% stylesheet %}", 1)[1].split("{% endstylesheet %}", 1)[0]
    for block in re.findall(r"[^{}]+\{[^{}]*\}", stylesheet):
        selector, _, declarations = block.partition("{")
        if ".sl-header__cta" in selector or ".sl-header__app" in selector:
            assert "display: none" not in declarations, block.strip()
    compact = HEADER.split("@media (max-width: 559px)", 1)[1].split("@media", 1)[0]
    assert ".sl-header__cta { padding: 0 var(--sl-space-3); font-size: var(--sl-text-xs); }" in compact
    # The cart label is visually hidden but still announced — clip, not
    # display:none.
    assert ".sl-header__cart-label" in compact
    assert "clip: rect(0 0 0 0)" in compact
    assert ".sl-menu { width: 100vw; }" in compact
    # 44px touch targets are the base rule, not a mobile patch.
    assert "min-height: 44px" in HEADER.split(".sl-header__cart {", 1)[1].split("}", 1)[0]
    toggle_rule = HEADER.split(".sl-header__toggle {", 1)[1].split("}", 1)[0]
    assert "width: 44px" in toggle_rule and "height: 44px" in toggle_rule
    # The burger retires exactly at the desktop stop — a 1024px desktop gets
    # real navigation, not a hamburger.
    desktop = HEADER.split("@media (min-width: 1000px)", 1)[1].split("@media", 1)[0]
    assert ".sl-header__toggle { display: none; }" in desktop
    assert ".sl-header__desktop-nav { display: flex; }" in desktop


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
