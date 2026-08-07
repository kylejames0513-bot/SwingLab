"""Selector-aware authentication for native unsafe-route boundaries."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import Request
from fastapi.testclient import TestClient

from swinglab.api.auth import require_mobile_bearer
from swinglab.api.errors import install_mobile_error_handlers
from swinglab.config import Config
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from tests.test_mobile_api_tokens import bearer, issue_token, signup
from tests.test_web import fake_analyze_ok


@pytest.fixture
def unsafe_native_route(tmp_path, monkeypatch):
    """A real write boundary used by later native-mutation tests."""
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    cfg = Config()
    cfg.web["require_account"] = True
    app = create_app(cfg, sessions_dir=tmp_path / "sessions")
    writes: list[str] = []

    @app.post("/_test/native-write", name="mobile.test_native_write")
    def native_write(request: Request):
        context = require_mobile_bearer(
            request,
            app.state.users,
            app.state.cfg.web["require_account"],
        )
        writes.append(context.user.id)
        return {"user_id": context.user.id, "selector": context.selector}

    install_mobile_error_handlers(app, {"mobile.test_native_write"})
    return app, writes


def test_native_unsafe_write_requires_bearer_without_cookie_fallback(unsafe_native_route):
    """Catches a cookie or invalid bearer authorizing a native mutation."""
    app, writes = unsafe_native_route
    owner_browser = TestClient(app)
    signup(owner_browser)
    token = issue_token(owner_browser, "Native phone")["token"]
    owner = app.state.users.get_by_email("golfer@example.com")
    assert owner is not None

    for origin in (None, "http://testserver", "https://attacker.example"):
        origin_headers = {} if origin is None else {"Origin": origin}
        cookie_only = owner_browser.post(
            "/_test/native-write", headers=origin_headers
        )
        assert cookie_only.status_code == 401
        assert cookie_only.json()["code"] == "bearer_required"
        assert cookie_only.headers["www-authenticate"] == "Bearer"
        assert writes == []

        invalid_with_cookie = owner_browser.post(
            "/_test/native-write",
            headers={
                **bearer("ciat_this-is-not-a-valid-token"),
                **origin_headers,
            },
        )
        assert invalid_with_cookie.status_code == 401
        assert invalid_with_cookie.json()["code"] == "http_401"
        assert invalid_with_cookie.json()["message"] == "Invalid mobile access token."
        assert writes == []

    other_browser = TestClient(app)
    signup(other_browser, "other@example.com")
    valid_without_origin = other_browser.post(
        "/_test/native-write", headers=bearer(token)
    )
    assert valid_without_origin.status_code == 200
    assert valid_without_origin.json()["user_id"] == owner.id
    assert valid_without_origin.json()["selector"] == token.removeprefix("ciat_").split(".")[0]
    assert writes == [owner.id]

    hostile_origin = other_browser.post(
        "/_test/native-write",
        headers={**bearer(token), "Origin": "https://attacker.example"},
    )
    assert hostile_origin.status_code == 200
    assert hostile_origin.json()["user_id"] == owner.id
    assert writes == [owner.id, owner.id]
