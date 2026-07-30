"""Outbound Shopify customer bridge: policy, retry, backfill, and admin tests."""

from __future__ import annotations

import logging
from collections import deque

import pytest
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.integrations.shopify import admin
from swinglab.integrations.shopify.backfill import run_backfill_batch
from swinglab.integrations.shopify.customer_sync import (
    ShopifyCustomerSyncCoordinator,
    ShopifySyncPolicyError,
    _next_retry_at,
    link_existing_shopify_customer,
    retry_shopify_customer_sync,
    sync_app_user_to_shopify,
    update_linked_shopify_customer,
    validate_sync_settings,
)
from swinglab.web.app import create_app
from swinglab.web.users import (
    SHOPIFY_SYNC_FAILED,
    SHOPIFY_SYNC_PENDING,
    SHOPIFY_SYNC_REQUIRES_REVIEW,
    SHOPIFY_SYNC_SYNCED,
    UserStore,
)


class FakeAdminClient:
    def __init__(self, set_results=(), lookup=None):
        self.set_results = deque(set_results)
        self.lookup = lookup or {}
        self.set_calls = []
        self.lookup_calls = []

    def set_customer(self, email, customer_id=None):
        self.set_calls.append((email, customer_id))
        if not self.set_results:
            raise AssertionError("unexpected customerSet call")
        result = self.set_results.popleft()
        if isinstance(result, BaseException):
            raise result
        return result

    def find_customer_by_email(self, email):
        self.lookup_calls.append(email)
        result = self.lookup.get(email)
        if isinstance(result, BaseException):
            raise result
        return result


def verified_user(users: UserStore, email: str):
    return users.verify_email_signin(email)


def test_verified_new_user_is_upserted_and_linked(tmp_path):
    users = UserStore(tmp_path / "users.db")
    user = verified_user(users, "  New@Example.com ")
    users.mark_shopify_sync_pending(user.id)
    client = FakeAdminClient(["gid://shopify/Customer/7001"])

    result = sync_app_user_to_shopify(users, user.id, client)

    stored = users.get(user.id)
    assert result.status == SHOPIFY_SYNC_SYNCED
    assert result.action == "upserted"
    assert result.customer_id == "7001"
    assert stored.shopify_customer_id == "7001"
    assert stored.shopify_sync_status == SHOPIFY_SYNC_SYNCED
    assert stored.shopify_last_synced_at is not None
    assert stored.shopify_sync_error is None
    assert stored.shopify_sync_attempts == 1
    assert client.set_calls == [("new@example.com", None)]


def test_sync_logging_is_structured_and_identifier_free(tmp_path, caplog):
    users = UserStore(tmp_path / "users.db")
    user = verified_user(users, "private.person@example.com")
    users.mark_shopify_sync_pending(user.id)

    with caplog.at_level(
        logging.INFO,
        logger="swinglab.integrations.shopify.customer_sync",
    ):
        sync_app_user_to_shopify(
            users,
            user.id,
            FakeAdminClient(["987654321"]),
        )

    record = next(
        item
        for item in caplog.records
        if item.getMessage() == "Shopify customer sync outcome."
    )
    assert record.shopify_sync_status == SHOPIFY_SYNC_SYNCED
    assert record.shopify_sync_action == "upserted"
    rendered = "\n".join(item.getMessage() for item in caplog.records)
    assert "private.person@example.com" not in rendered
    assert "987654321" not in rendered
    assert user.id not in rendered


def test_existing_shopify_customer_is_reused_by_idempotent_email_upsert(tmp_path):
    users = UserStore(tmp_path / "users.db")
    user = verified_user(users, "buyer@example.com")
    users.mark_shopify_sync_pending(user.id)
    # customerSet returns the same ID whether it found or created the unique
    # email. Returning an existing ID proves the durable link is reused.
    client = FakeAdminClient(["4242"])

    result = sync_app_user_to_shopify(users, user.id, client)

    assert result.customer_id == "4242"
    assert users.get(user.id).shopify_customer_id == "4242"


def test_linked_user_never_calls_email_upsert_again(tmp_path):
    users = UserStore(tmp_path / "users.db")
    user = verified_user(users, "linked@example.com")
    users.upsert_store_customer(user.email, "9001")
    users.mark_shopify_sync_pending(user.id)
    client = FakeAdminClient()

    result = sync_app_user_to_shopify(users, user.id, client)

    assert result.action == "already_linked"
    assert result.customer_id == "9001"
    assert client.set_calls == []


