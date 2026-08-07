"""Scoped structured errors for native-only route names."""

from __future__ import annotations

import re

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from swinglab.api.errors import install_mobile_error_handlers


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

    @app.get("/legacy", name="legacy")
    def legacy():
        raise HTTPException(404, "legacy detail")

    install_mobile_error_handlers(
        app,
        {
            "mobile.synthetic_status",
            "mobile.synthetic_validation",
            "mobile.synthetic_failure",
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

    failure = client.get("/mobile/failure")
    assert failure.status_code == 500
    assert failure.json()["resource_version"] == 1
    assert failure.json()["code"] == "internal_error"
    assert failure.json()["message"] == "Internal server error."
    assert failure.json()["retryable"] is True
    assert re.fullmatch(r"[0-9a-f]{32}", failure.json()["reference_id"])
    assert "secret implementation detail" not in failure.text


def test_legacy_routes_keep_fastapis_detail_error_contract():
    """Catches structured native errors spilling into compatibility routes."""
    response = TestClient(_app(), raise_server_exceptions=False).get("/legacy")
    assert response.status_code == 404
    assert response.json() == {"detail": "legacy detail"}
