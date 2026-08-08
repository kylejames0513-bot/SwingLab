"""Contracts that keep the CaddieInsight app and storefront visually coherent."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web.app import create_app


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "swinglab" / "templates"
LAYOUT = (TEMPLATES / "web_layout.html.j2").read_text(encoding="utf-8")
STOREFRONT = (ROOT / "storefront-theme" / "assets" / "base.css").read_text(
    encoding="utf-8"
)


def _token(source: str, name: str) -> str:
    match = re.search(rf"--{re.escape(name)}:\s*([^;]+);", source)
    assert match is not None, f"missing --{name}"
    return " ".join(match.group(1).split())


def test_shared_brand_tokens_match_the_storefront_source_of_truth():
    shared = (
        "sl-bg",
        "sl-bg-card",
        "sl-ink",
        "sl-ink-soft",
        "sl-green",
        "sl-green-btn",
        "sl-green-ink",
        "sl-orange",
        "sl-orange-soft",
        "sl-border",
        "sl-pad-x",
        "sl-radius-sm",
        "sl-radius-lg",
        "sl-radius-xl",
    )

    for name in shared:
        assert _token(LAYOUT, name) == _token(STOREFRONT, name)

    # These app values deliberately stay darker than the storefront's display
    # colors so small text and control edges retain AA contrast.
    assert _token(LAYOUT, "sl-ink-muted") == "#5a655e"
    assert _token(LAYOUT, "sl-orange-text") == "#8f4509"
    assert _token(LAYOUT, "sl-control-border") == "#6f7b72"


def test_app_shell_uses_homepage_premium_chrome_and_footer():
    assert '<body class="sl-premium-chrome' in LAYOUT
    assert '<header class="sl-header sl-header--premium"' in LAYOUT
    assert 'class="sl-app-banner' in LAYOUT
    assert 'class="sl-app-footer"' in LAYOUT
    assert 'class="sl-app-footer__inner"' in LAYOUT
    assert ".sl-premium-chrome .sl-menu .sl-menu__panel" in LAYOUT
    assert "background: rgba(6, 17, 12, .96);" in LAYOUT
    assert "background: #f07a18;" in LAYOUT
    assert ".sl-header--premium .sl-header__inner { min-height: 64px;" in LAYOUT
    assert "@media (max-width: 560px)" in LAYOUT
    assert ".sl-app-banner__detail { display: none; }" in LAYOUT


def test_free_and_pro_navigation_remain_dynamic(tmp_path, monkeypatch):
    for name in (
        "RESEND_API_KEY",
        "SWINGLAB_SMTP_URL",
        "SWINGLAB_MAIL_FROM",
        "SHOPIFY_STORE_DOMAIN",
        "SHOPIFY_WEBHOOK_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["passwordless_login"] = False
    app = create_app(cfg, sessions_dir=tmp_path / "sessions")
    user = app.state.users.create("parity@example.com", "longenough")
    app.state.users.upsert_golfer_profile(
        user.id,
        display_name="Kyle",
        experience_mode="improve",
        handicap_range="10_to_14",
        primary_goal="consistency",
        practice_minutes=20,
        sessions_per_week=2,
        handedness="right",
        camera_angle="face-on",
        preferred_club="driver",
    )
    client = TestClient(app)
    assert client.post(
        "/login",
        data={"email": user.email, "password": "longenough"},
        follow_redirects=False,
    ).status_code == 303

    free_shell = client.get("/today").text.split("</dialog>", 1)[0]
    assert 'href="/pricing"' in free_shell
    assert "data-pro-member-nav" not in free_shell
    assert free_shell.count('action="/logout" method="post"') == 2
    assert "Create free account" not in free_shell

    signed_out = TestClient(app).get("/").text.split("</dialog>", 1)[0]
    assert signed_out.count('class="sl-header__cta') == 2
    assert signed_out.count('href="/signup"') >= 3
    assert "Analyze free" in signed_out

    app.state.users.set_plan(user.id, "pro", "active")
    pro_shell = client.get("/today").text.split("</dialog>", 1)[0]
    assert 'href="/pricing"' not in pro_shell
    assert pro_shell.count("data-pro-member-nav") == 2
    assert pro_shell.count("Welcome back, Kyle") == 1
    assert 'data-pro-member-nav' in pro_shell
    assert ">Kyle</span>" in pro_shell or ">Game plan</span>" in pro_shell
    assert "Let&rsquo;s work on your swing" in pro_shell


def test_equal_height_rules_are_scoped_to_peer_cards():
    account = (TEMPLATES / "web_account.html.j2").read_text(encoding="utf-8")
    today = (TEMPLATES / "web_today.html.j2").read_text(encoding="utf-8")
    shop = (TEMPLATES / "web_shop.html.j2").read_text(encoding="utf-8")
    landing = (TEMPLATES / "web_login.html.j2").read_text(encoding="utf-8")

    assert ".account-grid" in account and "align-items: stretch;" in account
    assert ".account-card" in account and "height: 100%;" in account
    assert ".practice-option" in today and "height: 100%;" in today
    assert "aspect-ratio: 20 / 13;" in shop
    assert ".product__body" in shop and "flex: 1;" in shop
    assert ".flow-list li" in landing and "height: 100%;" in landing


def test_public_app_templates_do_not_reference_automated_image_generation():
    rendered_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(TEMPLATES.glob("web_*.html.j2"))
    ).lower()

    for phrase in ("ai-generated", "artificial intelligence", "synthetic"):
        assert phrase not in rendered_source


def test_manifest_uses_the_same_premium_page_chrome(tmp_path):
    cfg = Config()
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "sessions"))

    manifest = client.get("/app.webmanifest").json()
    assert manifest["background_color"] == "#eef2ef"
    assert manifest["theme_color"] == "#06110c"
