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

    # ...and the storefront's own value is the display-copy counterpart, not a
    # drifted duplicate. Both clear AA on both surface backgrounds (5.37:1 and
    # 4.94:1 on --sl-bg); the app's is darker because it sets small interface
    # text where the storefront sets prose. Pinning both ends stops a future
    # "unify the tokens" pass from quietly trading contrast for symmetry.
    assert _token(STOREFRONT, "sl-ink-muted") == "#626a63"


def test_shared_type_scale_is_spelled_identically_on_both_surfaces():
    """The rungs both surfaces define must match as text, not just as numbers.

    They were already numerically equal while spelled differently (`.15vw`
    against `0.15vw`). That is the kind of difference a comparison gets
    "fixed" to ignore, and a normalising comparison eventually normalises away
    a real divergence too — so the sources are spelled the same instead.
    """
    for rung in ("xs", "sm", "base", "lg", "xl"):
        name = f"sl-text-{rung}"
        assert _token(LAYOUT, name) == _token(STOREFRONT, name), name

    # The full eight-rung scale ships on both surfaces now. The app used to
    # stop at xl (the larger rungs were "dead custom properties"), but every
    # missing rung was an invitation for the next display heading to be a
    # hand-rolled clamp() instead of a decision — the exact rot the theme
    # rebuild measured. Three inert declarations are cheaper than one fork.
    for rung in ("sl-text-2xl", "sl-text-3xl", "sl-text-4xl"):
        assert _token(LAYOUT, rung) == _token(STOREFRONT, rung), rung


def test_tokens_that_differ_only_in_name_still_carry_the_same_value():
    """Two spellings, one value — asserted so the pair cannot drift apart.

    Renaming either side would touch several hundred call sites across
    base.css, the sections and the app templates for no customer benefit, so
    both vocabularies stay. What must not happen is the numbers diverging
    while the names hide it.
    """
    assert _token(LAYOUT, "sl-content") == _token(STOREFRONT, "sl-maxw")
    assert _token(LAYOUT, "sl-radius-md") == _token(STOREFRONT, "sl-radius")
    assert _token(LAYOUT, "sl-radius-control") == _token(
        STOREFRONT, "sl-radius-control"
    )


def test_no_surface_asks_for_a_weight_the_brand_face_does_not_load():
    """Archivo ships 400-800. Asking for 900 gets a synthetic bold.

    Eight declarations across both surfaces asked for 900 — the header
    lockups, the footer wordmark, the shop and comparison headings, the 404
    and the report brand. A weight that is not loaded is either clamped or
    faux-bolded by the browser, and faux-bold on a wordmark is the difference
    between a designed mark and a smeared one.
    """
    # Both surfaces self-host the same variable face (one file, 400-800).
    theme_layout = (ROOT / "storefront-theme" / "layout" / "theme.liquid").read_text(
        encoding="utf-8"
    )
    for source in (theme_layout, LAYOUT):
        assert "font-weight: 400 800" in source
        assert "archivo-latin-var.woff2" in source
        assert "fonts.googleapis.com" not in source
        assert "fonts.gstatic.com" not in source

    styled = list((ROOT / "storefront-theme" / "sections").glob("*.liquid"))
    styled += [ROOT / "storefront-theme" / "assets" / "base.css"]
    styled += sorted(TEMPLATES.glob("*.html.j2"))
    for path in styled:
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"font-weight:\s*900\b", source), path.name


def test_shared_photography_ships_the_same_bytes_to_both_surfaces():
    """One photograph, one encode — whichever surface the visitor reaches.

    The dusk-range hero and the default share card are the two images that
    appear on both the storefront and the app. The hero pair had drifted to a
    lighter off-recipe encode on the app side (54,298 B against the theme's
    97,918 B for the same 1672x941 frame), so the surface people pay to use
    was serving the worse copy of the same photograph. The recipe of record is
    store-assets/plan_card_webp.py: quality=82, method=6, LANCZOS.
    """
    pairs = (
        ("caddieinsight-range-hero-desktop.webp", "caddieinsight-range-hero.webp"),
        ("caddieinsight-range-hero-mobile.webp", "caddieinsight-range-hero-mobile.webp"),
        ("og-caddieinsight.png", "og-caddieinsight.png"),
    )
    for theme_name, app_name in pairs:
        theme_bytes = (ROOT / "storefront-theme" / "assets" / theme_name).read_bytes()
        app_bytes = (ROOT / "swinglab" / "web" / "static" / app_name).read_bytes()
        assert theme_bytes == app_bytes, theme_name
        # test_premium_landing.py holds the app to a 150 KB ceiling per asset;
        # assert it here too so the theme side can never push past it.
        assert len(theme_bytes) <= 150_000, theme_name


def test_app_and_storefront_share_tour_caddie_type_stack():
    theme = (ROOT / "storefront-theme" / "layout" / "theme.liquid").read_text(
        encoding="utf-8"
    )

    # Archivo is the wordmark's own typeface, so headings and the shipped
    # lockup are cut from one shape. The guided report already lists it
    # first and depends on the shell having loaded it.
    assert 'font-family: "Archivo";' in theme  # self-hosted @font-face
    assert 'font-family: "IBM Plex Mono";' in theme
    assert 'font-family: "Archivo";' in LAYOUT  # the app ships the same files
    assert 'font-family: "IBM Plex Mono";' in LAYOUT
    assert '"Archivo"' in _token(STOREFRONT, "sl-font-sans")
    assert '"Archivo"' in _token(STOREFRONT, "sl-font-display")
    assert '"IBM Plex Mono"' in _token(STOREFRONT, "sl-font-mono")
    assert '"Archivo"' in _token(LAYOUT, "sl-font-sans")
    assert '"Archivo"' in _token(LAYOUT, "sl-font-display")
    assert '"IBM Plex Mono"' in _token(LAYOUT, "sl-font-mono")
    assert "Sora" not in _token(LAYOUT, "sl-font-display")
    assert "--sl-font-display" in STOREFRONT
    assert ".sl-section-head" in STOREFRONT


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