def test_verified_current_email_update_targets_the_durable_customer_id(tmp_path):
    users = UserStore(tmp_path / "users.db")
    user = verified_user(users, "linked@example.com")
    linked = users.upsert_store_customer(user.email, "9001")
    client = FakeAdminClient(["gid://shopify/Customer/9001"])

    customer_id = update_linked_shopify_customer(
        linked,
        client,
        email="linked@example.com",
    )

    assert customer_id == "9001"
    assert client.set_calls == [
        ("linked@example.com", "9001")
    ]


def test_unverified_email_change_is_not_pushed_to_shopify(tmp_path):
    users = UserStore(tmp_path / "users.db")
    user = verified_user(users, "linked@example.com")
    linked = users.upsert_store_customer(user.email, "9001")
    client = FakeAdminClient()

    with pytest.raises(ShopifySyncPolicyError, match="new email"):
        update_linked_shopify_customer(
            linked,
            client,
            email="new.address@example.com",
        )

    assert client.set_calls == []


def test_invalid_operator_customer_id_closes_attempt_for_review(tmp_path):
    users = UserStore(tmp_path / "users.db")
    user = verified_user(users, "manual@example.com")

    result = link_existing_shopify_customer(
        users,
        user.id,
        "gid://shopify/Order/77",
    )

    stored = users.get(user.id)
    assert result.action == "invalid_customer_id"
    assert result.status == SHOPIFY_SYNC_REQUIRES_REVIEW
    assert stored.shopify_sync_status == SHOPIFY_SYNC_REQUIRES_REVIEW
    assert stored.shopify_sync_attempt_token is None


def test_operator_can_link_a_manually_verified_existing_customer(tmp_path):
    users = UserStore(tmp_path / "users.db")
    user = verified_user(users, "manual@example.com")

    result = link_existing_shopify_customer(
        users,
        user.id,
        "gid://shopify/Customer/77",
    )

    assert result.action == "linked_existing"
    assert result.customer_id == "77"
    assert users.get(user.id).shopify_customer_id == "77"


def test_unverified_email_requires_review_without_contacting_shopify(tmp_path):
    users = UserStore(tmp_path / "users.db")
    user = users.create("unverified@example.com", "longenough")
    users.mark_shopify_sync_pending(user.id)
    client = FakeAdminClient()

    result = sync_app_user_to_shopify(users, user.id, client)

    stored = users.get(user.id)
    assert result.status == SHOPIFY_SYNC_REQUIRES_REVIEW
    assert stored.shopify_sync_status == SHOPIFY_SYNC_REQUIRES_REVIEW
    assert "Verified email" in stored.shopify_sync_error
    assert client.set_calls == []


def test_outage_records_backoff_then_manual_retry_succeeds(tmp_path):
    users = UserStore(tmp_path / "users.db")
    user = verified_user(users, "retry@example.com")
    users.mark_shopify_sync_pending(user.id)
    unavailable = admin.ShopifyAdminTransportError(
        "Shopify is temporarily unavailable.",
        retryable=True,
        status_code=503,
    )
    client = FakeAdminClient([unavailable, "7002"])
    settings = {
        "max_attempts": 5,
        "retry_base_seconds": 30,
        "retry_max_seconds": 300,
        "retry_jitter_ratio": 0,
    }

    failed = sync_app_user_to_shopify(
        users, user.id, client, settings, clock=lambda: 1000
    )
    after_failure = users.get(user.id)
    assert failed.status == SHOPIFY_SYNC_FAILED
    assert failed.retryable
    assert after_failure.shopify_sync_next_attempt_at == 1030
    assert after_failure.shopify_sync_attempts == 1

    synced = retry_shopify_customer_sync(users, user.id, client, settings)
    stored = users.get(user.id)
    assert synced.status == SHOPIFY_SYNC_SYNCED
    assert stored.shopify_customer_id == "7002"
    assert stored.shopify_sync_attempts == 2
    assert stored.shopify_sync_error is None


def test_non_retryable_admin_failure_has_no_due_retry(tmp_path):
    users = UserStore(tmp_path / "users.db")
    user = verified_user(users, "auth-failure@example.com")
    users.mark_shopify_sync_pending(user.id)
    client = FakeAdminClient(
        [
            admin.ShopifyAdminTransportError(
                "Shopify Admin API authentication failed.",
                retryable=False,
                status_code=401,
            )
        ]
    )

    result = sync_app_user_to_shopify(
        users,
        user.id,
        client,
        {"max_attempts": 5},
        clock=lambda: 1000,
    )

    stored = users.get(user.id)
    assert result.status == SHOPIFY_SYNC_FAILED
    assert not result.retryable
    assert stored.shopify_sync_next_attempt_at is None
    assert users.list_due_shopify_syncs(now=9999) == []


