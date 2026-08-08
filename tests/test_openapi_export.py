"""The exported OpenAPI document, and the native client's agreement with it.

The document is committed so a route change shows up as a reviewable diff.
The native client's route table is checked against it because
`mobile/src/api/types.ts` is hand-written — the `/api/` handlers return bare
JSONResponse, so there is nothing to generate types from yet. This narrows the
drift that hand-writing invites: it catches a route that was renamed or
removed. It cannot catch a field that changed shape.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from scripts.export_openapi import build_document, serialize

ROOT = Path(__file__).resolve().parents[1]
EXPORTED = ROOT / "docs" / "api" / "openapi-v1.json"
CLIENT_TYPES = ROOT / "mobile" / "src" / "api" / "types.ts"


@pytest.fixture(scope="module")
def document() -> dict:
    return json.loads(EXPORTED.read_text(encoding="utf-8"))


def test_exported_document_is_committed_and_current():
    assert EXPORTED.exists(), "run scripts/export_openapi.py"
    assert EXPORTED.read_text(encoding="utf-8") == serialize(build_document()), (
        "docs/api/openapi-v1.json is stale; run scripts/export_openapi.py"
    )


def test_export_is_deterministic():
    # Two builds of the same app must be byte-identical, or the committed copy
    # churns on every run and stops being reviewable.
    assert serialize(build_document()) == serialize(build_document())


def test_owned_mobile_surface_is_present(document):
    paths = document["paths"]
    for route in (
        "/api/v1/me",
        "/api/v1/today",
        "/api/v1/sessions",
        "/api/v1/sessions/{job_id}",
        "/api/v1/sessions/{job_id}/brief",
        "/api/v1/mobile-tokens",
        "/api/v1/mobile-tokens/{selector}",
    ):
        assert route in paths, route


def test_native_client_only_calls_routes_that_exist(document):
    """Every path in the client's route table resolves in the document."""
    source = CLIENT_TYPES.read_text(encoding="utf-8")
    block = re.search(r"API_ROUTES = \{(.*?)\} as const;", source, re.S)
    assert block is not None, "API_ROUTES table not found in types.ts"

    routes = re.findall(r'"(/api/[^"]+)"', block.group(1))
    assert routes, "API_ROUTES declared no routes"

    paths = document["paths"]
    missing = [route for route in routes if route not in paths]
    assert not missing, f"native client calls routes the app does not serve: {missing}"


def _code_only(source: str) -> str:
    """Strip comments. These files explain the rules they follow in prose, so
    a naive substring check matches the explanation and not the code."""
    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", without_block)


def test_device_tokens_cannot_be_minted_by_the_native_client():
    """A bearer credential may not mint, list, or revoke device tokens — that
    needs a cookie-authenticated same-origin browser session. The client must
    not ship a helper that implies otherwise."""
    client = _code_only(
        (ROOT / "mobile" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
    )
    for forbidden in ("issueToken", "createToken", "revokeToken", "deleteToken"):
        assert forbidden not in client, forbidden


def test_device_token_is_only_ever_written_to_the_keychain():
    """The credential must not reach AsyncStorage or a plain-storage shim."""
    raw = (ROOT / "mobile" / "src" / "auth" / "token.ts").read_text(encoding="utf-8")
    assert "expo-secure-store" in raw

    auth = _code_only(raw)
    for forbidden in ("AsyncStorage", "localStorage", "console.log"):
        assert forbidden not in auth, forbidden
