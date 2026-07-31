"""The gear order ledger: orders/paid records every NON-Pro line item into
gear_orders (order id, sku, title, quantity, normalized email) with the
same per-order replay idempotence as the Pro ledger, orders/cancelled marks
the rows out of the KPI, and Pro grant processing is byte-for-byte
unchanged by any of it. Webhook payloads are signed exactly like Shopify's,
same as test_shopify_billing."""

from __future__ import annotations

import time

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.web.users import UserStore

from tests.test_shopify_billing import (  # noqa: F401  (app is a fixture)
    DAY,
    app,
    get_user,
    order_webhook,
    pro_order,
    signup,
)


def gear_rows(client):
    users: UserStore = client.app.state.users
    with users._lock:
        return [
            dict(row)
            for row in users._conn.execute(
                "SELECT * FROM gear_orders ORDER BY order_id, sku"
            )
        ]


def mixed_order(order_id=7001, email="kyle@example.com"):
    """One checkout: a Pro membership AND two gear items (one without a SKU
    — gear is whatever the billing.shopify_skus map doesn't claim)."""
    return {
        "id": order_id,
        "email": email,
        "line_items": [
            {"sku": "SL-PRO-1MO", "title": "CaddieInsight Pro (1 month)", "quantity": 1},
            {"sku": "SL-TEMPO-WAND", "title": "Tempo Wand", "quantity": 2},
            {"title": "Alignment Sticks"},
        ],
    }


def test_mixed_order_grants_pro_and_records_gear(app):
    client = TestClient(app)
    signup(client)
    resp = order_webhook(client, mixed_order(email="Kyle@Example.com"))
    assert resp.status_code == 200

    # Pro processing byte-for-byte unchanged: same 31-day grant as before.
    user = get_user(client)
    assert user.is_pro
    assert abs(user.pro_until - (time.time() + 31 * DAY)) < 60

    # Gear rows: only the non-Pro items, email normalized, quantity kept.
    rows = gear_rows(client)
    assert [(r["sku"], r["title"], r["quantity"]) for r in rows] == [
        ("", "Alignment Sticks", 1),
        ("SL-TEMPO-WAND", "Tempo Wand", 2),
    ]
    assert all(r["email"] == "kyle@example.com" for r in rows)
    assert all(r["order_id"] == "7001" for r in rows)
    assert all(r["cancelled_at"] is None for r in rows)


def test_replayed_webhook_never_double_counts_gear(app):
    client = TestClient(app)
    signup(client)
    order_webhook(client, mixed_order())
    before_pro = get_user(client).pro_until
    assert order_webhook(client, mixed_order()).status_code == 200
    assert len(gear_rows(client)) == 2  # still the one order's two items
    assert get_user(client).pro_until == before_pro  # Pro replay rule intact


def test_replay_repairs_legacy_gear_only_partial_mixed_order(app):
    client = TestClient(app)
    signup(client)
    users: UserStore = client.app.state.users
    order = mixed_order()
    users.record_gear_order(
        str(order["id"]),
        order["email"],
        [
            ("SL-TEMPO-WAND", "Tempo Wand", 2),
            ("", "Alignment Sticks", 1),
        ],
    )
    assert not get_user(client).is_pro

    order_webhook(client, order)

    assert get_user(client).is_pro
    assert len(gear_rows(client)) == 2
    assert users._conn.execute(
        "SELECT 1 FROM shopify_orders WHERE order_id = '7001'"
    ).fetchone() is not None


def test_replay_repairs_legacy_pro_only_partial_mixed_order(app):
    client = TestClient(app)
    signup(client)
    users: UserStore = client.app.state.users
    users.record_order("7001", "kyle@example.com", 31)
    users.grant_pro_days(get_user(client).id, 31)
    before = get_user(client).pro_until

    order_webhook(client, mixed_order())

    assert get_user(client).pro_until == before
    assert len(gear_rows(client)) == 2


