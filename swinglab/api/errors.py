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

from .contracts import APIError

logger = logging.getLogger("swinglab.api.errors")


def _is_named_mobile_route(request: Request, route_names: Collection[str]) -> bool:
    route = request.scope.get("route")
    return bool(route is not None and getattr(route, "name", None) in route_names)


def _response(error: APIError, status_code: int, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        error.model_dump(mode="json"), status_code=status_code, headers=headers
    )


def install_mobile_error_handlers(app: FastAPI, mobile_route_names: Collection[str]) -> None:
    """Install structured errors only for explicitly named native routes.

    A route must opt in by name. This keeps PWA and browser compatibility
    responses in FastAPI's established ``{"detail": ...}`` shape.
    """

    names = frozenset(mobile_route_names)

    @app.exception_handler(HTTPException)
    async def mobile_http_exception(request: Request, exc: HTTPException):
        if not _is_named_mobile_route(request, names):
            return await http_exception_handler(request, exc)
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
        if not _is_named_mobile_route(request, names):
            return await request_validation_exception_handler(request, exc)
        return _response(
            APIError(code="validation_error", message="Invalid request."), 422
        )

    @app.exception_handler(Exception)
    async def mobile_unhandled_exception(request: Request, exc: Exception):
        if not _is_named_mobile_route(request, names):
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
