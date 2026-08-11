"""Premium logged-out journey and shared-shell compatibility contracts."""

import mimetypes
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image

from swinglab.config import Config
from swinglab.web.app import create_app


TEMPLATES = Path(__file__).resolve().parents[1] / "swinglab" / "templates"
LAYOUT = (TEMPLATES / "web_layout.html.j2").read_text(encoding="utf-8")


def landing(tmp_path):
    cfg = Config()
    cfg.web["require_account"] = True
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "sessions"))
    response = client.get("/")
    return client, response


def test_landing_leads_with_one_primary_journey_and_auth_ctas(tmp_path):
    _client, response = landing(tmp_path)
    html = response.text

    assert response.status_code == 200
    assert '<h1 id="landing-title">Practice the move that matters.</h1>' in html
    assert '<a class="button landing-primary" href="/signup">' in html
    assert "Analyze a swing free" in html
    assert '<a class="button hero-secondary" href="/sample-report/">' in html
    assert '<a href="/login">Sign in to continue your coaching loop.</a>' in html


def test_landing_uses_real_sample_asset_with_clear_disclosure(tmp_path):
    client, response = landing(tmp_path)
    html = " ".join(response.text.split())

    assert 'src="/sample-report/media/strip_s1.png"' in html
    assert 'href="/sample-report/"' in html
    assert (
        "Illustrated demonstration report built through the same report engine. "
        "It is not a customer result or testimonial."
    ) in html
    sample = client.get("/sample-report/media/strip_s1.png")
    assert sample.status_code == 200
    assert sample.content[:4] == b"\x89PNG"


def test_landing_uses_storefront_hero_without_decorative_capture_frame(tmp_path):
    _client, response = landing(tmp_path)
    html = " ".join(response.text.split())

    desktop = 'src="/static/caddieinsight-range-hero.webp"'
    mobile = 'srcset="/static/caddieinsight-range-hero-mobile-v2.webp"'
    assert desktop in html
    assert mobile in html
    assert (
        'alt="Golfer completing a driver swing beside a phone on a tripod at '
        'a dusk driving range"'
    ) in html
    assert 'width="1672" height="941" decoding="async" fetchpriority="high"' in html
    assert "landing-hero__capture" not in html
    assert "sl-hero__capture" not in html
    assert "ai-generated" not in html.lower()
    assert "artificial intelligence" not in html.lower()
    assert "synthetic" not in html.lower()
    assert html.index(desktop) < html.index(
        'src="/sample-report/media/strip_s1.png"'
    ) < html.index('id="journey-title"')
    assert (
        "Illustrated demonstration report built through the same report engine. "
        "It is not a customer result or testimonial."
    ) in html


@pytest.mark.parametrize(
    ("asset_path", "expected_size"),
    (
        ("/static/caddieinsight-range-hero.webp", (1672, 941)),
        ("/static/caddieinsight-range-hero-mobile-v2.webp", (1122, 932)),
    ),
)
def test_storefront_hero_assets_are_local_optimized_and_public(
    tmp_path, asset_path, expected_size
):
    client, _response = landing(tmp_path)
    asset = client.get(asset_path)

    assert asset.status_code == 200
    assert asset.headers["content-type"] == "image/webp"
    assert "set-cookie" not in asset.headers
    cache_control = asset.headers.get("cache-control", "").lower()
    assert "private" not in cache_control and "no-store" not in cache_control
    assert asset.content[:4] == b"RIFF" and asset.content[8:12] == b"WEBP"
    assert len(asset.content) <= 150_000
    with Image.open(BytesIO(asset.content)) as image:
        assert image.format == "WEBP"
        assert image.size == expected_size

    assert asset.headers.get("etag")
    assert asset.headers.get("last-modified")
    unchanged = client.get(
        asset_path,
        headers={"If-None-Match": asset.headers["etag"]},
    )
    assert unchanged.status_code == 304


