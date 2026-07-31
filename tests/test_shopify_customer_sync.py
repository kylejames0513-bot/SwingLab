"""Outbound Shopify customer bridge: policy, retry, backfill, and admin tests."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import threading
import time
from collections import deque
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.integrations.shopify import admin
from swinglab.integrations.shopify.backfill import (
    BackfillSafetyError,
    ReadOnlyBackfillStore,
    ShopifyStoreBindingError,
    authenticate_and_bind_backfill_database,
    authenticate_and_require_backfill_binding,
    bind_backfill_database,
    run_backfill_all,
    run_backfill_batch,
)
from swinglab.integrations.shopify.customer_sync import (
    ShopifyCustomerSyncCoordinator,
    ShopifySyncPolicyError,
    _next_retry_at,
    link_existing_shopify_customer,
    operator_user_ref,
    retry_shopify_customer_sync,
    sync_app_user_to_shopify,
    update_linked_shopify_customer,
    validate_sync_settings,
    verify_and_link_existing_shopify_customer,
)
from swinglab.web.app import create_app
from swinglab.web.users import (
    SHOPIFY_SYNC_FAILED,
    SHOPIFY_SYNC_PENDING,
    SHOPIFY_SYNC_REQUIRES_REVIEW,
    SHOPIFY_SYNC_SYNCED,
    UserStore,
)


def _last_emailed_code(sent):
    match = re.search(r"\b(\d{6})\b", sent[-1][2])
    assert match is not None
    return match.group(1)


class FakeAdminClient:
    def __init__(
        self,
        set_results=(),
        lookup=None,
        store_domain="test-store.myshopify.com",
        shop_gid="gid://shopify/Shop/123",
        verify_error=None,
        verify_results=(),
    ):
        self.set_results = deque(set_results)
        self.lookup = lookup or {}
        self.set_calls = []
        self.lookup_calls = []
        self.store_domain = store_domain
        self.shop_gid = shop_gid
        self.verify_error = verify_error
        self.verify_results = deque(verify_results)
        self.verify_calls = 0

    def verify_store_access(self):
        self.verify_calls += 1
        if self.verify_results:
            result = self.verify_results.popleft()
            if isinstance(result, BaseException):
                raise result
            return result
        if self.verify_error is not None:
            raise self.verify_error
        return self.shop_gid

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


def bind_test_database(db_path, client: FakeAdminClient) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    UserStore(db_path)
    bind_backfill_database(
        db_path,
        client.store_domain,
        client.shop_gid,
        confirmation=client.store_domain,
    )


@pytest.fixture(autouse=True)
def matching_inbound_shopify_store(monkeypatch):
    """Default outbound tests to the same store used by signed webhooks."""

    monkeypatch.setenv(
        "SHOPIFY_STORE_DOMAIN",
        "test-store.myshopify.com",
    )


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


@pytest.mark.parametrize("redaction_scope", ("customer", "shop"))
def test_redaction_fences_worker_paused_after_attempt_start(
    tmp_path,
    monkeypatch,
    redaction_scope,
):
    """A privacy commit wins before a paused worker reaches Shopify."""

    users = UserStore(tmp_path / "users.db")
    user = verified_user(users, "paused-worker@example.com")
    queued = users.mark_shopify_sync_pending(user.id)
    assert queued is not None
    selected_generation = queued.shopify_sync_generation
    client = FakeAdminClient(["7001"])
    attempt_started = threading.Event()
    resume_worker = threading.Event()
    original_guard = users.guard_shopify_sync_remote_write

    @contextmanager
    def paused_guard(user_id, attempt, *, generation):
        attempt_started.set()
        assert resume_worker.wait(timeout=5)
        with original_guard(
            user_id,
            attempt,
            generation=generation,
        ) as allowed:
            yield allowed

    monkeypatch.setattr(
        users,
        "guard_shopify_sync_remote_write",
        paused_guard,
    )
    results = []
    worker = threading.Thread(
        target=lambda: results.append(
            sync_app_user_to_shopify(
                users,
                user.id,
                client,
                expected_generation=selected_generation,
            )
        )
    )
    worker.start()
    assert attempt_started.wait(timeout=5)

    if redaction_scope == "customer":
        users.remove_shopify_customer(
            "7001",
            user.email,
            redact=True,
        )
    else:
        result = users.redact_shopify_store(
            "test-store.myshopify.com",
            "test-store.myshopify.com",
            event_id="paused-shop-redaction",
        )
        assert result.applied

    resume_worker.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert len(results) == 1
    assert results[0].action == "superseded"
    assert client.set_calls == []
    current = users.get(user.id)
    assert current is not None
    assert current.shopify_customer_id is None
    assert current.shopify_sync_generation > selected_generation
    assert current.shopify_sync_blocked is (
        redaction_scope == "customer"
    )


@pytest.mark.parametrize("redaction_scope", ("customer", "shop"))
def test_stale_selection_cannot_start_after_redaction(
    tmp_path,
    redaction_scope,
):
    db_path = tmp_path / "users.db"
    users = UserStore(db_path)
    user = verified_user(users, "stale-selection@example.com")
    users.mark_shopify_sync_pending(user.id)
    selected = users.list_due_shopify_syncs()
    assert [item.id for item in selected] == [user.id]

    if redaction_scope == "customer":
        users.remove_shopify_customer(
            "7001",
            user.email,
            redact=True,
        )
    else:
        assert users.redact_shopify_store(
            "test-store.myshopify.com",
            "test-store.myshopify.com",
            event_id="stale-selection-redact",
        ).applied
        bind_backfill_database(
            db_path,
            "test-store.myshopify.com",
            "gid://shopify/Shop/123",
            confirmation="test-store.myshopify.com",
        )
    client = FakeAdminClient(["7001"])

    result = sync_app_user_to_shopify(
        users,
        selected[0].id,
        client,
        expected_generation=selected[0].shopify_sync_generation,
    )

    assert result.action == "superseded"
    assert client.set_calls == []
    assert users.get(user.id).shopify_customer_id is None


def test_authenticated_bind_and_shop_redaction_have_total_order(tmp_path):
    db_path = tmp_path / "users.db"
    users = UserStore(db_path)
    verification_started = threading.Event()
    release_verification = threading.Event()
    redaction_done = threading.Event()
    binding_results = []

    def verify_store():
        verification_started.set()
        assert release_verification.wait(timeout=5)
        return "gid://shopify/Shop/123"

    binder = threading.Thread(
        target=lambda: binding_results.append(
            authenticate_and_bind_backfill_database(
                db_path,
                "test-store.myshopify.com",
                verify_store,
                confirmation="test-store.myshopify.com",
            )
        )
    )
    binder.start()
    assert verification_started.wait(timeout=5)

    def redact():
        users.redact_shopify_store(
            "test-store.myshopify.com",
            "test-store.myshopify.com",
            event_id="bind-race-shop-redaction",
        )
        redaction_done.set()

    redactor = threading.Thread(target=redact)
    redactor.start()
    assert not redaction_done.wait(timeout=0.1)
    release_verification.set()
    binder.join(timeout=5)
    redactor.join(timeout=5)

    assert not binder.is_alive()
    assert not redactor.is_alive()
    assert len(binding_results) == 1
    assert binding_results[0].binding_status == "matched"
    assert redaction_done.is_set()
    assert users._conn.execute(
        "SELECT 1 FROM shopify_customer_backfill_binding WHERE id = 1"
    ).fetchone() is None
    assert users._conn.execute(
        "SELECT shop_redacted FROM shopify_sync_control WHERE id = 1"
    ).fetchone()[0] == 1


def test_authenticated_resolution_cannot_reopen_redacted_binding(tmp_path):
    db_path = tmp_path / "users.db"
    users = UserStore(db_path)
    bind_backfill_database(
        db_path,
        "test-store.myshopify.com",
        "gid://shopify/Shop/123",
        confirmation="test-store.myshopify.com",
    )
    assert users.redact_shopify_store(
        "test-store.myshopify.com",
        "test-store.myshopify.com",
        event_id="resolution-binding-redaction",
    ).applied

    with pytest.raises(ShopifyStoreBindingError):
        authenticate_and_require_backfill_binding(
            db_path,
            "test-store.myshopify.com",
            lambda: "gid://shopify/Shop/123",
        )

    assert users._conn.execute(
        "SELECT 1 FROM shopify_customer_backfill_binding WHERE id = 1"
    ).fetchone() is None
    assert users._conn.execute(
        "SELECT shop_redacted FROM shopify_sync_control WHERE id = 1"
    ).fetchone()[0] == 1


def test_redaction_waits_for_inflight_remote_write_then_wins(tmp_path):
    users = UserStore(tmp_path / "users.db")
    user = verified_user(users, "inflight-worker@example.com")
    users.mark_shopify_sync_pending(user.id)
    remote_started = threading.Event()
    release_remote = threading.Event()
    redaction_done = threading.Event()

    class BlockingAdminClient(FakeAdminClient):
        def set_customer(self, email, customer_id=None):
            remote_started.set()
            assert release_remote.wait(timeout=5)
            return super().set_customer(email, customer_id)

    client = BlockingAdminClient(["7001"])
    sync_results = []
    worker = threading.Thread(
        target=lambda: sync_results.append(
            sync_app_user_to_shopify(users, user.id, client)
        )
    )
    worker.start()
    assert remote_started.wait(timeout=5)

    def redact():
        users.remove_shopify_customer(
            "7001",
            user.email,
            redact=True,
        )
        redaction_done.set()

    redactor = threading.Thread(target=redact)
    redactor.start()
    assert not redaction_done.wait(timeout=0.1)
    release_remote.set()
    worker.join(timeout=5)
    redactor.join(timeout=5)

    assert not worker.is_alive() and not redactor.is_alive()
    assert redaction_done.is_set()
    assert client.set_calls == [(user.email, None)]
    current = users.get(user.id)
    assert current is not None
    assert current.shopify_customer_id is None
    assert current.shopify_sync_blocked


def test_stalled_remote_write_does_not_block_unrelated_database_work(
    tmp_path,
):
    users = UserStore(tmp_path / "users.db")
    syncing = verified_user(users, "stalled-provider@example.com")
    unrelated = verified_user(users, "local-work@example.com")
    users.mark_shopify_sync_pending(syncing.id)
    remote_started = threading.Event()
    release_remote = threading.Event()
    local_done = threading.Event()

    class BlockingAdminClient(FakeAdminClient):
        def set_customer(self, email, customer_id=None):
            remote_started.set()
            assert release_remote.wait(timeout=5)
            return super().set_customer(email, customer_id)

    worker = threading.Thread(
        target=lambda: sync_app_user_to_shopify(
            users,
            syncing.id,
            BlockingAdminClient(["7001"]),
        )
    )
    worker.start()
    assert remote_started.wait(timeout=5)

    def local_work():
        users.mark_shopify_sync_pending(unrelated.id)
        assert users.get(unrelated.id) is not None
        local_done.set()

    local_worker = threading.Thread(target=local_work)
    local_worker.start()
    responsive = local_done.wait(timeout=1)
    release_remote.set()
    worker.join(timeout=5)
    local_worker.join(timeout=5)

    assert responsive
    assert not worker.is_alive()
    assert not local_worker.is_alive()


def test_in_memory_privacy_lock_does_not_create_a_sidecar(monkeypatch):
    import swinglab.web.users as users_module

    def unexpected_open(*args, **kwargs):
        raise AssertionError("in-memory database must not open a lock file")

    monkeypatch.setattr(users_module.os, "open", unexpected_open)
    with users_module.shopify_remote_privacy_lock(":memory:"):
        pass


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
        users,
        linked.id,
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
            users,
            linked.id,
            client,
            email="new.address@example.com",
        )

    assert client.set_calls == []


def test_linked_update_paused_after_attempt_is_fenced_by_redaction(
    tmp_path,
    monkeypatch,
):
    users = UserStore(tmp_path / "users.db")
    user = verified_user(users, "linked-race@example.com")
    linked = users.upsert_store_customer(user.email, "9001")
    client = FakeAdminClient(["9001"])
    attempt_started = threading.Event()
    resume_update = threading.Event()
    original_guard = users.guard_shopify_sync_remote_write

    @contextmanager
    def paused_guard(user_id, attempt, *, generation):
        attempt_started.set()
        assert resume_update.wait(timeout=5)
        with original_guard(
            user_id,
            attempt,
            generation=generation,
        ) as allowed:
            yield allowed

    monkeypatch.setattr(
        users,
        "guard_shopify_sync_remote_write",
        paused_guard,
    )
    errors = []

    def update():
        try:
            update_linked_shopify_customer(
                users,
                linked.id,
                client,
                email=linked.email,
            )
        except BaseException as exc:  # captured for deterministic assertion
            errors.append(exc)

    worker = threading.Thread(target=update)
    worker.start()
    assert attempt_started.wait(timeout=5)
    users.remove_shopify_customer("9001", linked.email, redact=True)
    resume_update.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], ShopifySyncPolicyError)
    assert client.set_calls == []
    current = users.get(linked.id)
    assert current is not None
    assert current.shopify_customer_id is None
    assert current.shopify_sync_blocked


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
    db_path = tmp_path / "users.db"
    users = UserStore(db_path)
    user = verified_user(users, "queued@example.com")
    users.mark_shopify_sync_pending(user.id)
    client = FakeAdminClient(["88"])
    bind_backfill_database(
        db_path,
        client.store_domain,
        client.shop_gid,
        confirmation=client.store_domain,
    )
    coordinator = ShopifyCustomerSyncCoordinator(
        users,
        client,
        {"max_attempts": 3},
        binding_db_path=db_path,
        start=False,
    )

    assert coordinator.run_once() == 1
    assert users.get(user.id).shopify_customer_id == "88"
    assert coordinator.run_once() == 0


def test_unbound_coordinator_blocks_without_network_or_customer_writes(
    tmp_path,
):
    db_path = tmp_path / "users.db"
    users = UserStore(db_path)
    user = verified_user(users, "queued@example.com")
    users.mark_shopify_sync_pending(user.id)
    client = FakeAdminClient(["88"])
    coordinator = ShopifyCustomerSyncCoordinator(
        users,
        client,
        {"max_attempts": 3},
        binding_db_path=db_path,
        start=False,
    )

    assert coordinator.run_once() == 0
    assert coordinator.binding_status == "unbound"
    assert client.verify_calls == 0
    assert client.set_calls == []
    assert users.get(user.id).shopify_customer_id is None


def test_wrong_store_domain_is_hard_blocked_before_network(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "users.db"
    users = UserStore(db_path)
    user = verified_user(users, "queued@example.com")
    users.mark_shopify_sync_pending(user.id)
    bound = FakeAdminClient(store_domain="prod-store.myshopify.com")
    bind_backfill_database(
        db_path,
        bound.store_domain,
        bound.shop_gid,
        confirmation=bound.store_domain,
    )
    wrong = FakeAdminClient(
        ["88"],
        store_domain="dev-store.myshopify.com",
    )
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", wrong.store_domain)
    coordinator = ShopifyCustomerSyncCoordinator(
        users,
        wrong,
        {"max_attempts": 3},
        binding_db_path=db_path,
        start=False,
    )

    assert coordinator.run_once() == 0
    assert coordinator.binding_status == "mismatch"
    assert wrong.verify_calls == 0
    assert wrong.set_calls == []
    assert users.get(user.id).shopify_customer_id is None


def test_split_webhook_and_admin_stores_block_before_network_or_enrollment(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "users.db"
    admin_client = FakeAdminClient(
        ["88"],
        store_domain="admin-store-b.myshopify.com",
    )
    bind_test_database(db_path, admin_client)
    monkeypatch.setenv(
        "SHOPIFY_STORE_DOMAIN",
        "webhook-store-a.myshopify.com",
    )
    users = UserStore(db_path)
    pending = verified_user(users, "already-pending@example.com")
    users.mark_shopify_sync_pending(pending.id)
    new_user = verified_user(users, "not-enrolled@example.com")
    coordinator = ShopifyCustomerSyncCoordinator(
        users,
        admin_client,
        {"max_attempts": 3},
        binding_db_path=db_path,
        start=False,
    )

    assert coordinator.binding_status == "mismatch"
    assert not coordinator.enrollment_allowed
    assert not coordinator.enqueue(new_user.id)
    assert coordinator.run_once() == 0
    coordinator.start()

    assert coordinator._thread is None
    assert users.get(new_user.id).shopify_sync_status == "not_started"
    assert users.get(pending.id).shopify_sync_status == SHOPIFY_SYNC_PENDING
    diagnostic = coordinator.health_snapshot()
    assert diagnostic["binding_safe_error"] == (
        "Inbound and outbound Shopify store configuration does not match."
    )
    assert "webhook-store-a" not in repr(diagnostic)
    assert "admin-store-b" not in repr(diagnostic)
    assert admin_client.verify_calls == 0
    assert admin_client.lookup_calls == []
    assert admin_client.set_calls == []


def test_runtime_webhook_store_drift_revokes_then_matching_store_recovers(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "users.db"
    client = FakeAdminClient(["91"])
    bind_test_database(db_path, client)
    users = UserStore(db_path)
    coordinator = ShopifyCustomerSyncCoordinator(
        users,
        client,
        {"max_attempts": 3},
        binding_db_path=db_path,
        start=False,
    )
    assert coordinator.verify_store_binding()
    assert coordinator.enrollment_allowed
    user = verified_user(users, "runtime-drift@example.com")
    users.mark_shopify_sync_pending(user.id)

    monkeypatch.setenv(
        "SHOPIFY_STORE_DOMAIN",
        "other-store.myshopify.com",
    )

    assert not coordinator.enrollment_allowed
    assert coordinator.run_once() == 0
    assert coordinator.binding_status == "mismatch"
    assert client.verify_calls == 1
    assert client.set_calls == []

    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", client.store_domain)

    assert coordinator.verify_store_binding()
    assert coordinator.run_once() == 1
    assert users.get(user.id).shopify_customer_id == "91"


def test_exact_shop_gid_not_truncated_display_ref_authorizes_binding(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "users.db"
    users = UserStore(db_path)
    user = verified_user(users, "queued@example.com")
    users.mark_shopify_sync_pending(user.id)
    bound = FakeAdminClient(shop_gid="gid://shopify/Shop/111")
    bind_backfill_database(
        db_path,
        bound.store_domain,
        bound.shop_gid,
        confirmation=bound.store_domain,
    )
    # Simulate a diagnostic-ref collision. Correctness must still compare
    # the private exact canonical Shop GID.
    monkeypatch.setattr(
        "swinglab.integrations.shopify.backfill._safe_ref",
        lambda value: "same-ref",
    )
    wrong = FakeAdminClient(
        ["88"],
        shop_gid="gid://shopify/Shop/222",
    )
    coordinator = ShopifyCustomerSyncCoordinator(
        users,
        wrong,
        {"max_attempts": 3},
        binding_db_path=db_path,
        start=False,
    )

    assert coordinator.run_once() == 0
    assert coordinator.binding_status == "mismatch"
    assert wrong.verify_calls == 1
    assert wrong.set_calls == []
    assert users.get(user.id).shopify_customer_id is None


def test_remote_store_auth_failure_blocks_customer_work(tmp_path):
    db_path = tmp_path / "users.db"
    users = UserStore(db_path)
    user = verified_user(users, "queued@example.com")
    users.mark_shopify_sync_pending(user.id)
    failure = admin.ShopifyAdminTransportError(
        "temporary authentication outage",
        retryable=True,
        status_code=503,
    )
    client = FakeAdminClient(["88"], verify_error=failure)
    bind_backfill_database(
        db_path,
        client.store_domain,
        client.shop_gid,
        confirmation=client.store_domain,
    )
    coordinator = ShopifyCustomerSyncCoordinator(
        users,
        client,
        {"max_attempts": 3},
        binding_db_path=db_path,
        start=False,
    )

    assert coordinator.run_once() == 0
    assert coordinator.binding_status == "unverifiable"
    assert coordinator.enrollment_allowed
    assert client.verify_calls == 1
    assert client.set_calls == []
    assert users.get(user.id).shopify_customer_id is None


def test_transient_startup_outage_retries_and_processes_durable_outbox(
    tmp_path,
):
    db_path = tmp_path / "users.db"
    users = UserStore(db_path)
    user = verified_user(users, "queued@example.com")
    users.mark_shopify_sync_pending(user.id)
    outage = admin.ShopifyAdminTransportError(
        "temporary authentication outage",
        retryable=True,
        status_code=503,
    )
    client_gid = "gid://shopify/Shop/123"
    client = FakeAdminClient(
        ["88"],
        verify_results=[outage, client_gid],
        shop_gid=client_gid,
    )
    bind_backfill_database(
        db_path,
        client.store_domain,
        client.shop_gid,
        confirmation=client.store_domain,
    )
    coordinator = ShopifyCustomerSyncCoordinator(
        users,
        client,
        {"max_attempts": 3},
        binding_db_path=db_path,
        binding_retry_base_seconds=0.05,
        binding_retry_max_seconds=0.1,
        poll_seconds=0.05,
        start=False,
    )

    coordinator.start()
    try:
        assert coordinator.worker_alive
        deadline = time.time() + 2
        while (
            users.get(user.id).shopify_customer_id is None
            and time.time() < deadline
        ):
            time.sleep(0.01)
        assert users.get(user.id).shopify_customer_id == "88"
        assert coordinator.binding_verified
    finally:
        coordinator.shutdown()


def test_matching_binding_survives_coordinator_restart(tmp_path):
    db_path = tmp_path / "users.db"
    users = UserStore(db_path)
    client = FakeAdminClient()
    bind_backfill_database(
        db_path,
        client.store_domain,
        client.shop_gid,
        confirmation=client.store_domain,
    )
    first = ShopifyCustomerSyncCoordinator(
        users,
        client,
        {"max_attempts": 3},
        binding_db_path=db_path,
        start=False,
    )
    assert first.verify_store_binding()

    user = verified_user(users, "after-restart@example.com")
    users.mark_shopify_sync_pending(user.id)
    restarted_client = FakeAdminClient(["99"])
    restarted = ShopifyCustomerSyncCoordinator(
        users,
        restarted_client,
        {"max_attempts": 3},
        binding_db_path=db_path,
        start=False,
    )

    assert restarted.run_once() == 1
    assert users.get(user.id).shopify_customer_id == "99"


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
    assert first.next_cursor.startswith("bf1_")
    assert second.scanned == 1
    assert second.next_cursor is None
    assert first.would_link + first.would_create == 2
    assert second.would_link + second.would_create == 1
    rows, _ = users.list_shopify_sync_health(limit=10)
    assert all(row.shopify_sync_status == "not_started" for row in rows)
    assert all(row.shopify_customer_id is None for row in rows)


def test_readonly_backfill_lookup_is_fenced_by_concurrent_redaction(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "users.db"
    writer = UserStore(db_path)
    user = verified_user(writer, "dry-run-race@example.com")
    lookup_reached = threading.Event()
    resume_lookup = threading.Event()
    original_guard = ReadOnlyBackfillStore.guard_shopify_sync_remote_read

    @contextmanager
    def paused_guard(store, user_id, *, generation):
        lookup_reached.set()
        assert resume_lookup.wait(timeout=5)
        with original_guard(
            store,
            user_id,
            generation=generation,
        ) as current_email:
            yield current_email

    monkeypatch.setattr(
        ReadOnlyBackfillStore,
        "guard_shopify_sync_remote_read",
        paused_guard,
    )
    client = FakeAdminClient(
        lookup={user.email: "gid://shopify/Customer/7001"}
    )
    summaries = []

    def run_readonly():
        with ReadOnlyBackfillStore(db_path) as readonly:
            summaries.append(
                run_backfill_batch(
                    readonly,
                    client,
                    batch_size=10,
                    dry_run=True,
                )
            )

    worker = threading.Thread(target=run_readonly)
    worker.start()
    assert lookup_reached.wait(timeout=5)
    writer.remove_shopify_customer(
        "7001",
        user.email,
        redact=True,
    )
    resume_lookup.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert client.lookup_calls == []
    assert len(summaries) == 1
    assert summaries[0].requires_review == 1


def test_backfill_cursor_is_store_scoped_and_not_reversible(tmp_path):
    users = UserStore(tmp_path / "users.db")
    for number in range(2):
        verified_user(users, f"golfer{number}@example.com")
    first = run_backfill_batch(
        users,
        FakeAdminClient(),
        batch_size=1,
        dry_run=True,
    )
    rows, _ = users.list_shopify_sync_health(limit=10)

    assert first.next_cursor
    assert all(row.id not in first.next_cursor for row in rows)
    with pytest.raises(BackfillSafetyError, match="database and store"):
        run_backfill_batch(
            users,
            FakeAdminClient(store_domain="other-store.myshopify.com"),
            batch_size=1,
            after=first.next_cursor,
            dry_run=True,
        )


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


def test_backfill_apply_persists_locked_and_unverified_review_state(tmp_path):
    users = UserStore(tmp_path / "users.db")
    unverified = users.create("unverified@example.com", "longenough")
    locked = verified_user(users, "locked@example.com")
    users.upsert_store_customer(locked.email, "55")
    users.unlink_shopify(locked.id)

    summary = run_backfill_batch(
        users,
        FakeAdminClient(),
        batch_size=10,
        dry_run=False,
    )

    assert summary.requires_review == 2
    assert (
        users.get(unverified.id).shopify_sync_status
        == SHOPIFY_SYNC_REQUIRES_REVIEW
    )
    assert (
        users.get(locked.id).shopify_sync_status
        == SHOPIFY_SYNC_REQUIRES_REVIEW
    )
    assert users.get(unverified.id).shopify_sync_attempt_token is None
    assert users.get(locked.id).shopify_sync_attempt_token is None


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


def test_backfill_all_returns_one_cumulative_bounded_summary(tmp_path):
    users = UserStore(tmp_path / "users.db")
    for number in range(5):
        verified_user(users, f"golfer{number}@example.com")
    client = FakeAdminClient(
        lookup={
            f"golfer{number}@example.com": None
            for number in range(5)
        }
    )

    summary = run_backfill_all(
        users,
        client,
        batch_size=2,
        dry_run=True,
        max_items=3,
    )

    assert summary.scanned == 5
    assert summary.would_create == 5
    assert summary.batches == 3
    assert summary.next_cursor is None
    assert len(summary.items) == 3
    assert summary.items_truncated == 2


def test_operator_resolution_verifies_remote_email_before_linking(tmp_path):
    users = UserStore(tmp_path / "users.db")
    user = verified_user(users, "manual@example.com")
    client = FakeAdminClient(
        lookup={"manual@example.com": "gid://shopify/Customer/77"}
    )

    result = verify_and_link_existing_shopify_customer(
        users,
        user.id,
        "77",
        client,
    )

    assert result.status == SHOPIFY_SYNC_SYNCED
    assert users.get(user.id).shopify_customer_id == "77"
    assert client.lookup_calls == ["manual@example.com"]
    assert len(operator_user_ref(user.id)) == 12


def test_operator_resolution_rejects_remote_identity_mismatch(tmp_path):
    users = UserStore(tmp_path / "users.db")
    user = verified_user(users, "manual@example.com")
    client = FakeAdminClient(
        lookup={"manual@example.com": "88"}
    )

    result = verify_and_link_existing_shopify_customer(
        users,
        user.id,
        "77",
        client,
    )

    assert result.action == "remote_identity_mismatch"
    assert result.status == SHOPIFY_SYNC_REQUIRES_REVIEW
    assert users.get(user.id).shopify_customer_id is None
    assert (
        users.get(user.id).shopify_sync_status
        == SHOPIFY_SYNC_REQUIRES_REVIEW
    )


def test_operator_resolution_requires_verified_email_without_remote_call(
    tmp_path,
):
    users = UserStore(tmp_path / "users.db")
    user = users.create("manual@example.com", "longenough")
    client = FakeAdminClient(lookup={"manual@example.com": "77"})

    result = verify_and_link_existing_shopify_customer(
        users,
        user.id,
        "77",
        client,
    )

    assert result.action == "unverified_user"
    assert client.lookup_calls == []
    assert (
        users.get(user.id).shopify_sync_status
        == SHOPIFY_SYNC_REQUIRES_REVIEW
    )


def test_coordinator_health_snapshot_is_pii_free_and_counts_backlog(tmp_path):
    users = UserStore(tmp_path / "users.db")
    pending = verified_user(users, "pending@example.com")
    failed = verified_user(users, "failed@example.com")
    review = users.create("review@example.com", "longenough")
    users.mark_shopify_sync_pending(pending.id)
    _, failed_attempt = users.start_shopify_sync(failed.id)
    users.record_shopify_sync_failure(
        failed.id,
        failed_attempt,
        SHOPIFY_SYNC_FAILED,
        "retry later",
        next_attempt_at=100,
    )
    _, review_attempt = users.start_shopify_sync(review.id)
    users.record_shopify_sync_failure(
        review.id,
        review_attempt,
        SHOPIFY_SYNC_REQUIRES_REVIEW,
        "manual review",
    )
    coordinator = ShopifyCustomerSyncCoordinator(
        users,
        FakeAdminClient(),
        {"max_attempts": 3},
        start=False,
    )

    snapshot = coordinator.health_snapshot(now=200)

    assert snapshot["worker_alive"] is False
    assert snapshot["pending"] == 1
    assert snapshot["failed"] == 1
    assert snapshot["requires_review"] == 1
    assert snapshot["due"] == 2
    assert snapshot["oldest_due_at"] is not None
    rendered = repr(snapshot)
    assert "pending@example.com" not in rendered
    assert pending.id not in rendered


def test_global_flag_alone_keeps_new_registration_out_of_sync_cohort(tmp_path):
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
    assert user.shopify_sync_status == "not_started"
    assert app.state.shopify_sync.run_once() == 0
    assert client.set_calls == []


def test_disabled_bridge_makes_no_admin_network_calls(tmp_path):
    client = FakeAdminClient(
        verify_error=AssertionError("disabled bridge contacted Shopify")
    )
    app = create_app(
        Config(),
        sessions_dir=tmp_path / "sessions",
        shopify_admin_client=client,
    )

    with TestClient(app) as web:
        health = web.get("/healthz").json()

    assert app.state.shopify_sync is None
    assert client.verify_calls == 0
    assert client.lookup_calls == []
    assert client.set_calls == []
    assert health["shopify_customer_sync"]["binding_status"] == "disabled"


def test_dev_store_configuration_cannot_run_against_prod_bound_database(
    tmp_path, monkeypatch
):
    sessions = tmp_path / "sessions"
    prod = FakeAdminClient(store_domain="prod-store.myshopify.com")
    bind_test_database(sessions / "swinglab.db", prod)
    dev = FakeAdminClient(
        ["7003"],
        store_domain="dev-store.myshopify.com",
    )
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", dev.store_domain)
    cfg = Config()
    cfg.shopify_customer_sync["enabled"] = True
    app = create_app(
        cfg,
        sessions_dir=sessions,
        shopify_admin_client=dev,
    )

    with TestClient(app) as web:
        health = web.get("/healthz").json()

    assert health["status"] == "degraded"
    assert (
        health["shopify_customer_sync"]["binding_status"] == "mismatch"
    )
    assert app.state.shopify_sync._thread is None
    assert dev.verify_calls == 0
    assert dev.set_calls == []


def test_split_store_app_keeps_inbound_healthy_and_never_enrolls_outbound(
    tmp_path, monkeypatch
):
    sessions = tmp_path / "sessions"
    admin_client = FakeAdminClient(
        ["7003"],
        store_domain="admin-store-b.myshopify.com",
    )
    bind_test_database(sessions / "swinglab.db", admin_client)
    webhook_store = "webhook-store-a.myshopify.com"
    webhook_secret = "split-store-webhook-secret"
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", webhook_store)
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", webhook_secret)
    monkeypatch.setenv("SHOPIFY_CUSTOMER_SYNC_COHORT_PERCENT", "100")
    monkeypatch.setenv("SWINGLAB_SECRET", "stable-test-secret")
    monkeypatch.setattr("swinglab.web.app.mailer.enabled", lambda: True)
    sent = []
    monkeypatch.setattr(
        "swinglab.web.app.mailer.send",
        lambda to, subject, body: sent.append((to, subject, body)),
    )
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.shopify_customer_sync["enabled"] = True
    app = create_app(
        cfg,
        sessions_dir=sessions,
        shopify_admin_client=admin_client,
    )

    with TestClient(app) as web:
        health_response = web.get("/healthz")
        health = health_response.json()
        assert health_response.status_code == 200
        assert health["status"] == "degraded"
        assert (
            health["shopify_customer_sync"]["binding_status"]
            == "mismatch"
        )

        payload = json.dumps(
            {
                "id": 7001,
                "email": "store-first@example.com",
            }
        ).encode()
        signature = base64.b64encode(
            hmac.new(
                webhook_secret.encode(),
                payload,
                hashlib.sha256,
            ).digest()
        ).decode()
        inbound = web.post(
            "/webhooks/shopify",
            content=payload,
            headers={
                "X-Shopify-Hmac-Sha256": signature,
                "X-Shopify-Topic": "customers/create",
                "X-Shopify-Shop-Domain": webhook_store,
                "X-Shopify-Webhook-Id": "split-store-inbound-1",
                "Content-Type": "application/json",
            },
        )
        assert inbound.status_code == 200

        requested = web.post(
            "/login/email",
            data={
                "email": "app-first@example.com",
                "auth_intent": "signup",
            },
        )
        assert requested.status_code == 200
        registered = web.post(
            "/login/code",
            data={
                "email": "app-first@example.com",
                "code": _last_emailed_code(sent),
                "auth_intent": "signup",
            },
            follow_redirects=False,
        )
        assert registered.status_code == 303

    store_first = app.state.users.get_by_email("store-first@example.com")
    app_first = app.state.users.get_by_email("app-first@example.com")
    assert store_first.shopify_customer_id == "7001"
    assert app_first.shopify_sync_status == "not_started"
    assert app.state.shopify_sync._thread is None
    assert admin_client.verify_calls == 0
    assert admin_client.lookup_calls == []
    assert admin_client.set_calls == []


def test_unbound_enabled_app_stays_healthy_and_hard_blocks_worker(tmp_path):
    client = FakeAdminClient(["7003"])
    cfg = Config()
    cfg.shopify_customer_sync["enabled"] = True
    app = create_app(
        cfg,
        sessions_dir=tmp_path / "sessions",
        shopify_admin_client=client,
    )

    with TestClient(app) as web:
        response = web.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert (
        response.json()["shopify_customer_sync"]["binding_status"]
        == "unbound"
    )
    assert app.state.shopify_sync._thread is None
    assert client.verify_calls == 0
    assert client.set_calls == []


def test_malformed_admin_environment_keeps_web_healthy_and_sync_unverifiable(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(
        "SHOPIFY_ADMIN_STORE_DOMAIN",
        "https://wrong.example.com/path",
    )
    monkeypatch.setenv("SHOPIFY_ADMIN_ACCESS_TOKEN", "shpat_test_secret")
    monkeypatch.setenv("SHOPIFY_ADMIN_API_VERSION", "2026-07")
    cfg = Config()
    cfg.shopify_customer_sync["enabled"] = True
    app = create_app(cfg, sessions_dir=tmp_path / "sessions")

    with TestClient(app) as web:
        response = web.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert (
        response.json()["shopify_customer_sync"]["binding_status"]
        == "unverifiable"
    )
    assert app.state.shopify_admin_client is None


def test_registration_during_startup_auth_outage_remains_durable_and_recovers(
    tmp_path, monkeypatch
):
    sessions = tmp_path / "sessions"
    outage = admin.ShopifyAdminTransportError(
        "temporary authentication outage",
        retryable=True,
        status_code=503,
    )
    client = FakeAdminClient(
        ["7005"],
        verify_results=[outage, "gid://shopify/Shop/123"],
    )
    bind_test_database(sessions / "swinglab.db", client)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.shopify_customer_sync["enabled"] = True
    monkeypatch.setenv("SHOPIFY_CUSTOMER_SYNC_COHORT_PERCENT", "100")
    monkeypatch.setenv("SWINGLAB_SECRET", "stable-test-secret")
    monkeypatch.setattr("swinglab.web.app.mailer.enabled", lambda: True)
    sent = []
    monkeypatch.setattr(
        "swinglab.web.app.mailer.send",
        lambda to, subject, body: sent.append((to, subject, body)),
    )
    app = create_app(
        cfg,
        sessions_dir=sessions,
        shopify_admin_client=client,
    )
    app.state.shopify_sync.binding_retry_base_seconds = 0.05
    app.state.shopify_sync.binding_retry_max_seconds = 0.1

    with TestClient(app) as web:
        assert app.state.shopify_sync.worker_alive
        requested = web.post(
            "/login/email",
            data={
                "email": "outage-signup@example.com",
                "auth_intent": "signup",
            },
        )
        assert requested.status_code == 200
        response = web.post(
            "/login/code",
            data={
                "email": "outage-signup@example.com",
                "code": _last_emailed_code(sent),
                "auth_intent": "signup",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        user = app.state.users.get_by_email("outage-signup@example.com")
        assert user.shopify_sync_status != "not_started"
        deadline = time.time() + 2
        while (
            app.state.users.get(user.id).shopify_customer_id is None
            and time.time() < deadline
        ):
            time.sleep(0.01)
        stored = app.state.users.get(user.id)
        assert stored.shopify_customer_id == "7005"
        assert stored.shopify_sync_status == SHOPIFY_SYNC_SYNCED


def test_classic_signup_verification_then_syncs_automatically(
    tmp_path, monkeypatch
):
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.shopify_customer_sync["enabled"] = True
    monkeypatch.setenv("SHOPIFY_CUSTOMER_SYNC_COHORT_PERCENT", "100")
    monkeypatch.setenv("SWINGLAB_SECRET", "stable-test-secret")
    client = FakeAdminClient(["7003"])
    sessions = tmp_path / "sessions"
    bind_test_database(sessions / "swinglab.db", client)
    app = create_app(
        cfg,
        sessions_dir=sessions,
        shopify_admin_client=client,
        start_shopify_sync_worker=False,
    )
    assert app.state.shopify_sync.verify_store_binding()
    monkeypatch.setattr("swinglab.web.app.mailer.enabled", lambda: True)
    sent = []
    monkeypatch.setattr(
        "swinglab.web.app.mailer.send",
        lambda to, subject, body: sent.append((to, subject, body)),
    )
    web = TestClient(app)

    verification = web.post(
        "/signup",
        data={
            "email": "classic@example.com",
            "password": "longenough",
        },
        follow_redirects=False,
    )
    intent = re.search(
        r'name="signup_intent" value="([^"]+)"', verification.text
    ).group(1)
    assert verification.headers["cache-control"] == "no-store"
    assert "longenough" not in verification.text
    response = web.post(
        "/signup",
        data={
            "signup_intent": intent,
            "code": _last_emailed_code(sent),
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
    monkeypatch.setenv("SHOPIFY_CUSTOMER_SYNC_COHORT_PERCENT", "100")
    monkeypatch.setenv("SWINGLAB_SECRET", "stable-test-secret")
    outage = admin.ShopifyAdminTransportError(
        "Shopify is temporarily unavailable.",
        retryable=True,
        status_code=503,
    )
    client = FakeAdminClient([outage])
    sessions = tmp_path / "sessions"
    bind_test_database(sessions / "swinglab.db", client)
    app = create_app(
        cfg,
        sessions_dir=sessions,
        shopify_admin_client=client,
        start_shopify_sync_worker=False,
    )
    assert app.state.shopify_sync.verify_store_binding()
    monkeypatch.setattr("swinglab.web.app.mailer.enabled", lambda: True)
    sent = []
    monkeypatch.setattr(
        "swinglab.web.app.mailer.send",
        lambda to, subject, body: sent.append((to, subject, body)),
    )
    web = TestClient(app)

    requested = web.post(
        "/login/email",
        data={
            "email": "verified@example.com",
            "auth_intent": "signup",
        },
    )
    assert requested.status_code == 200
    response = web.post(
        "/login/code",
        data={
            "email": "verified@example.com",
            "code": _last_emailed_code(sent),
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
    client = FakeAdminClient()
    sessions = tmp_path / "sessions"
    bind_test_database(sessions / "swinglab.db", client)
    app = create_app(
        cfg,
        sessions_dir=sessions,
        shopify_admin_client=client,
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
    user_ref = operator_user_ref(user.id)
    item = next(
        row
        for row in response.json()["users"]
        if row["user_ref"] == user_ref
    )
    assert "email" not in item
    assert "user_id" not in item
    assert "shopify_customer_id" not in item
    assert item["linked"] is False
    assert response.json()["binding"]["binding_status"] == "unchecked"

    retry = web.post(
        f"/admin/shopify-sync/ref/{user_ref}/retry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert retry.status_code == 202
    assert retry.json() == {"queued": True, "user_ref": user_ref}
    assert app.state.shopify_sync.worker_alive
    assert app.state.users.get(user.id).shopify_sync_status == (
        SHOPIFY_SYNC_PENDING
    )
    app.state.shopify_sync.shutdown()


def test_admin_sync_exact_customer_id_is_on_demand_and_protected(
    tmp_path, monkeypatch
):
    cfg = Config()
    cfg.shopify_customer_sync["enabled"] = True
    client = FakeAdminClient()
    sessions = tmp_path / "sessions"
    bind_test_database(sessions / "swinglab.db", client)
    app = create_app(
        cfg,
        sessions_dir=sessions,
        shopify_admin_client=client,
        start_shopify_sync_worker=False,
    )
    user = verified_user(app.state.users, "exact-id@example.com")
    app.state.users.upsert_store_customer(user.email, "7001")
    user_ref = operator_user_ref(user.id)
    token = "admin-token-with-enough-entropy"
    monkeypatch.setenv("SWINGLAB_ADMIN_TOKEN", token)
    web = TestClient(app)
    detail_path = f"/admin/shopify-sync/ref/{user_ref}"

    assert web.get(detail_path).status_code == 404
    assert web.get(
        detail_path,
        headers={"Authorization": "Bearer wrong"},
    ).status_code == 404
    assert web.get(
        f"/admin/shopify-sync/ref/{user.id}",
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 404

    response = web.get(
        detail_path,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["user_ref"] == user_ref
    assert response.json()["linked"] is True
    assert response.json()["shopify_customer_id"] == "7001"
    assert user.id not in response.text
    assert user.email not in response.text

    broad = web.get(
        "/admin/shopify-sync",
        headers={"Authorization": f"Bearer {token}"},
    )
    broad_item = next(
        row
        for row in broad.json()["users"]
        if row["user_ref"] == user_ref
    )
    assert "shopify_customer_id" not in broad_item
    assert "7001" not in web.get("/account").text


def test_admin_sync_uses_only_opaque_refs_and_protected_exact_health(
    tmp_path, monkeypatch
):
    cfg = Config()
    cfg.shopify_customer_sync["enabled"] = True
    client = FakeAdminClient()
    sessions = tmp_path / "sessions"
    bind_test_database(sessions / "swinglab.db", client)
    app = create_app(
        cfg,
        sessions_dir=sessions,
        shopify_admin_client=client,
        start_shopify_sync_worker=False,
    )
    first = app.state.users.create("first-status@example.com", "longenough")
    second = app.state.users.create("second-status@example.com", "longenough")
    app.state.users.mark_shopify_sync_pending(first.id)
    token = "admin-token-with-enough-entropy"
    monkeypatch.setenv("SWINGLAB_ADMIN_TOKEN", token)
    web = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    page = web.get(
        "/admin/shopify-sync?limit=1",
        headers=headers,
    ).json()

    assert page["health"]["pending"] == 1
    assert page["next_cursor"]
    assert len(page["next_cursor"]) == 12
    rendered = repr(page)
    assert first.id not in rendered
    assert second.id not in rendered
    assert "user_id" not in rendered
    assert "shopify_customer_id" not in rendered

    next_page = web.get(
        f"/admin/shopify-sync?limit=1&after={page['next_cursor']}",
        headers=headers,
    )
    assert next_page.status_code == 200
    assert next_page.json()["users"]
    assert web.post(
        f"/admin/shopify-sync/{first.id}/retry",
        headers=headers,
    ).status_code == 404


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
    client = FakeAdminClient()
    sessions = tmp_path / "sessions"
    bind_test_database(sessions / "swinglab.db", client)
    app = create_app(
        cfg,
        sessions_dir=sessions,
        shopify_admin_client=client,
    )
    coordinator = app.state.shopify_sync

    assert coordinator._thread is None
    with TestClient(app) as web:
        assert coordinator._thread is not None
        assert coordinator._thread.is_alive()
        health = web.get("/healthz").json()
        assert health["status"] == "ok"
        sync = health["shopify_customer_sync"]
        assert sync["enabled"] is True
        assert sync["worker_expected"] is True
        assert sync["worker_alive"] is True
        assert sync["binding_status"] == "verified"
        assert "cohort_percent" not in sync
        assert "total" not in sync
        assert "pending" not in sync
        assert "email" not in repr(sync)
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


def test_invalid_sync_cohort_percentage_fails_before_database_start(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SHOPIFY_CUSTOMER_SYNC_COHORT_PERCENT", "101")
    sessions = tmp_path / "sessions"

    with pytest.raises(
        admin.ShopifyAdminConfigurationError,
        match="cohort percentage is invalid",
    ):
        create_app(Config(), sessions_dir=sessions)

    assert not sessions.exists()


def test_nonempty_sync_cohort_requires_stable_session_secret(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SHOPIFY_CUSTOMER_SYNC_COHORT_PERCENT", "5")
    monkeypatch.delenv("SWINGLAB_SECRET", raising=False)
    cfg = Config()
    cfg.shopify_customer_sync["enabled"] = True
    sessions = tmp_path / "sessions"

    with pytest.raises(
        admin.ShopifyAdminConfigurationError,
        match="stable SWINGLAB_SECRET",
    ):
        create_app(
            cfg,
            sessions_dir=sessions,
            shopify_admin_client=FakeAdminClient(),
        )

    assert not sessions.exists()


def test_healthz_exposes_pii_free_sync_backlog_without_running_worker(
    tmp_path
):
    cfg = Config()
    cfg.shopify_customer_sync["enabled"] = True
    app = create_app(
        cfg,
        sessions_dir=tmp_path / "sessions",
        shopify_admin_client=FakeAdminClient(),
        start_shopify_sync_worker=False,
    )
    user = app.state.users.verify_email_signin("private@example.com")
    app.state.users.mark_shopify_sync_pending(user.id)

    health = TestClient(app).get("/healthz").json()

    assert health["status"] == "ok"
    sync = health["shopify_customer_sync"]
    assert sync["worker_expected"] is False
    assert sync["worker_alive"] is False
    assert sync["backlog_present"] is True
    assert sync["due_work_present"] is True
    assert "backlog" not in sync
    assert "pending" not in sync
    assert "due" not in sync
    assert "oldest_due_at" not in sync
    assert "cohort_percent" not in sync
    assert "private@example.com" not in repr(sync)
    assert user.id not in repr(sync)
