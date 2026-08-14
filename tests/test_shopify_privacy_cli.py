"""Safe operator CLI contracts for durable Shopify privacy snapshots."""

from __future__ import annotations

import json
import os
import re
import stat
import time

from swinglab.cli import main
from swinglab.integrations.shopify import privacy_cli
from swinglab.web.users import (
    SHOPIFY_PRIVACY_DELIVERED,
    SHOPIFY_PRIVACY_READY,
    UserStore,
)


PRIVATE_EMAIL = "private.customer@example.com"
STORE_DOMAIN = "test-store.myshopify.com"
CUSTOMER_ID = "7001"


def _captured_request(sessions, *, event_id: str = "privacy-event-1"):
    sessions.mkdir(exist_ok=True)
    users = UserStore(sessions / "swinglab.db")
    users.upsert_store_customer(PRIVATE_EMAIL, CUSTOMER_ID)
    request = users.capture_shopify_data_request(
        shop_domain=STORE_DOMAIN,
        configured_shop_domain=STORE_DOMAIN,
        customer_id=CUSTOMER_ID,
        order_ids=[],
        event_id=event_id,
    )
    assert request is not None
    return users, request


def _identifier_scannable(rendered: str) -> str:
    """`rendered` with float timestamps blanked, so a leak scan reads identity.

    The customer id is a short run of digits and the payload carries epoch
    floats, so a raw substring scan asks the clock rather than the code:
    `7001` is not in this output, but it IS inside `1786714501.4067001`, and
    the test failed on the second rather than on a leak. Timestamps are the
    only numbers here that are not identity, so removing them restores the
    guarantee — the id must appear nowhere a value is printed — and makes it
    deterministic.
    """
    return re.sub(r"\d+\.\d+", "<timestamp>", rendered)


def _command(sessions, *action: str) -> int:
    return main(
        [
            "shopify-privacy",
            "--sessions-dir",
            str(sessions),
            *action,
        ]
    )


def test_privacy_cli_refuses_missing_database_without_creating_it(
    tmp_path, capsys
):
    sessions = tmp_path / "missing"

    assert _command(sessions, "list") == 2

    captured = capsys.readouterr()
    assert "database not found" in captured.err
    assert not (sessions / "swinglab.db").exists()


def test_privacy_list_outputs_only_pii_free_metadata(tmp_path, capsys):
    sessions = tmp_path / "sessions"
    _, request = _captured_request(sessions)

    assert _command(sessions, "list", "--json") == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload == [
        {
            "request_id": request.request_id,
            "status": SHOPIFY_PRIVACY_READY,
            "record_count": request.record_count,
            "snapshot_bytes": request.snapshot_bytes,
            "created_at": request.created_at,
            "completed_at": request.completed_at,
            "expires_at": request.expires_at,
            "delivered_at": None,
        }
    ]
    rendered = captured.out + captured.err
    assert PRIVATE_EMAIL not in rendered
    assert CUSTOMER_ID not in _identifier_scannable(rendered)
    assert STORE_DOMAIN not in rendered


def test_privacy_export_creates_private_file_without_marking_delivered(
    tmp_path, capsys
):
    sessions = tmp_path / "sessions"
    users, request = _captured_request(sessions)
    destination = tmp_path / "privacy-export.json"

    assert (
        _command(
            sessions,
            "export",
            request.request_id,
            "--output",
            str(destination),
            "--json",
        )
        == 0
    )

    snapshot = json.loads(destination.read_text(encoding="utf-8"))
    assert snapshot["accounts"][0]["email"] == PRIVATE_EMAIL
    assert snapshot["request"]["customer_id"] == CUSTOMER_ID
    assert (
        users.get_shopify_privacy_request(request.request_id).status
        == SHOPIFY_PRIVACY_READY
    )
    if os.name != "nt":
        assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "request_id": request.request_id,
        "status": "exported",
        "bytes": destination.stat().st_size,
    }
    rendered = captured.out + captured.err
    assert PRIVATE_EMAIL not in rendered
    assert CUSTOMER_ID not in _identifier_scannable(rendered)
    assert STORE_DOMAIN not in rendered


