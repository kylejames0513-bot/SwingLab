"""Shared native bearer authentication without browser-cookie fallback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, Request

from ..web.users import User, UserStore


@dataclass(frozen=True)
class MobileAuthContext:
    """The account and authentication source resolved for a mobile request."""

    user: User
    via_bearer: bool
    selector: str | None
    auth_epoch: int


class MobileAuthError(HTTPException):
    """A bounded native-auth error that named mobile routes serialize safely."""

    def __init__(
        self,
        code: Literal["bearer_required"],
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        if code != "bearer_required":
            raise ValueError("Unsupported mobile authentication error code.")
        super().__init__(
            401,
            message,
            headers={"WWW-Authenticate": "Bearer"},
        )
        self.mobile_error_code = code
        self.mobile_error_retryable = retryable


def mobile_bearer_unauthorized() -> HTTPException:
    """Return one non-enumerating error for every bad device credential."""

    return HTTPException(
        401,
        "Invalid mobile access token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def mobile_bearer_token(request: Request) -> str | None:
    """Read one strict Bearer token without accepting cookie fallback."""

    authorization = request.headers.get("authorization")
    if authorization is None:
        return None
    scheme, separator, token = authorization.partition(" ")
    if (
        separator != " "
        or scheme.lower() != "bearer"
        or not token
        or " " in token
    ):
        raise mobile_bearer_unauthorized()
    return token


def _cookie_user(request: Request, users: UserStore) -> User | None:
    """Resolve the established browser session without weakening its epoch guard."""

    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = users.get(user_id)
    try:
        session_epoch = int(request.session.get("auth_epoch", 0))
    except (TypeError, ValueError):
        session_epoch = -1
    if user is None or session_epoch != user.auth_epoch:
        request.session.clear()
        return None
    return user


def resolve_mobile_auth(
    request: Request, users: UserStore, require_account: bool
) -> MobileAuthContext:
    """Resolve bearer-or-cookie auth, never falling back after Authorization."""

    if not require_account:
        raise HTTPException(404, "Account API is not enabled.")
    bearer = mobile_bearer_token(request)
    if bearer is not None:
        principal = users.authenticate_mobile_api_principal(bearer)
        if principal is None:
            raise mobile_bearer_unauthorized()
        return MobileAuthContext(
            user=principal.user,
            via_bearer=True,
            selector=principal.selector,
            auth_epoch=principal.auth_epoch,
        )
    user = _cookie_user(request, users)
    if user is None:
        raise HTTPException(401, "Log in first.")
    return MobileAuthContext(
        user=user,
        via_bearer=False,
        selector=None,
        auth_epoch=user.auth_epoch,
    )


def require_mobile_bearer(
    request: Request, users: UserStore, require_account: bool
) -> MobileAuthContext:
    """Require explicit native bearer auth before an unsafe mobile mutation."""

    if not require_account:
        raise HTTPException(404, "Account API is not enabled.")
    bearer = mobile_bearer_token(request)
    if bearer is None:
        raise MobileAuthError(
            "bearer_required", "A mobile access token is required."
        )
    principal = users.authenticate_mobile_api_principal(bearer)
    if principal is None:
        raise mobile_bearer_unauthorized()
    return MobileAuthContext(
        user=principal.user,
        via_bearer=True,
        selector=principal.selector,
        auth_epoch=principal.auth_epoch,
    )


# Private compatibility for older in-package callers while new routes use the
# deliberately exported strict parser before normal bearer authentication.
_mobile_bearer_token = mobile_bearer_token
