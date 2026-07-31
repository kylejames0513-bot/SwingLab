"""Operator CLI contracts for the dry-run-first Shopify backfill."""

from __future__ import annotations

import json
import sqlite3

import pytest

from swinglab.cli import main
from swinglab.integrations.shopify import admin
from swinglab.integrations.shopify.backfill import (
    bind_backfill_database,
    preflight_backfill_database,
)
from swinglab.integrations.shopify.customer_sync import operator_user_ref
from swinglab.web.users import SHOPIFY_SYNC_REQUIRES_REVIEW, UserStore


class _LookupClient:
    store_domain = "test-store.myshopify.com"

    def __init__(self, customer_id: str | None):
        self.customer_id = customer_id
        self.lookup_calls: list[str] = []
        self.set_calls: list[tuple[str, str | None]] = []
        self.verify_calls = 0

    def verify_store_access(self) -> str:
        self.verify_calls += 1
        return "gid://shopify/Shop/123"

    def find_customer_by_email(self, email: str) -> str | None:
        self.lookup_calls.append(email)
        return self.customer_id

    def set_customer(
        self,
        email: str,
        customer_id: str | None = None,
    ) -> str:
        self.set_calls.append((email, customer_id))
        return self.customer_id or "123456789"


def _install_client(monkeypatch, customer_id: str | None = None):
    client = _LookupClient(customer_id)
    monkeypatch.setattr(
        admin.ShopifyAdminClient,
        "from_env",
        classmethod(lambda cls, **kwargs: client),
    )
    return client


def _admin_env(monkeypatch, token: str = "shpat_test_secret") -> None:
    monkeypatch.setenv(
        "SHOPIFY_STORE_DOMAIN",
        "test-store.myshopify.com",
    )
    monkeypatch.setenv(
        "SHOPIFY_ADMIN_STORE_DOMAIN",
        "test-store.myshopify.com",
    )
    monkeypatch.setenv("SHOPIFY_ADMIN_ACCESS_TOKEN", token)
    monkeypatch.setenv("SHOPIFY_ADMIN_API_VERSION", "2026-07")


def test_backfill_refuses_missing_database_without_creating_one(
    tmp_path, capsys
):
    sessions = tmp_path / "missing"

    exit_code = main(
        ["shopify-backfill", "--sessions-dir", str(sessions), "--json"]
    )

    assert exit_code == 2
    assert "database not found" in capsys.readouterr().err
    assert not (sessions / "swinglab.db").exists()


