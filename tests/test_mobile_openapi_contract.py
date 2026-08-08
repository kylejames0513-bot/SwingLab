"""Frozen, deterministic public API schema for native clients."""

from __future__ import annotations

import json
from pathlib import Path

from swinglab.api import create_app
from swinglab.config import Config
from scripts.export_openapi import export_openapi


SNAPSHOT = Path(__file__).parents[1] / "docs" / "api" / "openapi-v1.json"
LEGACY_PATHS = {
    "/api/v1/me",
    "/api/v1/mobile-tokens",
    "/api/v1/mobile-tokens/{selector}",
    "/api/v1/profile",
    "/api/v1/today",
    "/api/v1/sessions",
    "/api/v1/sessions/{job_id}",
    "/api/v1/sessions/{job_id}/brief",
    "/api/v1/practice-checkins",
    "/api/v1/events",
}


def _canonical(schema: dict) -> bytes:
    schema.pop("servers", None)
    return (json.dumps(schema, sort_keys=True, separators=(",", ":")) + "\n").encode()


def test_openapi_v1_matches_the_frozen_snapshot(tmp_path):
    """Catches an accidental public-schema change without a deliberate freeze."""
    app = create_app(
        Config(), tmp_path / "sessions", start_background_workers=False
    )
    try:
        assert _canonical(app.openapi()) == SNAPSHOT.read_bytes()
    finally:
        for resource in (app.state.jobs, app.state.users, app.state.throttle):
            resource.close()


def test_openapi_keeps_legacy_resources_and_requires_upload_club(tmp_path):
    """Catches a missing legacy path or a generated client that can omit club."""
    app = create_app(
        Config(), tmp_path / "sessions", start_background_workers=False
    )
    try:
        schema = app.openapi()
        assert LEGACY_PATHS <= set(schema["paths"])
        upload = schema["paths"]["/upload"]["post"]["requestBody"]["content"]
        multipart = upload["multipart/form-data"]["schema"]
        target = schema
        for part in multipart.get("$ref", "").removeprefix("#/").split("/"):
            if part:
                target = target[part]
        if not multipart.get("$ref"):
            target = multipart
        assert "club" in target["required"]
    finally:
        for resource in (app.state.jobs, app.state.users, app.state.throttle):
            resource.close()


def test_exporter_writes_a_deterministic_schema_without_a_persistent_session_dir(tmp_path):
    """Catches an export that writes a non-canonical schema or owns local state."""
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    export_openapi(first)
    export_openapi(second)
    assert first.read_bytes() == second.read_bytes()
    assert first.read_bytes().endswith(b"\n")
    assert b"\r\n" not in first.read_bytes()


def test_openapi_exposes_only_closed_native_resource_contracts(tmp_path):
    """Catches generated clients accepting diagnostic or invented target fields."""

    app = create_app(
        Config(), tmp_path / "sessions", start_background_workers=False
    )
    try:
        schema = app.openapi()
        assert {
            "/api/v1/capabilities",
            "/api/v1/progress",
            "/api/v1/mobile/sessions",
            "/api/v1/mobile/sessions/{session_id}",
            "/api/v1/mobile/sessions/{session_id}/brief",
            "/api/v1/mobile/today",
        } <= set(schema["paths"])
        for path in (
            "/api/v1/capabilities",
            "/api/v1/progress",
            "/api/v1/mobile/sessions",
            "/api/v1/mobile/sessions/{session_id}",
            "/api/v1/mobile/sessions/{session_id}/brief",
            "/api/v1/mobile/today",
        ):
            assert schema["paths"][path]["get"]["security"] == [
                {"MobileBearer": []}
            ]
        for path in (
            "/api/v1/mobile/sessions/{session_id}",
            "/api/v1/mobile/sessions/{session_id}/brief",
        ):
            validation = schema["paths"][path]["get"]["responses"]["422"]
            assert validation["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/APIError"
            }
        target = schema["components"]["schemas"]["ProofCycleTargetResponse"]
        assert target["additionalProperties"] is False
        assert set(target["properties"]) == {
            "baseline_session_id",
            "target_fingerprint",
            "drill_id",
            "club",
            "hand",
            "angle",
        }
        assert set(target["required"]) == set(target["properties"])

        session = schema["components"]["schemas"]["MobileSessionResponse"]
        assert {"log", "error", "traceback", "command", "path"}.isdisjoint(
            session["properties"]
        )
        assert session["properties"]["report_url"] == {
            "type": "null",
            "title": "Report Url",
        }
        assert session["properties"]["metrics_url"] == {
            "type": "null",
            "title": "Metrics Url",
        }
    finally:
        for resource in (app.state.jobs, app.state.users, app.state.throttle):
            resource.close()