def test_storefront_hero_asset_mime_type_does_not_depend_on_host_database(
    tmp_path, monkeypatch
):
    isolated_db = mimetypes.MimeTypes(filenames=())
    isolated_db.types_map[True].pop(".webp", None)
    isolated_db.types_map[False].pop(".webp", None)
    monkeypatch.setattr(mimetypes, "_db", isolated_db)
    assert mimetypes.guess_type("asset.webp", strict=True)[0] is None

    client, _response = landing(tmp_path)
    asset = client.get("/static/caddieinsight-range-hero.webp")

    assert asset.status_code == 200
    assert asset.headers["content-type"] == "image/webp"


def test_landing_explains_the_full_loop_and_measurement_boundary(tmp_path):
    _client, response = landing(tmp_path)
    html = response.text

    steps = [
        html.index("<h3>Choose the club</h3>"),
        html.index("<h3>Film a repeatable view</h3>"),
        html.index("<h3>Work one plan</h3>"),
        html.index("<h3>Re-film to prove it</h3>"),
    ]
    assert steps == sorted(steps)
    assert "Phone-video timing plus 2D body movement" in html
    assert "supported face-on footage" in html
    assert "Club path, face angle, attack angle" in html
    assert "ball flight" in html


def test_landing_names_the_proof_cycle_with_a_labeled_demo_verdict(tmp_path):
    _client, response = landing(tmp_path)
    html = " ".join(response.text.split())

    assert 'id="proof-cycle-title"' in html
    assert "0.41 &rarr; 0.29 shoulder widths" in html
    assert (
        "Held across 2 matched re-films with the same club, handedness, "
        "and camera angle."
    ) in html
    # Trust rule: the example verdict is labeled at the point it appears.
    assert "Demonstration data" in html
    assert (
        "your driver and your 7-iron get different coaching priorities"
    ) in html
    assert 'href="/drills"' in html
    assert "Beginner Path" in html


def test_apple_touch_icon_is_a_local_opaque_180px_png(tmp_path):
    client, response = landing(tmp_path)

    assert (
        '<link rel="apple-touch-icon" sizes="180x180" '
        'href="/static/apple-touch-icon.png">'
    ) in response.text
    icon = client.get("/static/apple-touch-icon.png")
    assert icon.status_code == 200
    assert icon.content[:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(BytesIO(icon.content)) as image:
        assert image.format == "PNG"
        assert image.size == (180, 180)
        # iOS composites transparent touch icons over black — the mark
        # ships on its own opaque field instead.
        assert image.mode == "RGB"


def test_header_ships_the_brand_lockup_instead_of_plain_type(tmp_path):
    """A visitor crossing from the store must not watch the logo become text.

    Bare defaults are what matter here: the shell fell back to
    ``{{ brand.name | upper }}`` for every install that had not set
    brand.logo_url, which was every install running on code defaults.
    """
    client, response = landing(tmp_path)
    html = response.text

    assert (
        '<img class="sl-header__logo-img" src="/static/caddieinsight-logo.png"'
    ) in html
    assert ">CADDIEINSIGHT<" not in html
    # The v3 mark is still on disk (and still named in config.yaml); nothing
    # rendered may point at it.
    assert "swinglab-logo" not in html

    lockup = client.get("/static/caddieinsight-logo.png")
    assert lockup.status_code == 200
    assert lockup.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_every_page_carries_a_complete_share_card_on_defaults_alone(tmp_path):
    """Sharing a link is the product's most viral action, so the tags may not
    depend on a caller remembering to pass anything."""
    client, response = landing(tmp_path)
    html = " ".join(response.text.split())

    assert (
        '<meta name="description" content="Film one swing on your phone.'
    ) in html
    assert '<meta property="og:site_name" content="CaddieInsight">' in html
    assert '<meta property="og:type" content="website">' in html
    # og:title reuses the page's own <title>, so a shared /pricing link does
    # not preview under the same words as the landing page.
    assert (
        '<meta property="og:title" content="CaddieInsight — Swing '
        'analysis from your phone">'
    ) in html
    assert '<meta property="og:description" content="Film one swing' in html
    assert (
        '<meta property="og:url" content="https://app.caddieinsight.com/">'
    ) in html
    assert (
        '<meta property="og:image" '
        'content="https://app.caddieinsight.com/static/og-caddieinsight.png">'
    ) in html
    assert '<meta name="twitter:card" content="summary_large_image">' in html
    assert (
        '<link rel="canonical" href="https://app.caddieinsight.com/">'
    ) in html

    # An og:image that 404s previews as a grey box just like no tag at all.
    card = client.get("/static/og-caddieinsight.png")
    assert card.status_code == 200
    assert card.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_share_tags_take_per_page_values_without_a_template_change():
    """The defaults are a floor, not a ceiling: a report page can pass its own
    canonical, description and card once app.py has them to pass."""
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html", "j2"]),
    )
    html = " ".join(
        env.get_template("web_offline.html.j2")
        .render(
            brand=Config().brand,
            current_path="/session/abc/report",
            meta_description="One priority, one drill, one pass mark.",
            og_title="Swing report",
            og_type="article",
            canonical_url="https://app.example.test/session/abc/report",
            og_image_url="https://app.example.test/card.png",
        )
        .split()
    )

    assert (
        '<meta name="description" '
        'content="One priority, one drill, one pass mark.">'
    ) in html
    assert '<meta property="og:title" content="Swing report">' in html
    assert '<meta property="og:type" content="article">' in html
    assert (
        '<meta property="og:url" '
        'content="https://app.example.test/session/abc/report">'
    ) in html
    assert (
        '<meta property="og:image" content="https://app.example.test/card.png">'
    ) in html
    assert (
        '<link rel="canonical" '
        'href="https://app.example.test/session/abc/report">'
    ) in html
    assert "app.caddieinsight.com" not in html


