"""Scoped structured errors for native-only route names."""

from __future__ import annotations

import re

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from swinglab.api.auth import MobileAuthError
from swinglab.api.errors import MobileAPIHTTPError, install_mobile_error_handlers


class _RequiredPayload(BaseModel):
    value: str


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/mobile/status/{status}", name="mobile.synthetic_status")
    def mobile_status(status: int):
        headers = {"WWW-Authenticate": "Bearer"}
        if status == 429:
            headers["Retry-After"] = "60"
        raise HTTPException(
            status, f"mobile status {status}", headers=headers
        )

    @app.post("/mobile/validation", name="mobile.synthetic_validation")
    def mobile_validation(payload: _RequiredPayload):
        return payload

    @app.get("/mobile/failure", name="mobile.synthetic_failure")
    def mobile_failure():
        raise RuntimeError("secret implementation detail")

    @app.get("/mobile/structured-failure", name="mobile.structured_failure")
    def mobile_structured_failure():
        raise MobileAPIHTTPError(
            503,
            "auth_unavailable",
            "caller supplied secret detail",
            headers={"Cache-Control": "no-store", "Retry-After": "7"},
        )

    @app.get("/legacy", name="legacy")
    def legacy():
        raise HTTPException(404, "legacy detail")

    install_mobile_error_handlers(
        app,
        {
            "mobile.synthetic_status",
            "mobile.synthetic_validation",
            "mobile.synthetic_failure",
            "mobile.structured_failure",
        },
    )
    return app


def test_named_mobile_routes_use_the_exact_api_error_shape_and_keep_headers():
    """Catches a native error escaping as FastAPI's legacy detail payload."""
    client = TestClient(_app(), raise_server_exceptions=False)
    for status in (401, 403, 404, 409, 429):
        response = client.get(f"/mobile/status/{status}")
        assert response.status_code == status
        assert response.json() == {
            "resource_version": 1,
            "code": f"http_{status}",
            "message": f"mobile status {status}",
            "retryable": status == 429,
            "reference_id": None,
        }
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"
        if status == 429:
            assert response.headers["retry-after"] == "60"


def test_named_mobile_validation_and_failures_do_not_leak_request_or_exception_details():
    """Catches schema/input or internal details leaking into native responses."""
    client = TestClient(_app(), raise_server_exceptions=False)
    validation = client.post("/mobile/validation", json={"unexpected": "input"})
    assert validation.status_code == 422
    assert validation.json() == {
        "resource_version": 1,
        "code": "validation_error",
        "message": "Invalid request.",
        "retryable": False,
        "reference_id": None,
    }
    assert validation.headers["cache-control"] == "no-store"
    assert validation.headers["pragma"] == "no-cache"

    failure = client.get("/mobile/failure")
    assert failure.status_code == 500
    assert failure.json()["resource_version"] == 1
    assert failure.json()["code"] == "internal_error"
    assert failure.json()["message"] == "Internal server error."
    assert failure.json()["retryable"] is True
    assert re.fullmatch(r"[0-9a-f]{32}", failure.json()["reference_id"])
    assert "secret implementation detail" not in failure.text
    assert failure.headers["cache-control"] == "no-store"
    assert failure.headers["pragma"] == "no-cache"


def test_named_structured_5xx_is_sanitized_with_fresh_reference_and_safe_headers():
    """Catches MobileAPIHTTPError bypassing the named-route 5xx boundary."""

    client = TestClient(_app(), raise_server_exceptions=False)
    responses = [client.get("/mobile/structured-failure") for _ in range(2)]
    reference_ids = []
    for response in responses:
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"
        assert response.headers["retry-after"] == "7"
        assert response.json()["resource_version"] == 1
        assert response.json()["code"] == "internal_error"
        assert response.json()["message"] == "Internal server error."
        assert response.json()["retryable"] is True
        reference_id = response.json()["reference_id"]
        assert re.fullmatch(r"[0-9a-f]{32}", reference_id)
        assert "caller supplied secret detail" not in response.text
        reference_ids.append(reference_id)
    assert reference_ids[0] != reference_ids[1]


def test_legacy_routes_keep_fastapis_detail_error_contract():
    """Catches structured native errors spilling into compatibility routes."""
    response = TestClient(_app(), raise_server_exceptions=False).get("/legacy")
    assert response.status_code == 404
    assert response.json() == {"detail": "legacy detail"}
    assert "cache-control" not in response.headers
    assert "pragma" not in response.headers


def test_mobile_auth_error_keeps_its_bounded_code_on_named_routes_only():
    """Catches bearer-required errors degrading to generic HTTP 401 responses."""
    app = FastAPI()

    @app.get("/mobile/bearer", name="mobile.synthetic_bearer")
    def mobile_bearer():
        raise MobileAuthError("bearer_required", "A mobile access token is required.")

    @app.get("/legacy/bearer", name="legacy.bearer")
    def legacy_bearer():
        raise MobileAuthError("bearer_required", "A mobile access token is required.")

    install_mobile_error_handlers(app, {"mobile.synthetic_bearer"})
    client = TestClient(app)

    mobile = client.get("/mobile/bearer")
    assert mobile.status_code == 401
    assert mobile.json() == {
        "resource_version": 1,
        "code": "bearer_required",
        "message": "A mobile access token is required.",
        "retryable": False,
        "reference_id": None,
    }
    assert mobile.headers["www-authenticate"] == "Bearer"
    assert mobile.headers["cache-control"] == "no-store"
    assert mobile.headers["pragma"] == "no-cache"

    legacy = client.get("/legacy/bearer")
    assert legacy.status_code == 401
    assert legacy.json() == {"detail": "A mobile access token is required."}
    assert legacy.headers["www-authenticate"] == "Bearer"
