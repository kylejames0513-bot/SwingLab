"""Error boundary for named native routes, without changing legacy APIs."""

from __future__ import annotations

import logging
import secrets
from collections.abc import Collection

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match

from .auth import MobileAuthError
from .contracts import APIError

logger = logging.getLogger("swinglab.api.errors")


class MobileAPIHTTPError(HTTPException):
    """A closed, structured error for explicitly named native routes."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        headers: dict[str, str] | None = None,
    ) -> None:
        if not code or len(code) > 64 or not all(
            character.islower() or character.isdigit() or character == "_"
            for character in code
        ):
            raise ValueError("A bounded native API error code is required.")
        super().__init__(status_code, message, headers=headers)
        self.mobile_error_code = code
        self.mobile_error_retryable = bool(retryable)


def _named_mobile_route(
    request: Request, route_names: Collection[str]
) -> str | None:
    route = request.scope.get("route")
    name = getattr(route, "name", None) if route is not None else None
    if name in route_names:
        return name

    # Starlette raises method-mismatch errors while routing, before it places
    # the partial route on the request scope. Match only opted-in route names
    # so similarly prefixed legacy and unknown paths retain their contracts.
    for candidate in request.app.routes:
        candidate_name = getattr(candidate, "name", None)
        if candidate_name not in route_names:
            continue
        match, _child_scope = candidate.matches(request.scope)
        if match is not Match.NONE:
            return candidate_name
    return None


def _response(
    error: APIError,
    status_code: int,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    response_headers = {
        key: value
        for key, value in (headers or {}).items()
        if key.lower() not in {"cache-control", "pragma"}
    }
    response_headers.update({"Cache-Control": "no-store", "Pragma": "no-cache"})
    return JSONResponse(
        error.model_dump(mode="json"),
        status_code=status_code,
        headers=response_headers,
    )


def install_mobile_error_handlers(
    app: FastAPI,
    mobile_route_names: Collection[str],
    *,
    concealed_route_names: Collection[str] = (),
) -> None:
    """Install structured errors only for explicitly named native routes.

    A route must opt in by name. This keeps PWA and browser compatibility
    responses in FastAPI's established ``{"detail": ...}`` shape.
    """

    names = frozenset(mobile_route_names)
    concealed_names = frozenset(concealed_route_names)
    if not concealed_names.issubset(names):
        raise ValueError("Concealed native route names must be opted in.")

    @app.exception_handler(StarletteHTTPException)
    @app.exception_handler(HTTPException)
    async def mobile_http_exception(request: Request, exc: StarletteHTTPException):
        route_name = _named_mobile_route(request, names)
        if route_name is None:
            return await http_exception_handler(request, exc)
        if exc.status_code == 405 and route_name in concealed_names:
            return _response(
                APIError(
                    code="not_found",
                    message="Mobile resources are not enabled.",
                ),
                404,
            )
        if exc.status_code >= 500:
            return _response(
                APIError(
                    code="internal_error",
                    message="Internal server error.",
                    retryable=True,
                    reference_id=secrets.token_hex(16),
                ),
                exc.status_code,
                exc.headers,
            )
        if isinstance(exc, (MobileAuthError, MobileAPIHTTPError)):
            return _response(
                APIError(
                    code=exc.mobile_error_code,
                    message=str(exc.detail),
                    retryable=exc.mobile_error_retryable,
                ),
                exc.status_code,
                exc.headers,
            )
        return _response(
            APIError(
                code=f"http_{exc.status_code}",
                message=str(exc.detail),
                retryable=exc.status_code == 429,
            ),
            exc.status_code,
            exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def mobile_validation_error(request: Request, exc: RequestValidationError):
        if _named_mobile_route(request, names) is None:
            return await request_validation_exception_handler(request, exc)
        return _response(
            APIError(code="validation_error", message="Invalid request."), 422
        )

    @app.exception_handler(Exception)
    async def mobile_unhandled_exception(request: Request, exc: Exception):
        if _named_mobile_route(request, names) is None:
            raise exc
        reference_id = secrets.token_hex(16)
        logger.exception("Unhandled native API failure reference_id=%s", reference_id)
        return _response(
            APIError(
                code="internal_error",
                message="Internal server error.",
                retryable=True,
                reference_id=reference_id,
            ),
            500,
        )
