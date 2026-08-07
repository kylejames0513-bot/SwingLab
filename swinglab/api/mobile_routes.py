"""Named native routes that do not share browser-cookie authentication."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Annotated

from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .auth import (
    MobileAuthError,
    mobile_bearer_token,
    mobile_bearer_unauthorized,
)
from .contracts import (
    APIError,
    NativeAuthExchangePendingResponse,
    NativeAuthExchangeRequest,
    NativeAuthExchangeSuccessResponse,
    NativeAuthStartRequest,
    NativeAuthStartResponse,
    NativeReviewAuthExchangeRequest,
    NativeReviewAuthStartRequest,
    NativeSignOutPendingResponse,
)
from .errors import MobileAPIHTTPError
from ..web.credential_mutations import (
    MobileSignOutInvalidRequest,
    MobileSignOutService,
    MobileSignOutUnauthorized,
    MobileSignOutUnavailable,
)
from ..web.mobile_auth import (
    MobileAuthService,
    MobileNativeAuthConflict,
    MobileNativeAuthInvalidRequest,
    MobileNativeAuthRateLimited,
    MobileNativeAuthRejected,
    MobileNativeAuthUnavailable,
)
from ..web.users import MobileAPITokenLimitError
from ..web.review_auth import (
    ReviewAuthService,
    parse_app_identity_headers,
)


MOBILE_EMAIL_START_ROUTE_NAME = "mobile.auth.email_start"
MOBILE_EMAIL_EXCHANGE_ROUTE_NAME = "mobile.auth.email_exchange"
MOBILE_REVIEW_START_ROUTE_NAME = "mobile.auth.review_start"
MOBILE_REVIEW_EXCHANGE_ROUTE_NAME = "mobile.auth.review_exchange"
MOBILE_SIGN_OUT_ROUTE_NAME = "mobile.auth.sign_out"
MOBILE_AUTH_CALLBACK_ROUTE_NAME = "mobile.auth.callback"
_MOBILE_BEARER_SCHEME = HTTPBearer(
    auto_error=False,
    scheme_name="MobileBearer",
)
_NATIVE_AUTH_BODY_MAX_BYTES = 4096


async def _native_auth_payload(request: Request, model):
    content_length = request.headers.get("content-length")
    try:
        if content_length is not None:
            declared_length = int(content_length)
            if not 0 <= declared_length <= _NATIVE_AUTH_BODY_MAX_BYTES:
                raise ValueError
        chunks: list[bytes] = []
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > _NATIVE_AUTH_BODY_MAX_BYTES:
                raise ValueError
            chunks.append(chunk)
        body = b"".join(chunks)
        return model.model_validate(json.loads(body))
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MobileAPIHTTPError(
            422,
            "validation_error",
            "Invalid request.",
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        ) from exc


def install_mobile_routes(
    app: FastAPI,
    *,
    sign_out_service: MobileSignOutService,
    email_auth_service: MobileAuthService,
    review_auth_service: ReviewAuthService,
    native_email_auth_enabled: bool,
    mobile_deployment_environment: str,
    client_ip_resolver: Callable[[Request], str | None],
    require_account: bool,
) -> None:
    no_store = {"Cache-Control": "no-store", "Pragma": "no-cache"}
    identity_parameters = [
        {
            "name": name,
            "in": "header",
            "required": True,
            "schema": {"type": "string"},
        }
        for name in (
            "X-CaddieInsight-Environment",
            "X-CaddieInsight-Platform",
            "X-CaddieInsight-App-Version",
            "X-CaddieInsight-App-Build",
            "X-CaddieInsight-Application-Id",
        )
    ]

    @app.post(
        "/api/v1/auth/email/start",
        name=MOBILE_EMAIL_START_ROUTE_NAME,
        status_code=202,
        response_model=NativeAuthStartResponse,
        responses={
            404: {"model": APIError},
            422: {"model": APIError},
            429: {"model": APIError},
            503: {"model": APIError},
        },
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": NativeAuthStartRequest.model_json_schema()
                    }
                },
            }
        },
    )
    async def mobile_email_start(request: Request):
        if not native_email_auth_enabled:
            raise MobileAPIHTTPError(
                404,
                "not_found",
                "Native email authentication is not enabled.",
                headers=no_store,
            )
        payload = await _native_auth_payload(request, NativeAuthStartRequest)
        try:
            started = email_auth_service.start(
                email=payload.email,
                code_challenge=payload.code_challenge,
                installation_id=payload.installation_id,
                device_label=payload.device_label,
                client_ip=client_ip_resolver(request),
            )
        except MobileNativeAuthRateLimited as exc:
            retry_after = str(exc.retry_after_seconds)
            raise MobileAPIHTTPError(
                429,
                "rate_limited",
                "Too many authentication attempts.",
                retryable=True,
                headers={**no_store, "Retry-After": retry_after},
            ) from exc
        except MobileNativeAuthInvalidRequest as exc:
            raise MobileAPIHTTPError(
                422,
                "validation_error",
                "Invalid request.",
                headers=no_store,
            ) from exc
        except MobileNativeAuthUnavailable as exc:
            raise MobileAPIHTTPError(
                503,
                "auth_unavailable",
                "Native authentication is temporarily unavailable.",
                retryable=True,
                headers=no_store,
            ) from exc
        response = NativeAuthStartResponse(
            challenge_id=started.challenge_id,
            expires_at=started.expires_at,
        )
        return JSONResponse(
            response.model_dump(mode="json"), status_code=202, headers=no_store
        )

    @app.post(
        "/api/v1/auth/email/exchange",
        name=MOBILE_EMAIL_EXCHANGE_ROUTE_NAME,
        status_code=201,
        response_model=NativeAuthExchangeSuccessResponse,
        responses={
            202: {"model": NativeAuthExchangePendingResponse},
            400: {"model": APIError},
            401: {"model": APIError},
            404: {"model": APIError},
            409: {"model": APIError},
            422: {"model": APIError},
            429: {"model": APIError},
            503: {"model": APIError},
        },
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": NativeAuthExchangeRequest.model_json_schema()
                    }
                },
            },
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
    async def mobile_email_exchange(request: Request):
        if not native_email_auth_enabled:
            raise MobileAPIHTTPError(
                404,
                "not_found",
                "Native email authentication is not enabled.",
                headers=no_store,
            )
        payload = await _native_auth_payload(request, NativeAuthExchangeRequest)
        idempotency_values = request.headers.getlist("idempotency-key")
        if len(idempotency_values) != 1:
            raise MobileAPIHTTPError(
                400,
                "invalid_idempotency_key",
                "Invalid Idempotency-Key.",
                headers=no_store,
            )
        try:
            exchanged = email_auth_service.exchange(
                challenge_id=payload.challenge_id,
                email_code=payload.email_code,
                code_verifier=payload.code_verifier,
                idempotency_key=idempotency_values[0],
                client_ip=client_ip_resolver(request),
            )
        except MobileNativeAuthRateLimited as exc:
            retry_after = str(exc.retry_after_seconds)
            raise MobileAPIHTTPError(
                429,
                "rate_limited",
                "Too many authentication attempts.",
                retryable=True,
                headers={**no_store, "Retry-After": retry_after},
            ) from exc
        except MobileNativeAuthRejected as exc:
            raise MobileAPIHTTPError(
                401,
                "authentication_rejected",
                "Invalid authentication challenge.",
                headers=no_store,
            ) from exc
        except MobileNativeAuthConflict as exc:
            raise MobileAPIHTTPError(
                409,
                "exchange_conflict",
                "The authentication exchange conflicts.",
                headers=no_store,
            ) from exc
        except MobileAPITokenLimitError as exc:
            raise MobileAPIHTTPError(
                409,
                "device_limit",
                "This account has reached its device limit.",
                headers=no_store,
            ) from exc
        except MobileNativeAuthInvalidRequest as exc:
            raise MobileAPIHTTPError(
                422,
                "validation_error",
                "Invalid request.",
                headers=no_store,
            ) from exc
        except MobileNativeAuthUnavailable as exc:
            raise MobileAPIHTTPError(
                503,
                "auth_unavailable",
                "Native authentication is temporarily unavailable.",
                retryable=True,
                headers=no_store,
            ) from exc

        if exchanged.pending:
            response = NativeAuthExchangePendingResponse(
                exchange_id=exchanged.exchange_id,
                status="pending",
                retry_after_seconds=exchanged.retry_after_seconds,
            )
            retry_after = str(exchanged.retry_after_seconds)
            return JSONResponse(
                response.model_dump(mode="json"),
                status_code=202,
                headers={**no_store, "Retry-After": retry_after},
            )
        if exchanged.access_token is None or exchanged.expires_at is None:
            raise MobileAPIHTTPError(
                503,
                "auth_unavailable",
                "Native authentication is temporarily unavailable.",
                retryable=True,
                headers=no_store,
            )
        response = NativeAuthExchangeSuccessResponse(
            status="authenticated",
            access_token=exchanged.access_token,
            expires_at=exchanged.expires_at,
        )
        return JSONResponse(
            response.model_dump(mode="json"), status_code=201, headers=no_store
        )

    def _review_identity(request: Request):
        try:
            return parse_app_identity_headers(
                request,
                deployment_environment=mobile_deployment_environment,
            )
        except MobileNativeAuthInvalidRequest as exc:
            raise MobileAPIHTTPError(
                422,
                "invalid_app_identity",
                "Invalid application identity.",
                headers=no_store,
            ) from exc

    def _require_review_lane() -> None:
        try:
            available = review_auth_service.available()
        except MobileNativeAuthUnavailable as exc:
            raise MobileAPIHTTPError(
                503,
                "auth_unavailable",
                "Review authentication is temporarily unavailable.",
                retryable=True,
                headers=no_store,
            ) from exc
        if not available:
            raise MobileAPIHTTPError(
                404,
                "not_found",
                "Review authentication is not enabled.",
                headers=no_store,
            )

    @app.post(
        "/api/v1/auth/review/start",
        name=MOBILE_REVIEW_START_ROUTE_NAME,
        status_code=202,
        response_model=NativeAuthStartResponse,
        responses={
            404: {"model": APIError},
            422: {"model": APIError},
            429: {"model": APIError},
            503: {"model": APIError},
        },
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": NativeReviewAuthStartRequest.model_json_schema()
                    }
                },
            },
            "parameters": identity_parameters,
        },
    )
    async def mobile_review_start(request: Request):
        # Default deny precedes even header/body parsing and therefore performs
        # no challenge write, credential lookup, or provider operation.
        _require_review_lane()
        identity = _review_identity(request)
        payload = await _native_auth_payload(request, NativeReviewAuthStartRequest)
        try:
            started = review_auth_service.start(
                provider=payload.provider,
                account=payload.account,
                identity=identity,
                code_challenge=payload.code_challenge,
                installation_id=payload.installation_id,
                device_label=payload.device_label,
                client_ip=client_ip_resolver(request),
            )
        except MobileNativeAuthRateLimited as exc:
            retry_after = str(exc.retry_after_seconds)
            raise MobileAPIHTTPError(
                429,
                "rate_limited",
                "Too many authentication attempts.",
                retryable=True,
                headers={**no_store, "Retry-After": retry_after},
            ) from exc
        except MobileNativeAuthInvalidRequest as exc:
            raise MobileAPIHTTPError(
                422,
                "validation_error",
                "Invalid request.",
                headers=no_store,
            ) from exc
        except MobileNativeAuthUnavailable as exc:
            raise MobileAPIHTTPError(
                503,
                "auth_unavailable",
                "Review authentication is temporarily unavailable.",
                retryable=True,
                headers=no_store,
            ) from exc
        response = NativeAuthStartResponse(
            challenge_id=started.challenge_id,
            expires_at=started.expires_at,
        )
        return JSONResponse(
            response.model_dump(mode="json"), status_code=202, headers=no_store
        )

    @app.post(
        "/api/v1/auth/review/exchange",
        name=MOBILE_REVIEW_EXCHANGE_ROUTE_NAME,
        status_code=201,
        response_model=NativeAuthExchangeSuccessResponse,
        responses={
            202: {"model": NativeAuthExchangePendingResponse},
            400: {"model": APIError},
            401: {"model": APIError},
            404: {"model": APIError},
            409: {"model": APIError},
            422: {"model": APIError},
            429: {"model": APIError},
            503: {"model": APIError},
        },
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": NativeReviewAuthExchangeRequest.model_json_schema()
                    }
                },
            },
            "parameters": [
                *identity_parameters,
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
                },
            ],
        },
    )
    async def mobile_review_exchange(request: Request):
        _require_review_lane()
        identity = _review_identity(request)
        payload = await _native_auth_payload(request, NativeReviewAuthExchangeRequest)
        idempotency_values = request.headers.getlist("idempotency-key")
        if len(idempotency_values) != 1:
            raise MobileAPIHTTPError(
                400,
                "invalid_idempotency_key",
                "Invalid Idempotency-Key.",
                headers=no_store,
            )
        try:
            exchanged = review_auth_service.exchange(
                challenge_id=payload.challenge_id,
                password=payload.password,
                code_verifier=payload.code_verifier,
                idempotency_key=idempotency_values[0],
                identity=identity,
                client_ip=client_ip_resolver(request),
            )
        except MobileNativeAuthRateLimited as exc:
            retry_after = str(exc.retry_after_seconds)
            raise MobileAPIHTTPError(
                429,
                "rate_limited",
                "Too many authentication attempts.",
                retryable=True,
                headers={**no_store, "Retry-After": retry_after},
            ) from exc
        except MobileNativeAuthRejected as exc:
            raise MobileAPIHTTPError(
                401,
                "authentication_rejected",
                "Invalid review authentication.",
                headers=no_store,
            ) from exc
        except MobileNativeAuthConflict as exc:
            raise MobileAPIHTTPError(
                409,
                "exchange_conflict",
                "The authentication exchange conflicts.",
                headers=no_store,
            ) from exc
        except MobileAPITokenLimitError as exc:
            raise MobileAPIHTTPError(
                409,
                "device_limit",
                "This account has reached its device limit.",
                headers=no_store,
            ) from exc
        except MobileNativeAuthInvalidRequest as exc:
            raise MobileAPIHTTPError(
                422,
                "validation_error",
                "Invalid request.",
                headers=no_store,
            ) from exc
        except MobileNativeAuthUnavailable as exc:
            raise MobileAPIHTTPError(
                503,
                "auth_unavailable",
                "Review authentication is temporarily unavailable.",
                retryable=True,
                headers=no_store,
            ) from exc
        if exchanged.pending:
            response = NativeAuthExchangePendingResponse(
                exchange_id=exchanged.exchange_id,
                status="pending",
                retry_after_seconds=exchanged.retry_after_seconds,
            )
            return JSONResponse(
                response.model_dump(mode="json"),
                status_code=202,
                headers={**no_store, "Retry-After": str(exchanged.retry_after_seconds)},
            )
        if exchanged.access_token is None or exchanged.expires_at is None:
            raise MobileAPIHTTPError(
                503,
                "auth_unavailable",
                "Review authentication is temporarily unavailable.",
                retryable=True,
                headers=no_store,
            )
        response = NativeAuthExchangeSuccessResponse(
            status="authenticated",
            access_token=exchanged.access_token,
            expires_at=exchanged.expires_at,
        )
        return JSONResponse(
            response.model_dump(mode="json"), status_code=201, headers=no_store
        )

    @app.get(
        "/app/auth/callback",
        name=MOBILE_AUTH_CALLBACK_ROUTE_NAME,
        include_in_schema=False,
    )
    def mobile_auth_callback():
        body = (
            "<!doctype html><html><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>Open CaddieInsight</title></head><body>"
            "<main><h1>Open CaddieInsight</h1>"
            "<p>Enter the code from this email on the device where sign-in started.</p>"
            "<p>If the code expired, request a new code in CaddieInsight.</p>"
            "</main></body></html>"
        )
        return HTMLResponse(
            body,
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": (
                    "default-src 'self'; base-uri 'none'; form-action 'self'; "
                    "frame-ancestors 'none'"
                ),
            },
        )

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
