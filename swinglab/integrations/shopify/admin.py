"""Server-only Shopify Admin GraphQL customer synchronization.

The Storefront catalog client deliberately remains separate. This module uses
either Dev Dashboard client credentials or a legacy/admin-created-app access
token only on the backend. The Admin API has its own canonical host and
explicitly configured version, ``SHOPIFY_ADMIN_STORE_DOMAIN`` and
``SHOPIFY_ADMIN_API_VERSION``.

Dev Dashboard apps exchange ``SHOPIFY_ADMIN_CLIENT_ID`` and
``SHOPIFY_ADMIN_CLIENT_SECRET`` for a short-lived token using Shopify's client
credentials grant. ``SHOPIFY_ADMIN_ACCESS_TOKEN`` remains a mutually exclusive
static-token mode for legacy/admin-created apps.

The two operations are intentionally narrow:

* ``customerByIdentifier`` finds an existing customer by normalized email.
* ``customerSet`` upserts by email or updates an already-linked customer by ID.

Both operations were validated against Shopify Admin GraphQL 2026-07. They
require ``read_customers`` and ``write_customers`` as applicable.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

from .identity import customer_gid, normalize_customer_id

DEFAULT_API_VERSION = "2026-07"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_BASE_SECONDS = 0.5
DEFAULT_RETRY_MAX_SECONDS = 30.0
DEFAULT_TOKEN_REFRESH_MARGIN_SECONDS = 60.0

_MAX_RESPONSE_BYTES = 1_000_000
_VERSION_RE = re.compile(r"^\d{4}-(?:01|04|07|10)$")
_MYSHOPIFY_DOMAIN_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.myshopify\.com$"
)
_SHOP_GID_RE = re.compile(r"^gid://shopify/Shop/[1-9]\d*$")
_SAFE_CODE_RE = re.compile(r"^[A-Z0-9_]+$")
_SAFE_FIELD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_RETRYABLE_GRAPHQL_ERROR_CODES = frozenset(
    {
        "INTERNAL_SERVER_ERROR",
        "THROTTLED",
    }
)

# https://shopify.dev/docs/api/admin-graphql/2026-07/queries/shop
VERIFY_SHOP_ACCESS_QUERY = """
query VerifyShopAccess {
  shop {
    id
  }
}
""".strip()

# https://shopify.dev/docs/api/admin-graphql/2026-07/queries/customerByIdentifier
CUSTOMER_BY_IDENTIFIER_QUERY = """
query CustomerByEmail($identifier: CustomerIdentifierInput!) {
  customerByIdentifier(identifier: $identifier) {
    id
  }
}
""".strip()

# https://shopify.dev/docs/api/admin-graphql/2026-07/mutations/customerSet
CUSTOMER_SET_MUTATION = """
mutation SetCustomer(
  $identifier: CustomerSetIdentifiers
  $input: CustomerSetInput!
) {
  customerSet(identifier: $identifier, input: $input) {
    customer {
      id
    }
    userErrors {
      code
      field
    }
  }
}
""".strip()


class ShopifyAdminError(RuntimeError):
    """Base error whose public text is safe for logs and persisted status."""

    def __init__(self, safe_summary: str):
        self.safe_summary = safe_summary
        super().__init__(safe_summary)


class ShopifyAdminConfigurationError(ShopifyAdminError):
    """Missing or invalid server-side Admin API configuration."""


@dataclass(frozen=True)
class ShopifyAdminUserIssue:
    """PII-free projection of one ``customerSet`` user error."""

    code: str
    field: tuple[str, ...]


class ShopifyAdminUserError(ShopifyAdminError):
    """Customer input was invalid or Shopify rejected the mutation."""

    def __init__(
        self,
        issues: tuple[ShopifyAdminUserIssue, ...],
        safe_summary: str = "Shopify rejected the customer data.",
    ):
        self.issues = issues
        super().__init__(safe_summary)


class ShopifyAdminTransportError(ShopifyAdminError):
    """The Admin API request or response failed outside mutation user errors."""

    def __init__(
        self,
        safe_summary: str,
        *,
        retryable: bool,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ):
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(safe_summary)


@dataclass(frozen=True)
class TransportResponse:
    """Small transport-neutral HTTP response used by the injectable client."""

    status_code: int
    headers: Mapping[str, str]
    body: bytes


class ShopifyAdminTransport(Protocol):
    def __call__(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> TransportResponse: ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forward Admin credentials to a redirect target."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _default_transport(
    *,
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout_seconds: float,
) -> TransportResponse:
    try:
        request = urllib.request.Request(
            url,
            data=body,
            headers=dict(headers),
            method="POST",
        )
        opener = urllib.request.build_opener(_NoRedirectHandler())
        with opener.open(request, timeout=timeout_seconds) as response:
            response_body = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(response_body) > _MAX_RESPONSE_BYTES:
                raise ShopifyAdminTransportError(
                    "Shopify Admin API returned an oversized response.",
                    retryable=False,
                    status_code=getattr(response, "status", None),
                )
            return TransportResponse(
                status_code=int(getattr(response, "status", 200)),
                headers=dict(response.headers.items()),
                body=response_body,
            )
    except ValueError:
        raise ShopifyAdminTransportError(
            "Shopify Admin API request configuration is invalid.",
            retryable=False,
        ) from None
    except urllib.error.HTTPError as exc:
        response_body = exc.read(_MAX_RESPONSE_BYTES + 1)
        if len(response_body) > _MAX_RESPONSE_BYTES:
            response_body = b""
        return TransportResponse(
            status_code=int(exc.code),
            headers=dict(exc.headers.items()) if exc.headers else {},
            body=response_body,
        )


def _normalize_email(email: str) -> str:
    if not isinstance(email, str):
        raise ShopifyAdminUserError(
            (ShopifyAdminUserIssue("INVALID_EMAIL", ("email",)),)
        )
    normalized = email.strip().lower()
    if (
        not normalized
        or len(normalized) > 254
        or normalized.count("@") != 1
        or "." not in normalized.rsplit("@", 1)[-1]
        or any(character.isspace() for character in normalized)
    ):
        raise ShopifyAdminUserError(
            (ShopifyAdminUserIssue("INVALID_EMAIL", ("email",)),)
        )
    return normalized


def _normalize_domain(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        raise ShopifyAdminConfigurationError(
            "Shopify Admin API store domain is not configured."
        )
    try:
        parsed = urlsplit(
            raw if "://" in raw else f"//{raw}",
            scheme="https",
        )
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ShopifyAdminConfigurationError(
            "Shopify Admin API store domain is invalid."
        ) from None
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port is not None
    ):
        raise ShopifyAdminConfigurationError(
            "Shopify Admin API store domain is invalid."
        )
    hostname = hostname.lower().rstrip(".")
    if (
        len(hostname) > 253
        or _MYSHOPIFY_DOMAIN_RE.fullmatch(hostname) is None
    ):
        raise ShopifyAdminConfigurationError(
            "Shopify Admin API requires the canonical myshopify.com store domain."
        )
    return hostname


def _positive_finite(
    value: float | int,
    *,
    field: str,
    allow_zero: bool = False,
) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError):
        converted = math.nan
    if not math.isfinite(converted) or (
        converted < 0 if allow_zero else converted <= 0
    ):
        raise ShopifyAdminConfigurationError(
            f"Shopify Admin API {field} is invalid."
        )
    return converted


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value)
    return None


def _retry_after_seconds(
    headers: Mapping[str, str],
    *,
    now: datetime | None = None,
) -> float | None:
    raw = _header(headers, "Retry-After")
    if raw is None:
        return None
    raw = raw.strip()
    try:
        seconds = float(raw)
    except ValueError:
        try:
            target = parsedate_to_datetime(raw)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            current = now or datetime.now(timezone.utc)
            seconds = (target - current).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


def _safe_user_issues(raw_errors: Any) -> tuple[ShopifyAdminUserIssue, ...]:
    if not isinstance(raw_errors, list):
        return ()
    issues: list[ShopifyAdminUserIssue] = []
    for raw in raw_errors:
        if not isinstance(raw, Mapping):
            issues.append(ShopifyAdminUserIssue("UNKNOWN", ()))
            continue
        raw_code = str(raw.get("code") or "UNKNOWN").upper()
        code = raw_code if _SAFE_CODE_RE.fullmatch(raw_code) else "UNKNOWN"
        raw_field = raw.get("field")
        field = ()
        if isinstance(raw_field, list):
            field = tuple(
                str(part)
                for part in raw_field
                if _SAFE_FIELD_RE.fullmatch(str(part))
            )
        issues.append(ShopifyAdminUserIssue(code, field))
    return tuple(issues)


def _graphql_error_codes(raw_errors: Any) -> tuple[str, ...]:
    """Return sanitized codes only when every top-level error has one.

    Retry classification is intentionally allowlist-only. A malformed error,
    an unknown code, or a mixture containing any terminal code must not be
    retried merely because another item happens to be transient.
    """

    if not isinstance(raw_errors, list) or not raw_errors:
        return ()
    codes: list[str] = []
    for raw_error in raw_errors:
        if not isinstance(raw_error, Mapping):
            return ()
        extensions = raw_error.get("extensions")
        if not isinstance(extensions, Mapping):
            return ()
        raw_code = str(extensions.get("code") or "").upper()
        if _SAFE_CODE_RE.fullmatch(raw_code) is None:
            return ()
        codes.append(raw_code)
    return tuple(codes)


def _optional_credential(
    value: str | None,
    *,
    field: str,
) -> str | None:
    """Normalize a backend credential without ever including it in an error."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise ShopifyAdminConfigurationError(
            f"Shopify Admin API {field} is invalid."
        )
    credential = value.strip()
    if not credential:
        return None
    if len(credential) > 1024 or any(
        ord(character) < 33 or ord(character) > 126
        for character in credential
    ):
        raise ShopifyAdminConfigurationError(
            f"Shopify Admin API {field} is invalid."
        )
    return credential


