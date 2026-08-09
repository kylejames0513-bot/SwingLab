"""What a crawler is told about app.caddieinsight.com.

Neither file existed before. The consequence was not that private pages were
indexed — every one of them is behind a cookie and answers a redirect — it
was that a crawler following a shared /session/<job_id> link spent this
origin's crawl budget rediscovering the login redirect, while the four pages
that are actually public had nothing pointing at them.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web.app import create_app


SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(Config(), sessions_dir=tmp_path / "sessions"))


def _directives(body: str, keyword: str) -> list[str]:
    prefix = f"{keyword.lower()}:"
    return [
        line.split(":", 1)[1].strip()
        for line in body.splitlines()
        if line.lower().startswith(prefix)
    ]


def test_robots_is_public_plain_text_and_sets_no_cookie(client):
    response = client.get("/robots.txt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    # A Set-Cookie here would make the file uncacheable at every CDN hop and
    # hand a session to a crawler.
    assert "set-cookie" not in response.headers


def test_robots_opens_the_public_pages_and_closes_the_private_ones(client):
    body = client.get("/robots.txt").text

    assert body.startswith("User-agent: *")
    assert set(_directives(body, "Allow")) == {
        "/",
        "/pricing",
        "/drills",
        "/sample-report/",
    }

    disallowed = _directives(body, "Disallow")
    # The shareable one. A report link pasted into a group chat is the most
    # crawled private URL this app has.
    assert "/session/" in disallowed
    for private in ("/account", "/today", "/progress", "/upload", "/api/"):
        assert private in disallowed


def test_robots_does_not_advertise_the_operator_routes(client):
    """/admin answers 404 when the token is unset — that IS the guard.

    Naming it in a world-readable file would be the single place on the
    internet that says the routes exist, which is the opposite of what
    require_admin's 404 cloaking is for.
    """
    assert "/admin" not in client.get("/robots.txt").text


def test_robots_points_at_the_sitemap_on_the_same_origin(client):
    body = client.get("/robots.txt").text

    sitemaps = _directives(body, "Sitemap")
    assert len(sitemaps) == 1
    assert sitemaps[0].endswith("/sitemap.xml")
    assert sitemaps[0].startswith("http")


def test_sitemap_is_valid_xml_listing_exactly_the_public_pages(client):
    response = client.get("/sitemap.xml")

    assert response.status_code == 200
    assert "xml" in response.headers["content-type"]

    root = ET.fromstring(response.text)
    assert root.tag == f"{SITEMAP_NS}urlset"

    locations = [node.text or "" for node in root.iter(f"{SITEMAP_NS}loc")]
    assert all(location.startswith("http") for location in locations)
    assert {"/" + location.split("/", 3)[-1] for location in locations} == {
        "/",
        "/pricing",
        "/drills",
        "/sample-report/",
    }

    # Every advertised page must actually answer — a sitemap of redirects is
    # worse than no sitemap.
    for path in ("/pricing", "/drills"):
        assert client.get(path, follow_redirects=False).status_code == 200


def test_sitemap_and_robots_agree_on_which_pages_are_public(client):
    allowed = set(_directives(client.get("/robots.txt").text, "Allow"))
    root = ET.fromstring(client.get("/sitemap.xml").text)
    listed = {
        "/" + (node.text or "").split("/", 3)[-1]
        for node in root.iter(f"{SITEMAP_NS}loc")
    }

    assert listed == allowed
