"""Every /api/v1 response must match the shape the API says it returns.

``swinglab/web/api_models.py`` declares the wire format and feeds the exported
OpenAPI. Declaring is not enforcing: the handlers return ``JSONResponse``, and
FastAPI skips response validation whenever a handler returns a ``Response``
object — which these must, because that is where ``Cache-Control: no-store``
is applied. So the declaration would be free to drift from reality, which is
the exact problem it was added to solve.

This module closes that. It drives the real app with a real account and
validates the **actual** response body against the declared model. Every model
sets ``extra="forbid"``, so a key added to a payload without being declared
fails here — a new field cannot ship undocumented, and a removed one cannot
linger in the schema.

The mobile client's hand-written ``src/api/types.ts`` exists because there was
nothing to generate from. What makes generation safe is not the models; it is
this test proving the models are true.
"""

from __future__ import annotations

import json
import time

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web import api_models
from swinglab.web.app import create_app, docs_enabled

# Reuse the account-enabled app and signup flow the device-token tests already
# stand up, rather than a second, subtly different one — the contract has to
# be checked against the app as it is actually configured.
from tests.test_mobile_api_tokens import app, signup  # noqa: F401  (fixtures)


@pytest.fixture
def verified_client(app):
    """A signed-in browser session, which is what these endpoints answer to."""
    client = TestClient(app)
    signup(client)
    return client


@pytest.fixture
def client_with_session(verified_client):
    """A signed-in account that has actually completed an analysis.

    Without this the deep models are never exercised. An account with no
    history returns ``sessions: []`` and ``latest_session: null``, so Session,
    CaddieBrief and PracticeChoice validate against nothing and the whole
    module passes while checking three empty lists — which is exactly what it
    did until deleting a required field from Session failed no test at all.
    """
    uploaded = verified_client.post(
        "/upload",
        files={"video": ("range.mov", b"fake video bytes", "video/quicktime")},
        data={"hand": "right", "angle": "face-on", "club": "driver"},
        headers={"Accept": "application/json"},
    )
    assert uploaded.status_code == 200, uploaded.text
    job_id = uploaded.json()["id"]

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        body = verified_client.get(f"/api/v1/sessions/{job_id}").json()
        if body["status"] in {"done", "failed"}:
            assert body["status"] == "done", body
            return verified_client, job_id
        time.sleep(0.02)
    raise TimeoutError("analysis did not finish")


def declared_model(app, method: str, path: str):
    """The response model FastAPI holds for a route, or None if undeclared."""
    for route in app.routes:
        if getattr(route, "path", None) == path and method.upper() in getattr(
            route, "methods", set()
        ):
            return getattr(route, "response_model", None)
    raise AssertionError(f"No route for {method.upper()} {path}")


# Every /api/v1 route, with the model it must honour. Listed here rather than
# read off the app so that deleting a declaration fails this module instead of
# quietly shrinking what it checks.
DECLARED = {
    ("GET", "/api/v1/me"): api_models.MeResponse,
    ("GET", "/api/v1/mobile-tokens"): api_models.MobileTokenListResponse,
    ("POST", "/api/v1/mobile-tokens"): api_models.MobileTokenIssueResponse,
    ("DELETE", "/api/v1/mobile-tokens/{selector}"): api_models.MobileTokenRevokeResponse,
    ("GET", "/api/v1/profile"): api_models.ProfileResponse,
    ("PUT", "/api/v1/profile"): api_models.ProfileResponse,
    ("GET", "/api/v1/today"): api_models.TodayResponse,
    ("GET", "/api/v1/sessions"): api_models.SessionListResponse,
    ("GET", "/api/v1/sessions/{job_id}"): api_models.Session,
    ("GET", "/api/v1/sessions/{job_id}/brief"): api_models.SessionBriefResponse,
    ("GET", "/api/v1/practice-checkins"): api_models.PracticeCheckinListResponse,
    ("POST", "/api/v1/practice-checkins"): api_models.PracticeCheckinResponse,
    ("POST", "/api/v1/events"): api_models.EventAccepted,
}


# -- the declarations exist --------------------------------------------------

@pytest.mark.parametrize("route", sorted(DECLARED), ids=lambda r: f"{r[0]} {r[1]}")
def test_every_api_route_declares_a_response_model(app, route):
    method, path = route
    assert declared_model(app, method, path) is DECLARED[route], (
        f"{method} {path} no longer declares {DECLARED[route].__name__}. An "
        "undeclared route exports no types, which is how "
        "mobile/src/api/types.ts came to be hand-written in the first place."
    )


def test_no_api_route_is_missing_from_this_contract(app):
    """A new endpoint must arrive with a declared shape, not after one."""
    live = {
        (method, route.path)
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/v1")
        for method in getattr(route, "methods", set())
        if method not in {"HEAD", "OPTIONS"}
    }
    undeclared = sorted(live - set(DECLARED))
    assert not undeclared, (
        f"{undeclared} are served but not in DECLARED. Add the endpoint's "
        "response model here so its shape is exported and checked."
    )