def test_absolute_url_tags_are_omitted_when_no_origin_is_configured():
    """A white-label install that has not declared its own origin must get no
    canonical rather than one pointing at CaddieInsight's app."""
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html", "j2"]),
    )
    brand = dict(Config().brand, name="AceCoach", site_url="")
    html = env.get_template("web_offline.html.j2").render(
        brand=brand, current_path="/offline"
    )

    assert "rel=\"canonical\"" not in html
    assert "og:url" not in html
    assert "og:image" not in html
    # The tags that need no origin still ship.
    assert '<meta property="og:site_name" content="AceCoach">' in html
    assert '<meta name="twitter:card" content="summary_large_image">' in html


def test_shared_shell_keeps_navigation_accessibility_contracts():
    assert '<a class="sl-skip" href="#MainContent">' in LAYOUT
    assert '<main id="MainContent">' in LAYOUT
    assert "@media (max-width: 999px)" in LAYOUT  # the 1000px system stop
    assert "@media (prefers-reduced-motion: reduce)" in LAYOUT
    assert "data-header-dropdown" in LAYOUT
    assert "data-sl-menu" in LAYOUT
    assert "data-menu-link" in LAYOUT
    assert LAYOUT.count("data-pro-member-nav") == 2
    # The header hamburger and the tab bar's More button both open the menu,
    # so closing it returns focus to whichever one was used rather than to a
    # single hard-coded trigger.
    assert "if (restoreMenuFocus && lastOpener) lastOpener.focus()" in LAYOUT
    assert "lastOpener = event.currentTarget" in LAYOUT
    assert "if (summary) summary.focus()" in LAYOUT


def test_unlimited_free_configuration_never_advertises_zero_analyses(tmp_path):
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["passwordless_login"] = False
    cfg.billing["free_per_month"] = 0
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "sessions"))

    landing_html = client.get("/").text
    signup_html = client.get("/signup?password=1").text

    assert "unlimited full analyses" in landing_html
    assert "Unlimited full analyses" in signup_html
    assert "0 full analys" not in landing_html
    assert "0 full analys" not in signup_html
