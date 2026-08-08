"""Named native routes that do not share browser-cookie authentication."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Annotated

from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .auth import (
    MobileAuthContext,
    MobileAuthError,
    mobile_bearer_token,
    mobile_bearer_unauthorized,
    require_mobile_bearer,
)
from .contracts import (
    APIError,
    BriefResponse,
    CapabilitiesResponse,
    DeviceListResponse,
    MobileSessionResponse,
    MobileSessionsResponse,
    MobileTodayResponse,
    MobileTokenMetadata,
    PracticeEvidenceReceipt,
    PracticeEvidenceRequest,
    ProfileResponse,
    ProfileUpdateRequest,
    ProgressResponse,
    UploadCompleteResponse,
    UploadCreateRequest,
    UploadReservationResponse,
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
    CredentialMutationGuard,
    CredentialMutationRejected,
    MobileDeviceRevokeNotFound,
    MobileDeviceRevokeService,
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
from ..web.mobile_resources import (
    MobilePracticeDayConflict,
    MobilePracticeHistoryConflict,
    MobilePracticeIdempotencyConflict,
    MobilePracticeUnauthorized,
    MobilePracticeUnavailable,
    MobileProfileHistoryConflict,
    MobileProfileUnauthorized,
    MobileProfileUnavailable,
    MobileResourceNotFound,
    MobileResourceService,
    serialize_mobile_session,
)
from ..web.resumable_uploads import (
    Reservation,
    ResumableUploadManager,
    UploadBusy,
    UploadCapacityError,
    UploadChecksumMismatch,
    UploadChunkTooLarge,
    UploadComparisonConflict,
    UploadExpired,
    UploadHistoryConflict,
    UploadIdempotencyConflict,
    UploadNotFound,
    UploadOffsetMismatch,
    UploadRepairRequired,
    UploadStateConflict,
)
from ..web.users import MobileAPITokenLimitError, UserStore
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
MOBILE_CAPABILITIES_ROUTE_NAME = "mobile.resources.capabilities"
MOBILE_SESSIONS_ROUTE_NAME = "mobile.resources.sessions"
MOBILE_SESSION_ROUTE_NAME = "mobile.resources.session"
MOBILE_SESSION_BRIEF_ROUTE_NAME = "mobile.resources.session_brief"
MOBILE_TODAY_ROUTE_NAME = "mobile.resources.today"
MOBILE_PROGRESS_ROUTE_NAME = "mobile.resources.progress"
MOBILE_PROFILE_WRITE_ROUTE_NAME = "mobile.resources.profile_write"
MOBILE_PRACTICE_EVIDENCE_ROUTE_NAME = "mobile.resources.practice_evidence"
MOBILE_DEVICES_LIST_ROUTE_NAME = "mobile.devices.list"
MOBILE_DEVICE_REVOKE_ROUTE_NAME = "mobile.devices.revoke"
MOBILE_UPLOAD_CREATE_ROUTE_NAME = "mobile.uploads.create"
MOBILE_UPLOAD_STATUS_ROUTE_NAME = "mobile.uploads.status"
MOBILE_UPLOAD_CHUNK_ROUTE_NAME = "mobile.uploads.chunk"
MOBILE_UPLOAD_COMPLETE_ROUTE_NAME = "mobile.uploads.complete"
MOBILE_UPLOAD_ABORT_ROUTE_NAME = "mobile.uploads.abort"
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
    device_revoke_service: MobileDeviceRevokeService,
    device_management_enabled: bool,
    email_auth_service: MobileAuthService,
    review_auth_service: ReviewAuthService,
    native_email_auth_enabled: bool,
    mobile_deployment_environment: str,
    client_ip_resolver: Callable[[Request], str | None],
    require_account: bool,
    resource_service: MobileResourceService,
    resolve_read_auth: Callable[[Request], MobileAuthContext],
    users: UserStore,
    credential_mutation_guard: CredentialMutationGuard,
    review_auth_admission=None,
    resumable_upload_manager: ResumableUploadManager | None = None,
    resumable_upload_enabled: bool = False,
) -> None:
    no_store = {"Cache-Control": "no-store", "Pragma": "no-cache"}
    read_security = {"security": [{"MobileBearer": []}]}
    write_security = {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": ProfileUpdateRequest.model_json_schema()
                }
            },
        },
    }
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

    @app.get(
        "/api/v1/capabilities",
        name=MOBILE_CAPABILITIES_ROUTE_NAME,
        response_model=CapabilitiesResponse,
        responses={401: {"model": APIError}, 404: {"model": APIError}},
        openapi_extra=read_security,
    )
    def mobile_capabilities(request: Request):
        if not resource_service.settings.resources_enabled:
            raise MobileAPIHTTPError(
                404,
                "not_found",
                "Mobile resources are not enabled.",
                headers=no_store,
            )
        try:
            response = resource_service.capabilities(resolve_read_auth(request))
        except MobileResourceNotFound as exc:
            raise MobileAPIHTTPError(
                404,
                "not_found",
                "Account history not found.",
                headers=no_store,
            ) from exc
        return JSONResponse(response.model_dump(mode="json"), headers=no_store)

    def _resource_not_found(exc: MobileResourceNotFound):
        raise MobileAPIHTTPError(
            404,
            "not_found",
            "Session not found.",
            headers=no_store,
        ) from exc

    @app.get(
        "/api/v1/mobile/sessions",
        name=MOBILE_SESSIONS_ROUTE_NAME,
        response_model=MobileSessionsResponse,
        responses={401: {"model": APIError}, 404: {"model": APIError}},
        openapi_extra=read_security,
    )
    def mobile_sessions(request: Request):
        if not resource_service.settings.resources_enabled:
            raise MobileAPIHTTPError(
                404,
                "not_found",
                "Mobile resources are not enabled.",
                headers=no_store,
            )
        try:
            response = resource_service.sessions(resolve_read_auth(request))
        except MobileResourceNotFound as exc:
            _resource_not_found(exc)
        return JSONResponse(response.model_dump(mode="json"), headers=no_store)

    @app.get(
        "/api/v1/mobile/sessions/{session_id}",
        name=MOBILE_SESSION_ROUTE_NAME,
        response_model=MobileSessionResponse,
        responses={
            401: {"model": APIError},
            404: {"model": APIError},
            422: {"model": APIError},
        },
        openapi_extra=read_security,
    )
    def mobile_session(session_id: str, request: Request):
        if not resource_service.settings.resources_enabled:
            raise MobileAPIHTTPError(
                404,
                "not_found",
                "Mobile resources are not enabled.",
                headers=no_store,
            )
        try:
            response = resource_service.session(resolve_read_auth(request), session_id)
        except MobileResourceNotFound as exc:
            _resource_not_found(exc)
        return JSONResponse(response.model_dump(mode="json"), headers=no_store)

    @app.get(
        "/api/v1/mobile/sessions/{session_id}/brief",
        name=MOBILE_SESSION_BRIEF_ROUTE_NAME,
        response_model=BriefResponse,
        responses={
            401: {"model": APIError},
            404: {"model": APIError},
            422: {"model": APIError},
        },
        openapi_extra=read_security,
    )
    def mobile_session_brief(session_id: str, request: Request):
        if not resource_service.settings.resources_enabled:
            raise MobileAPIHTTPError(
                404,
                "not_found",
                "Mobile resources are not enabled.",
                headers=no_store,
            )
        try:
            response = resource_service.brief(resolve_read_auth(request), session_id)
        except MobileResourceNotFound as exc:
            _resource_not_found(exc)
        return JSONResponse(response.model_dump(mode="json"), headers=no_store)

    @app.get(
        "/api/v1/mobile/today",
        name=MOBILE_TODAY_ROUTE_NAME,
        response_model=MobileTodayResponse,
        responses={401: {"model": APIError}, 404: {"model": APIError}},
        openapi_extra=read_security,
    )
    def mobile_today(request: Request):
        if not resource_service.settings.resources_enabled:
            raise MobileAPIHTTPError(
                404,
                "not_found",
                "Mobile resources are not enabled.",
                headers=no_store,
            )
        try:
            response = resource_service.today(resolve_read_auth(request))
        except MobileResourceNotFound as exc:
            raise MobileAPIHTTPError(
                404,
                "not_found",
                "Account history not found.",
                headers=no_store,
            ) from exc
        return JSONResponse(response.model_dump(mode="json"), headers=no_store)

    @app.get(
        "/api/v1/progress",
        name=MOBILE_PROGRESS_ROUTE_NAME,
        response_model=ProgressResponse,
        responses={401: {"model": APIError}, 404: {"model": APIError}},
        openapi_extra=read_security,
    )
    def mobile_progress(request: Request):
        if not resource_service.settings.resources_enabled:
            raise MobileAPIHTTPError(
                404,
                "not_found",
                "Mobile resources are not enabled.",
                headers=no_store,
            )
        try:
            response = resource_service.progress(resolve_read_auth(request))
        except MobileResourceNotFound as exc:
            raise MobileAPIHTTPError(
                404,
                "not_found",
                "Account history not found.",
                headers=no_store,
            ) from exc
        return JSONResponse(response.model_dump(mode="json"), headers=no_store)

    @app.put(
        "/api/v1/mobile/profile",
        name=MOBILE_PROFILE_WRITE_ROUTE_NAME,
        response_model=ProfileResponse,
        responses={
            401: {"model": APIError},
            404: {"model": APIError},
            409: {"model": APIError},
            422: {"model": APIError},
        },
        openapi_extra=write_security,
    )
    async def mobile_profile_write(
        request: Request,
        _documented_bearer: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(_MOBILE_BEARER_SCHEME),
        ],
    ):
        if not resource_service.settings.profile_writes_enabled:
            raise MobileAPIHTTPError(
                404,
                "not_found",
                "Mobile profile writes are not enabled.",
                headers=no_store,
            )
        payload = await _native_auth_payload(request, ProfileUpdateRequest)
        try:
            context = require_mobile_bearer(
                request,
                users,
                require_account,
                review_auth_admission,
                credential_mutation_guard,
            )
            response = resource_service.update_profile(
                context,
                payload,
                guard=credential_mutation_guard,
            )
        except MobileAuthError:
            raise
        except MobileProfileUnauthorized as exc:
            raise mobile_bearer_unauthorized() from exc
        except MobileProfileUnavailable as exc:
            raise MobileAPIHTTPError(
                404,
                "not_found",
                "Account history not found.",
                headers=no_store,
            ) from exc
        except MobileProfileHistoryConflict as exc:
            raise MobileAPIHTTPError(
                409,
                "history_epoch_conflict",
                str(exc),
                headers=no_store,
            ) from exc
        return JSONResponse(response.model_dump(mode="json"), headers=no_store)

    @app.post(
        "/api/v1/practice-evidence",
        name=MOBILE_PRACTICE_EVIDENCE_ROUTE_NAME,
        status_code=201,
        response_model=PracticeEvidenceReceipt,
        responses={
            400: {"model": APIError},
            401: {"model": APIError},
            404: {"model": APIError},
            409: {"model": APIError},
            422: {"model": APIError},
        },
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": PracticeEvidenceRequest.model_json_schema()
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
            ],
            "security": [{"MobileBearer": []}],
        },
    )
    async def mobile_practice_evidence(
        request: Request,
        _documented_bearer: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(_MOBILE_BEARER_SCHEME),
        ],
    ):
        if not resource_service.settings.practice_writes_enabled:
            raise MobileAPIHTTPError(
                404,
                "not_found",
                "Mobile practice writes are not enabled.",
                headers=no_store,
            )
        payload = await _native_auth_payload(request, PracticeEvidenceRequest)
        idempotency_values = request.headers.getlist("idempotency-key")
        if len(idempotency_values) != 1:
            raise MobileAPIHTTPError(
                400,
                "invalid_idempotency_key",
                "Invalid Idempotency-Key.",
                headers=no_store,
            )
        try:
            UserStore._mobile_auth_idempotency_bytes(idempotency_values[0])
        except ValueError as exc:
            raise MobileAPIHTTPError(
                400,
                "invalid_idempotency_key",
                "Invalid Idempotency-Key.",
                headers=no_store,
            ) from exc
        try:
            context = require_mobile_bearer(
                request,
                users,
                require_account,
                review_auth_admission,
                credential_mutation_guard,
            )
            receipt = resource_service.record_practice_evidence(
                context,
                payload,
                idempotency_key=idempotency_values[0],
                guard=credential_mutation_guard,
            )
        except MobileAuthError:
            raise
        except MobilePracticeUnauthorized as exc:
            raise mobile_bearer_unauthorized() from exc
        except MobilePracticeUnavailable as exc:
            raise MobileAPIHTTPError(
                404,
                "not_found",
                "Account history not found.",
                headers=no_store,
            ) from exc
        except MobilePracticeHistoryConflict as exc:
            raise MobileAPIHTTPError(
                409,
                "history_epoch_conflict",
                str(exc),
                headers=no_store,
            ) from exc
        except MobilePracticeIdempotencyConflict as exc:
            raise MobileAPIHTTPError(
                409,
                "idempotency_conflict",
                str(exc),
                headers=no_store,
            ) from exc
        except MobilePracticeDayConflict as exc:
            raise MobileAPIHTTPError(
                409,
                "practice_conflict",
                str(exc),
                headers=no_store,
            ) from exc
        return JSONResponse(
            receipt.model_dump(mode="json"),
            status_code=201,
            headers=no_store,
        )

    # -- resumable uploads ------------------------------------------------
    def _uploads_ready() -> ResumableUploadManager:
        if resumable_upload_manager is None or not resumable_upload_enabled:
            raise MobileAPIHTTPError(
                404,
                "not_found",
                "Resumable uploads are not enabled.",
                headers=no_store,
            )
        return resumable_upload_manager

    def _upload_bearer(request: Request) -> MobileAuthContext:
        return require_mobile_bearer(
            request,
            users,
            require_account,
            review_auth_admission,
            credential_mutation_guard,
        )

    def _single_idempotency_key(request: Request) -> str:
        values = request.headers.getlist("idempotency-key")
        if len(values) != 1:
            raise MobileAPIHTTPError(
                400,
                "invalid_idempotency_key",
                "Invalid Idempotency-Key.",
                headers=no_store,
            )
        try:
            UserStore._mobile_auth_idempotency_bytes(values[0])
        except ValueError as exc:
            raise MobileAPIHTTPError(
                400,
                "invalid_idempotency_key",
                "Invalid Idempotency-Key.",
                headers=no_store,
            ) from exc
        return values[0]

    def _reservation_response(
        reservation: Reservation, *, status_code: int = 200
    ) -> JSONResponse:
        body = UploadReservationResponse(
            upload_id=reservation.upload_id,
            status=reservation.status,
            offset=reservation.committed_offset,
            file_bytes=reservation.file_bytes,
            chunk_bytes=reservation.chunk_bytes,
            expires_at=reservation.expires_at,
        )
        return JSONResponse(
            body.model_dump(mode="json"),
            status_code=status_code,
            headers=no_store,
        )

    def _map_upload_error(exc: Exception) -> MobileAPIHTTPError:
        if isinstance(exc, UploadNotFound):
            return MobileAPIHTTPError(
                404, "not_found", "Upload not found.", headers=no_store
            )
        if isinstance(exc, UploadIdempotencyConflict):
            return MobileAPIHTTPError(
                409, "idempotency_conflict", str(exc), headers=no_store
            )
        if isinstance(exc, UploadHistoryConflict):
            return MobileAPIHTTPError(
                409, "history_epoch_conflict", str(exc), headers=no_store
            )
        if isinstance(exc, UploadComparisonConflict):
            return MobileAPIHTTPError(
                409, "comparison_conflict", str(exc), headers=no_store
            )
        if isinstance(exc, UploadOffsetMismatch):
            headers = dict(no_store)
            headers["Upload-Offset"] = str(exc.acknowledged_offset)
            return MobileAPIHTTPError(
                409, "offset_mismatch", str(exc), headers=headers
            )
        if isinstance(exc, UploadRepairRequired):
            return MobileAPIHTTPError(
                409, "upload_repairing", str(exc), retryable=True, headers=no_store
            )
        if isinstance(exc, UploadBusy):
            return MobileAPIHTTPError(
                409, "upload_busy", str(exc), retryable=True, headers=no_store
            )
        if isinstance(exc, UploadStateConflict):
            return MobileAPIHTTPError(
                409, "upload_conflict", str(exc), headers=no_store
            )
        if isinstance(exc, UploadExpired):
            return MobileAPIHTTPError(
                410, "upload_expired", str(exc), headers=no_store
            )
        if isinstance(exc, UploadChunkTooLarge):
            return MobileAPIHTTPError(
                413, "chunk_too_large", str(exc), headers=no_store
            )
        if isinstance(exc, UploadChecksumMismatch):
            return MobileAPIHTTPError(
                422, "checksum_mismatch", str(exc), headers=no_store
            )
        if isinstance(exc, UploadCapacityError):
            headers = dict(no_store)
            headers["Retry-After"] = str(int(exc.retry_after_seconds))
            return MobileAPIHTTPError(
                507,
                "insufficient_storage",
                str(exc),
                retryable=True,
                headers=headers,
            )
        return MobileAPIHTTPError(
            500, "internal_error", "Internal server error.", retryable=True
        )

    async def _read_raw_chunk(request: Request, limit: int) -> bytes:
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > limit:
                    raise MobileAPIHTTPError(
                        413,
                        "chunk_too_large",
                        "The chunk exceeds the configured size.",
                        headers=no_store,
                    )
            except ValueError as exc:
                raise MobileAPIHTTPError(
                    400,
                    "validation_error",
                    "Invalid Content-Length.",
                    headers=no_store,
                ) from exc
        chunks: list[bytes] = []
        received = 0
        async for piece in request.stream():
            received += len(piece)
            if received > limit:
                raise MobileAPIHTTPError(
                    413,
                    "chunk_too_large",
                    "The chunk exceeds the configured size.",
                    headers=no_store,
                )
            chunks.append(piece)
        return b"".join(chunks)

    upload_idempotency_parameter = {
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

    @app.post(
        "/api/v1/uploads",
        name=MOBILE_UPLOAD_CREATE_ROUTE_NAME,
        status_code=201,
        response_model=UploadReservationResponse,
        responses={
            400: {"model": APIError},
            401: {"model": APIError},
            404: {"model": APIError},
            409: {"model": APIError},
            422: {"model": APIError},
            507: {"model": APIError},
        },
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": UploadCreateRequest.model_json_schema()
                    }
                },
            },
            "parameters": [upload_idempotency_parameter],
            "security": [{"MobileBearer": []}],
        },
    )
    async def mobile_upload_create(
        request: Request,
        _documented_bearer: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(_MOBILE_BEARER_SCHEME),
        ],
    ):
        manager = _uploads_ready()
        payload = await _native_auth_payload(request, UploadCreateRequest)
        idempotency_key = _single_idempotency_key(request)
        try:
            context = _upload_bearer(request)
            with credential_mutation_guard.admit(context):
                reservation = manager.create(
                    context.user.id, payload, idempotency_key
                )
        except MobileAuthError:
            raise
        except CredentialMutationRejected as exc:
            raise mobile_bearer_unauthorized() from exc
        except Exception as exc:
            raise _map_upload_error(exc) from exc
        return _reservation_response(reservation, status_code=201)

    @app.get(
        "/api/v1/uploads/{upload_id}",
        name=MOBILE_UPLOAD_STATUS_ROUTE_NAME,
        response_model=UploadReservationResponse,
        responses={
            401: {"model": APIError},
            404: {"model": APIError},
            409: {"model": APIError},
        },
        openapi_extra={"security": [{"MobileBearer": []}]},
    )
    def mobile_upload_status(
        upload_id: str,
        request: Request,
        _documented_bearer: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(_MOBILE_BEARER_SCHEME),
        ],
    ):
        manager = _uploads_ready()
        try:
            context = _upload_bearer(request)
            reservation = manager.status(context.user.id, upload_id)
        except MobileAuthError:
            raise
        except Exception as exc:
            raise _map_upload_error(exc) from exc
        return _reservation_response(reservation)

    @app.patch(
        "/api/v1/uploads/{upload_id}",
        name=MOBILE_UPLOAD_CHUNK_ROUTE_NAME,
        response_model=UploadReservationResponse,
        responses={
            400: {"model": APIError},
            401: {"model": APIError},
            404: {"model": APIError},
            409: {"model": APIError},
            410: {"model": APIError},
            413: {"model": APIError},
            422: {"model": APIError},
            507: {"model": APIError},
        },
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/offset+octet-stream": {
                        "schema": {"type": "string", "format": "binary"}
                    }
                },
            },
            "parameters": [
                {
                    "name": "Upload-Offset",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "integer", "minimum": 0},
                },
                {
                    "name": "Upload-Checksum",
                    "in": "header",
                    "required": True,
                    "description": "Base64 SHA-256 digest of the chunk bytes.",
                    "schema": {"type": "string"},
                },
            ],
            "security": [{"MobileBearer": []}],
        },
    )
    async def mobile_upload_chunk(
        upload_id: str,
        request: Request,
        _documented_bearer: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(_MOBILE_BEARER_SCHEME),
        ],
    ):
        manager = _uploads_ready()
        offset_header = request.headers.get("upload-offset")
        checksum_header = request.headers.get("upload-checksum")
        if offset_header is None or checksum_header is None:
            raise MobileAPIHTTPError(
                400,
                "validation_error",
                "Upload-Offset and Upload-Checksum are required.",
                headers=no_store,
            )
        try:
            offset = int(offset_header)
            if offset < 0:
                raise ValueError
        except ValueError as exc:
            raise MobileAPIHTTPError(
                400,
                "validation_error",
                "Invalid Upload-Offset.",
                headers=no_store,
            ) from exc
        chunk = await _read_raw_chunk(request, manager.settings.upload_chunk_bytes)
        try:
            context = _upload_bearer(request)
            with credential_mutation_guard.admit(context):
                reservation = manager.patch_chunk(
                    context.user.id,
                    upload_id,
                    offset=offset,
                    chunk=chunk,
                    checksum_b64=checksum_header,
                )
        except MobileAuthError:
            raise
        except CredentialMutationRejected as exc:
            raise mobile_bearer_unauthorized() from exc
        except Exception as exc:
            raise _map_upload_error(exc) from exc
        return _reservation_response(reservation)

    @app.post(
        "/api/v1/uploads/{upload_id}/complete",
        name=MOBILE_UPLOAD_COMPLETE_ROUTE_NAME,
        response_model=UploadCompleteResponse,
        responses={
            401: {"model": APIError},
            404: {"model": APIError},
            409: {"model": APIError},
            410: {"model": APIError},
            422: {"model": APIError},
        },
        openapi_extra={"security": [{"MobileBearer": []}]},
    )
    def mobile_upload_complete(
        upload_id: str,
        request: Request,
        _documented_bearer: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(_MOBILE_BEARER_SCHEME),
        ],
    ):
        manager = _uploads_ready()
        try:
            context = _upload_bearer(request)
            with credential_mutation_guard.admit(context):
                job, replayed = manager.complete_mobile_upload(
                    context.user.id, upload_id
                )
        except MobileAuthError:
            raise
        except CredentialMutationRejected as exc:
            raise mobile_bearer_unauthorized() from exc
        except Exception as exc:
            raise _map_upload_error(exc) from exc
        session = serialize_mobile_session(
            job,
            context.user,
            queue_position=manager._jobs.queue_position(job),
            coaching_eligible=None,
            retry_max_attempts=resource_service.settings.analysis_retry_max_attempts,
        )
        body = UploadCompleteResponse(job=session, replayed=replayed)
        return JSONResponse(body.model_dump(mode="json"), headers=no_store)

    @app.delete(
        "/api/v1/uploads/{upload_id}",
        name=MOBILE_UPLOAD_ABORT_ROUTE_NAME,
        status_code=204,
        responses={
            400: {"model": APIError},
            401: {"model": APIError},
            404: {"model": APIError},
            409: {"model": APIError},
        },
        openapi_extra={
            "parameters": [upload_idempotency_parameter],
            "security": [{"MobileBearer": []}],
        },
    )
    def mobile_upload_abort(
        upload_id: str,
        request: Request,
        _documented_bearer: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(_MOBILE_BEARER_SCHEME),
        ],
    ):
        manager = _uploads_ready()
        idempotency_key = _single_idempotency_key(request)
        try:
            context = _upload_bearer(request)
            with credential_mutation_guard.admit(context):
                manager.abort(context.user.id, upload_id, idempotency_key)
        except MobileAuthError:
            raise
        except CredentialMutationRejected as exc:
            raise mobile_bearer_unauthorized() from exc
        except Exception as exc:
            raise _map_upload_error(exc) from exc
        return Response(status_code=204, headers=no_store)

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

    def _require_review_exposure() -> None:
        if not review_auth_service.exposed_at_startup():
            raise MobileAPIHTTPError(
                404,
                "not_found",
                "Review authentication is not enabled.",
                headers=no_store,
            )

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
        _require_review_exposure()
        identity = _review_identity(request)
        _require_review_lane()
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
        _require_review_exposure()
        identity = _review_identity(request)
        _require_review_lane()
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

    @app.get(
        "/api/v1/devices",
        name=MOBILE_DEVICES_LIST_ROUTE_NAME,
        response_model=DeviceListResponse,
        responses={401: {"model": APIError}, 404: {"model": APIError}},
        openapi_extra=read_security,
    )
    def mobile_devices_list(request: Request):
        # Default deny precedes even bearer parsing so a disabled flag never
        # authenticates, reads, or writes device state.
        if not device_management_enabled:
            raise MobileAPIHTTPError(
                404,
                "not_found",
                "Mobile device management is not enabled.",
                headers=no_store,
            )
        context = require_mobile_bearer(
            request,
            users,
            require_account,
            review_auth_admission,
            credential_mutation_guard,
        )
        response = DeviceListResponse(
            devices=[
                MobileTokenMetadata(
                    selector=token.selector,
                    label=token.label,
                    created_at=token.created_at,
                    last_used_at=token.last_used_at,
                    expires_at=token.expires_at,
                    revoked_at=token.revoked_at,
                    active=token.active,
                )
                for token in users.list_mobile_api_tokens(context.user.id)
            ]
        )
        return JSONResponse(response.model_dump(mode="json"), headers=no_store)

    @app.delete(
        "/api/v1/devices/{selector}",
        name=MOBILE_DEVICE_REVOKE_ROUTE_NAME,
        status_code=204,
        responses={
            202: {"model": NativeSignOutPendingResponse},
            400: {"model": APIError},
            401: {"model": APIError},
            404: {"model": APIError},
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
            ],
        },
    )
    def mobile_device_revoke(
        selector: str,
        request: Request,
        _documented_bearer: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(_MOBILE_BEARER_SCHEME),
        ],
    ):
        # Default deny precedes bearer parsing, body reads, and every write.
        if not device_management_enabled:
            raise MobileAPIHTTPError(
                404,
                "not_found",
                "Mobile device management is not enabled.",
                headers=no_store,
            )
        raw_token = mobile_bearer_token(request)
        if raw_token is None:
            raise MobileAuthError(
                "bearer_required", "A mobile access token is required."
            )
        idempotency_values = request.headers.getlist("idempotency-key")
        if len(idempotency_values) != 1:
            raise MobileAPIHTTPError(
                400,
                "invalid_idempotency_key",
                "Invalid Idempotency-Key.",
                headers=no_store,
            )
        try:
            result = device_revoke_service.revoke(
                raw_token,
                selector,
                idempotency_values[0],
            )
        except MobileSignOutInvalidRequest as exc:
            raise MobileAPIHTTPError(
                400,
                "invalid_idempotency_key",
                "Invalid Idempotency-Key.",
                headers=no_store,
            ) from exc
        except MobileDeviceRevokeNotFound as exc:
            raise MobileAPIHTTPError(
                404,
                "not_found",
                "Mobile device not found.",
                headers=no_store,
            ) from exc
        except MobileSignOutUnauthorized:
            raise mobile_bearer_unauthorized()
        except MobileSignOutUnavailable as exc:
            raise MobileAPIHTTPError(
                503,
                "device_revoke_unavailable",
                "Device revocation is temporarily unavailable.",
                retryable=True,
                headers=no_store,
            ) from exc
        if result.pending:
            pending = NativeSignOutPendingResponse(
                status="pending",
                retry_after_seconds=1,
            )
            return JSONResponse(
                pending.model_dump(mode="json"),
                status_code=202,
                headers={**no_store, "Retry-After": "1"},
            )
        return Response(status_code=204, headers=no_store)