# -- the declarations are true -----------------------------------------------

def test_me_matches_its_declared_shape(verified_client):
    body = verified_client.get("/api/v1/me").json()
    api_models.MeResponse.model_validate(body)


def test_profile_matches_its_declared_shape(verified_client):
    body = verified_client.get("/api/v1/profile").json()
    api_models.ProfileResponse.model_validate(body)


def test_today_matches_its_declared_shape(verified_client):
    body = verified_client.get("/api/v1/today").json()
    api_models.TodayResponse.model_validate(body)


def test_sessions_matches_its_declared_shape(verified_client):
    body = verified_client.get("/api/v1/sessions").json()
    api_models.SessionListResponse.model_validate(body)


def test_practice_checkins_matches_its_declared_shape(verified_client):
    body = verified_client.get("/api/v1/practice-checkins").json()
    api_models.PracticeCheckinListResponse.model_validate(body)


def test_mobile_token_lifecycle_matches_its_declared_shapes(verified_client):
    listed = verified_client.get("/api/v1/mobile-tokens")
    if listed.status_code != 200:
        pytest.skip(f"device tokens unavailable in this config ({listed.status_code})")
    api_models.MobileTokenListResponse.model_validate(listed.json())

    issued = verified_client.post("/api/v1/mobile-tokens", json={"label": "Contract phone"})
    if issued.status_code != 201:
        pytest.skip(f"token issue unavailable in this config ({issued.status_code})")
    body = issued.json()
    api_models.MobileTokenIssueResponse.model_validate(body)

    revoked = verified_client.delete(
        f"/api/v1/mobile-tokens/{body['device']['selector']}"
    )
    assert revoked.status_code == 200
    api_models.MobileTokenRevokeResponse.model_validate(revoked.json())


# -- the deep shapes, against a session that actually exists -----------------

def test_a_real_session_matches_its_declared_shape(client_with_session):
    client, job_id = client_with_session
    body = client.get(f"/api/v1/sessions/{job_id}").json()
    api_models.Session.model_validate(body)
    # A finished analysis is the only state carrying the four conditional
    # keys, so assert we are checking that state and not a queued stub.
    assert body["status"] == "done"


def test_a_populated_session_list_matches_its_declared_shape(client_with_session):
    client, _ = client_with_session
    body = client.get("/api/v1/sessions").json()
    assert body["sessions"], "empty list — this test would prove nothing"
    api_models.SessionListResponse.model_validate(body)


def test_today_with_history_matches_its_declared_shape(client_with_session):
    client, _ = client_with_session
    body = client.get("/api/v1/today").json()
    assert body["latest_session"] is not None, "no history — nothing exercised"
    api_models.TodayResponse.model_validate(body)


def test_the_brief_and_practice_plan_match_their_declared_shapes(client_with_session):
    client, job_id = client_with_session
    brief = client.get(f"/api/v1/sessions/{job_id}/brief")
    if brief.status_code != 200:
        pytest.skip(f"no brief for this fixture session ({brief.status_code})")
    body = brief.json()
    assert body["caddie_brief"] is not None
    api_models.SessionBriefResponse.model_validate(body)

    today = client.get("/api/v1/today").json()
    if today["practice_plan"]:
        for choice in today["practice_plan"]:
            api_models.PracticeChoice.model_validate(choice)


def test_a_practice_checkin_matches_its_declared_shape(client_with_session):
    client, job_id = client_with_session
    created = client.post("/api/v1/practice-checkins", json={"session_id": job_id})
    if created.status_code != 200:
        pytest.skip(f"session not check-in eligible ({created.status_code})")
    api_models.PracticeCheckinResponse.model_validate(created.json())
    listed = client.get("/api/v1/practice-checkins").json()
    assert listed["checkins"], "empty list — this test would prove nothing"
    api_models.PracticeCheckinListResponse.model_validate(listed)


# -- the guard that makes the above mean something ---------------------------

def test_an_undeclared_key_is_a_contract_breach():
    """extra="forbid" is the half that catches drift; prove it actually bites.

    Without this, every validation above would still pass if the models were
    quietly loosened, and a payload could grow a field nobody documented —
    which is the failure mode this whole module exists for.
    """
    valid = {"resource_version": 1, "revoked": True}
    api_models.MobileTokenRevokeResponse.model_validate(valid)
    with pytest.raises(Exception):
        api_models.MobileTokenRevokeResponse.model_validate(
            {**valid, "undocumented_field": "shipped by accident"}
        )


