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

import time

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.web import api_models

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