def test_cancelled_legacy_gear_only_partial_blocks_paid_replay(app):
    client = TestClient(app)
    signup(client)
    users: UserStore = client.app.state.users
    order = mixed_order()
    users.record_gear_order(
        str(order["id"]),
        order["email"],
        [
            ("SL-TEMPO-WAND", "Tempo Wand", 2),
            ("", "Alignment Sticks", 1),
        ],
    )
    users.cancel_gear_order(str(order["id"]))

    order_webhook(client, order)

    assert not get_user(client).is_pro
    assert all(row["cancelled_at"] is not None for row in gear_rows(client))
    tombstone = users._conn.execute(
        "SELECT days, pending_days, cancelled_at FROM shopify_orders"
        " WHERE order_id = '7001'"
    ).fetchone()
    assert tombstone["days"] == tombstone["pending_days"] == 0
    assert tombstone["cancelled_at"] is not None


def test_gear_only_order_is_recorded_and_grants_nothing(app):
    client = TestClient(app)
    signup(client)
    order_webhook(client, pro_order(order_id=7002, sku="SL-TEMPO-WAND"))
    assert not get_user(client).is_pro  # unchanged behavior
    rows = gear_rows(client)
    assert len(rows) == 1 and rows[0]["sku"] == "SL-TEMPO-WAND"


def test_pro_only_order_records_no_gear(app):
    client = TestClient(app)
    signup(client)
    order_webhook(client, pro_order(order_id=7003))
    assert get_user(client).is_pro
    assert gear_rows(client) == []


def test_cancelled_order_marks_gear_and_replays_safely(app):
    client = TestClient(app)
    signup(client)
    order_webhook(client, mixed_order())
    order_webhook(client, mixed_order(), topic="orders/cancelled")
    rows = gear_rows(client)
    assert all(r["cancelled_at"] is not None for r in rows)
    stamps = [r["cancelled_at"] for r in rows]
    # Replayed cancellation: nothing changes (not even the timestamps),
    # and Pro cancellation semantics are untouched.
    order_webhook(client, mixed_order(), topic="orders/cancelled")
    assert [r["cancelled_at"] for r in gear_rows(client)] == stamps
    assert not get_user(client).is_pro


def test_cancellation_before_paid_blocks_delayed_gear_and_pro(app):
    client = TestClient(app)
    signup(client)

    order_webhook(client, mixed_order(), topic="orders/cancelled")
    order_webhook(client, mixed_order(), topic="orders/paid")

    assert gear_rows(client) == []
    assert not get_user(client).is_pro


def test_cancel_unknown_order_is_a_noop(app):
    client = TestClient(app)
    signup(client)
    assert order_webhook(
        client, {"id": 9999}, topic="orders/cancelled"
    ).status_code == 200
    assert gear_rows(client) == []


def test_gear_attach_kpi_measures_the_ledger(app):
    """End-to-end: an upload plus a twice-delivered gear webhook = one gear
    order per one report, and the replay never inflates the KPI."""
    from swinglab.kpis import compute_kpis

    from tests.test_web import wait_for

    client = TestClient(app)
    signup(client)
    resp = client.post(
        "/upload",
        files={"video": ("swing.mov", b"fake video bytes", "video/quicktime")},
        follow_redirects=False,
    )
    wait_for(client, resp.headers["location"].rsplit("/", 1)[-1])

    order_webhook(client, pro_order(order_id=7004, sku="SL-TEMPO-WAND"))
    order_webhook(client, pro_order(order_id=7004, sku="SL-TEMPO-WAND"))  # replay

    manager = client.app.state.jobs
    kpis = {
        k.key: k
        for k in compute_kpis(
            manager.sessions_dir / "swinglab.db", client.app.state.cfg
        )
    }
    gear = kpis["gear_attach_per_100_reports"]
    assert (gear.numerator, gear.denominator) == (1, 1)
    assert gear.value == pytest.approx(100.0)
