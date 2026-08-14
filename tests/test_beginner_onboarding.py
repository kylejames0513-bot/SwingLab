"""Beginner-facing surfaces: the public /drills library with the four-week
Beginner Path, and the first-session guide panel on the upload page."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.drills import DRILLS
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from tests.test_trends import signup, upload_and_wait
from tests.test_web import fake_analyze_ok


# -- the public drill library -------------------------------------------------

def make_app(tmp_path, monkeypatch, accounts=False):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    cfg = Config()
    cfg.web["require_account"] = accounts
    return create_app(cfg, sessions_dir=tmp_path / "sessions")


def test_drills_page_is_public_and_complete(tmp_path, monkeypatch):
    client = TestClient(make_app(tmp_path, monkeypatch, accounts=True))
    resp = client.get("/drills")  # logged out on purpose
    assert resp.status_code == 200
    html = resp.text
    # Every drill in the library is on the page, with its SVG media.
    for drills in DRILLS.values():
        for drill in drills:
            assert drill.name in html
            assert f"drill-{drill.id}" in html
    assert "<svg" in html
    assert "Pass mark" in html


def test_drills_page_has_the_beginner_path(tmp_path, monkeypatch):
    client = TestClient(make_app(tmp_path, monkeypatch))
    html = client.get("/drills").text
    assert "Beginner Path" in html
    for week in ("Week 1", "Week 2", "Week 3", "Week 4"):
        assert week in html
    # The path's anchors resolve to real family sections.
    for anchor in (
        "family-tempo", "family-balance", "family-sway",
        "family-hip-slide", "family-consistency",
    ):
        assert f'href="#{anchor}"' in html
        assert f'id="{anchor}"' in html


def test_drills_link_is_in_the_nav(tmp_path, monkeypatch):
    client = TestClient(make_app(tmp_path, monkeypatch))
    assert 'href="/drills"' in client.get("/").text


def test_drills_page_derives_its_count_setup_and_free_footer(
    tmp_path, monkeypatch
):
    client = TestClient(make_app(tmp_path, monkeypatch, accounts=True))
    html = client.get("/drills").text  # logged out on purpose

    # The eyebrow counter comes from the registry, never a hard-coded number.
    count = sum(len(drills) for drills in DRILLS.values())
    assert f"{count} drills" in html
    assert "Free to read before you film" in html

    # Family filter chips: All plus one chip per real family key.
    assert 'data-filter-family="all"' in html
    for key in DRILLS:
        assert f'data-filter-family="{key}"' in html

    # The Setup spec cell renders only where an authored presentation exists.
    assert "<dt>Setup</dt>" in html
    assert "Ball teed low with room for three-quarter swings." in html

    # The free-on-every-plan footer.
    assert "Drills are free to read on every plan, including Free." in html

    # No personal priority chrome for an anonymous visitor.
    assert "Matched to your priority" not in html
    assert "Your priority" not in html
    assert "Next matched re-film" not in html


def test_drills_page_flags_the_priority_for_a_logged_in_golfer(
    tmp_path, monkeypatch
):
    from tests.test_shop import make_fake_analyze, metrics_payload

    # tempo=2.0 fires the tempo flag, so the latest report carries a priority.
    monkeypatch.setattr(
        jobs_module,
        "analyze_video",
        make_fake_analyze(metrics_payload(tempo=2.0)),
    )
    cfg = Config()
    cfg.web["require_account"] = True
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "sessions"))
    signup(client)
    upload_and_wait(client)

    html = client.get("/drills").text
    assert "Matched to your priority" in html
    assert ">Your priority<" in html
    # The matched re-film context is the job's own club · hand · angle.
    assert "Next matched re-film" in html
    assert "Iron · Right-handed · Face-on" in html


def test_drills_page_gear_line_is_matched_and_quiet(tmp_path, monkeypatch):
    from swinglab.web import shop
    from tests.test_shop import CATALOG, make_fake_analyze, metrics_payload

    shop.clear_cache()
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "teststore.myshopify.com")
    monkeypatch.setattr(shop, "_fetch", lambda: [dict(p) for p in CATALOG])
    monkeypatch.setattr(
        jobs_module,
        "analyze_video",
        make_fake_analyze(metrics_payload(tempo=2.0)),
    )
    cfg = Config()
    cfg.web["require_account"] = True
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "sessions"))
    try:
        signup(client)
        upload_and_wait(client)

        html = client.get("/drills").text
        # One product, on the priority drill only, phrased as optional.
        assert html.count("Optional. This drill works without it.") == 1
        assert "Tempo Wand" in html
        assert "$79.00" in html

        # Logged out, the same instance shows no product line at all.
        anon = TestClient(create_app(cfg, sessions_dir=tmp_path / "sessions"))
        assert "Tempo Wand" not in anon.get("/drills").text
    finally:
        shop.clear_cache()


# -- the first-session guide --------------------------------------------------

def test_first_run_panel_shows_until_the_first_upload(tmp_path, monkeypatch):
    client = TestClient(make_app(tmp_path, monkeypatch, accounts=True))
    signup(client)
    assert "Your first swing check" in client.get("/").text

    upload_and_wait(client)
    assert "Your first swing check" not in client.get("/").text


def test_first_run_panel_never_shows_logged_out_or_open(tmp_path, monkeypatch):
    # Open instance (no accounts): no first-run panel — there is no
    # "first session" to detect without an owner.
    client = TestClient(make_app(tmp_path, monkeypatch))
    assert "Your first swing check" not in client.get("/").text