def test_privacy_export_never_overwrites_existing_file(tmp_path, capsys):
    sessions = tmp_path / "sessions"
    _, request = _captured_request(sessions)
    destination = tmp_path / "existing.json"
    destination.write_bytes(b"operator-owned")

    assert (
        _command(
            sessions,
            "export",
            request.request_id,
            "--output",
            str(destination),
        )
        == 2
    )

    assert destination.read_bytes() == b"operator-owned"
    captured = capsys.readouterr()
    assert "refusing to overwrite" in captured.err
    assert PRIVATE_EMAIL not in captured.err


def test_privacy_export_removes_partial_file_after_write_failure(
    tmp_path, monkeypatch, capsys
):
    sessions = tmp_path / "sessions"
    _, request = _captured_request(sessions)
    destination = tmp_path / "partial.json"

    def fail_sync(_descriptor):
        raise OSError("synthetic write failure")

    monkeypatch.setattr(privacy_cli.os, "fsync", fail_sync)
    assert (
        _command(
            sessions,
            "export",
            request.request_id,
            "--output",
            str(destination),
        )
        == 2
    )

    assert not destination.exists()
    captured = capsys.readouterr()
    assert "could not create" in captured.err
    assert "synthetic" not in captured.err
    assert PRIVATE_EMAIL not in captured.err


def test_mark_delivered_requires_confirmation_after_external_handoff(
    tmp_path, capsys
):
    sessions = tmp_path / "sessions"
    users, request = _captured_request(sessions)

    assert (
        _command(sessions, "mark-delivered", request.request_id)
        == 2
    )
    assert (
        users.get_shopify_privacy_request(request.request_id).status
        == SHOPIFY_PRIVACY_READY
    )
    assert "confirm-external-delivery" in capsys.readouterr().err

    assert (
        _command(
            sessions,
            "mark-delivered",
            request.request_id,
            "--confirm-external-delivery",
            "--json",
        )
        == 0
    )

    delivered = users.get_shopify_privacy_request(request.request_id)
    assert delivered is not None
    assert delivered.status == SHOPIFY_PRIVACY_DELIVERED
    assert delivered.delivered_at is not None
    captured = capsys.readouterr()
    rendered = captured.out + captured.err
    assert PRIVATE_EMAIL not in rendered
    assert CUSTOMER_ID not in _identifier_scannable(rendered)
    assert STORE_DOMAIN not in rendered


def test_privacy_cli_delegates_invalid_request_id_validation_to_store(
    tmp_path, capsys
):
    sessions = tmp_path / "sessions"
    _captured_request(sessions)

    assert (
        _command(
            sessions,
            "export",
            "customer@example.com",
            "--output",
            str(tmp_path / "must-not-exist.json"),
        )
        == 1
    )

    captured = capsys.readouterr()
    assert "not found, invalid, or expired" in captured.err
    assert "customer@example.com" not in captured.err
    assert not (tmp_path / "must-not-exist.json").exists()


def test_purge_expired_removes_only_expired_snapshots(tmp_path, capsys):
    sessions = tmp_path / "sessions"
    users, expired = _captured_request(sessions)
    current = users.capture_shopify_data_request(
        shop_domain=STORE_DOMAIN,
        configured_shop_domain=STORE_DOMAIN,
        customer_id=CUSTOMER_ID,
        order_ids=["9002"],
        event_id="privacy-event-current",
    )
    assert current is not None
    users._conn.execute(
        "UPDATE shopify_privacy_requests SET expires_at = ? "
        "WHERE request_id = ?",
        (time.time() - 1, expired.request_id),
    )
    users._conn.commit()

    assert _command(sessions, "purge-expired", "--json") == 0

    assert json.loads(capsys.readouterr().out) == {"removed": 1}
    assert users.get_shopify_privacy_request(expired.request_id) is None
    assert users.get_shopify_privacy_request(current.request_id) is not None
