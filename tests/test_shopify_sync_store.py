"""Persistence contracts for outbound Shopify customer synchronization."""

from __future__ import annotations

import sqlite3

import pytest

from swinglab.integrations.shopify.identity import (
    customer_gid,
    normalize_customer_id,
)
from swinglab.web.users import (
    SHOPIFY_SYNC_FAILED,
    SHOPIFY_SYNC_NOT_STARTED,
    SHOPIFY_SYNC_PENDING,
    SHOPIFY_SYNC_REQUIRES_REVIEW,
    SHOPIFY_SYNC_SYNCED,
    UserStore,
)


def _legacy_users(path, rows):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE users ("
        " id TEXT PRIMARY KEY,"
        " email TEXT NOT NULL UNIQUE,"
        " password_hash TEXT NOT NULL,"
        " created_at REAL NOT NULL,"
        " stripe_customer_id TEXT,"
        " plan TEXT NOT NULL DEFAULT 'free',"
        " subscription_status TEXT NOT NULL DEFAULT 'none',"
        " shopify_customer_id TEXT"
        ")"
    )
    conn.executemany(
        "INSERT INTO users"
        " (id, email, password_hash, created_at, shopify_customer_id)"
        " VALUES (?, ?, 'hash', 1, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def _has_customer_index(store: UserStore) -> bool:
    return (
        store._conn.execute(
            "SELECT 1 FROM sqlite_master"
            " WHERE type = 'index'"
            " AND name = 'users_shopify_customer_id_unique'"
        ).fetchone()
        is not None
    )


def test_customer_identity_normalization():
    assert normalize_customer_id(123) == "123"
    assert normalize_customer_id(" 00123 ") == "123"
    assert normalize_customer_id("gid://shopify/Customer/123") == "123"
    assert customer_gid("00123") == "gid://shopify/Customer/123"
    assert customer_gid("gid://shopify/Customer/123") == (
        "gid://shopify/Customer/123"
    )
    assert normalize_customer_id(None) is None
    assert normalize_customer_id("  ") is None

    for invalid in (
        True,
        0,
        -1,
        "customer-123",
        "gid://shopify/Order/123",
        "gid://shopify/Customer/nope",
    ):
        with pytest.raises(ValueError, match="Invalid Shopify customer ID"):
            normalize_customer_id(invalid)


def test_legacy_migration_initializes_status_and_canonical_id(tmp_path):
    db = tmp_path / "legacy.sqlite"
    _legacy_users(
        db,
        (
            ("linked", "linked@example.com", "gid://shopify/Customer/7001"),
            ("local", "local@example.com", None),
        ),
    )

    store = UserStore(db)

    linked = store.get("linked")
    local = store.get("local")
    assert linked.shopify_customer_id == "7001"
    assert linked.shopify_sync_status == SHOPIFY_SYNC_SYNCED
    assert local.shopify_sync_status == SHOPIFY_SYNC_NOT_STARTED
    assert _has_customer_index(store)

    reopened = UserStore(db)
    assert reopened.get("linked").shopify_sync_status == SHOPIFY_SYNC_SYNCED
    assert _has_customer_index(reopened)


def test_duplicate_preflight_marks_review_without_crashing_or_deleting(tmp_path):
    db = tmp_path / "conflict.sqlite"
    _legacy_users(
        db,
        (
            ("numeric", "numeric@example.com", "7001"),
            ("gid", "gid@example.com", "gid://shopify/Customer/7001"),
        ),
    )

    store = UserStore(db)

    assert {
        store.get("numeric").shopify_sync_status,
        store.get("gid").shopify_sync_status,
    } == {SHOPIFY_SYNC_REQUIRES_REVIEW}
    assert store._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2
    assert not _has_customer_index(store)

    # Once an operator resolves rather than deletes the conflicting identity,
    # the same idempotent startup migration can install the constraint.
    store._conn.execute(
        "UPDATE users SET shopify_customer_id = NULL WHERE id = 'gid'"
    )
    store._conn.commit()
    reopened = UserStore(db)
    assert reopened.get("numeric").shopify_sync_status == (
        SHOPIFY_SYNC_REQUIRES_REVIEW
    )
    assert _has_customer_index(reopened)


def test_deferred_unique_index_blocks_new_logical_owner_and_webhook(tmp_path):
    db = tmp_path / "conflict.sqlite"
    _legacy_users(
        db,
        (
            ("first", "first@example.com", "gid://shopify/Customer/7001"),
            ("second", "second@example.com", "gid://shopify/Customer/7001"),
        ),
    )
    store = UserStore(db)
    contender = store.create("third@example.com", "longenough")
    _, attempt = store.start_shopify_sync(contender.id)

    assert not store.record_shopify_sync_success(
        contender.id, attempt, "7001"
    )
    assert store.get(contender.id).shopify_sync_status == (
        SHOPIFY_SYNC_REQUIRES_REVIEW
    )

    existing = store.upsert_store_customer(
        "webhook@example.com",
        "7001",
    )
    assert existing.id in {"first", "second"}
    assert store.get_by_email("webhook@example.com") is None
    assert store._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 3


def test_create_and_passwordless_creation_can_atomically_queue_sync(tmp_path):
    store = UserStore(tmp_path / "users.sqlite")

    password_user = store.create(
        "password@example.com",
        "longenough",
        shopify_sync_pending=True,
    )
    code_user = store.verify_email_signin(
        "code@example.com", shopify_sync_pending=True
    )
    assert password_user.shopify_sync_status == SHOPIFY_SYNC_PENDING
    assert code_user.shopify_sync_status == SHOPIFY_SYNC_PENDING

    stub = store.upsert_store_customer("buyer@example.com", "7001")
    claimed = store.create(
        stub.email,
        "longenough",
        shopify_sync_pending=True,
    )
    assert claimed.id == stub.id
    assert claimed.shopify_sync_status == SHOPIFY_SYNC_SYNCED
    assert claimed.shopify_customer_id == "7001"


def test_claim_code_signup_can_atomically_stamp_verified_email(tmp_path):
    store = UserStore(tmp_path / "users.sqlite")

    created = store.create(
        "verified@example.com",
        "longenough",
        shopify_sync_pending=True,
        email_verified=True,
    )

    assert created.email_verified
    assert created.shopify_sync_status == SHOPIFY_SYNC_PENDING


def test_attempt_tokens_reject_stale_results_and_store_numeric_identity(tmp_path):
    store = UserStore(tmp_path / "users.sqlite")
    user = store.create("sync@example.com", "longenough")
    pending = store.mark_shopify_sync_pending(user.id, " retry requested ")
    assert pending.shopify_sync_status == SHOPIFY_SYNC_PENDING
    assert pending.shopify_sync_error == "retry requested"

    first_user, first_attempt = store.start_shopify_sync(user.id)
    second_user, second_attempt = store.start_shopify_sync(user.id)
    assert first_attempt != second_attempt
    assert first_user.shopify_sync_attempts == 1
    assert second_user.shopify_sync_attempts == 2
    assert not store.record_shopify_sync_failure(
        user.id,
        first_attempt,
        SHOPIFY_SYNC_FAILED,
        "stale failure",
    )
    assert not store.record_shopify_sync_success(
        user.id, first_attempt, "gid://shopify/Customer/7001"
    )

    assert store.record_shopify_sync_success(
        user.id, second_attempt, "gid://shopify/Customer/7001"
    )
    synced = store.get(user.id)
    assert synced.shopify_customer_id == "7001"
    assert synced.shopify_sync_status == SHOPIFY_SYNC_SYNCED
    assert synced.shopify_last_synced_at is not None
    assert synced.shopify_sync_error is None
    assert synced.shopify_sync_attempt_token is None
    assert store.get_by_shopify("gid://shopify/Customer/7001").id == user.id


def test_failure_retry_schedule_and_crashed_pending_are_due(tmp_path):
    store = UserStore(tmp_path / "users.sqlite")
    failed = store.create("failed@example.com", "longenough")
    crashed = store.create(
        "crashed@example.com",
        "longenough",
        shopify_sync_pending=True,
    )
    _, failed_attempt = store.start_shopify_sync(failed.id)
    store.start_shopify_sync(crashed.id)  # token remains after simulated crash

    assert store.record_shopify_sync_failure(
        failed.id,
        failed_attempt,
        SHOPIFY_SYNC_FAILED,
        "  retryable   network failure  ",
        next_attempt_at=200,
    )
    assert [user.id for user in store.list_due_shopify_syncs(now=100)] == [
        crashed.id
    ]
    assert {user.id for user in store.list_due_shopify_syncs(now=200)} == {
        crashed.id,
        failed.id,
    }
    assert store.get(failed.id).shopify_sync_error == (
        "retryable network failure"
    )


def test_requeue_preserves_last_error_until_success(tmp_path):
    store = UserStore(tmp_path / "users.sqlite")
    user = store.create("retry@example.com", "longenough")
    _, failed_attempt = store.start_shopify_sync(user.id)
    assert store.record_shopify_sync_failure(
        user.id,
        failed_attempt,
        SHOPIFY_SYNC_FAILED,
        "Shopify temporarily unavailable.",
    )

    pending = store.mark_shopify_sync_pending(user.id)
    assert pending.shopify_sync_status == SHOPIFY_SYNC_PENDING
    assert pending.shopify_sync_error == "Shopify temporarily unavailable."

    _, success_attempt = store.start_shopify_sync(user.id)
    assert store.record_shopify_sync_success(
        user.id, success_attempt, "7001"
    )
    assert store.get(user.id).shopify_sync_error is None


def test_unique_conflict_marks_new_attempt_for_review(tmp_path):
    store = UserStore(tmp_path / "users.sqlite")
    owner = store.create("owner@example.com", "longenough")
    contender = store.create("contender@example.com", "longenough")
    _, owner_attempt = store.start_shopify_sync(owner.id)
    assert store.record_shopify_sync_success(owner.id, owner_attempt, "7001")

    _, contender_attempt = store.start_shopify_sync(contender.id)
    assert not store.record_shopify_sync_success(
        contender.id, contender_attempt, "gid://shopify/Customer/7001"
    )
    assert store.get(contender.id).shopify_sync_status == (
        SHOPIFY_SYNC_REQUIRES_REVIEW
    )
    assert store.get(owner.id).shopify_customer_id == "7001"


def test_health_and_backfill_helpers_use_stable_cursor_pages(tmp_path):
    store = UserStore(tmp_path / "users.sqlite")
    local = store.create("local@example.com", "longenough")
    failed = store.create("failed@example.com", "longenough")
    pending = store.create(
        "pending@example.com", "longenough", shopify_sync_pending=True
    )
    linked = store.upsert_store_customer("linked@example.com", "7001")
    _, attempt = store.start_shopify_sync(failed.id)
    assert store.record_shopify_sync_failure(
        failed.id, attempt, SHOPIFY_SYNC_FAILED, "retry later"
    )

    first_page, cursor = store.list_shopify_sync_health(limit=2)
    second_page, final_cursor = store.list_shopify_sync_health(
        limit=2, after=cursor
    )
    all_ids = sorted((local.id, failed.id, pending.id, linked.id))
    assert [user.id for user in first_page] == all_ids[:2]
    assert [user.id for user in second_page] == all_ids[2:]
    assert cursor == all_ids[1]
    assert final_cursor is None

    candidates, cursor = store.list_shopify_backfill(limit=10)
    assert {user.id for user in candidates} == {
        local.id,
        failed.id,
        pending.id,
        linked.id,
    }
    assert cursor is None
