"""Static contracts for the Shopify storefront header hierarchy."""

from __future__ import annotations

import json
from pathlib import Path


THEME_ROOT = Path(__file__).resolve().parents[1] / "storefront-theme"
HEADER = (THEME_ROOT / "sections" / "header.liquid").read_text(encoding="utf-8")
LOCALE = json.loads(
    (THEME_ROOT / "locales" / "en.default.json").read_text(encoding="utf-8")
)


def test_storefront_header_has_one_state_aware_app_action():
    assert "app_login_url" not in HEADER
    assert "app_signup_url" not in HEADER
    assert "'layout.navigation.analyze' | t" not in HEADER
    assert HEADER.count("'layout.navigation.analyze_a_swing' | t") == 2
    assert "app_url | append: '/drills'" in HEADER


def test_mobile_header_does_not_repeat_cart():
    assert (
        'href="{{ routes.cart_url }}" class="sl-header__link sl-header__mobile-only"'
        not in HEADER
    )
    assert "sl-header__mobile-cta" in HEADER


def test_header_labels_describe_destinations():
    navigation = LOCALE["layout"]["navigation"]
    assert navigation["analyze_a_swing"] == "Analyze a swing"
    assert navigation["drills"] == "Drills"