def test_provider_retry_after_is_persisted_without_blocking_worker(tmp_path):
    users = UserStore(tmp_path / "users.db")
    user = verified_user(users, "throttled@example.com")
    users.mark_shopify_sync_pending(user.id)
    client = FakeAdminClient(
        [
            admin.ShopifyAdminTransportError(
                "Shopify Admin API is temporarily throttled.",
                retryable=True,
                status_code=429,
                retry_after_seconds=300,
            )
        ]
    )

    result = sync_app_user_to_shopify(
        users,
        user.id,
        client,
        {
            "max_attempts": 5,
            "retry_base_seconds": 30,
            "retry_max_seconds": 3600,
            "retry_jitter_ratio": 0,
        },
        clock=lambda: 1000,
    )

    assert result.retryable
    assert users.get(user.id).shopify_sync_next_attempt_at == 1300


def test_retry_jitter_is_stable_bounded_and_spreads_users():
    settings = validate_sync_settings(
        {
            "retry_base_seconds": 100,
            "retry_max_seconds": 1000,
            "retry_jitter_ratio": 0.2,
        }
    )

    first = _next_retry_at(
        1, settings, 1000, jitter_seed="user-one:1"
    )
    repeated = _next_retry_at(
        1, settings, 1000, jitter_seed="user-one:1"
    )
    second = _next_retry_at(
        1, settings, 1000, jitter_seed="user-two:1"
    )

    assert 1080 <= first <= 1120
    assert repeated == first
    assert 1080 <= second <= 1120
    assert second != first


def test_invalid_retry_configuration_is_terminal_not_hot_looped(tmp_path):
    users = UserStore(tmp_path / "users.db")
    user = verified_user(users, "config@example.com")
    users.mark_shopify_sync_pending(user.id)
    client = FakeAdminClient()

    result = sync_app_user_to_shopify(
        users,
        user.id,
        client,
        {"max_attempts": "not-an-integer"},
    )

    stored = users.get(user.id)
    assert result.action == "configuration_error"
    assert stored.shopify_sync_status == SHOPIFY_SYNC_FAILED
    assert stored.shopify_sync_next_attempt_at is None
    assert users.list_due_shopify_syncs(now=9999) == []
    assert client.set_calls == []


def test_graphql_user_error_is_safe_and_requires_review(tmp_path):
    users = UserStore(tmp_path / "users.db")
    user = verified_user(users, "invalid@example.com")
    users.mark_shopify_sync_pending(user.id)
    issue = admin.ShopifyAdminUserIssue("INVALID", ("input", "email"))
    client = FakeAdminClient([admin.ShopifyAdminUserError((issue,))])

    result = sync_app_user_to_shopify(users, user.id, client)

    assert result.status == SHOPIFY_SYNC_REQUIRES_REVIEW
    stored = users.get(user.id)
    assert stored.shopify_sync_error == "Shopify rejected the customer data."
    assert "invalid@example.com" not in stored.shopify_sync_error


def test_duplicate_customer_id_is_never_assigned_to_two_users(tmp_path):
    users = UserStore(tmp_path / "users.db")
    first = verified_user(users, "first@example.com")
    second = verified_user(users, "second@example.com")
    users.mark_shopify_sync_pending(first.id)
    assert sync_app_user_to_shopify(
        users, first.id, FakeAdminClient(["77"])
    ).status == SHOPIFY_SYNC_SYNCED
    users.mark_shopify_sync_pending(second.id)

    conflict = sync_app_user_to_shopify(
        users, second.id, FakeAdminClient(["gid://shopify/Customer/77"])
    )

    assert conflict.status == SHOPIFY_SYNC_REQUIRES_REVIEW
    assert users.get(first.id).shopify_customer_id == "77"
    assert users.get(second.id).shopify_customer_id is None


def test_coordinator_recovers_persisted_pending_work(tmp_path):
    users = UserStore(tmp_path / "users.db")
    user = verified_user(users, "queued@example.com")
    users.mark_shopify_sync_pending(user.id)
    coordinator = ShopifyCustomerSyncCoordinator(
        users,
        FakeAdminClient(["88"]),
        {"max_attempts": 3},
        start=False,
    )

    assert coordinator.run_once() == 1
    assert users.get(user.id).shopify_customer_id == "88"
    assert coordinator.run_once() == 0