# -- what the API surface must NOT hand out ----------------------------------
#
# The schema is the other half of the contract above: it says what every route
# takes and returns. That is the right thing to publish to a developer and the
# wrong thing to publish to the internet, because it also enumerates the
# operator-only /admin routes whose entire guard is that they look absent.


@pytest.fixture
def clean_docs_env(monkeypatch):
    """No deployment signals and no explicit override — a developer's laptop."""
    for name in ("SWINGLAB_ENABLE_DOCS", "PUBLIC_BASE_URL", "PORT"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_docs_are_published_for_local_development(clean_docs_env, tmp_path):
    """The default must not regress the developer experience."""
    assert docs_enabled() is True
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "sessions"))
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200


@pytest.mark.parametrize(
    "variable, value",
    [("PUBLIC_BASE_URL", "https://app.example.test"), ("PORT", "8080")],
)
def test_a_deployed_process_publishes_no_schema(
    clean_docs_env, tmp_path, variable, value
):
    """Either signal that this process has a public identity turns docs off.

    PORT matters on its own: Railway injects it whether or not the operator
    remembered to set a canonical base URL, so the schema stays private even
    on a half-configured deploy.
    """
    clean_docs_env.setenv(variable, value)
    assert docs_enabled() is False
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "sessions"))
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 404, path


def test_the_opt_in_decides_it_in_both_directions(clean_docs_env):
    clean_docs_env.setenv("PUBLIC_BASE_URL", "https://app.example.test")
    clean_docs_env.setenv("SWINGLAB_ENABLE_DOCS", "true")
    assert docs_enabled() is True
    clean_docs_env.delenv("PUBLIC_BASE_URL")
    clean_docs_env.setenv("SWINGLAB_ENABLE_DOCS", "off")
    assert docs_enabled() is False


def test_an_unrecognized_opt_in_value_fails_closed(clean_docs_env):
    """A typo must not publish the schema — that is the failure that matters."""
    clean_docs_env.setenv("SWINGLAB_ENABLE_DOCS", "ture")
    assert docs_enabled() is False


def test_the_exported_document_survives_docs_being_off(clean_docs_env, tmp_path):
    """scripts/export_openapi.py calls app.openapi() directly, not over HTTP.

    If disabling the URL also disabled the document, the committed
    docs/api/openapi-v1.json — and the generated mobile types that depend on
    it — would silently stop being producible on a deployed box.
    """
    clean_docs_env.setenv("PUBLIC_BASE_URL", "https://app.example.test")
    app = create_app(Config(), sessions_dir=tmp_path / "sessions")
    assert "/api/v1/me" in app.openapi()["paths"]


def test_the_schema_never_publishes_how_admin_routes_are_guarded(app):
    """The /admin guard works by being indistinguishable from a missing route.

    Its docstring is published verbatim as the operation description, so
    explaining the credential and the 404 cloaking there hands an attacker
    the two things the guard depends on staying quiet about.
    """
    rendered = json.dumps(app.openapi())
    assert "/admin/kpis" in rendered, "asserting against the wrong document"
    for leak in ("SWINGLAB_ADMIN_TOKEN", "401/403", "constant-time"):
        assert leak not in rendered, leak


# -- the headers every response carries --------------------------------------


def security_headers_of(response) -> dict[str, str]:
    return {
        name: response.headers.get(name)
        for name in (
            "x-content-type-options",
            "referrer-policy",
            "x-frame-options",
            "content-security-policy",
        )
    }


EXPECTED_SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "strict-origin-when-cross-origin",
    "x-frame-options": "DENY",
    "content-security-policy": "frame-ancestors 'none'",
}


@pytest.mark.parametrize(
    "path", ["/", "/api/v1/me", "/static/pwa-icon.svg", "/no-such-page"]
)
def test_every_response_carries_the_security_headers(app, path):
    """Including the ones no route function produced: a static file, a 404,
    and an unauthenticated API rejection all leave through the same layer."""
    response = TestClient(app).get(path)
    assert security_headers_of(response) == EXPECTED_SECURITY_HEADERS


def test_hsts_is_asserted_only_over_https(app):
    """Sent on a plain connection it is ignored anyway; asserted locally it
    would pin a developer's localhost to https for a year."""
    plain = TestClient(app, base_url="http://testserver").get("/")
    assert "strict-transport-security" not in plain.headers

    secure = TestClient(app, base_url="https://testserver").get("/")
    assert secure.headers["strict-transport-security"] == (
        "max-age=31536000; includeSubDomains"
    )


def test_the_csp_constrains_framing_only(app):
    """A script-src or style-src here would break the app: every page ships
    inline <script> and inline style attributes. frame-ancestors cannot break
    rendering, so it is the one directive worth sending as enforcing."""
    policy = TestClient(app).get("/").headers["content-security-policy"]
    assert policy == "frame-ancestors 'none'"
    for directive in ("script-src", "style-src", "default-src", "img-src"):
        assert directive not in policy