class ShopifyAdminClient:
    """Narrow, retry-safe Admin GraphQL client for customer identity sync."""

    def __init__(
        self,
        *,
        store_domain: str,
        access_token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        api_version: str = DEFAULT_API_VERSION,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_base_seconds: float = DEFAULT_RETRY_BASE_SECONDS,
        retry_max_seconds: float = DEFAULT_RETRY_MAX_SECONDS,
        token_refresh_margin_seconds: float = (
            DEFAULT_TOKEN_REFRESH_MARGIN_SECONDS
        ),
        transport: ShopifyAdminTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.store_domain = _normalize_domain(store_domain)
        token = _optional_credential(access_token, field="access token")
        normalized_client_id = _optional_credential(
            client_id,
            field="client ID",
        )
        normalized_client_secret = _optional_credential(
            client_secret,
            field="client secret",
        )
        if token and (normalized_client_id or normalized_client_secret):
            raise ShopifyAdminConfigurationError(
                "Shopify Admin API authentication configuration is ambiguous."
            )
        if bool(normalized_client_id) != bool(normalized_client_secret):
            raise ShopifyAdminConfigurationError(
                "Shopify Admin API client credentials are incomplete."
            )
        if not token and not normalized_client_id:
            raise ShopifyAdminConfigurationError(
                "Shopify Admin API authentication credentials are not configured."
            )
        version = (api_version or "").strip()
        if not version:
            raise ShopifyAdminConfigurationError(
                "Shopify Admin API version is not configured."
            )
        if not _VERSION_RE.fullmatch(version):
            raise ShopifyAdminConfigurationError(
                "Shopify Admin API version is invalid."
            )
        if isinstance(max_attempts, bool):
            attempts = 0
        else:
            try:
                attempts = int(max_attempts)
            except (TypeError, ValueError):
                attempts = 0
        if attempts < 1 or attempts != max_attempts:
            raise ShopifyAdminConfigurationError(
                "Shopify Admin API max attempts is invalid."
            )
        timeout = _positive_finite(
            timeout_seconds,
            field="request timeout",
        )
        retry_base = _positive_finite(
            retry_base_seconds,
            field="retry base",
            allow_zero=True,
        )
        retry_max = _positive_finite(
            retry_max_seconds,
            field="retry maximum",
            allow_zero=True,
        )
        if retry_max < retry_base:
            raise ShopifyAdminConfigurationError(
                "Shopify Admin API retry maximum is invalid."
            )
        refresh_margin = _positive_finite(
            token_refresh_margin_seconds,
            field="token refresh margin",
            allow_zero=True,
        )
        if not callable(sleep):
            raise ShopifyAdminConfigurationError(
                "Shopify Admin API sleep function is invalid."
            )
        if not callable(clock):
            raise ShopifyAdminConfigurationError(
                "Shopify Admin API clock function is invalid."
            )

        self.api_version = version
        self.auth_mode = (
            "static_access_token" if token else "client_credentials"
        )
        self.timeout_seconds = timeout
        self.max_attempts = attempts
        self.retry_base_seconds = retry_base
        self.retry_max_seconds = retry_max
        self.token_refresh_margin_seconds = refresh_margin
        self.endpoint = (
            f"https://{self.store_domain}/admin/api/{version}/graphql.json"
        )
        self.token_endpoint = (
            f"https://{self.store_domain}/admin/oauth/access_token"
        )
        self._static_access_token = token
        self._client_id = normalized_client_id
        self._client_secret = normalized_client_secret
        self._cached_access_token: str | None = None
        self._cached_token_expires_at = 0.0
        self._token_lock = threading.Lock()
        self._transport = transport or _default_transport
        self._sleep = sleep
        self._clock = clock

    @classmethod
    def from_env(
        cls,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_base_seconds: float = DEFAULT_RETRY_BASE_SECONDS,
        retry_max_seconds: float = DEFAULT_RETRY_MAX_SECONDS,
        token_refresh_margin_seconds: float = (
            DEFAULT_TOKEN_REFRESH_MARGIN_SECONDS
        ),
        transport: ShopifyAdminTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        environ: Mapping[str, str] | None = None,
    ) -> "ShopifyAdminClient":
        env = os.environ if environ is None else environ
        return cls(
            store_domain=env.get("SHOPIFY_ADMIN_STORE_DOMAIN", ""),
            access_token=env.get("SHOPIFY_ADMIN_ACCESS_TOKEN", ""),
            client_id=env.get("SHOPIFY_ADMIN_CLIENT_ID", ""),
            client_secret=env.get("SHOPIFY_ADMIN_CLIENT_SECRET", ""),
            api_version=env.get("SHOPIFY_ADMIN_API_VERSION", ""),
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            retry_base_seconds=retry_base_seconds,
            retry_max_seconds=retry_max_seconds,
            token_refresh_margin_seconds=token_refresh_margin_seconds,
            transport=transport,
            sleep=sleep,
            clock=clock,
        )

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(store_domain={self.store_domain!r}, "
            f"api_version={self.api_version!r}, "
            f"auth_mode={self.auth_mode!r}, credentials='<redacted>')"
        )

    def _now(self) -> float:
        try:
            current = float(self._clock())
        except (TypeError, ValueError, OverflowError):
            current = math.nan
        if not math.isfinite(current):
            raise ShopifyAdminConfigurationError(
                "Shopify Admin API clock function returned an invalid value."
            )
        return current

    def _get_access_token(self) -> str:
        if self._static_access_token is not None:
            return self._static_access_token

        now = self._now()
        cached = self._cached_access_token
        if (
            cached is not None
            and now
            < (
                self._cached_token_expires_at
                - self.token_refresh_margin_seconds
            )
        ):
            return cached

        # Keep the network exchange inside the lock. A second worker rechecks
        # the cache after the first refresh so concurrent jobs make one token
        # request rather than stampeding Shopify.
        with self._token_lock:
            now = self._now()
            cached = self._cached_access_token
            if (
                cached is not None
                and now
                < (
                    self._cached_token_expires_at
                    - self.token_refresh_margin_seconds
                )
            ):
                return cached

            token, expires_in = self._exchange_client_credentials_token()
            acquired_at = self._now()
            self._cached_access_token = token
            self._cached_token_expires_at = acquired_at + expires_in
            return token

    def _invalidate_client_credentials_token(
        self,
        rejected_token: str,
    ) -> None:
        """Forget only the cached token that Shopify actually rejected."""

        if self._static_access_token is not None:
            return
        with self._token_lock:
            if self._cached_access_token == rejected_token:
                self._cached_access_token = None
                self._cached_token_expires_at = 0.0

    def _exchange_client_credentials_token(self) -> tuple[str, float]:
        # https://shopify.dev/docs/apps/build/dev-dashboard/get-api-access-tokens
        client_id = self._client_id
        client_secret = self._client_secret
        if client_id is None or client_secret is None:
            raise ShopifyAdminConfigurationError(
                "Shopify Admin API client credentials are not configured."
            )

        body = urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            }
        ).encode("ascii")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        last_status: int | None = None

        for attempt_index in range(self.max_attempts):
            try:
                response = self._transport(
                    url=self.token_endpoint,
                    headers=headers,
                    body=body,
                    timeout_seconds=self.timeout_seconds,
                )
            except ShopifyAdminTransportError as exc:
                if not exc.retryable:
                    raise ShopifyAdminTransportError(
                        "Shopify Admin API token request failed.",
                        retryable=False,
                        status_code=exc.status_code,
                    ) from None
                if (
                    attempt_index + 1 >= self.max_attempts
                    or (
                        exc.retry_after_seconds is not None
                        and exc.retry_after_seconds
                        > self.retry_max_seconds
                    )
                ):
                    raise ShopifyAdminTransportError(
                        "Shopify Admin API token service is temporarily unavailable.",
                        retryable=True,
                        status_code=exc.status_code,
                        retry_after_seconds=exc.retry_after_seconds,
                    ) from None
                retry_headers = (
                    {"Retry-After": str(exc.retry_after_seconds)}
                    if exc.retry_after_seconds is not None
                    else {}
                )
                self._sleep_for_retry(attempt_index, retry_headers)
                continue
            except (TimeoutError, OSError, urllib.error.URLError):
                if attempt_index + 1 >= self.max_attempts:
                    raise ShopifyAdminTransportError(
                        "Shopify Admin API token service is temporarily unavailable.",
                        retryable=True,
                    ) from None
                self._sleep_for_retry(attempt_index, {})
                continue

            try:
                status_code = int(response.status_code)
                response_headers = response.headers
                response_body = response.body
            except (AttributeError, TypeError, ValueError):
                raise ShopifyAdminTransportError(
                    "Shopify Admin API token transport returned an invalid response.",
                    retryable=False,
                ) from None
            last_status = status_code

            if (
                not isinstance(response_body, bytes)
                or len(response_body) > _MAX_RESPONSE_BYTES
            ):
                raise ShopifyAdminTransportError(
                    "Shopify Admin API returned an invalid token response.",
                    retryable=False,
                    status_code=status_code,
                )

            retryable_http = (
                status_code in {408, 429}
                or 500 <= status_code <= 599
            )
            if retryable_http:
                retry_after = _retry_after_seconds(response_headers)
                if (
                    attempt_index + 1 >= self.max_attempts
                    or (
                        retry_after is not None
                        and retry_after > self.retry_max_seconds
                    )
                ):
                    raise ShopifyAdminTransportError(
                        "Shopify Admin API token service is temporarily unavailable.",
                        retryable=True,
                        status_code=status_code,
                        retry_after_seconds=retry_after,
                    )
                self._sleep_for_retry(attempt_index, response_headers)
                continue
            if status_code in {400, 401, 403}:
                raise ShopifyAdminConfigurationError(
                    "Shopify Admin API client credentials were rejected."
                )
            if status_code < 200 or status_code >= 300:
                raise ShopifyAdminTransportError(
                    "Shopify Admin API token request was rejected.",
                    retryable=False,
                    status_code=status_code,
                )

            try:
                decoded = json.loads(response_body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                decoded = None

            token: str | None = None
            expires_in = math.nan
            if isinstance(decoded, Mapping):
                raw_token = decoded.get("access_token")
                if isinstance(raw_token, str):
                    candidate = raw_token
                    if (
                        candidate
                        and len(candidate) <= 1024
                        and all(
                            33 <= ord(character) <= 126
                            for character in candidate
                        )
                    ):
                        token = candidate
                raw_expires_in = decoded.get("expires_in")
                if not isinstance(raw_expires_in, bool):
                    try:
                        expires_in = float(raw_expires_in)
                    except (TypeError, ValueError, OverflowError):
                        expires_in = math.nan

            if token is not None and math.isfinite(expires_in) and expires_in > 0:
                return token, expires_in

            if attempt_index + 1 >= self.max_attempts:
                raise ShopifyAdminTransportError(
                    "Shopify Admin API returned an invalid token response.",
                    retryable=True,
                    status_code=status_code,
                )
            self._sleep_for_retry(attempt_index, response_headers)

        raise ShopifyAdminTransportError(
            "Shopify Admin API token service is temporarily unavailable.",
            retryable=True,
            status_code=last_status,
        )

    def find_customer_by_email(self, email: str) -> str | None:
        normalized = _normalize_email(email)
        data = self._execute(
            operation_name="CustomerByEmail",
            query=CUSTOMER_BY_IDENTIFIER_QUERY,
            variables={"identifier": {"emailAddress": normalized}},
        )
        customer = data.get("customerByIdentifier")
        if customer is None:
            return None
        if not isinstance(customer, Mapping):
            raise ShopifyAdminTransportError(
                "Shopify Admin API returned an invalid customer response.",
                retryable=False,
            )
        try:
            customer_id = normalize_customer_id(customer.get("id"))
        except ValueError:
            customer_id = None
        if customer_id is None:
            raise ShopifyAdminTransportError(
                "Shopify Admin API returned an invalid customer identifier.",
                retryable=False,
            )
        return customer_id

    def verify_store_access(self) -> str:
        """Authenticate the configured store and return its stable shop GID."""

        data = self._execute(
            operation_name="VerifyShopAccess",
            query=VERIFY_SHOP_ACCESS_QUERY,
            variables={},
        )
        shop = data.get("shop")
        if not isinstance(shop, Mapping):
            raise ShopifyAdminTransportError(
                "Shopify Admin API returned an invalid shop response.",
                retryable=False,
            )
        shop_id = shop.get("id")
        if not isinstance(shop_id, str) or _SHOP_GID_RE.fullmatch(shop_id) is None:
            raise ShopifyAdminTransportError(
                "Shopify Admin API returned an invalid shop identifier.",
                retryable=False,
            )
        return shop_id

    def set_customer(
        self,
        email: str,
        customer_id: str | int | None = None,
    ) -> str:
        normalized = _normalize_email(email)
        identifier: dict[str, str]
        if customer_id is None:
            identifier = {"email": normalized}
        else:
            try:
                canonical_id = normalize_customer_id(customer_id)
                gid = customer_gid(canonical_id)
            except ValueError:
                canonical_id = gid = None
            if canonical_id is None or gid is None:
                raise ShopifyAdminUserError(
                    (
                        ShopifyAdminUserIssue(
                            "INVALID_CUSTOMER_ID",
                            ("customerId",),
                        ),
                    )
                )
            identifier = {"id": gid}

        data = self._execute(
            operation_name="SetCustomer",
            query=CUSTOMER_SET_MUTATION,
            variables={
                "identifier": identifier,
                "input": {"email": normalized},
            },
        )
        payload = data.get("customerSet")
        if not isinstance(payload, Mapping):
            raise ShopifyAdminTransportError(
                "Shopify Admin API returned an invalid mutation response.",
                retryable=False,
            )
        issues = _safe_user_issues(payload.get("userErrors"))
        if issues:
            raise ShopifyAdminUserError(issues)
        customer = payload.get("customer")
        if not isinstance(customer, Mapping):
            raise ShopifyAdminTransportError(
                "Shopify Admin API did not return a customer.",
                retryable=False,
            )
        try:
            canonical_id = normalize_customer_id(customer.get("id"))
        except ValueError:
            canonical_id = None
        if canonical_id is None:
            raise ShopifyAdminTransportError(
                "Shopify Admin API returned an invalid customer identifier.",
                retryable=False,
            )
        return canonical_id

    def _execute(
        self,
        *,
        operation_name: str,
        query: str,
        variables: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        body = json.dumps(
            {
                "operationName": operation_name,
                "query": query,
                "variables": variables,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        access_token = self._get_access_token()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": access_token,
        }

        last_status: int | None = None
        refreshed_after_unauthorized = False
        for attempt_index in range(self.max_attempts):
            try:
                response = self._transport(
                    url=self.endpoint,
                    headers=headers,
                    body=body,
                    timeout_seconds=self.timeout_seconds,
                )
            except ShopifyAdminTransportError as exc:
                if not exc.retryable:
                    raise
                if (
                    attempt_index + 1 >= self.max_attempts
                    or (
                        exc.retry_after_seconds is not None
                        and exc.retry_after_seconds > self.retry_max_seconds
                    )
                ):
                    raise ShopifyAdminTransportError(
                        "Shopify Admin API is temporarily unavailable.",
                        retryable=True,
                        status_code=exc.status_code,
                        retry_after_seconds=exc.retry_after_seconds,
                    ) from None
                retry_headers = (
                    {"Retry-After": str(exc.retry_after_seconds)}
                    if exc.retry_after_seconds is not None
                    else {}
                )
                self._sleep_for_retry(attempt_index, retry_headers)
                continue
            except (TimeoutError, OSError, urllib.error.URLError):
                if attempt_index + 1 >= self.max_attempts:
                    raise ShopifyAdminTransportError(
                        "Shopify Admin API is temporarily unavailable.",
                        retryable=True,
                    ) from None
                self._sleep_for_retry(attempt_index, {})
                continue

            try:
                status_code = int(response.status_code)
                response_headers = response.headers
                response_body = response.body
            except (AttributeError, TypeError, ValueError):
                raise ShopifyAdminTransportError(
                    "Shopify Admin API transport returned an invalid response.",
                    retryable=False,
                ) from None
            last_status = status_code

            if (
                status_code == 401
                and self._static_access_token is None
                and not refreshed_after_unauthorized
                and attempt_index + 1 < self.max_attempts
            ):
                # A Dev Dashboard client-credentials token can be revoked
                # before its advertised expiry. Invalidate exactly the token
                # that failed, then exchange and replay once. Repeated 401s
                # and legacy static-token 401s remain terminal.
                self._invalidate_client_credentials_token(access_token)
                access_token = self._get_access_token()
                headers = {
                    **headers,
                    "X-Shopify-Access-Token": access_token,
                }
                refreshed_after_unauthorized = True
                continue

            retryable_http = (
                status_code in {408, 429}
                or 500 <= status_code <= 599
            )
            if retryable_http:
                retry_after = _retry_after_seconds(response_headers)
                if (
                    attempt_index + 1 >= self.max_attempts
                    or (
                        retry_after is not None
                        and retry_after > self.retry_max_seconds
                    )
                ):
                    raise ShopifyAdminTransportError(
                        "Shopify Admin API is temporarily unavailable.",
                        retryable=True,
                        status_code=status_code,
                        retry_after_seconds=retry_after,
                    )
                self._sleep_for_retry(attempt_index, response_headers)
                continue
            if status_code < 200 or status_code >= 300:
                raise ShopifyAdminTransportError(
                    "Shopify Admin API request was rejected.",
                    retryable=False,
                    status_code=status_code,
                )

            try:
                decoded = json.loads(response_body)
            except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
                if attempt_index + 1 >= self.max_attempts:
                    raise ShopifyAdminTransportError(
                        "Shopify Admin API returned an invalid response.",
                        retryable=True,
                        status_code=status_code,
                    ) from None
                self._sleep_for_retry(attempt_index, response_headers)
                continue
            if not isinstance(decoded, Mapping):
                raise ShopifyAdminTransportError(
                    "Shopify Admin API returned an invalid response.",
                    retryable=False,
                    status_code=status_code,
                )

            errors = decoded.get("errors")
            if errors:
                error_codes = _graphql_error_codes(errors)
                retryable_graphql = bool(error_codes) and all(
                    code in _RETRYABLE_GRAPHQL_ERROR_CODES
                    for code in error_codes
                )
                if retryable_graphql:
                    retry_after = _retry_after_seconds(response_headers)
                    throttled = all(
                        code == "THROTTLED" for code in error_codes
                    )
                    safe_summary = (
                        "Shopify Admin API is temporarily throttled."
                        if throttled
                        else "Shopify Admin API is temporarily unavailable."
                    )
                    if (
                        attempt_index + 1 >= self.max_attempts
                        or (
                            retry_after is not None
                            and retry_after > self.retry_max_seconds
                        )
                    ):
                        raise ShopifyAdminTransportError(
                            safe_summary,
                            retryable=True,
                            status_code=status_code,
                            retry_after_seconds=retry_after,
                        )
                    self._sleep_for_retry(attempt_index, response_headers)
                    continue
                raise ShopifyAdminTransportError(
                    "Shopify Admin GraphQL request failed.",
                    retryable=False,
                    status_code=status_code,
                )

            data = decoded.get("data")
            if not isinstance(data, Mapping):
                raise ShopifyAdminTransportError(
                    "Shopify Admin API returned an invalid response.",
                    retryable=False,
                    status_code=last_status,
                )
            return data

        # The loop always returns or raises. Keep a safe defensive fallback.
        raise ShopifyAdminTransportError(
            "Shopify Admin API is temporarily unavailable.",
            retryable=True,
            status_code=last_status,
        )

    def _sleep_for_retry(
        self,
        attempt_index: int,
        headers: Mapping[str, str],
    ) -> None:
        exponential = self.retry_base_seconds * (2**attempt_index)
        retry_after = _retry_after_seconds(headers)
        delay = exponential
        if retry_after is not None:
            delay = max(delay, retry_after)
        delay = min(delay, self.retry_max_seconds)
        if delay > 0:
            self._sleep(delay)


__all__ = [
    "CUSTOMER_BY_IDENTIFIER_QUERY",
    "CUSTOMER_SET_MUTATION",
    "DEFAULT_API_VERSION",
    "DEFAULT_TOKEN_REFRESH_MARGIN_SECONDS",
    "ShopifyAdminClient",
    "ShopifyAdminConfigurationError",
    "ShopifyAdminError",
    "ShopifyAdminTransportError",
    "ShopifyAdminUserError",
    "ShopifyAdminUserIssue",
    "TransportResponse",
    "VERIFY_SHOP_ACCESS_QUERY",
]
