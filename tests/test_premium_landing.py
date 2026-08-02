"""Premium logged-out journey and shared-shell compatibility contracts."""

import mimetypes
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from swinglab.config import Config
from swinglab.web.app import create_app


LAYOUT = (
    Path(__file__).resolve().parents[1]
    / "swinglab"
    / "templates"
    / "web_layout.html.j2"
).read_text(encoding="utf-8")


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
    assert (
        '<h1 id="landing-title">One swing priority. One practice plan. '
        "Proof when you re-film.</h1>"
    ) in html
    assert '<a class="button landing-primary" href="/signup">' in html
    assert ">Create a free account</a>" in html
    assert '<a class="button secondary" href="/login">Sign in</a>' in html


def test_landing_uses_real_sample_asset_with_clear_disclosure(tmp_path):
    client, response = landing(tmp_path)
    html = " ".join(response.text.split())

    assert 'src="/sample-report/media/strip_s1.png"' in html
    assert 'href="/sample-report/"' in html
    assert (
        "Illustrated sample generated from synthetic measurements through "
        "the same report engine. It is not a customer result or testimonial."
    ) in html
    sample = client.get("/sample-report/media/strip_s1.png")
    assert sample.status_code == 200
    assert sample.content[:4] == b"\x89PNG"


def test_landing_discloses_atmosphere_after_real_product_proof(tmp_path):
    _client, response = landing(tmp_path)
    html = " ".join(response.text.split())

    atmosphere = 'src="/static/homepage-range-atmosphere-v1.webp"'
    assert atmosphere in html
    assert (
        'alt="AI-generated scene of an anonymous golfer filmed face-on by a '
        'phone on a hip-height tripod at sunrise"'
    ) in html
    assert 'width="1600" height="900" loading="lazy" decoding="async"' in html
    assert "AI-generated atmosphere" in html
    assert (
        "AI-generated range scene for atmosphere only — not a customer, "
        "testimonial, or analyzed swing."
    ) in html
    assert "The product proof remains the real-engine sample report above." in html
    assert "aspect-ratio: 16 / 9;" in response.text
    assert ".atmosphere-media { aspect-ratio: 4 / 3; }" in response.text
    assert html.index('src="/sample-report/media/strip_s1.png"') < html.index(
        atmosphere
    ) < html.index('id="journey-title"')
    assert (
        "Illustrated sample generated from synthetic measurements through "
        "the same report engine. It is not a customer result or testimonial."
    ) in html


def test_atmosphere_asset_is_local_optimized_and_public(tmp_path):
    client, _response = landing(tmp_path)
    asset = client.get("/static/homepage-range-atmosphere-v1.webp")

    assert asset.status_code == 200
    assert asset.headers["content-type"] == "image/webp"
    assert "set-cookie" not in asset.headers
    cache_control = asset.headers.get("cache-control", "").lower()
    assert "private" not in cache_control and "no-store" not in cache_control
    assert asset.content[:4] == b"RIFF" and asset.content[8:12] == b"WEBP"
    assert len(asset.content) <= 300_000
    with Image.open(BytesIO(asset.content)) as image:
        assert image.format == "WEBP"
        assert image.size == (1600, 900)

    assert asset.headers.get("etag")
    assert asset.headers.get("last-modified")
    unchanged = client.get(
        "/static/homepage-range-atmosphere-v1.webp",
        headers={"If-None-Match": asset.headers["etag"]},
    )
    assert unchanged.status_code == 304


def test_atmosphere_asset_mime_type_does_not_depend_on_host_database(
    tmp_path, monkeypatch
):
    isolated_db = mimetypes.MimeTypes(filenames=())
    isolated_db.types_map[True].pop(".webp", None)
    isolated_db.types_map[False].pop(".webp", None)
    monkeypatch.setattr(mimetypes, "_db", isolated_db)
    assert mimetypes.guess_type("asset.webp", strict=True)[0] is None

    client, _response = landing(tmp_path)
    asset = client.get("/static/homepage-range-atmosphere-v1.webp")

    assert asset.status_code == 200
    assert asset.headers["content-type"] == "image/webp"


def test_landing_explains_the_full_loop_and_measurement_boundary(tmp_path):
    _client, response = landing(tmp_path)
    html = response.text

    steps = [
        html.index("<h3>Film</h3>"),
        html.index("<h3>Coach</h3>"),
        html.index("<h3>Practice</h3>"),
        html.index("<h3>Re-film</h3>"),
    ]
    assert steps == sorted(steps)
    assert "Phone-video timing plus 2D body movement" in html
    assert "supported face-on footage" in html
    assert "Club path, face angle, attack angle" in html
    assert "ball flight" in html


def test_shared_shell_keeps_navigation_accessibility_contracts():
    assert '<a class="sl-skip" href="#MainContent">' in LAYOUT
    assert '<main id="MainContent">' in LAYOUT
    assert "@media (max-width: 980px)" in LAYOUT
    assert "@media (prefers-reduced-motion: reduce)" in LAYOUT
    assert "data-header-dropdown" in LAYOUT
    assert "data-sl-menu" in LAYOUT
    assert "data-menu-link" in LAYOUT
    assert LAYOUT.count("data-pro-member-nav") == 2
    assert "if (restoreMenuFocus) openButton.focus()" in LAYOUT
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
