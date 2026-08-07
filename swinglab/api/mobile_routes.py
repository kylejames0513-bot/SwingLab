"""Named native routes that do not share browser-cookie authentication."""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .auth import (
    MobileAuthError,
    mobile_bearer_token,
    mobile_bearer_unauthorized,
)
from .contracts import APIError, NativeSignOutPendingResponse
from ..web.credential_mutations import (
    MobileSignOutInvalidRequest,
    MobileSignOutService,
    MobileSignOutUnauthorized,
    MobileSignOutUnavailable,
)


MOBILE_SIGN_OUT_ROUTE_NAME = "mobile.auth.sign_out"
_MOBILE_BEARER_SCHEME = HTTPBearer(
    auto_error=False,
    scheme_name="MobileBearer",
)


def install_mobile_routes(
    app: FastAPI,
    *,
    sign_out_service: MobileSignOutService,
    require_account: bool,
) -> None:
    @app.post(
        "/api/v1/auth/sign-out",
        name=MOBILE_SIGN_OUT_ROUTE_NAME,
        status_code=204,
        responses={
            202: {"model": NativeSignOutPendingResponse},
            400: {"model": APIError},
            401: {"model": APIError},
            422: {"model": APIError},
            503: {"model": APIError},
        },
        openapi_extra={
            "parameters": [
                {
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "description": "Exactly 32 hexadecimal characters (128 bits).",
                    "schema": {
                        "type": "string",
                        "minLength": 32,
                        "maxLength": 32,
                        "pattern": "^[0-9A-Fa-f]{32}$",
                    },
                }
            ]
        },
    )
    def mobile_sign_out(
        request: Request,
        _documented_bearer: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(_MOBILE_BEARER_SCHEME),
        ],
    ):
        if not require_account:
            raise HTTPException(404, "Account API is not enabled.")
        raw_token = mobile_bearer_token(request)
        if raw_token is None:
            raise MobileAuthError(
                "bearer_required", "A mobile access token is required."
            )
        idempotency_values = request.headers.getlist("idempotency-key")
        if len(idempotency_values) != 1:
            raise HTTPException(400, "Invalid Idempotency-Key.")
        try:
            result = sign_out_service.sign_out(
                raw_token,
                idempotency_values[0],
            )
        except MobileSignOutInvalidRequest as exc:
            raise HTTPException(400, str(exc)) from exc
        except MobileSignOutUnauthorized:
            raise mobile_bearer_unauthorized()
        except MobileSignOutUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc

        headers = {
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
        }
        if result.pending:
            pending = NativeSignOutPendingResponse(
                status="pending",
                retry_after_seconds=1,
            )
            return JSONResponse(
                pending.model_dump(mode="json"),
                status_code=202,
                headers={**headers, "Retry-After": "1"},
            )
        return Response(status_code=204, headers=headers)
