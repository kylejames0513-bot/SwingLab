"""The public sample report: generated at startup from synthetic session
data through the real report machinery, served with no auth, honest about
being a sample, and advertised from the landing page."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab import sample
from swinglab.config import Config
from swinglab.web.app import create_app


def test_ensure_sample_report_writes_report_and_media(tmp_path):
    path = sample.ensure_sample_report(tmp_path / "sr", Config())
    assert path.is_file()
    media = sorted(p.name for p in (tmp_path / "sr" / "media").iterdir())
    assert media == [
        "overlay_s1.png", "overlay_s2.png", "overlay_s3.png",
        "strip_s1.png", "strip_s2.png", "strip_s3.png",
    ]
    html = path.read_text()
    # The banner says what this is, and where signup lives.
    assert sample.BANNER_TEXT in html
    assert 'href="/"' in html
    # Three swings, tempo + head sway flagged, through the REAL machinery:
    assert html.count("media/strip_s") == 3
    assert "Start here" in html
    assert "Head sway" in html and "Tempo" in html
    # The praise strip has content (most metrics are in range by design).
    assert "What&#39;s working" in html or "What's working" in html
    # No synthetic footage is faked — video sections are simply absent.
    assert "<video" not in html
    assert "Slow motion" not in html


def test_ensure_sample_report_is_idempotent(tmp_path):
    first = sample.ensure_sample_report(tmp_path / "sr", Config())
    marker = "<!-- untouched -->"
    first.write_text(first.read_text() + marker)
    second = sample.ensure_sample_report(tmp_path / "sr", Config())
    assert second == first
    assert marker in second.read_text()  # existing report left alone


def test_sample_report_route_is_public(tmp_path):
    cfg = Config()
    cfg.web["require_account"] = True  # locked-down instance...
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    # ...but the sample needs no login.
    resp = client.get("/sample-report", follow_redirects=True)
    assert resp.status_code == 200
    assert sample.BANNER_TEXT in resp.text
    media = client.get("/sample-report/media/strip_s1.png")
    assert media.status_code == 200
    assert media.content[:4] == b"\x89PNG"


def test_sample_report_route_blocks_traversal(tmp_path):
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s"))
    assert client.get("/sample-report/../swinglab.db").status_code == 404
    assert client.get("/sample-report/nope.html").status_code == 404


def test_landing_page_advertises_sample_and_free_tier(tmp_path):
    cfg = Config()
    cfg.web["require_account"] = True
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    html = client.get("/").text  # logged-out landing
    assert "See a sample report first" in html
    assert "/sample-report/" in html
    assert "no card required" in html

    open_client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s2"))
    upload_html = open_client.get("/").text  # open-mode hero
    assert "See a sample report first" in upload_html


def test_sample_uses_branded_config(tmp_path):
    from tests.test_report import branded_cfg

    path = sample.ensure_sample_report(tmp_path / "sr", branded_cfg())
    html = path.read_text()
    assert "AceCoach" in html and "SwingLab" not in html
    assert "#123456" in html  # branded primary color