def test_backfill_dry_run_is_read_only_and_restartable(tmp_path):
    users = UserStore(tmp_path / "users.db")
    for number in range(3):
        verified_user(users, f"golfer{number}@example.com")
    client = FakeAdminClient(
        lookup={
            "golfer0@example.com": "10",
            "golfer1@example.com": None,
            "golfer2@example.com": "12",
        }
    )

    first = run_backfill_batch(
        users, client, batch_size=2, dry_run=True
    )
    second = run_backfill_batch(
        users,
        client,
        batch_size=2,
        after=first.next_cursor,
        dry_run=True,
    )

    assert first.scanned == 2
    assert first.next_cursor
    assert second.scanned == 1
    assert second.next_cursor is None
    assert first.would_link + first.would_create == 2
    assert second.would_link + second.would_create == 1
    rows, _ = users.list_shopify_sync_health(limit=10)
    assert all(row.shopify_sync_status == "not_started" for row in rows)
    assert all(row.shopify_customer_id is None for row in rows)


def test_backfill_reports_unverified_and_locked_users_without_writes(tmp_path):
    users = UserStore(tmp_path / "users.db")
    unverified = users.create("unverified@example.com", "longenough")
    locked = verified_user(users, "locked@example.com")
    # A prior store relationship is removed; identity_locked deliberately
    # remains true to prevent email-based takeover.
    users.upsert_store_customer(locked.email, "55")
    users.unlink_shopify(locked.id)

    summary = run_backfill_batch(
        users, FakeAdminClient(), batch_size=10, dry_run=True
    )

    assert summary.requires_review == 2
    assert summary.scanned == 2
    assert users.get(unverified.id).shopify_sync_status == "not_started"
    assert users.get(locked.id).shopify_customer_id is None


def test_backfill_explicitly_skips_already_linked_users(tmp_path):
    users = UserStore(tmp_path / "users.db")
    linked = verified_user(users, "linked@example.com")
    users.upsert_store_customer(linked.email, "55")

    summary = run_backfill_batch(
        users, FakeAdminClient(), batch_size=10, dry_run=True
    )

    assert summary.scanned == 1
    assert summary.skipped == 1
    assert summary.items[0].outcome == "already_linked"


def test_backfill_apply_is_idempotent_on_rerun(tmp_path):
    users = UserStore(tmp_path / "users.db")
    user = verified_user(users, "existing@example.com")
    client = FakeAdminClient(["7004"])

    first = run_backfill_batch(
        users,
        client,
        batch_size=10,
        dry_run=False,
        settings={"retry_jitter_ratio": 0},
    )
    second = run_backfill_batch(
        users,
        client,
        batch_size=10,
        dry_run=False,
        settings={"retry_jitter_ratio": 0},
    )

    assert first.linked == 1
    assert users.get(user.id).shopify_customer_id == "7004"
    assert second.skipped == 1
    assert client.set_calls == [("existing@example.com", None)]


