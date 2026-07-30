"""Operator CLI contracts for the dry-run-first Shopify backfill."""

from __future__ import annotations

import json

from swinglab.cli import main
from swinglab.web.users import UserStore


def _admin_env(monkeypatch, token: str = "shpat_test_secret") -> None:
    monkeypatch.setenv(
        "SHOPIFY_ADMIN_STORE_DOMAIN",
        "test-store.myshopify.com",
    )
    monkeypatch.setenv("SHOPIFY_ADMIN_ACCESS_TOKEN", token)
    monkeypatch.delenv("SHOPIFY_ADMIN_API_VERSION", raising=False)


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
    assert "missing or invalid" in captured.err
    assert leaked not in captured.err
    assert "line2_secret" not in captured.err


def test_empty_backfill_defaults_to_dry_run_json(
    tmp_path, monkeypatch, capsys
):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    UserStore(sessions / "swinglab.db")
    _admin_env(monkeypatch)

    exit_code = main(
        ["shopify-backfill", "--sessions-dir", str(sessions), "--json"]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["scanned"] == 0
    assert payload["items"] == []
