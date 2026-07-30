"""Server-only Shopify Admin GraphQL customer client."""

from __future__ import annotations

import json
import io
import logging
import urllib.error

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


def set_payload(customer_id="gid://shopify/Customer/7001", errors=None):
    return {
        "data": {
            "customerSet": {
                "customer": {"id": customer_id} if customer_id else None,
                "userErrors": errors or [],
            }
        }
    }


def client(transport, sleep=lambda _seconds: None, **kwargs):
    return ShopifyAdminClient(
        store_domain="test-store.myshopify.com",
        access_token="shpat_server_secret",
        transport=transport,
        sleep=sleep,
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
    assert "shpat_server_secret" not in repr(admin)
    assert "<redacted>" in repr(admin)


def test_default_admin_version_is_2026_07():
    admin = ShopifyAdminClient.from_env(
        environ={
            "SHOPIFY_ADMIN_STORE_DOMAIN": "test-store.myshopify.com",
            "SHOPIFY_ADMIN_ACCESS_TOKEN": "secret",
        },
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
            "Shopify Admin API access token is missing or invalid.",
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


def test_graphql_throttled_retries_then_succeeds():
    sleeps = []
    transport = FakeTransport(
        response(
            {
                "errors": [
                    {
                        "message": "Throttled",
                        "extensions": {"code": "THROTTLED"},
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


def test_non_retryable_http_and_graphql_errors_do_not_retry():
    for first in (
        response({}, status=400),
        response(
            {
                "errors": [
                    {
                        "message": "Schema failure with private@example.com",
                        "extensions": {"code": "GRAPHQL_VALIDATION_FAILED"},
                    }
                ]
            }
        ),
    ):
        transport = FakeTransport(first)
        admin = client(transport)
        with pytest.raises(ShopifyAdminTransportError) as caught:
            admin.find_customer_by_email("private@example.com")
        assert not caught.value.retryable
        assert len(transport.calls) == 1
        assert "private@example.com" not in str(caught.value)


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