def test_registration_succeeds_while_unverified_sync_needs_review(tmp_path):
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.shopify_customer_sync["enabled"] = True
    client = FakeAdminClient()
    app = create_app(
        cfg,
        sessions_dir=tmp_path / "sessions",
        shopify_admin_client=client,
        start_shopify_sync_worker=False,
    )
    web = TestClient(app)

    response = web.post(
        "/signup",
        data={"email": "new@example.com", "password": "longenough"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    user = app.state.users.get_by_email("new@example.com")
    assert user is not None
    assert user.shopify_sync_status == SHOPIFY_SYNC_PENDING
    assert app.state.shopify_sync.run_once() == 1
    assert app.state.users.get(user.id).shopify_sync_status == (
        SHOPIFY_SYNC_REQUIRES_REVIEW
    )
    assert client.set_calls == []


def test_classic_signup_verification_then_syncs_automatically(
    tmp_path, monkeypatch
):
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.shopify_customer_sync["enabled"] = True
    client = FakeAdminClient(["7003"])
    app = create_app(
        cfg,
        sessions_dir=tmp_path / "sessions",
        shopify_admin_client=client,
        start_shopify_sync_worker=False,
    )
    monkeypatch.setattr("swinglab.web.app.mailer.enabled", lambda: True)
    code = app.state.users.issue_email_code(
        "classic@example.com", "claim"
    )
    web = TestClient(app)

    response = web.post(
        "/signup",
        data={
            "email": "classic@example.com",
            "password": "longenough",
            "code": code,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    user = app.state.users.get_by_email("classic@example.com")
    assert user is not None and user.email_verified
    assert user.shopify_sync_status == SHOPIFY_SYNC_PENDING
    assert app.state.shopify_sync.run_once() == 1
    assert app.state.users.get(user.id).shopify_customer_id == "7003"


def test_verified_passwordless_registration_survives_shopify_outage(
    tmp_path, monkeypatch
):
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.shopify_customer_sync["enabled"] = True
    outage = admin.ShopifyAdminTransportError(
        "Shopify is temporarily unavailable.",
        retryable=True,
        status_code=503,
    )
    client = FakeAdminClient([outage])
    app = create_app(
        cfg,
        sessions_dir=tmp_path / "sessions",
        shopify_admin_client=client,
        start_shopify_sync_worker=False,
    )
    monkeypatch.setattr("swinglab.web.app.mailer.enabled", lambda: True)
    code = app.state.users.issue_email_code("verified@example.com", "login")
    web = TestClient(app)

    response = web.post(
        "/login/code",
        data={
            "email": "verified@example.com",
            "code": code,
            "auth_intent": "signup",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    user = app.state.users.get_by_email("verified@example.com")
    assert user is not None and user.email_verified
    assert user.shopify_sync_status == SHOPIFY_SYNC_PENDING
    assert app.state.shopify_sync.run_once() == 1
    failed = app.state.users.get(user.id)
    assert failed.shopify_sync_status == SHOPIFY_SYNC_FAILED
    assert failed.shopify_sync_next_attempt_at is not None


def test_admin_sync_health_and_retry_are_token_protected(
    tmp_path, monkeypatch
):
    cfg = Config()
    cfg.shopify_customer_sync["enabled"] = True
    app = create_app(
        cfg,
        sessions_dir=tmp_path / "sessions",
        shopify_admin_client=FakeAdminClient(),
        start_shopify_sync_worker=False,
    )
    web = TestClient(app)
    user = app.state.users.create("status@example.com", "longenough")
    token = "admin-token-with-enough-entropy"
    monkeypatch.setenv("SWINGLAB_ADMIN_TOKEN", token)

    assert web.get("/admin/shopify-sync").status_code == 404
    assert web.get(
        "/admin/shopify-sync",
        headers={"Authorization": "Bearer wrong"},
    ).status_code == 404
    response = web.get(
        "/admin/shopify-sync",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    item = next(row for row in response.json()["users"] if row["user_id"] == user.id)
    assert "email" not in item
    assert item["linked"] is False

    retry = web.post(
        f"/admin/shopify-sync/{user.id}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert retry.status_code == 202
    assert app.state.users.get(user.id).shopify_sync_status == (
        SHOPIFY_SYNC_PENDING
    )


def test_admin_token_never_reaches_regular_pages(tmp_path, monkeypatch):
    secret = "shpat_should_never_be_rendered"
    monkeypatch.setenv("SHOPIFY_ADMIN_ACCESS_TOKEN", secret)
    cfg = Config()
    app = create_app(cfg, sessions_dir=tmp_path / "sessions")
    web = TestClient(app)

    for path in ("/", "/login", "/signup", "/pricing"):
        assert secret not in web.get(path).text


def test_sync_worker_lifecycle_is_bound_to_asgi_startup(tmp_path):
    cfg = Config()
    cfg.shopify_customer_sync["enabled"] = True
    app = create_app(
        cfg,
        sessions_dir=tmp_path / "sessions",
        shopify_admin_client=FakeAdminClient(),
    )
    coordinator = app.state.shopify_sync

    assert coordinator._thread is None
    with TestClient(app):
        assert coordinator._thread is not None
        assert coordinator._thread.is_alive()
    assert not coordinator._thread.is_alive()


def test_invalid_sync_settings_fail_before_database_or_worker_start(tmp_path):
    cfg = Config()
    cfg.shopify_customer_sync["enabled"] = True
    cfg.shopify_customer_sync["max_attempts"] = "five"
    sessions = tmp_path / "sessions"

    with pytest.raises(
        admin.ShopifyAdminConfigurationError,
        match="settings are invalid",
    ):
        create_app(
            cfg,
            sessions_dir=sessions,
            shopify_admin_client=FakeAdminClient(),
        )

    assert not sessions.exists()