def test_backfill_rejects_malformed_token_without_echoing_it(
    tmp_path, monkeypatch, capsys
):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    UserStore(sessions / "swinglab.db")
    leaked = "shpat_line1\nline2_secret"
    _admin_env(monkeypatch, leaked)

    exit_code = main(
        ["shopify-backfill", "--sessions-dir", str(sessions), "--json"]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "invalid" in captured.err
    assert leaked not in captured.err
    assert "line2_secret" not in captured.err


def test_empty_backfill_defaults_to_dry_run_json(
    tmp_path, monkeypatch, capsys
):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    db_path = sessions / "swinglab.db"
    UserStore(db_path)
    _admin_env(monkeypatch)
    client = _install_client(monkeypatch)
    bind_backfill_database(
        db_path,
        client.store_domain,
        client.verify_store_access(),
        confirmation=client.store_domain,
    )
    client.verify_calls = 0

    exit_code = main(
        [
            "shopify-backfill",
            "--sessions-dir",
            str(sessions),
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["scanned"] == 0
    assert payload["items"] == []
    assert payload["binding_status"] == "matched"
    assert client.verify_calls == 1


def test_unbound_backfill_refuses_customer_reads_without_store_confirmation(
    tmp_path, monkeypatch, capsys
):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    UserStore(sessions / "swinglab.db").verify_email_signin(
        "customer@example.com"
    )
    _admin_env(monkeypatch)

    exit_code = main(
        ["shopify-backfill", "--sessions-dir", str(sessions)]
    )

    assert exit_code == 2
    assert "--bind-only" in capsys.readouterr().err


def test_preflight_only_is_read_only_and_does_not_require_binding(
    tmp_path, monkeypatch, capsys
):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    db_path = sessions / "swinglab.db"
    UserStore(db_path)
    _admin_env(monkeypatch)
    monkeypatch.delenv("SHOPIFY_ADMIN_ACCESS_TOKEN")
    monkeypatch.delenv("SHOPIFY_ADMIN_API_VERSION")
    before = db_path.read_bytes()

    exit_code = main(
        [
            "shopify-backfill",
            "--sessions-dir",
            str(sessions),
            "--preflight-only",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_ready"] is True
    assert payload["binding_status"] == "unbound"
    assert db_path.read_bytes() == before


def test_customer_lookup_dry_run_keeps_database_byte_for_byte_read_only(
    tmp_path, monkeypatch, capsys
):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    db_path = sessions / "swinglab.db"
    store = UserStore(db_path)
    user = store.verify_email_signin("verified@example.com")
    _admin_env(monkeypatch)
    client = _install_client(monkeypatch)
    bind_backfill_database(
        db_path,
        client.store_domain,
        client.verify_store_access(),
        confirmation=client.store_domain,
    )
    client.verify_calls = 0
    before = db_path.read_bytes()

    exit_code = main(
        [
            "shopify-backfill",
            "--sessions-dir",
            str(sessions),
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["would_create"] == 1
    assert client.verify_calls == 1
    assert client.lookup_calls == ["verified@example.com"]
    assert db_path.read_bytes() == before
    assert store.get(user.id).shopify_sync_status == "not_started"


def test_preflight_reports_schema_migration_risk_without_writing(
    tmp_path, monkeypatch, capsys
):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    db_path = sessions / "swinglab.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT)"
    )
    connection.commit()
    connection.close()
    monkeypatch.setenv(
        "SHOPIFY_ADMIN_STORE_DOMAIN",
        "test-store.myshopify.com",
    )
    before = db_path.read_bytes()

    exit_code = main(
        [
            "shopify-backfill",
            "--sessions-dir",
            str(sessions),
            "--preflight-only",
            "--json",
        ]
    )

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_ready"] is False
    assert "shopify_customer_id" in payload["missing_columns"]
    assert payload["dry_run_read_only"] is True
    assert db_path.read_bytes() == before


def test_bound_database_refuses_a_different_configured_store(
    tmp_path, monkeypatch, capsys
):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    db_path = sessions / "swinglab.db"
    UserStore(db_path)
    bind_backfill_database(
        db_path,
        "first-store.myshopify.com",
        "gid://shopify/Shop/101",
        confirmation="first-store.myshopify.com",
    )
    monkeypatch.setenv(
        "SHOPIFY_ADMIN_STORE_DOMAIN",
        "other-store.myshopify.com",
    )
    monkeypatch.setenv(
        "SHOPIFY_STORE_DOMAIN",
        "other-store.myshopify.com",
    )
    monkeypatch.setenv("SHOPIFY_ADMIN_ACCESS_TOKEN", "shpat_test_secret")
    monkeypatch.setenv("SHOPIFY_ADMIN_API_VERSION", "2026-07")

    exit_code = main(
        [
            "shopify-backfill",
            "--sessions-dir",
            str(sessions),
            "--confirm-store",
            "other-store.myshopify.com",
            "--json",
        ]
    )

    assert exit_code == 2
    assert "different Shopify store" in capsys.readouterr().err


def test_bind_only_authenticates_and_binds_without_customer_calls(
    tmp_path, monkeypatch, capsys
):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    db_path = sessions / "swinglab.db"
    UserStore(db_path)
    _admin_env(monkeypatch)
    client = _install_client(monkeypatch)

    exit_code = main(
        [
            "shopify-backfill",
            "--sessions-dir",
            str(sessions),
            "--bind-only",
            "--confirm-store",
            "test-store.myshopify.com",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "bound"
    assert payload["binding_status"] == "matched"
    preflight = preflight_backfill_database(
        db_path,
        "test-store.myshopify.com",
    )
    assert preflight.binding_status == "matched"
    assert preflight.shop_ref
    assert client.verify_calls == 1
    assert client.lookup_calls == []


@pytest.mark.parametrize(
    "inbound_store",
    [None, "other-store.myshopify.com"],
)
def test_bind_only_refuses_missing_or_split_inbound_store_without_side_effects(
    tmp_path, monkeypatch, capsys, inbound_store
):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    db_path = sessions / "swinglab.db"
    UserStore(db_path)
    _admin_env(monkeypatch)
    if inbound_store is None:
        monkeypatch.delenv("SHOPIFY_STORE_DOMAIN")
    else:
        monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", inbound_store)
    client = _install_client(monkeypatch)
    before = db_path.read_bytes()

    exit_code = main(
        [
            "shopify-backfill",
            "--sessions-dir",
            str(sessions),
            "--bind-only",
            "--confirm-store",
            client.store_domain,
            "--json",
        ]
    )

    assert exit_code == 2
    assert "does not match" in capsys.readouterr().err
    assert client.verify_calls == 0
    assert client.lookup_calls == []
    assert client.set_calls == []
    assert db_path.read_bytes() == before
    assert (
        preflight_backfill_database(db_path, client.store_domain).binding_status
        == "unbound"
    )


def test_apply_refuses_unbound_database_before_any_shopify_network_call(
    tmp_path, monkeypatch, capsys
):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    db_path = sessions / "swinglab.db"
    UserStore(db_path).verify_email_signin("verified@example.com")
    _admin_env(monkeypatch)
    client = _install_client(monkeypatch)

    exit_code = main(
        [
            "shopify-backfill",
            "--sessions-dir",
            str(sessions),
            "--apply",
            "--confirm-store",
            "test-store.myshopify.com",
            "--json",
        ]
    )

    assert exit_code == 2
    assert "--bind-only" in capsys.readouterr().err
    assert client.verify_calls == 0
    assert client.lookup_calls == []
    assert client.set_calls == []
    assert (
        preflight_backfill_database(
            db_path,
            "test-store.myshopify.com",
        ).binding_status
        == "unbound"
    )


def test_shop_redact_requires_explicit_rebind_before_dry_run_customer_calls(
    tmp_path, monkeypatch, capsys
):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    db_path = sessions / "swinglab.db"
    store = UserStore(db_path)
    store.verify_email_signin("verified@example.com")
    _admin_env(monkeypatch)
    client = _install_client(monkeypatch)
    bind_backfill_database(
        db_path,
        client.store_domain,
        client.verify_store_access(),
        confirmation=client.store_domain,
    )
    store.redact_shopify_store(
        client.store_domain,
        client.store_domain,
        event_id="shop-redact-cli-gate-1",
    )
    client.verify_calls = 0
    before = db_path.read_bytes()

    exit_code = main(
        [
            "shopify-backfill",
            "--sessions-dir",
            str(sessions),
            "--confirm-store",
            client.store_domain,
            "--json",
        ]
    )

    assert exit_code == 2
    assert "--bind-only" in capsys.readouterr().err
    assert client.verify_calls == 0
    assert client.lookup_calls == []
    assert client.set_calls == []
    assert db_path.read_bytes() == before
    assert (
        preflight_backfill_database(db_path, client.store_domain).binding_status
        == "unbound"
    )


def test_apply_does_not_bind_database_when_remote_store_authentication_fails(
    tmp_path, monkeypatch, capsys
):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    db_path = sessions / "swinglab.db"
    UserStore(db_path)
    _admin_env(monkeypatch)
    client = _install_client(monkeypatch)

    def fail_verification():
        client.verify_calls += 1
        raise admin.ShopifyAdminTransportError(
            "Shopify store verification failed.",
            retryable=False,
            status_code=401,
        )

    monkeypatch.setattr(client, "verify_store_access", fail_verification)

    exit_code = main(
        [
            "shopify-backfill",
            "--sessions-dir",
            str(sessions),
            "--bind-only",
            "--confirm-store",
            "test-store.myshopify.com",
            "--json",
        ]
    )

    assert exit_code == 2
    assert "verification failed" in capsys.readouterr().err
    assert (
        preflight_backfill_database(
            db_path,
            "test-store.myshopify.com",
        ).binding_status
        == "unbound"
    )


def test_apply_returns_nonzero_and_persists_requires_review(
    tmp_path, monkeypatch, capsys
):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    store = UserStore(sessions / "swinglab.db")
    user = store.create("unverified@example.com", "longenough")
    _admin_env(monkeypatch)
    client = _install_client(monkeypatch)
    bind_backfill_database(
        sessions / "swinglab.db",
        client.store_domain,
        client.verify_store_access(),
        confirmation=client.store_domain,
    )
    client.verify_calls = 0

    exit_code = main(
        [
            "shopify-backfill",
            "--sessions-dir",
            str(sessions),
            "--apply",
            "--json",
        ]
    )

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["requires_review"] == 1
    assert (
        store.get(user.id).shopify_sync_status
        == SHOPIFY_SYNC_REQUIRES_REVIEW
    )
    assert client.verify_calls == 1


def test_resolution_cli_verifies_remote_match_and_never_echoes_customer_id(
    tmp_path, monkeypatch, capsys
):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    store = UserStore(sessions / "swinglab.db")
    user = store.verify_email_signin("verified@example.com")
    _admin_env(monkeypatch)
    client = _install_client(monkeypatch, "987654321")
    monkeypatch.setenv("SHOPIFY_CUSTOMER_TO_RESOLVE", "987654321")
    bind_backfill_database(
        sessions / "swinglab.db",
        client.store_domain,
        client.verify_store_access(),
        confirmation=client.store_domain,
    )
    client.verify_calls = 0

    exit_code = main(
        [
            "shopify-resolve-customer",
            "--sessions-dir",
            str(sessions),
            "--user-ref",
            operator_user_ref(user.id),
            "--customer-id-env",
            "SHOPIFY_CUSTOMER_TO_RESOLVE",
            "--json",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["status"] == "synced"
    assert payload["action"] == "linked_existing"
    assert "987654321" not in output
    assert store.get(user.id).shopify_customer_id == "987654321"
    assert client.lookup_calls == ["verified@example.com"]
    assert client.verify_calls == 1


def test_resolution_cli_rejects_remote_mismatch_without_linking(
    tmp_path, monkeypatch, capsys
):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    store = UserStore(sessions / "swinglab.db")
    user = store.verify_email_signin("verified@example.com")
    _admin_env(monkeypatch)
    client = _install_client(monkeypatch, "222")
    bind_backfill_database(
        sessions / "swinglab.db",
        client.store_domain,
        client.verify_store_access(),
        confirmation=client.store_domain,
    )
    client.verify_calls = 0

    exit_code = main(
        [
            "shopify-resolve-customer",
            "--sessions-dir",
            str(sessions),
            "--user-ref",
            operator_user_ref(user.id),
            "--customer-id",
            "111",
            "--json",
        ]
    )

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "remote_identity_mismatch"
    assert store.get(user.id).shopify_customer_id is None


def test_resolution_cli_refuses_split_store_before_network_or_database_write(
    tmp_path, monkeypatch, capsys
):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    db_path = sessions / "swinglab.db"
    store = UserStore(db_path)
    user = store.verify_email_signin("verified@example.com")
    _admin_env(monkeypatch)
    client = _install_client(monkeypatch, "987654321")
    bind_backfill_database(
        db_path,
        client.store_domain,
        client.verify_store_access(),
        confirmation=client.store_domain,
    )
    client.verify_calls = 0
    monkeypatch.setenv(
        "SHOPIFY_STORE_DOMAIN",
        "other-store.myshopify.com",
    )
    monkeypatch.setenv("SHOPIFY_CUSTOMER_TO_RESOLVE", "987654321")
    before = db_path.read_bytes()

    exit_code = main(
        [
            "shopify-resolve-customer",
            "--sessions-dir",
            str(sessions),
            "--user-ref",
            operator_user_ref(user.id),
            "--customer-id-env",
            "SHOPIFY_CUSTOMER_TO_RESOLVE",
            "--json",
        ]
    )

    assert exit_code == 2
    assert "does not match" in capsys.readouterr().err
    assert client.verify_calls == 0
    assert client.lookup_calls == []
    assert client.set_calls == []
    assert db_path.read_bytes() == before
    assert store.get(user.id).shopify_customer_id is None


def test_resolution_cli_never_rebinds_after_shop_redaction(
    tmp_path, monkeypatch, capsys
):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    db_path = sessions / "swinglab.db"
    store = UserStore(db_path)
    user = store.verify_email_signin("verified@example.com")
    _admin_env(monkeypatch)
    client = _install_client(monkeypatch, "987654321")
    bind_backfill_database(
        db_path,
        client.store_domain,
        client.verify_store_access(),
        confirmation=client.store_domain,
    )
    client.verify_calls = 0
    monkeypatch.setenv("SHOPIFY_CUSTOMER_TO_RESOLVE", "987654321")
    store.redact_shopify_store(
        client.store_domain,
        client.store_domain,
        event_id="shop-redact-before-resolution",
    )

    exit_code = main(
        [
            "shopify-resolve-customer",
            "--sessions-dir",
            str(sessions),
            "--user-ref",
            operator_user_ref(user.id),
            "--customer-id-env",
            "SHOPIFY_CUSTOMER_TO_RESOLVE",
            "--json",
        ]
    )

    assert exit_code == 2
    assert "--bind-only" in capsys.readouterr().err
    assert client.verify_calls == 0
    assert client.lookup_calls == []
    assert client.set_calls == []
    assert (
        preflight_backfill_database(db_path, client.store_domain).binding_status
        == "unbound"
    )
