"""The unhandled-exception surface — the one error state that wasn't designed.

Every other failure in this app is humanized: the status page translates
pipeline errors, the paywall explains itself, the cloaked admin routes 404 by
design. An unhandled route exception fell through to FastAPI's bare
plain-text ``Internal Server Error`` — no brand, no navigation, no way
forward. These tests pin the replacement, and pin the property that makes it
safe: the handler is self-contained, so it works even when the machinery
that just failed is the render pipeline itself.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web.app import create_app


@pytest.fixture()
def exploding_app(tmp_path):
    app = create_app(Config(), sessions_dir=tmp_path / "sessions")

    # A route that fails the way real routes fail: after routing succeeded,
    # inside the handler, unexpectedly.
    @app.get("/boom")
    def boom():
        raise RuntimeError("wired to fail: secret-internal-detail")

    @app.get("/api/v1/boom")
    def api_boom():
        raise RuntimeError("wired to fail: secret-internal-detail")

    return app


@pytest.fixture()
def client(exploding_app):
    # raise_server_exceptions=False makes the TestClient behave like a real
    # browser: the exception reaches the handler instead of the test.
    return TestClient(exploding_app, raise_server_exceptions=False)


def test_an_unhandled_error_renders_a_branded_page(client):
    response = client.get("/boom")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "CaddieInsight" in body
    assert "Something went wrong on our side." in body
    # A way forward, not a dead end.
    assert 'href="/"' in body


def test_the_error_page_discloses_nothing_internal(client):
    body = client.get("/boom").text

    assert "secret-internal-detail" not in body
    assert "RuntimeError" not in body
    assert "Traceback" not in body
    assert "boom" not in body


def test_api_routes_get_json_not_html(client):
    response = client.get("/api/v1/boom")

    assert response.status_code == 500
    payload = response.json()
    assert "error" in payload
    assert "secret-internal-detail" not in str(payload)


def test_the_failure_is_logged_with_its_traceback(client, caplog):
    with caplog.at_level(logging.ERROR, logger="swinglab.web.app"):
        client.get("/boom")

    records = [r for r in caplog.records if "Unhandled error" in r.message]
    assert records and records[0].exc_info  # full detail for the log/Sentry


def test_the_page_is_self_contained(client):
    """No stylesheet links, no scripts, no template machinery.

    The handler runs when something is broken; a 500 page that depends on
    the render pipeline, the DB, or a static asset request may not survive
    the same failure. Inline everything.
    """
    body = client.get("/boom").text

    assert "<link" not in body
    assert "<script" not in body
    assert "{{" not in body and "{%" not in body
