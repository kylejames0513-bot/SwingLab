"""Server-only Shopify Admin GraphQL customer client."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import io
import logging
import threading
import urllib.error
from urllib.parse import parse_qs

import pytest
import swinglab.integrations.shopify.admin as admin_module

from swinglab.integrations.shopify.admin import (
    CUSTOMER_BY_IDENTIFIER_QUERY,
    CUSTOMER_SET_MUTATION,
    DEFAULT_API_VERSION,
    ShopifyAdminClient,
    ShopifyAdminConfigurationError,
    ShopifyAdminTransportError,
    ShopifyAdminUserError,
    TransportResponse,
    VERIFY_SHOP_ACCESS_QUERY,
)


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, **request):
        self.calls.append(request)
        outcome = self.responses.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def response(payload, status=200, headers=None):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return TransportResponse(status, headers or {}, body)


def customer_payload(customer_id="gid://shopify/Customer/7001"):
    return {"data": {"customerByIdentifier": {"id": customer_id}}}


def shop_payload(shop_id="gid://shopify/Shop/123456789"):
    return {"data": {"shop": {"id": shop_id}}}


def set_payload(customer_id="gid://shopify/Customer/7001", errors=None):
    return {
        "data": {
            "customerSet": {
                "customer": {"id": customer_id} if customer_id else None,
                "userErrors": errors or [],
            }
        }
    }


def token_payload(
    access_token="short_lived_admin_token",
    expires_in=86399,
):
    return {
        "access_token": access_token,
        "scope": "read_customers,write_customers",
        "expires_in": expires_in,
    }


def client(transport, sleep=lambda _seconds: None, **kwargs):
    return ShopifyAdminClient(
        store_domain="test-store.myshopify.com",
        access_token="shpat_server_secret",
        transport=transport,
        sleep=sleep,
        **kwargs,
    )


def credential_client(
    transport,
    sleep=lambda _seconds: None,
    clock=lambda: 100.0,
    **kwargs,
):
    return ShopifyAdminClient(
        store_domain="test-store.myshopify.com",
        client_id="dev_dashboard_client_id",
        client_secret="dev_dashboard_client_secret",
        transport=transport,
        sleep=sleep,
        clock=clock,
        **kwargs,
    )


def posted(call):
    return json.loads(call["body"])


def test_from_env_uses_distinct_admin_version_and_redacts_token():
    transport = FakeTransport(response(customer_payload()))
    admin = ShopifyAdminClient.from_env(
        environ={
            "SHOPIFY_ADMIN_STORE_DOMAIN": "https://test-store.myshopify.com/",
            "SHOPIFY_ADMIN_ACCESS_TOKEN": "  shpat_server_secret  ",
            "SHOPIFY_API_VERSION": "2025-10",
            "SHOPIFY_ADMIN_API_VERSION": "2026-04",
        },
        transport=transport,
        sleep=lambda _seconds: None,
    )

    assert admin.find_customer_by_email("a@b.co") == "7001"
    call = transport.calls[0]
    assert call["url"] == (
        "https://test-store.myshopify.com/admin/api/2026-04/graphql.json"
    )
    assert call["timeout_seconds"] == 10
    assert call["headers"]["X-Shopify-Access-Token"] == "shpat_server_secret"
    assert admin.auth_mode == "static_access_token"
    assert "shpat_server_secret" not in repr(admin)
    assert "<redacted>" in repr(admin)


def test_from_env_exchanges_dev_dashboard_credentials_for_token():
    transport = FakeTransport(
        response(token_payload()),
        response(customer_payload()),
    )
    admin = ShopifyAdminClient.from_env(
        environ={
            "SHOPIFY_ADMIN_STORE_DOMAIN": "test-store.myshopify.com",
            "SHOPIFY_ADMIN_CLIENT_ID": "  dev_dashboard_client_id  ",
            "SHOPIFY_ADMIN_CLIENT_SECRET": (
                "  dev_dashboard_client_secret  "
            ),
            "SHOPIFY_ADMIN_API_VERSION": "2026-07",
        },
        transport=transport,
        sleep=lambda _seconds: None,
        clock=lambda: 100.0,
    )

    assert admin.find_customer_by_email("a@b.co") == "7001"
    assert admin.auth_mode == "client_credentials"
    assert len(transport.calls) == 2

    token_call, graphql_call = transport.calls
    assert token_call["url"] == (
        "https://test-store.myshopify.com/admin/oauth/access_token"
    )
    assert token_call["timeout_seconds"] == 10
    assert token_call["headers"] == {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    assert parse_qs(
        token_call["body"].decode("ascii"),
        strict_parsing=True,
    ) == {
        "grant_type": ["client_credentials"],
        "client_id": ["dev_dashboard_client_id"],
        "client_secret": ["dev_dashboard_client_secret"],
    }
    assert graphql_call["headers"]["X-Shopify-Access-Token"] == (
        "short_lived_admin_token"
    )
    rendered = repr(admin)
    for secret in (
        "dev_dashboard_client_id",
        "dev_dashboard_client_secret",
        "short_lived_admin_token",
    ):
        assert secret not in rendered
    assert "<redacted>" in rendered


def test_constructor_default_admin_version_is_2026_07():
    admin = ShopifyAdminClient(
        store_domain="test-store.myshopify.com",
        access_token="secret",
        transport=FakeTransport(response(customer_payload())),
    )
    assert admin.api_version == DEFAULT_API_VERSION == "2026-07"
    assert "/admin/api/2026-07/graphql.json" in admin.endpoint


@pytest.mark.parametrize(
    ("env", "summary"),
    [
        (
            {"SHOPIFY_ADMIN_ACCESS_TOKEN": "secret"},
            "Shopify Admin API store domain is not configured.",
        ),
        (
            {"SHOPIFY_ADMIN_STORE_DOMAIN": "test-store.myshopify.com"},
            (
                "Shopify Admin API authentication credentials are not "
                "configured."
            ),
        ),
        (
            {
                "SHOPIFY_ADMIN_STORE_DOMAIN": "test-store.myshopify.com",
                "SHOPIFY_ADMIN_ACCESS_TOKEN": "secret",
                "SHOPIFY_API_VERSION": "2026-07",
            },
            "Shopify Admin API version is not configured.",
        ),
        (
            {
                "SHOPIFY_ADMIN_STORE_DOMAIN": "test-store.myshopify.com",
                "SHOPIFY_ADMIN_ACCESS_TOKEN": "secret",
                "SHOPIFY_ADMIN_API_VERSION": "   ",
            },
            "Shopify Admin API version is not configured.",
        ),
        (
            {
                "SHOPIFY_ADMIN_STORE_DOMAIN": "test-store.myshopify.com",
                "SHOPIFY_ADMIN_CLIENT_ID": "dev_client_id",
                "SHOPIFY_ADMIN_CLIENT_SECRET": "dev_client_secret",
            },
            "Shopify Admin API version is not configured.",
        ),
    ],
)
def test_missing_configuration_has_safe_summary(env, summary):
    with pytest.raises(ShopifyAdminConfigurationError) as caught:
        ShopifyAdminClient.from_env(environ=env)
    assert caught.value.safe_summary == summary
    assert str(caught.value) == summary


@pytest.mark.parametrize(
    "kwargs",
    [
        {"store_domain": "https://test-store.myshopify.com/path"},
        {"store_domain": "http://test-store.myshopify.com"},
        {"store_domain": "test-store.myshopify.com:not-a-port"},
        {"store_domain": "shop.example.com"},
        {"store_domain": "127.0.0.1"},
        {"api_version": "unstable"},
        {"timeout_seconds": 0},
        {"max_attempts": 0},
        {"max_attempts": 1.5},
        {"retry_base_seconds": -1},
        {"retry_base_seconds": 2, "retry_max_seconds": 1},
        {"token_refresh_margin_seconds": -1},
        {"clock": None},
        {"access_token": "secret\r\nleak"},
    ],
)
def test_invalid_client_settings_are_configuration_errors(kwargs):
    base = {
        "store_domain": "test-store.myshopify.com",
        "access_token": "secret",
    }
    base.update(kwargs)
    with pytest.raises(ShopifyAdminConfigurationError) as caught:
        ShopifyAdminClient(**base)
    assert caught.value.safe_summary
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("kwargs", "summary"),
    [
        (
            {
                "access_token": "legacy_static_token",
                "client_id": "dev_client_id",
                "client_secret": "dev_client_secret",
            },
            "Shopify Admin API authentication configuration is ambiguous.",
        ),
        (
            {"client_id": "dev_client_id"},
            "Shopify Admin API client credentials are incomplete.",
        ),
        (
            {"client_secret": "dev_client_secret"},
            "Shopify Admin API client credentials are incomplete.",
        ),
        (
            {},
            (
                "Shopify Admin API authentication credentials are not "
                "configured."
            ),
        ),
        (
            {
                "client_id": "dev_client_id\nprivate",
                "client_secret": "dev_client_secret",
            },
            "Shopify Admin API client ID is invalid.",
        ),
        (
            {
                "client_id": "dev_client_id",
                "client_secret": "dev_client_secret\nprivate",
            },
            "Shopify Admin API client secret is invalid.",
        ),
    ],
)
def test_authentication_modes_reject_ambiguous_incomplete_or_invalid_config(
    kwargs,
    summary,
):
    with pytest.raises(ShopifyAdminConfigurationError) as caught:
        ShopifyAdminClient(
            store_domain="test-store.myshopify.com",
            **kwargs,
        )
    assert caught.value.safe_summary == summary
    rendered = f"{caught.value!s} {caught.value!r}"
    for secret in (
        "legacy_static_token",
        "dev_client_id",
        "dev_client_secret",
        "private",
    ):
        assert secret not in rendered


def test_default_transport_disables_redirects(monkeypatch):
    captured = {}

    class RedirectingOpener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(
                request.full_url,
                302,
                "Found",
                {"Location": "https://attacker.example/collect"},
                io.BytesIO(b""),
            )

    def build_opener(*handlers):
        captured["handlers"] = handlers
        return RedirectingOpener()

    monkeypatch.setattr(
        admin_module.urllib.request,
        "build_opener",
        build_opener,
    )

    result = admin_module._default_transport(
        url="https://test-store.myshopify.com/admin/api/2026-07/graphql.json",
        headers={"X-Shopify-Access-Token": "secret"},
        body=b"{}",
        timeout_seconds=1,
    )

    assert result.status_code == 302
    assert any(
        isinstance(handler, admin_module._NoRedirectHandler)
        for handler in captured["handlers"]
    )


def test_client_credentials_token_is_cached_and_refreshed_before_expiry():
    now = [100.0]
    transport = FakeTransport(
        response(token_payload("first_short_lived_token", expires_in=120)),
        response(customer_payload()),
        response(customer_payload()),
        response(token_payload("refreshed_short_lived_token", expires_in=120)),
        response(customer_payload()),
    )
    admin = credential_client(
        transport,
        clock=lambda: now[0],
        token_refresh_margin_seconds=60,
    )

    assert admin.find_customer_by_email("first@example.com") == "7001"
    now[0] = 159.0
    assert admin.find_customer_by_email("second@example.com") == "7001"
    now[0] = 160.0
    assert admin.find_customer_by_email("third@example.com") == "7001"

    token_calls = [
        call
        for call in transport.calls
        if call["url"] == admin.token_endpoint
    ]
    graphql_calls = [
        call for call in transport.calls if call["url"] == admin.endpoint
    ]
    assert len(token_calls) == 2
    assert [
        call["headers"]["X-Shopify-Access-Token"]
        for call in graphql_calls
    ] == [
        "first_short_lived_token",
        "first_short_lived_token",
        "refreshed_short_lived_token",
    ]


def test_concurrent_requests_share_one_client_credentials_exchange():
    class BlockingTransport:
        def __init__(self):
            self.calls = []
            self.token_calls = 0
            self.guard = threading.Lock()
            self.token_started = threading.Event()
            self.release_token = threading.Event()

        def __call__(self, **request):
            with self.guard:
                self.calls.append(request)
            if request["url"].endswith("/admin/oauth/access_token"):
                with self.guard:
                    self.token_calls += 1
                self.token_started.set()
                assert self.release_token.wait(timeout=5)
                return response(token_payload())
            return response(customer_payload())

    transport = BlockingTransport()
    admin = credential_client(transport)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(
                admin.find_customer_by_email,
                f"golfer{index}@example.com",
            )
            for index in range(8)
        ]
        assert transport.token_started.wait(timeout=5)
        transport.release_token.set()
        assert [future.result(timeout=5) for future in futures] == [
            "7001"
        ] * 8

    assert transport.token_calls == 1
    assert sum(call["url"] == admin.endpoint for call in transport.calls) == 8


def test_token_exchange_retries_network_and_server_failures_with_bounds():
    sleeps = []
    transport = FakeTransport(
        urllib.error.URLError(
            "failed with dev_dashboard_client_secret"
        ),
        response(
            {"error": "dev_dashboard_client_secret"},
            status=503,
        ),
        response(token_payload()),
        response(customer_payload()),
    )
    admin = credential_client(
        transport,
        sleep=sleeps.append,
        max_attempts=3,
        retry_base_seconds=0.25,
        retry_max_seconds=1,
        timeout_seconds=4,
    )

    assert admin.find_customer_by_email("private@example.com") == "7001"
    assert sleeps == [0.25, 0.5]
    assert [call["timeout_seconds"] for call in transport.calls] == [4] * 4
    assert [call["url"] for call in transport.calls[:3]] == [
        admin.token_endpoint
    ] * 3


def test_token_exchange_honors_bounded_retry_after():
    sleeps = []
    transport = FakeTransport(
        response({}, status=408),
        response({}, status=429, headers={"Retry-After": "7"}),
        response(token_payload()),
        response(customer_payload()),
    )
    admin = credential_client(
        transport,
        sleep=sleeps.append,
        retry_max_seconds=10,
    )

    assert admin.find_customer_by_email("a@b.co") == "7001"
    assert sleeps == [0.5, 7]


def test_client_credentials_401_invalidates_token_and_replays_once():
    transport = FakeTransport(
        response(token_payload("first_short_lived_token")),
        response({}, status=401),
        response(token_payload("refreshed_short_lived_token")),
        response(customer_payload()),
        response(customer_payload()),
    )
    admin = credential_client(transport)

    assert admin.find_customer_by_email("a@b.co") == "7001"
    assert admin.find_customer_by_email("c@d.co") == "7001"

    token_calls = [
        call for call in transport.calls
        if call["url"] == admin.token_endpoint
    ]
    graphql_calls = [
        call for call in transport.calls
        if call["url"] == admin.endpoint
    ]
    assert len(token_calls) == 2
    assert [
        call["headers"]["X-Shopify-Access-Token"]
        for call in graphql_calls
    ] == [
        "first_short_lived_token",
        "refreshed_short_lived_token",
        "refreshed_short_lived_token",
    ]


def test_client_credentials_repeated_401_is_terminal_and_bounded():
    transport = FakeTransport(
        response(token_payload("first_short_lived_token")),
        response({}, status=401),
        response(token_payload("refreshed_short_lived_token")),
        response({}, status=401),
    )
    admin = credential_client(transport)

    with pytest.raises(ShopifyAdminTransportError) as caught:
        admin.find_customer_by_email("a@b.co")

    assert not caught.value.retryable
    assert caught.value.status_code == 401
    assert len(transport.calls) == 4


def test_static_access_token_401_is_terminal_without_refresh():
    transport = FakeTransport(response({}, status=401))
    admin = client(transport)

    with pytest.raises(ShopifyAdminTransportError) as caught:
        admin.find_customer_by_email("a@b.co")

    assert not caught.value.retryable
    assert caught.value.status_code == 401
    assert len(transport.calls) == 1


def test_graphql_http_408_retries_with_same_idempotent_request():
    sleeps = []
    transport = FakeTransport(
        response({}, status=408),
        response(customer_payload()),
    )
    admin = client(transport, sleep=sleeps.append)

    assert admin.find_customer_by_email("a@b.co") == "7001"
    assert sleeps == [0.5]
    assert len(transport.calls) == 2
    assert transport.calls[0]["body"] == transport.calls[1]["body"]


def test_token_exchange_long_retry_after_is_deferred_and_redacted():
    transport = FakeTransport(
        response(
            {
                "error": (
                    "dev_dashboard_client_secret for private@example.com"
                )
            },
            status=429,
            headers={"Retry-After": "120"},
        )
    )
    sleeps = []
    admin = credential_client(
        transport,
        sleep=sleeps.append,
        retry_max_seconds=8,
    )

    with pytest.raises(ShopifyAdminTransportError) as caught:
        admin.find_customer_by_email("private@example.com")
    error = caught.value
    assert error.retryable
    assert error.status_code == 429
    assert error.retry_after_seconds == 120
    assert sleeps == []
    assert len(transport.calls) == 1
    rendered = f"{error!s} {error!r}"
    assert "dev_dashboard_client_secret" not in rendered
    assert "private@example.com" not in rendered


@pytest.mark.parametrize("retryable", [False, True])
def test_token_transport_errors_are_rewrapped_without_secret_text(
    retryable,
):
    transport = FakeTransport(
        ShopifyAdminTransportError(
            (
                "dev_dashboard_client_secret and "
                "private@example.com must not escape"
            ),
            retryable=retryable,
            status_code=503 if retryable else 400,
        )
    )
    admin = credential_client(transport, max_attempts=1)

    with pytest.raises(ShopifyAdminTransportError) as caught:
        admin.find_customer_by_email("private@example.com")
    assert caught.value.retryable is retryable
    rendered = f"{caught.value!s} {caught.value!r}"
    assert "dev_dashboard_client_secret" not in rendered
    assert "private@example.com" not in rendered
    assert len(transport.calls) == 1


@pytest.mark.parametrize("status_code", [400, 401, 403])
def test_token_exchange_rejected_credentials_are_terminal_and_redacted(
    status_code,
):
    transport = FakeTransport(
        response(
            {
                "error": "invalid dev_dashboard_client_secret",
                "error_description": "private@example.com",
            },
            status=status_code,
        )
    )
    admin = credential_client(transport)

    with pytest.raises(ShopifyAdminConfigurationError) as caught:
        admin.find_customer_by_email("private@example.com")
    assert caught.value.safe_summary == (
        "Shopify Admin API client credentials were rejected."
    )
    rendered = f"{caught.value!s} {caught.value!r}"
    assert "dev_dashboard_client_secret" not in rendered
    assert "private@example.com" not in rendered
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        [],
        {"expires_in": 86399},
        {"access_token": "short_lived_token", "expires_in": 0},
        {"access_token": "short_lived\nleak", "expires_in": 86399},
        {"access_token": "short_lived_token\n", "expires_in": 86399},
        {"access_token": "short_lived_token", "expires_in": True},
    ],
)
def test_invalid_token_responses_are_retryable_and_redacted(payload):
    transport = FakeTransport(response(payload))
    admin = credential_client(transport, max_attempts=1)

    with pytest.raises(ShopifyAdminTransportError) as caught:
        admin.find_customer_by_email("private@example.com")
    assert caught.value.retryable
    assert caught.value.status_code == 200
    rendered = f"{caught.value!s} {caught.value!r}"
    assert "short_lived" not in rendered
    assert "private@example.com" not in rendered
    assert len(transport.calls) == 1


def test_token_exchange_retries_invalid_response_then_succeeds():
    sleeps = []
    transport = FakeTransport(
        response(b"not-json"),
        response(token_payload()),
        response(customer_payload()),
    )
    admin = credential_client(
        transport,
        sleep=sleeps.append,
        retry_base_seconds=0.25,
    )

    assert admin.find_customer_by_email("a@b.co") == "7001"
    assert sleeps == [0.25]


def test_oversized_token_response_is_rejected_without_retry():
    transport = FakeTransport(
        response(b"x" * (admin_module._MAX_RESPONSE_BYTES + 1))
    )
    admin = credential_client(transport)

    with pytest.raises(ShopifyAdminTransportError) as caught:
        admin.find_customer_by_email("a@b.co")
    assert not caught.value.retryable
    assert caught.value.status_code == 200
    assert len(transport.calls) == 1


def test_invalid_clock_value_never_sends_credentials():
    transport = FakeTransport()
    admin = credential_client(transport, clock=lambda: float("nan"))

    with pytest.raises(ShopifyAdminConfigurationError) as caught:
        admin.find_customer_by_email("a@b.co")
    assert caught.value.safe_summary == (
        "Shopify Admin API clock function returned an invalid value."
    )
    assert transport.calls == []


def test_client_credentials_redirect_is_not_followed_or_disclosed(
    monkeypatch,
):
    captured = {}

    class RedirectingOpener:
        def open(self, request, timeout):
            captured["url"] = request.full_url
            raise urllib.error.HTTPError(
                request.full_url,
                302,
                "Found",
                {"Location": "https://attacker.example/collect"},
                io.BytesIO(b""),
            )

    def build_opener(*handlers):
        captured["handlers"] = handlers
        return RedirectingOpener()

    monkeypatch.setattr(
        admin_module.urllib.request,
        "build_opener",
        build_opener,
    )
    admin = credential_client(transport=admin_module._default_transport)

    with pytest.raises(ShopifyAdminTransportError) as caught:
        admin.find_customer_by_email("private@example.com")
    assert not caught.value.retryable
    assert captured["url"] == admin.token_endpoint
    assert any(
        isinstance(handler, admin_module._NoRedirectHandler)
        for handler in captured["handlers"]
    )
    rendered = f"{caught.value!s} {caught.value!r}"
    assert "dev_dashboard_client_secret" not in rendered
    assert "private@example.com" not in rendered


def test_verify_store_access_returns_stable_shop_gid_without_pii_fields():
    transport = FakeTransport(response(shop_payload()))
    admin = client(transport)

    assert admin.verify_store_access() == "gid://shopify/Shop/123456789"
    assert posted(transport.calls[0]) == {
        "operationName": "VerifyShopAccess",
        "query": VERIFY_SHOP_ACCESS_QUERY,
        "variables": {},
    }
    assert VERIFY_SHOP_ACCESS_QUERY == (
        "query VerifyShopAccess {\n"
        "  shop {\n"
        "    id\n"
        "  }\n"
        "}"
    )


def test_verify_store_access_uses_client_credentials_authentication():
    transport = FakeTransport(
        response(token_payload()),
        response(shop_payload("gid://shopify/Shop/987654321")),
    )
    admin = credential_client(transport)

    assert admin.verify_store_access() == "gid://shopify/Shop/987654321"
    assert len(transport.calls) == 2
    assert transport.calls[0]["url"] == admin.token_endpoint
    assert transport.calls[1]["url"] == admin.endpoint
    assert transport.calls[1]["headers"]["X-Shopify-Access-Token"] == (
        "short_lived_admin_token"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"data": {"shop": None}},
        {"data": {"shop": {}}},
        {"data": {"shop": {"id": 123}}},
        {"data": {"shop": {"id": "gid://shopify/Customer/123"}}},
        {"data": {"shop": {"id": "gid://shopify/Shop/0"}}},
        {"data": {"shop": {"id": "gid://shopify/Shop/123/extra"}}},
    ],
)
def test_verify_store_access_rejects_invalid_identity_without_disclosure(
    payload,
):
    transport = FakeTransport(response(payload))
    admin = client(transport)

    with pytest.raises(ShopifyAdminTransportError) as caught:
        admin.verify_store_access()
    assert not caught.value.retryable
    assert "gid://" not in str(caught.value)
    assert len(transport.calls) == 1


def test_find_customer_by_email_normalizes_input_and_returns_numeric_id():
    transport = FakeTransport(
        response(customer_payload("gid://shopify/Customer/987654321"))
    )
    admin = client(transport)

    assert admin.find_customer_by_email("  Golfer@Example.COM ") == "987654321"
    request = posted(transport.calls[0])
    assert request == {
        "operationName": "CustomerByEmail",
        "query": CUSTOMER_BY_IDENTIFIER_QUERY,
        "variables": {
            "identifier": {"emailAddress": "golfer@example.com"}
        },
    }


def test_find_customer_returns_none_when_shopify_has_no_match():
    transport = FakeTransport(
        response({"data": {"customerByIdentifier": None}})
    )
    assert client(transport).find_customer_by_email("new@example.com") is None


def test_set_customer_upserts_by_email_and_returns_numeric_id():
    transport = FakeTransport(response(set_payload()))
    admin = client(transport)

    assert admin.set_customer("  Golfer@Example.COM ") == "7001"
    request = posted(transport.calls[0])
    assert request == {
        "operationName": "SetCustomer",
        "query": CUSTOMER_SET_MUTATION,
        "variables": {
            "identifier": {"email": "golfer@example.com"},
            "input": {"email": "golfer@example.com"},
        },
    }


def test_set_customer_updates_linked_id_using_graphql_gid():
    transport = FakeTransport(
        response(set_payload("gid://shopify/Customer/7001"))
    )
    admin = client(transport)

    assert admin.set_customer("new@example.com", customer_id="7001") == "7001"
    request = posted(transport.calls[0])
    assert request["variables"] == {
        "identifier": {"id": "gid://shopify/Customer/7001"},
        "input": {"email": "new@example.com"},
    }


@pytest.mark.parametrize(
    ("email", "customer_id", "code"),
    [
        ("not-an-email", None, "INVALID_EMAIL"),
        ("good@example.com", "not-an-id", "INVALID_CUSTOMER_ID"),
    ],
)
def test_invalid_local_identity_never_calls_shopify(email, customer_id, code):
    transport = FakeTransport()
    admin = client(transport)
    with pytest.raises(ShopifyAdminUserError) as caught:
        admin.set_customer(email, customer_id)
    assert caught.value.issues[0].code == code
    assert transport.calls == []


def test_customer_set_user_errors_are_safe_and_pii_free():
    transport = FakeTransport(
        response(
            set_payload(
                customer_id=None,
                errors=[
                    {
                        "code": "TAKEN",
                        "field": ["input", "email"],
                        "message": "private.person@example.com is already used",
                    }
                ],
            )
        )
    )
    admin = client(transport)

    with pytest.raises(ShopifyAdminUserError) as caught:
        admin.set_customer("private.person@example.com")
    error = caught.value
    assert error.safe_summary == "Shopify rejected the customer data."
    assert error.issues[0].code == "TAKEN"
    assert error.issues[0].field == ("input", "email")
    assert "private.person@example.com" not in str(error)
    assert "private.person@example.com" not in repr(error)
    assert "shpat_server_secret" not in repr(error)


def test_network_failure_retries_with_bounded_exponential_backoff():
    sleeps = []
    transport = FakeTransport(
        urllib.error.URLError("connection unavailable"),
        TimeoutError("timed out"),
        response(customer_payload()),
    )
    admin = client(
        transport,
        sleep=sleeps.append,
        max_attempts=3,
        retry_base_seconds=0.25,
        retry_max_seconds=1,
        timeout_seconds=4,
    )

    assert admin.find_customer_by_email("a@b.co") == "7001"
    assert sleeps == [0.25, 0.5]
    assert [call["timeout_seconds"] for call in transport.calls] == [4, 4, 4]


def test_exhausted_network_failure_is_retryable_and_redacted():
    secret = "shpat_do_not_expose"
    transport = FakeTransport(
        urllib.error.URLError(f"failed with {secret}"),
        urllib.error.URLError(f"failed with {secret}"),
    )
    admin = ShopifyAdminClient(
        store_domain="test-store.myshopify.com",
        access_token=secret,
        transport=transport,
        sleep=lambda _seconds: None,
        max_attempts=2,
    )

    with pytest.raises(ShopifyAdminTransportError) as caught:
        admin.find_customer_by_email("private@example.com")
    error = caught.value
    assert error.retryable
    assert error.status_code is None
    assert secret not in str(error)
    assert "private@example.com" not in str(error)


def test_429_honors_retry_after_without_exceeding_retry_bound():
    sleeps = []
    transport = FakeTransport(
        response({}, status=429, headers={"retry-after": "7"}),
        response(customer_payload()),
    )
    admin = client(
        transport,
        sleep=sleeps.append,
        retry_base_seconds=0.5,
        retry_max_seconds=10,
    )
    assert admin.find_customer_by_email("a@b.co") == "7001"
    assert sleeps == [7]


def test_long_retry_after_is_deferred_to_persisted_scheduler():
    sleeps = []
    transport = FakeTransport(
        response({}, status=503, headers={"Retry-After": "300"}),
    )
    admin = client(
        transport,
        sleep=sleeps.append,
        retry_base_seconds=0.5,
        retry_max_seconds=8,
    )
    with pytest.raises(ShopifyAdminTransportError) as caught:
        admin.find_customer_by_email("a@b.co")
    assert caught.value.retryable
    assert caught.value.retry_after_seconds == 300
    assert sleeps == []
    assert len(transport.calls) == 1


def test_5xx_retries_and_exhaustion_retains_status():
    transport = FakeTransport(
        response({}, status=502),
        response({}, status=503),
    )
    admin = client(
        transport,
        max_attempts=2,
        sleep=lambda _seconds: None,
    )
    with pytest.raises(ShopifyAdminTransportError) as caught:
        admin.find_customer_by_email("a@b.co")
    assert caught.value.retryable
    assert caught.value.status_code == 503


@pytest.mark.parametrize(
    "code",
    [
        "THROTTLED",
        "INTERNAL_SERVER_ERROR",
        "internal_server_error",
    ],
)
def test_graphql_transient_error_codes_retry_then_succeed(code):
    sleeps = []
    transport = FakeTransport(
        response(
            {
                "errors": [
                    {
                        "message": "Do not persist private@example.com",
                        "extensions": {"code": code},
                    }
                ]
            }
        ),
        response(customer_payload()),
    )
    admin = client(
        transport,
        sleep=sleeps.append,
        retry_base_seconds=1,
    )
    assert admin.find_customer_by_email("a@b.co") == "7001"
    assert sleeps == [1]


@pytest.mark.parametrize(
    "code",
    [
        "ACCESS_DENIED",
        "SHOP_INACTIVE",
        "GRAPHQL_VALIDATION_FAILED",
        "BAD_USER_INPUT",
        "UNKNOWN_FUTURE_CODE",
    ],
)
def test_graphql_terminal_error_codes_do_not_retry(code):
    transport = FakeTransport(
        response(
            {
                "errors": [
                    {
                        "message": "Failure involving private@example.com",
                        "extensions": {"code": code},
                    }
                ]
            }
        )
    )
    admin = client(transport)

    with pytest.raises(ShopifyAdminTransportError) as caught:
        admin.find_customer_by_email("private@example.com")
    assert not caught.value.retryable
    assert len(transport.calls) == 1
    assert "private@example.com" not in str(caught.value)


@pytest.mark.parametrize(
    "errors",
    [
        [{"message": "No extensions"}],
        [{"extensions": {}}],
        [{"extensions": {"code": "not-safe!"}}],
        ["not-an-error-object"],
        {"extensions": {"code": "INTERNAL_SERVER_ERROR"}},
    ],
)
def test_malformed_graphql_errors_do_not_retry(errors):
    transport = FakeTransport(response({"errors": errors}))
    admin = client(transport)

    with pytest.raises(ShopifyAdminTransportError) as caught:
        admin.find_customer_by_email("private@example.com")
    assert not caught.value.retryable
    assert len(transport.calls) == 1


def test_mixed_transient_and_terminal_graphql_errors_do_not_retry():
    transport = FakeTransport(
        response(
            {
                "errors": [
                    {"extensions": {"code": "INTERNAL_SERVER_ERROR"}},
                    {"extensions": {"code": "ACCESS_DENIED"}},
                ]
            }
        )
    )
    admin = client(transport)

    with pytest.raises(ShopifyAdminTransportError) as caught:
        admin.find_customer_by_email("private@example.com")
    assert not caught.value.retryable
    assert len(transport.calls) == 1


def test_multiple_known_transient_graphql_errors_retry():
    transport = FakeTransport(
        response(
            {
                "errors": [
                    {"extensions": {"code": "INTERNAL_SERVER_ERROR"}},
                    {"extensions": {"code": "THROTTLED"}},
                ]
            }
        ),
        response(customer_payload()),
    )
    sleeps = []
    admin = client(transport, sleep=sleeps.append, retry_base_seconds=0.25)

    assert admin.find_customer_by_email("a@b.co") == "7001"
    assert sleeps == [0.25]


@pytest.mark.parametrize(
    ("code", "summary"),
    [
        ("THROTTLED", "Shopify Admin API is temporarily throttled."),
        (
            "INTERNAL_SERVER_ERROR",
            "Shopify Admin API is temporarily unavailable.",
        ),
    ],
)
def test_exhausted_graphql_transient_error_stays_retryable(code, summary):
    transient = response(
        {
            "errors": [
                {
                    "message": (
                        "Failure for private@example.com with "
                        "shpat_server_secret"
                    ),
                    "extensions": {"code": code},
                }
            ]
        },
    )
    transport = FakeTransport(transient, transient)
    admin = client(
        transport,
        max_attempts=2,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(ShopifyAdminTransportError) as caught:
        admin.find_customer_by_email("private@example.com")
    assert caught.value.retryable
    assert caught.value.status_code == 200
    assert caught.value.retry_after_seconds is None
    assert caught.value.safe_summary == summary
    assert "private@example.com" not in str(caught.value)
    assert "shpat_server_secret" not in repr(caught.value)
    assert len(transport.calls) == 2


def test_graphql_transient_long_retry_after_is_deferred():
    transport = FakeTransport(
        response(
            {
                "errors": [
                    {"extensions": {"code": "INTERNAL_SERVER_ERROR"}}
                ]
            },
            headers={"Retry-After": "120"},
        )
    )
    sleeps = []
    admin = client(
        transport,
        sleep=sleeps.append,
        retry_max_seconds=8,
    )

    with pytest.raises(ShopifyAdminTransportError) as caught:
        admin.find_customer_by_email("a@b.co")
    assert caught.value.retryable
    assert caught.value.status_code == 200
    assert caught.value.retry_after_seconds == 120
    assert sleeps == []
    assert len(transport.calls) == 1


def test_non_retryable_http_error_does_not_retry():
    transport = FakeTransport(response({}, status=400))
    admin = client(transport)

    with pytest.raises(ShopifyAdminTransportError) as caught:
        admin.find_customer_by_email("private@example.com")
    assert not caught.value.retryable
    assert len(transport.calls) == 1


def test_invalid_success_response_is_safe():
    transport = FakeTransport(response(b"not-json"), response(b"still-not-json"))
    admin = client(
        transport,
        max_attempts=2,
        sleep=lambda _seconds: None,
    )
    with pytest.raises(ShopifyAdminTransportError) as caught:
        admin.find_customer_by_email("private@example.com")
    assert caught.value.retryable
    assert "private@example.com" not in str(caught.value)


def test_client_emits_no_token_or_customer_logs(caplog):
    caplog.set_level(logging.DEBUG)
    token = "shpat_top_secret"
    transport = FakeTransport(response(customer_payload()))
    admin = ShopifyAdminClient(
        store_domain="test-store.myshopify.com",
        access_token=token,
        transport=transport,
    )
    assert admin.find_customer_by_email("private@example.com") == "7001"
    assert token not in caplog.text
    assert "private@example.com" not in caplog.text
