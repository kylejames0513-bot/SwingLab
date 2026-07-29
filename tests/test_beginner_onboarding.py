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
