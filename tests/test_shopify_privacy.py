"""Mandatory Shopify privacy workflows and store-bound webhook safety."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import sqlite3
import threading
import time

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.integrations.shopify.backfill import bind_backfill_database
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from swinglab.web.users import (
    SHOPIFY_PRIVACY_DELIVERED,
    SHOPIFY_PRIVACY_READY,
    UserStore,
    shopify_remote_privacy_lock,
)
from tests.test_web import fake_analyze_ok


PRIMARY_SECRET = "primary-shopify-test-secret"
PRIVACY_SECRET = "privacy-app-test-secret"
STORE = "privacy-test.myshopify.com"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", STORE)
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", PRIMARY_SECRET)
    monkeypatch.setenv("SHOPIFY_PRIVACY_WEBHOOK_SECRET", PRIVACY_SECRET)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("SWINGLAB_SMTP_URL", raising=False)
    monkeypatch.delenv("SWINGLAB_MAIL_FROM", raising=False)
    cfg = Config()
    cfg.web["require_account"] = True
    return create_app(cfg, sessions_dir=tmp_path / "sessions")


def signed_webhook(
    client: TestClient,
    payload: dict,
    topic: str,
    *,
    secret: str = PRIMARY_SECRET,
    shop_domain: str = STORE,
    webhook_id: str = "privacy-delivery-1",
):
    body = json.dumps(payload).encode()
    signature = base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()
    return client.post(
        "/webhooks/shopify",
        content=body,
        headers={
            "X-Shopify-Hmac-Sha256": signature,
            "X-Shopify-Topic": topic,
            "X-Shopify-Shop-Domain": shop_domain,
            "X-Shopify-Webhook-Id": webhook_id,
            "Content-Type": "application/json",
        },
    )


def test_data_request_is_durable_idempotent_and_pii_free_in_logs(app, caplog):
    client = TestClient(app)
    users: UserStore = client.app.state.users
    email = "privacy.person@example.com"
    customer_id = "7001001"
    order_id = "9001001"
    user = users.create(
        email,
        "private-password",
        email_verified=True,
    )
    linked = users.upsert_store_customer(email, customer_id)
    assert linked is not None and linked.id == user.id
    applied, _, _ = users.apply_shopify_order(
        order_id,
        email,
        31,
        customer_id,
        gear=[("PRIVATE-SKU", "Private title", 1)],
    )
    assert applied
    job = client.app.state.jobs.create_session(
        source_name="private-swing.mov",
        user_id=user.id,
    )
    (job.session_dir / "report.html").write_text(
        "private report", encoding="utf-8"
    )
    practice = users.record_proof_cycle_practice_evidence(
        user.id,
        baseline_session_id="private-baseline",
        target_fingerprint="a" * 64,
        drill_id="wall-turn",
        minutes=20,
        outcome="completed",
        now=100.0,
    )
    transfer = users.record_proof_cycle_transfer_check(
        user.id,
        session_id=job.id,
        baseline_session_id=practice.baseline_session_id,
        target_fingerprint=practice.target_fingerprint,
        drill_id=practice.drill_id,
        club="driver",
        hand="right",
        angle="face-on",
        normal_swings=True,
        now=101.0,
    )
    code = users.issue_email_code(email, "reset")
    assert code is not None
    payload = {
        "shop_domain": STORE,
        "customer": {"id": customer_id, "email": email},
        "orders_requested": [order_id],
    }

    with caplog.at_level(logging.INFO, logger="swinglab.web.shopify"):
        response = signed_webhook(
            client,
            payload,
            "customers/data_request",
            secret=PRIVACY_SECRET,
            webhook_id="data-request-stable-id",
        )
        replay = signed_webhook(
            client,
            payload,
            "customers/data_request",
            secret=PRIVACY_SECRET,
            webhook_id="data-request-stable-id",
        )

    assert response.status_code == replay.status_code == 200
    requests = users.list_shopify_privacy_requests()
    assert len(requests) == 1
    request = requests[0]
    assert request.status == SHOPIFY_PRIVACY_READY
    assert request.created_at == request.completed_at
    assert request.expires_at > request.completed_at
    snapshot = users.export_shopify_privacy_request(request.request_id)
    assert snapshot is not None
    assert snapshot["request"]["customer_id"] == customer_id
    assert snapshot["request"]["order_ids"] == [order_id]
    assert snapshot["accounts"][0]["email"] == email
    assert snapshot["analyses"][0]["id"] == job.id
    assert snapshot["session_artifacts"][0]["files"][0]["path"] == (
        "report.html"
    )
    assert snapshot["proof_cycle_practice_evidence"] == [
        {
            "user_id": user.id,
            "baseline_session_id": practice.baseline_session_id,
            "target_fingerprint": practice.target_fingerprint,
            "drill_id": practice.drill_id,
            "minutes": 20,
            "outcome": "completed",
            "completed_at": 100.0,
            "completed_day": 0,
        }
    ]
    assert snapshot["proof_cycle_transfer_checks"] == [
        {
            "session_id": transfer.session_id,
            "user_id": user.id,
            "baseline_session_id": transfer.baseline_session_id,
            "target_fingerprint": transfer.target_fingerprint,
            "drill_id": transfer.drill_id,
            "club": "driver",
            "hand": "right",
            "angle": "face-on",
            "normal_swings": 1,
            "declared_at": 101.0,
        }
    ]
    encoded = json.dumps(snapshot)
    assert "password_hash" not in encoded
    assert "code_hash" not in encoded
    assert "private-password" not in encoded
    assert code not in encoded
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert email not in log_text
    assert customer_id not in log_text
    assert order_id not in log_text

    delivered = users.mark_shopify_privacy_request_delivered(
        request.request_id,
        now=request.completed_at + 10,
    )
    assert delivered is not None
    assert delivered.status == SHOPIFY_PRIVACY_DELIVERED
    assert users.purge_expired_shopify_privacy_requests(
        now=request.expires_at + 1
    ) == 1
    assert users.get_shopify_privacy_request(request.request_id) is None


def test_customer_redaction_erases_structured_proof_cycle_context(app):
    users: UserStore = app.state.users
    email = "proof-redact@example.com"
    customer_id = "7001999"
    user = users.create(email, "private-password", email_verified=True)
    linked = users.upsert_store_customer(email, customer_id)
    assert linked is not None and linked.id == user.id
    fields = {
        "baseline_session_id": "baseline",
        "target_fingerprint": "b" * 64,
        "drill_id": "wall-turn",
    }
    users.record_proof_cycle_practice_evidence(
        user.id, minutes=20, outcome="completed", **fields
    )
    users.record_proof_cycle_transfer_check(
        user.id,
        session_id="refilm",
        club="driver",
        hand="right",
        angle="face-on",
        normal_swings=True,
        **fields,
    )

    result = users.remove_shopify_customer(customer_id, email, redact=True)

    assert result == "unlinked"
    assert users.list_proof_cycle_practice_evidence(user.id) == []
    assert users.get_proof_cycle_transfer_check(user.id, "refilm") is None


def test_privacy_key_is_topic_limited_and_every_mutation_is_store_bound(app):
    client = TestClient(app)
    users: UserStore = client.app.state.users
    privacy = signed_webhook(
        client,
        {
            "shop_domain": STORE,
            "customer": {"id": 7001},
            "orders_requested": [],
        },
        "customers/data_request",
        secret=PRIVACY_SECRET,
        webhook_id="privacy-key-accepted",
    )
    assert privacy.status_code == 200
    assert len(users.list_shopify_privacy_requests()) == 1

    order = {
        "id": 9001,
        "email": "buyer@example.com",
        "line_items": [{"sku": "SL-PRO-1MO", "quantity": 1}],
    }
    wrong_key = signed_webhook(
        client,
        order,
        "orders/paid",
        secret=PRIVACY_SECRET,
        webhook_id="privacy-key-order-rejected",
    )
    assert wrong_key.status_code == 400
    assert users._conn.execute(
        "SELECT 1 FROM shopify_orders WHERE order_id = '9001'"
    ).fetchone() is None

    wrong_store = signed_webhook(
        client,
        order,
        "orders/paid",
        shop_domain="other-store.myshopify.com",
        webhook_id="cross-store-order-rejected",
    )
    assert wrong_store.status_code == 400
    assert users._conn.execute(
        "SELECT 1 FROM shopify_orders WHERE order_id = '9001'"
    ).fetchone() is None

    wrong_body_store = signed_webhook(
        client,
        {
            "shop_domain": "other-store.myshopify.com",
            "customer": {"id": 7002},
            "orders_requested": [],
        },
        "customers/data_request",
        secret=PRIVACY_SECRET,
        webhook_id="privacy-body-store-rejected",
    )
    assert wrong_body_store.status_code == 200
    assert len(users.list_shopify_privacy_requests()) == 1


def test_privacy_webhook_without_delivery_id_fails_closed(app):
    client = TestClient(app)
    users: UserStore = client.app.state.users
    payload = {
        "shop_domain": STORE,
        "customer": {"id": 7001},
        "orders_requested": [],
    }
    body = json.dumps(payload).encode()
    signature = base64.b64encode(
        hmac.new(
            PRIVACY_SECRET.encode(),
            body,
            hashlib.sha256,
        ).digest()
    ).decode()

    response = client.post(
        "/webhooks/shopify",
        content=body,
        headers={
            "X-Shopify-Hmac-Sha256": signature,
            "X-Shopify-Topic": "customers/data_request",
            "X-Shopify-Shop-Domain": STORE,
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 400
    assert users.list_shopify_privacy_requests() == []
    assert users._conn.execute(
        "SELECT 1 FROM shopify_privacy_event_fences"
    ).fetchone() is None


def test_customer_redact_replay_cannot_fence_new_independent_account(app):
    client = TestClient(app)
    users: UserStore = client.app.state.users
    email = "post-redaction@example.com"
    payload = {
        "shop_domain": STORE,
        "customer": {"id": 7001, "email": email},
    }
    first = signed_webhook(
        client,
        payload,
        "customers/redact",
        secret=PRIVACY_SECRET,
        webhook_id="durable-customer-redact",
    )
    assert first.status_code == 200
    account = users.create(
        email,
        "independent-password",
        email_verified=True,
    )
    before = users.get(account.id)
    assert before is not None and not before.shopify_sync_blocked

    replay = signed_webhook(
        client,
        payload,
        "customers/redact",
        secret=PRIVACY_SECRET,
        webhook_id="durable-customer-redact",
    )

    assert replay.status_code == 200
    after = users.get(account.id)
    assert after is not None
    assert after.shopify_sync_blocked == before.shopify_sync_blocked
    assert after.shopify_sync_generation == before.shopify_sync_generation
    assert after.shopify_sync_status == before.shopify_sync_status
    assert users._conn.execute(
        "SELECT COUNT(*) FROM shopify_privacy_event_fences"
    ).fetchone()[0] == 1


def test_waiting_redaction_does_not_block_other_http_requests(
    app,
    monkeypatch,
):
    users: UserStore = app.state.users
    redaction_entered = threading.Event()
    health_done = threading.Event()
    redaction_responses = []
    health_responses = []
    original_remove = users.remove_shopify_customer

    def marked_remove(*args, **kwargs):
        redaction_entered.set()
        return original_remove(*args, **kwargs)

    monkeypatch.setattr(users, "remove_shopify_customer", marked_remove)
    payload = {
        "shop_domain": STORE,
        "customer": {
            "id": 7001,
            "email": "waiting-redaction@example.com",
        },
        "orders_to_redact": [],
    }
    with TestClient(app) as client:
        with shopify_remote_privacy_lock(users._db_path):
            redactor = threading.Thread(
                target=lambda: redaction_responses.append(
                    signed_webhook(
                        client,
                        payload,
                        "customers/redact",
                        secret=PRIVACY_SECRET,
                        webhook_id="waiting-redaction",
                    )
                )
            )
            redactor.start()
            assert redaction_entered.wait(timeout=5)

            def get_health():
                health_responses.append(client.get("/healthz"))
                health_done.set()

            health_reader = threading.Thread(target=get_health)
            health_reader.start()
            responsive = health_done.wait(timeout=1)
        redactor.join(timeout=5)
        health_reader.join(timeout=5)

    assert responsive
    assert not redactor.is_alive()
    assert not health_reader.is_alive()
    assert redaction_responses[0].status_code == 200
    assert health_responses[0].status_code == 200


def test_data_request_inventory_does_not_hold_database_writer_lock(
    app,
    monkeypatch,
):
    users: UserStore = app.state.users
    subject = users.create(
        "inventory-subject@example.com",
        "subject-password",
        email_verified=True,
    )
    users.upsert_store_customer(subject.email, "7001")
    app.state.jobs.create_session(
        source_name="inventory.mov",
        user_id=subject.id,
    )
    unrelated = users.verify_email_signin("inventory-local@example.com")
    inventory_started = threading.Event()
    release_inventory = threading.Event()
    local_done = threading.Event()
    captures = []

    def stalled_inventory(job_ids):
        inventory_started.set()
        assert release_inventory.wait(timeout=5)
        return []

    monkeypatch.setattr(
        users,
        "_privacy_artifact_inventory",
        stalled_inventory,
    )
    worker = threading.Thread(
        target=lambda: captures.append(
            users.capture_shopify_data_request(
                shop_domain=STORE,
                configured_shop_domain=STORE,
                customer_id="7001",
                order_ids=[],
                event_id="stalled-inventory-request",
            )
        )
    )
    worker.start()
    assert inventory_started.wait(timeout=5)
    assert users._conn.execute(
        "SELECT 1 FROM shopify_privacy_event_fences"
        " WHERE topic = 'customers/data_request'"
    ).fetchone() is None

    def local_work():
        users.mark_shopify_sync_pending(unrelated.id)
        assert users.get(unrelated.id) is not None
        local_done.set()

    local_worker = threading.Thread(target=local_work)
    local_worker.start()
    responsive = local_done.wait(timeout=1)
    release_inventory.set()
    worker.join(timeout=5)
    local_worker.join(timeout=5)

    assert responsive
    assert not worker.is_alive()
    assert not local_worker.is_alive()
    assert len(captures) == 1
    assert captures[0] is not None
    assert users._conn.execute(
        "SELECT 1 FROM shopify_privacy_event_fences"
        " WHERE topic = 'customers/data_request'"
    ).fetchone() is not None


def test_customer_redact_preserves_other_customer_snapshot_at_shared_email(
    app,
):
    client = TestClient(app)
    users: UserStore = app.state.users
    email = "shared-checkout@example.com"
    assert users.apply_shopify_order(
        "shared-order-1",
        email,
        31,
        "7001",
    )[0]
    assert users.apply_shopify_order(
        "shared-order-2",
        email,
        31,
        "7002",
    )[0]
    first = users.capture_shopify_data_request(
        shop_domain=STORE,
        configured_shop_domain=STORE,
        customer_id="7001",
        order_ids=["shared-order-1"],
        event_id="shared-export-1",
    )
    second = users.capture_shopify_data_request(
        shop_domain=STORE,
        configured_shop_domain=STORE,
        customer_id="7002",
        order_ids=["shared-order-2"],
        event_id="shared-export-2",
    )
    assert first is not None and second is not None

    response = signed_webhook(
        client,
        {
            "shop_domain": STORE,
            "customer": {"id": "7001", "email": email},
            "orders_to_redact": ["shared-order-1"],
        },
        "customers/redact",
        secret=PRIVACY_SECRET,
        webhook_id="shared-customer-redact",
    )

    assert response.status_code == 200
    assert users.get_shopify_privacy_request(first.request_id) is None
    assert users.get_shopify_privacy_request(second.request_id) is not None
    snapshot = users.export_shopify_privacy_request(second.request_id)
    assert snapshot is not None
    assert snapshot["request"]["customer_id"] == "7002"
    assert users._conn.execute(
        "SELECT 1 FROM shopify_orders WHERE order_id = 'shared-order-1'"
    ).fetchone() is None
    assert users._conn.execute(
        "SELECT 1 FROM shopify_orders WHERE order_id = 'shared-order-2'"
    ).fetchone() is not None
    assert users.pending_grant_days(email) == pytest.approx(31)


@pytest.mark.parametrize("claimed", (False, True))
def test_customer_redact_erases_every_shopify_pii_table(
    app,
    claimed,
):
    client = TestClient(app)
    users: UserStore = client.app.state.users
    email = "redacted.subject@example.com"
    customer_id = "7001"
    if claimed:
        account = users.create(
            email,
            "independent-password",
            email_verified=True,
        )
        users.upsert_store_customer(email, customer_id)
        job = client.app.state.jobs.create_session(
            source_name="preserved-analysis.mov",
            user_id=account.id,
        )
    else:
        account = users.upsert_store_customer(email, customer_id)
        assert account is not None and not account.claimed
        job = None

    assert users.apply_shopify_order(
        "subject-pro-order",
        email,
        31,
        customer_id,
        gear=[("SUBJECT-GEAR", "Private training aid", 1)],
    )[0]
    assert users.apply_shopify_order(
        "subject-gear-only",
        email,
        0,
        customer_id,
        gear=[("GEAR-ONLY", "Private accessory", 1)],
    )[0]
    users.add_pending_grant(email, 7)
    before = users.get(account.id)
    assert before is not None and before.pro_until > time.time()
    request = users.capture_shopify_data_request(
        shop_domain=STORE,
        configured_shop_domain=STORE,
        customer_id=customer_id,
        order_ids=["subject-pro-order", "subject-gear-only"],
        event_id=f"subject-export-{claimed}",
    )
    assert request is not None
    for suffix, unknown_snapshot in (
        ("malformed", "{"),
        ("list", "[]"),
        ("scalar", "null"),
        ("future", '{"schema_version":999}'),
    ):
        users._conn.execute(
            "INSERT INTO shopify_privacy_requests"
            " (request_id, shop_domain, status, snapshot_json,"
            "  snapshot_sha256, record_count, snapshot_bytes,"
            "  created_at, completed_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, 0, 1, ?, ?, ?)",
            (
                f"{suffix}-{claimed}",
                STORE,
                SHOPIFY_PRIVACY_READY,
                unknown_snapshot,
                "0" * 64,
                time.time(),
                time.time(),
                time.time() + 3600,
            ),
        )
    users._conn.commit()

    response = signed_webhook(
        client,
        {
            "shop_domain": STORE,
            "customer": {"id": customer_id, "email": email},
            "orders_to_redact": [
                "subject-pro-order",
                "subject-gear-only",
            ],
        },
        "customers/redact",
        secret=PRIVACY_SECRET,
        webhook_id=f"subject-redact-{claimed}",
    )

    assert response.status_code == 200
    current = users.get(account.id)
    if claimed:
        assert current is not None
        assert current.email == email
        assert current.pro_until == before.pro_until
        assert current.shopify_customer_id is None
        assert current.source is None
        assert current.shopify_sync_blocked
        assert users.authenticate(
            email,
            "independent-password",
        ) is not None
        assert client.app.state.jobs.get(job.id) is not None
    else:
        assert current is None

    for table, predicate, parameters in (
        (
            "shopify_orders",
            "email = ? OR shopify_customer_id = ?"
            " OR order_id IN (?, ?)",
            (
                email,
                customer_id,
                "subject-pro-order",
                "subject-gear-only",
            ),
        ),
        (
            "gear_orders",
            "email = ? OR order_id IN (?, ?)",
            (email, "subject-pro-order", "subject-gear-only"),
        ),
        (
            "shopify_pending_customer_links",
            "email = ? OR customer_id = ?",
            (email, customer_id),
        ),
        (
            "shopify_privacy_requests",
            "snapshot_json LIKE ?",
            (f"%{email}%",),
        ),
    ):
        assert users._conn.execute(
            f"SELECT 1 FROM {table} WHERE {predicate}",
            parameters,
        ).fetchone() is None
    assert users._conn.execute(
        "SELECT 1 FROM pro_grants WHERE email = ?",
        (email,),
    ).fetchone() is None
    tombstone = users._conn.execute(
        "SELECT customer_id, redacted, former_user_id"
        " FROM shopify_customer_tombstones WHERE customer_id = ?",
        (customer_id,),
    ).fetchone()
    assert tuple(tombstone) == (customer_id, 1, None)
    assert "email" not in {
        row["name"]
        for row in users._conn.execute(
            "PRAGMA table_info(shopify_customer_tombstones)"
        )
    }
    order_fences = [
        str(row["order_key"])
        for row in users._conn.execute(
            "SELECT order_key FROM shopify_redacted_order_fences"
        )
    ]
    assert len(order_fences) == 2
    assert all(
        email not in value
        and "subject-pro-order" not in value
        and "subject-gear-only" not in value
        for value in order_fences
    )

    assert not users.apply_shopify_order(
        "subject-pro-order",
        email,
        31,
        None,
        gear=[("REPLAY-GEAR", "Replayed private item", 1)],
    )[0]
    assert users.cancel_shopify_order(
        "subject-gear-only",
        email,
        None,
    ) == (False, "", 0.0)

    # The deliberately minimal customer-id suppression record prevents late
    # paid and cancellation deliveries from recreating erased PII or value.
    assert not users.apply_shopify_order(
        "late-subject-order",
        email,
        31,
        customer_id,
        gear=[("LATE-GEAR", "Late private item", 1)],
    )[0]
    assert users.cancel_shopify_order(
        "late-subject-order",
        email,
        customer_id,
    ) == (False, "", 0.0)
    assert users._conn.execute(
        "SELECT 1 FROM shopify_orders"
        " WHERE order_id = 'late-subject-order'"
    ).fetchone() is None
    assert users._conn.execute(
        "SELECT 1 FROM gear_orders"
        " WHERE order_id = 'late-subject-order'"
    ).fetchone() is None


def test_data_request_replay_after_shop_redact_does_not_recapture(app):
    client = TestClient(app)
    users: UserStore = client.app.state.users
    request_payload = {
        "shop_domain": STORE,
        "customer": {"id": 7001},
        "orders_requested": [],
    }
    first = signed_webhook(
        client,
        request_payload,
        "customers/data_request",
        secret=PRIVACY_SECRET,
        webhook_id="durable-data-request",
    )
    assert first.status_code == 200
    assert len(users.list_shopify_privacy_requests()) == 1

    erased = signed_webhook(
        client,
        {"shop_domain": STORE},
        "shop/redact",
        secret=PRIVACY_SECRET,
        webhook_id="erase-data-request-snapshot",
    )
    assert erased.status_code == 200
    assert users.list_shopify_privacy_requests() == []
    assert users._conn.execute(
        "SELECT COUNT(*) FROM shopify_privacy_event_fences"
    ).fetchone()[0] == 2

    replay = signed_webhook(
        client,
        request_payload,
        "customers/data_request",
        secret=PRIVACY_SECRET,
        webhook_id="durable-data-request",
    )

    assert replay.status_code == 200
    assert users.list_shopify_privacy_requests() == []
    assert users._conn.execute(
        "SELECT COUNT(*) FROM shopify_privacy_event_fences"
    ).fetchone()[0] == 2


def test_shop_redact_erases_store_state_but_preserves_independent_account(app):
    client = TestClient(app)
    users: UserStore = client.app.state.users
    claimed = users.create(
        "claimed@example.com",
        "claimed-password",
        email_verified=True,
    )
    users.upsert_store_customer(claimed.email, "7001")
    users.apply_shopify_order(
        "9001",
        claimed.email,
        31,
        "7001",
        gear=[("GEAR-1", "Training aid", 1)],
    )
    before = users.get(claimed.id)
    assert before is not None and before.pro_until > time.time()
    client.app.state.jobs.create_session(
        source_name="kept-swing.mov",
        user_id=claimed.id,
    )
    independent = users.create(
        "independent@example.com",
        "independent-password",
        email_verified=True,
    )
    stub = users.upsert_store_customer("stub@example.com", "7002")
    assert stub is not None and not stub.claimed
    users.apply_shopify_order(
        "9002",
        "pending@example.com",
        31,
        "7003",
    )
    users.issue_signup_intent("intent@example.com", "intent-password")
    users.capture_shopify_data_request(
        shop_domain=STORE,
        configured_shop_domain=STORE,
        customer_id="7001",
        order_ids=["9001"],
        event_id="stored-before-redact",
    )
    users._conn.execute(
        "CREATE TABLE shopify_customer_backfill_binding ("
        " id INTEGER PRIMARY KEY, store_domain TEXT, bound_at REAL)"
    )
    users._conn.execute(
        "INSERT INTO shopify_customer_backfill_binding"
        " (id, store_domain, bound_at) VALUES (1, ?, ?)",
        (STORE, time.time()),
    )
    users._conn.commit()

    response = signed_webhook(
        client,
        {"shop_domain": STORE, "shop_id": 42},
        "shop/redact",
        secret=PRIVACY_SECRET,
        webhook_id="shop-redact-1",
    )
    assert response.status_code == 200
    kept = users.get(claimed.id)
    assert kept is not None
    assert kept.has_password and kept.email_verified
    assert kept.pro_until == before.pro_until
    assert kept.shopify_customer_id is None
    assert not kept.shopify_identity_locked
    assert kept.shopify_sync_status == "not_started"
    assert users.get(independent.id) is not None
    assert users.get(stub.id) is None
    assert users.authenticate(
        claimed.email, "claimed-password"
    ) is not None
    assert client.app.state.jobs.list_recent(user_id=claimed.id)
    for table in (
        "shopify_orders",
        "gear_orders",
        "pro_grants",
        "shopify_customer_tombstones",
        "shopify_pending_customer_links",
        "signup_intents",
        "shopify_privacy_requests",
        "shopify_customer_backfill_binding",
    ):
        assert users._conn.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0] == 0

    replay = signed_webhook(
        client,
        {"shop_domain": STORE, "shop_id": 42},
        "shop/redact",
        secret=PRIVACY_SECRET,
        webhook_id="shop-redact-1",
    )
    assert replay.status_code == 200
    assert users.get(claimed.id) is not None


def test_shop_redact_replay_fence_survives_rebind_and_new_store_state(app):
    client = TestClient(app)
    users: UserStore = client.app.state.users
    initial = users.upsert_store_customer("initial@example.com", "7001")
    assert initial is not None
    payload = {"shop_domain": STORE, "shop_id": 42}

    first = signed_webhook(
        client,
        payload,
        "shop/redact",
        secret=PRIVACY_SECRET,
        webhook_id="durable-shop-redact-event",
    )
    assert first.status_code == 200
    assert users.get(initial.id) is None
    assert users._conn.execute(
        "SELECT shop_redacted FROM shopify_sync_control WHERE id = 1"
    ).fetchone()[0] == 1

    db_path = client.app.state.jobs.sessions_dir / "swinglab.db"
    bind_backfill_database(
        db_path,
        STORE,
        "gid://shopify/Shop/42",
        confirmation=STORE,
    )
    account = users.create(
        "after-reinstall@example.com",
        "independent-password",
        email_verified=True,
    )
    users.upsert_store_customer(account.email, "7002")
    users.apply_shopify_order("9002", account.email, 31, "7002")
    users.apply_shopify_order(
        "gear-only-9003",
        account.email,
        0,
        "7002",
        gear=[("SHOP-REDACT-GEAR", "Private gear", 1)],
    )
    assert users.get(account.id).shopify_customer_id == "7002"
    assert users._conn.execute(
        "SELECT shop_redacted FROM shopify_sync_control WHERE id = 1"
    ).fetchone()[0] == 0
    reopened = UserStore(db_path)
    assert reopened._conn.execute(
        "SELECT COUNT(*) FROM shopify_privacy_event_fences"
    ).fetchone()[0] == 1
    assert reopened._conn.execute(
        "SELECT shop_redacted FROM shopify_sync_control WHERE id = 1"
    ).fetchone()[0] == 0
    reopened._conn.close()

    replay = signed_webhook(
        client,
        payload,
        "shop/redact",
        secret=PRIVACY_SECRET,
        webhook_id="durable-shop-redact-event",
    )
    assert replay.status_code == 200
    assert users.get(account.id).shopify_customer_id == "7002"
    assert users._conn.execute(
        "SELECT 1 FROM shopify_orders WHERE order_id = '9002'"
    ).fetchone() is not None
    assert users._conn.execute(
        "SELECT 1 FROM shopify_customer_backfill_binding WHERE id = 1"
    ).fetchone() is not None
    assert users._conn.execute(
        "SELECT shop_redacted FROM shopify_sync_control WHERE id = 1"
    ).fetchone()[0] == 0
    assert users._conn.execute(
        "SELECT COUNT(*) FROM shopify_privacy_event_fences"
    ).fetchone()[0] == 1

    distinct = signed_webhook(
        client,
        payload,
        "shop/redact",
        secret=PRIVACY_SECRET,
        webhook_id="distinct-shop-redact-event",
    )
    assert distinct.status_code == 200
    assert users.get(account.id).shopify_customer_id is None
    assert users._conn.execute(
        "SELECT 1 FROM shopify_orders WHERE order_id = '9002'"
    ).fetchone() is None
    assert users._conn.execute(
        "SELECT 1 FROM shopify_customer_backfill_binding WHERE id = 1"
    ).fetchone() is None
    assert users._conn.execute(
        "SELECT shop_redacted FROM shopify_sync_control WHERE id = 1"
    ).fetchone()[0] == 1
    assert users._conn.execute(
        "SELECT COUNT(*) FROM shopify_privacy_event_fences"
    ).fetchone()[0] == 2
    assert users._conn.execute(
        "SELECT COUNT(*) FROM shopify_redacted_order_fences"
    ).fetchone()[0] == 2

    bind_backfill_database(
        db_path,
        STORE,
        "gid://shopify/Shop/42",
        confirmation=STORE,
    )
    assert not users.apply_shopify_order(
        "9002",
        account.email,
        31,
        None,
        gear=[("REPLAYED-GEAR", "Replayed private gear", 1)],
    )[0]
    assert users.cancel_shopify_order(
        "gear-only-9003",
        account.email,
        None,
    ) == (False, "", 0.0)
    assert users._conn.execute(
        "SELECT 1 FROM shopify_orders WHERE order_id = '9002'"
    ).fetchone() is None
    assert users._conn.execute(
        "SELECT 1 FROM gear_orders WHERE order_id = 'gear-only-9003'"
    ).fetchone() is None


def test_shop_redact_rolls_back_every_table_on_failure(app):
    client = TestClient(app)
    users: UserStore = client.app.state.users
    user = users.create(
        "rollback@example.com",
        "rollback-password",
        email_verified=True,
    )
    users.upsert_store_customer(user.email, "7001")
    users.apply_shopify_order("9001", user.email, 31, "7001")
    users.issue_signup_intent("intent@example.com", "intent-password")
    privacy = users.capture_shopify_data_request(
        shop_domain=STORE,
        configured_shop_domain=STORE,
        customer_id="7001",
        order_ids=["9001"],
        event_id="rollback-privacy",
    )
    assert privacy is not None
    users._conn.execute(
        "CREATE TRIGGER fail_shop_redact"
        " BEFORE DELETE ON shopify_orders"
        " BEGIN SELECT RAISE(ABORT, 'simulated failure'); END"
    )
    users._conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        signed_webhook(
            client,
            {"shop_domain": STORE},
            "shop/redact",
            secret=PRIVACY_SECRET,
            webhook_id="shop-redact-failure",
        )

    current = users.get(user.id)
    assert current is not None
    assert current.shopify_customer_id == "7001"
    assert users._conn.execute(
        "SELECT 1 FROM shopify_orders WHERE order_id = '9001'"
    ).fetchone() is not None
    assert users.get_shopify_privacy_request(
        privacy.request_id
    ) is not None
    assert users._conn.execute(
        "SELECT 1 FROM signup_intents"
    ).fetchone() is not None
    assert users._conn.execute(
        "SELECT COUNT(*) FROM shopify_privacy_event_fences"
    ).fetchone()[0] == 1

    users._conn.execute("DROP TRIGGER fail_shop_redact")
    users._conn.commit()
    retry = signed_webhook(
        client,
        {"shop_domain": STORE},
        "shop/redact",
        secret=PRIVACY_SECRET,
        webhook_id="shop-redact-failure",
    )
    assert retry.status_code == 200
    assert users._conn.execute(
        "SELECT 1 FROM shopify_orders WHERE order_id = '9001'"
    ).fetchone() is None
    assert users._conn.execute(
        "SELECT COUNT(*) FROM shopify_privacy_event_fences"
    ).fetchone()[0] == 2


def test_privacy_schema_migrates_old_db_and_rejects_partial_table(tmp_path):
    old = tmp_path / "old.sqlite"
    connection = sqlite3.connect(old)
    connection.execute(
        "CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,"
        " password_hash TEXT NOT NULL, created_at REAL NOT NULL,"
        " stripe_customer_id TEXT, plan TEXT NOT NULL DEFAULT 'free',"
        " subscription_status TEXT NOT NULL DEFAULT 'none')"
    )
    connection.execute(
        "INSERT INTO users (id, email, password_hash, created_at)"
        " VALUES ('u1', 'old@example.com', 'scrypt$x', 0)"
    )
    connection.commit()
    connection.close()

    store = UserStore(old)
    user_columns = {
        row["name"] for row in store._conn.execute("PRAGMA table_info(users)")
    }
    code_columns = {
        row["name"]
        for row in store._conn.execute("PRAGMA table_info(email_codes)")
    }
    intent_columns = {
        row["name"]
        for row in store._conn.execute("PRAGMA table_info(signup_intents)")
    }
    assert "auth_epoch" in user_columns
    assert "shopify_sync_generation" in user_columns
    assert "shopify_sync_blocked" in user_columns
    assert "session_nonce_hash" in code_columns
    assert "session_nonce_hash" in intent_columns
    assert store._conn.execute(
        "SELECT 1 FROM sqlite_master"
        " WHERE type = 'table'"
        " AND name = 'shopify_privacy_requests'"
    ).fetchone() is not None
    for table in (
        "shopify_sync_control",
        "shopify_privacy_event_fences",
    ):
        assert store._conn.execute(
            "SELECT 1 FROM sqlite_master"
            " WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone() is not None
    store._conn.close()
    reopened = UserStore(old)
    assert reopened.get("u1") is not None
    reopened._conn.close()

    partial = tmp_path / "partial.sqlite"
    connection = sqlite3.connect(partial)
    connection.execute(
        "CREATE TABLE shopify_privacy_requests"
        " (request_id TEXT PRIMARY KEY)"
    )
    connection.commit()
    connection.close()
    with pytest.raises(
        RuntimeError,
        match="Incompatible Shopify privacy request schema",
    ):
        UserStore(partial)
