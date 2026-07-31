"""Feature-gated Shopify Customer Account authorization-code integration.

This module is intentionally separate from the Admin GraphQL customer bridge:

* Shopify Customer Accounts proves sign-in and account recovery.
* The existing Admin bridge owns only durable customer-record reconciliation.
* CaddieInsight continues to own golfer preferences, swing videos, reports,
  practice history, and recommendations.

Nothing here runs unless ``SHOPIFY_CUSTOMER_ACCOUNTS_ENABLED=true`` and every
required configuration value is present.  The app never copies a Shopify
password and never falls back to matching an app user by email during login.

Shopify documentation requires endpoint discovery from the storefront domain;
the implementation uses the discovered OAuth and Customer Account GraphQL
URLs rather than pinning an endpoint or version.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit, urlunsplit

from .identity import normalize_customer_id

DEFAULT_TIMEOUT_SECONDS = 10.0
_MAX_RESPONSE_BYTES = 1_000_000
_MAX_CODE_LENGTH = 4096
_MAX_TOKEN_LENGTH = 16_384
_ALLOWED_REDIRECT_PATH = "/auth/shopify/callback"
_OAUTH_SCOPE = "openid email customer-account-api:full"

# Shopify Customer Account API 2026-07:
# https://shopify.dev/docs/api/customer/latest
CURRENT_CUSTOMER_QUERY = """
query CurrentCustomer {
  customer {
    id
    emailAddress {
      emailAddress
    }
  }
}
""".strip()


class ShopifyCustomerAccountError(RuntimeError):
    """Base error with a safe public message; never include tokens or PII."""


class ShopifyCustomerAccountConfigurationError(ShopifyCustomerAccountError):
    pass


class ShopifyCustomerAccountProtocolError(ShopifyCustomerAccountError):
    pass


class _Response(Protocol):
    def read(self, amount: int = -1) -> bytes: ...

    def __enter__(self) -> "_Response": ...

    def __exit__(self, *args: object) -> object: ...


HttpOpen = Callable[..., _Response]


def _required(env: Mapping[str, str], name: str) -> str:
    value = str(env.get(name) or "").strip()
    if not value:
        raise ShopifyCustomerAccountConfigurationError(
            "Shopify Customer Accounts is enabled but incomplete."
        )
    return value


def _https_url(value: str, *, label: str) -> str:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ShopifyCustomerAccountConfigurationError(
            f"Invalid Shopify Customer Account {label}."
        ) from None
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
    ):
        raise ShopifyCustomerAccountConfigurationError(
            f"Invalid Shopify Customer Account {label}."
        )
    netloc = hostname.lower()
    if port is not None and port != 443:
        netloc = f"{netloc}:{port}"
    return urlunsplit(("https", netloc, parsed.path or "/", parsed.query, ""))


def _storefront_domain(value: str) -> str:
    raw = value.strip().lower().removeprefix("https://").removeprefix("http://")
    if not raw or "/" in raw or "@" in raw or len(raw) > 255:
        raise ShopifyCustomerAccountConfigurationError(
            "Invalid Shopify Customer Account storefront domain."
        )
    return raw


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    netloc = parsed.hostname or ""
    if parsed.port and parsed.port != 443:
        netloc = f"{netloc}:{parsed.port}"
    return f"{parsed.scheme}://{netloc}"


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def new_pkce_verifier() -> str:
    """Return an RFC 7636-compatible verifier without base64 padding."""

    return secrets.token_urlsafe(48).rstrip("=")


@dataclass(frozen=True)
class CustomerAccountSettings:
    storefront_domain: str
    client_id: str
    client_secret: str
    redirect_uri: str
    public_origin: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None
    ) -> "CustomerAccountSettings | None":
        environment = os.environ if env is None else env
        enabled = str(
            environment.get("SHOPIFY_CUSTOMER_ACCOUNTS_ENABLED") or ""
        ).strip().lower()
        if enabled in ("", "0", "false", "no", "off"):
            return None
        if enabled not in ("1", "true", "yes", "on"):
            raise ShopifyCustomerAccountConfigurationError(
                "Shopify Customer Accounts enablement is invalid."
            )
        storefront_domain = _storefront_domain(
            _required(environment, "SHOPIFY_CUSTOMER_ACCOUNT_STOREFRONT_DOMAIN")
        )
        public_base = _https_url(
            _required(environment, "PUBLIC_BASE_URL"), label="public base URL"
        ).rstrip("/")
        redirect_uri = _https_url(
            _required(environment, "SHOPIFY_CUSTOMER_ACCOUNT_REDIRECT_URI"),
            label="redirect URI",
        )
        expected_redirect = public_base + _ALLOWED_REDIRECT_PATH
        if not hmac.compare_digest(redirect_uri, expected_redirect):
            raise ShopifyCustomerAccountConfigurationError(
                "Shopify Customer Account redirect URI must be the public callback URL."
            )
        try:
            timeout_seconds = float(
                environment.get("SHOPIFY_CUSTOMER_ACCOUNT_TIMEOUT_SECONDS")
                or DEFAULT_TIMEOUT_SECONDS
            )
        except (TypeError, ValueError):
            raise ShopifyCustomerAccountConfigurationError(
                "Shopify Customer Account timeout is invalid."
            ) from None
        if not 1 <= timeout_seconds <= 30:
            raise ShopifyCustomerAccountConfigurationError(
                "Shopify Customer Account timeout is invalid."
            )
        return cls(
            storefront_domain=storefront_domain,
            client_id=_required(environment, "SHOPIFY_CUSTOMER_ACCOUNT_CLIENT_ID"),
            client_secret=_required(
                environment, "SHOPIFY_CUSTOMER_ACCOUNT_CLIENT_SECRET"
            ),
            redirect_uri=redirect_uri,
            public_origin=_origin(public_base),
            timeout_seconds=timeout_seconds,
        )


@dataclass(frozen=True)
class CustomerAccountIdentity:
    """Provider-proven mapping; email is informational and never a join key."""

    subject: str
    customer_id: str
    email: str | None
    id_token: str
    expires_at: float


class ShopifyCustomerAccountClient:
    """Small dependency-free Customer Account OAuth + identity client."""

    def __init__(
        self,
        settings: CustomerAccountSettings,
        *,
        opener: HttpOpen = urllib.request.urlopen,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self._opener = opener
        self._now = now

    def _request_json(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={"User-Agent": "CaddieInsight/1.0"} | dict(headers or {}),
        )
        try:
            with self._opener(request, timeout=self.settings.timeout_seconds) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ShopifyCustomerAccountProtocolError(
                "Shopify Customer Accounts is temporarily unavailable."
            ) from exc
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ShopifyCustomerAccountProtocolError(
                "Shopify Customer Accounts returned an oversized response."
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ShopifyCustomerAccountProtocolError(
                "Shopify Customer Accounts returned an invalid response."
            ) from exc
        if not isinstance(payload, dict):
            raise ShopifyCustomerAccountProtocolError(
                "Shopify Customer Accounts returned an invalid response."
            )
        return payload

    def _discovery_url(self, path: str) -> str:
        return f"https://{self.settings.storefront_domain}{path}"

    @staticmethod
    def _discovered_endpoint(payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str):
            raise ShopifyCustomerAccountProtocolError(
                "Shopify Customer Accounts returned an incomplete configuration."
            )
        # Discovery is fetched from the merchant-configured storefront.  It
        # may point to a Customer Accounts vanity domain, so require HTTPS but
        # do not incorrectly require the hostname to equal the storefront.
        try:
            parsed = urlsplit(value)
        except ValueError:
            parsed = None
        if (
            parsed is None
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ShopifyCustomerAccountProtocolError(
                "Shopify Customer Accounts returned an unsafe endpoint."
            )
        return value

    def discover_openid(self) -> dict[str, Any]:
        payload = self._request_json(
            self._discovery_url("/.well-known/openid-configuration")
        )
        for key in ("authorization_endpoint", "token_endpoint", "end_session_endpoint"):
            self._discovered_endpoint(payload, key)
        return payload

    def discover_customer_api(self) -> dict[str, Any]:
        payload = self._request_json(
            self._discovery_url("/.well-known/customer-account-api")
        )
        self._discovered_endpoint(payload, "graphql_api")
        return payload

    def authorization_url(self, *, state: str, nonce: str, verifier: str) -> str:
        openid = self.discover_openid()
        endpoint = self._discovered_endpoint(openid, "authorization_endpoint")
        return endpoint + ("&" if "?" in endpoint else "?") + urlencode(
            {
                "scope": _OAUTH_SCOPE,
                "client_id": self.settings.client_id,
                "response_type": "code",
                "redirect_uri": self.settings.redirect_uri,
                "state": state,
                "nonce": nonce,
                # Sending PKCE for this confidential web client provides an
                # additional binding between the start and callback routes.
                "code_challenge": _pkce_challenge(verifier),
                "code_challenge_method": "S256",
            }
        )

    @staticmethod
    def _id_token_payload(id_token: str) -> dict[str, Any]:
        pieces = id_token.split(".")
        if len(pieces) != 3:
            raise ShopifyCustomerAccountProtocolError(
                "Shopify Customer Account verification failed."
            )
        try:
            encoded = pieces[1] + "=" * (-len(pieces[1]) % 4)
            decoded = base64.urlsafe_b64decode(encoded.encode("ascii"))
            payload = json.loads(decoded.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            raise ShopifyCustomerAccountProtocolError(
                "Shopify Customer Account verification failed."
            ) from None
        if not isinstance(payload, dict):
            raise ShopifyCustomerAccountProtocolError(
                "Shopify Customer Account verification failed."
            )
        return payload

    def _exchange_code(
        self,
        *,
        code: str,
        verifier: str,
        expected_nonce: str,
        openid: dict[str, Any],
    ) -> tuple[str, str, float]:
        if not code or len(code) > _MAX_CODE_LENGTH:
            raise ShopifyCustomerAccountProtocolError(
                "Shopify Customer Account authorization failed."
            )
        endpoint = self._discovered_endpoint(openid, "token_endpoint")
        basic = base64.b64encode(
            f"{self.settings.client_id}:{self.settings.client_secret}".encode("utf-8")
        ).decode("ascii")
        encoded = urlencode(
            {
                "grant_type": "authorization_code",
                "client_id": self.settings.client_id,
                "redirect_uri": self.settings.redirect_uri,
                "code": code,
                "code_verifier": verifier,
            }
        ).encode("ascii")
        payload = self._request_json(
            endpoint,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {basic}",
                "Origin": self.settings.public_origin,
            },
            body=encoded,
        )
        access_token = payload.get("access_token")
        id_token = payload.get("id_token")
        expires_in = payload.get("expires_in")
        if (
            not isinstance(access_token, str)
            or not 1 <= len(access_token) <= _MAX_TOKEN_LENGTH
            or not isinstance(id_token, str)
            or not 1 <= len(id_token) <= _MAX_TOKEN_LENGTH
        ):
            raise ShopifyCustomerAccountProtocolError(
                "Shopify Customer Account authorization failed."
            )
        try:
            expires_seconds = float(expires_in)
        except (TypeError, ValueError):
            raise ShopifyCustomerAccountProtocolError(
                "Shopify Customer Account authorization failed."
            ) from None
        if not 1 <= expires_seconds <= 86_400:
            raise ShopifyCustomerAccountProtocolError(
                "Shopify Customer Account authorization failed."
            )
        token_payload = self._id_token_payload(id_token)
        nonce = token_payload.get("nonce")
        audience = token_payload.get("aud")
        expected_audience = (
            audience == self.settings.client_id
            or (
                isinstance(audience, list)
                and self.settings.client_id in audience
            )
        )
        if not (
            isinstance(nonce, str)
            and hmac.compare_digest(nonce, expected_nonce)
            and expected_audience
        ):
            raise ShopifyCustomerAccountProtocolError(
                "Shopify Customer Account verification failed."
            )
        return access_token, id_token, self._now() + expires_seconds

    def _current_customer(self, access_token: str) -> tuple[str, str | None]:
        api = self.discover_customer_api()
        endpoint = self._discovered_endpoint(api, "graphql_api")
        payload = self._request_json(
            endpoint,
            method="POST",
            headers={
                "Content-Type": "application/json",
                # Shopify's Customer Account API docs specify the raw token,
                # not a Bearer-prefixed Authorization value.
                "Authorization": access_token,
                "Origin": self.settings.public_origin,
            },
            body=json.dumps(
                {
                    "operationName": "CurrentCustomer",
                    "query": CURRENT_CUSTOMER_QUERY,
                    "variables": {},
                },
                separators=(",", ":"),
            ).encode("utf-8"),
        )
        if payload.get("errors"):
            raise ShopifyCustomerAccountProtocolError(
                "Shopify Customer Account identity could not be verified."
            )
        data = payload.get("data")
        customer = data.get("customer") if isinstance(data, dict) else None
        if not isinstance(customer, dict) or not isinstance(customer.get("id"), str):
            raise ShopifyCustomerAccountProtocolError(
                "Shopify Customer Account identity could not be verified."
            )
        try:
            customer_id = normalize_customer_id(customer["id"])
        except ValueError:
            customer_id = None
        if customer_id is None:
            raise ShopifyCustomerAccountProtocolError(
                "Shopify Customer Account identity could not be verified."
            )
        email_address = customer.get("emailAddress")
        email = (
            email_address.get("emailAddress").strip().lower()
            if isinstance(email_address, dict)
            and isinstance(email_address.get("emailAddress"), str)
            else None
        )
        return customer["id"], email

    def authenticate_callback(
        self, *, code: str, verifier: str, nonce: str
    ) -> CustomerAccountIdentity:
        """Exchange a code and resolve the customer through Customer Account API.

        The id token nonce/audience check binds the authorization response to
        the request.  The identity itself comes from the Customer Account API
        using the newly issued access token, not from an email claim.
        """

        openid = self.discover_openid()
        access_token, id_token, expires_at = self._exchange_code(
            code=code,
            verifier=verifier,
            expected_nonce=nonce,
            openid=openid,
        )
        subject, email = self._current_customer(access_token)
        customer_id = normalize_customer_id(subject)
        assert customer_id is not None
        return CustomerAccountIdentity(
            subject=subject,
            customer_id=customer_id,
            email=email,
            id_token=id_token,
            expires_at=expires_at,
        )

    def logout_url(self, *, id_token: str) -> str:
        """Build the provider logout redirect for an active browser session."""

        openid = self.discover_openid()
        endpoint = self._discovered_endpoint(openid, "end_session_endpoint")
        return endpoint + ("&" if "?" in endpoint else "?") + urlencode(
            {
                "id_token_hint": id_token,
                "post_logout_redirect_uri": self.settings.public_origin + "/",
            }
        )
