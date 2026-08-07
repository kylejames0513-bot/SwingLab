"""Production web startup gate for the off-volume recovery chain."""

from __future__ import annotations

import sqlite3
import uuid

import pytest

pytest.importorskip("fastapi")

from swinglab.config import Config
from swinglab.web.app import create_app
from swinglab.web.recovery_startup import compose_web_recovery_fence
from swinglab.web.users import UserStore
from tests.test_recovery_fence_ledger import (
    _MemoryRemote,
    _baseline_event,
    _keyring,
)


def _config(*, require_account: bool = False) -> Config:
    cfg = Config()
    cfg.web["require_account"] = require_account
    cfg.web["mobile_native_auth_enabled"] = False
    cfg.web["history_reset_enabled"] = False
    return cfg


def test_pristine_development_startup_makes_zero_recovery_provider_calls(
    tmp_path, monkeypatch
):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    users = UserStore(sessions / "swinglab.db")
    called = []

    def forbidden():
        called.append(True)
        raise AssertionError("dedicated provider settings must stay untouched")

    monkeypatch.setattr(
        "swinglab.web.recovery_startup.RecoveryFenceStoreSettings.from_env",
        forbidden,
    )
    try:
        composition = compose_web_recovery_fence(
            users=users,
            sessions_dir=sessions,
            web_config=_config().web,
            deployment_environment="development",
            keyring=None,
            injected_ledger=None,
            review_lane_active=False,
            shopify_privacy_webhooks_enabled=False,
        )
        assert composition.ledger is None
        assert composition.decision.remote_io_required is False
        assert composition.decision.startup_allowed is True
        assert called == []
    finally:
        users.close()


def test_production_dependent_routes_fail_before_manager_without_accepted_baseline(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(
        "CADDIEINSIGHT_MOBILE_DEPLOYMENT_ENVIRONMENT", "production"
    )
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    with pytest.raises(RuntimeError, match="accepted.*baseline|recovery"):
        create_app(
            _config(require_account=True),
            tmp_path / "sessions",
            start_background_workers=False,
            mobile_state_hmac=_keyring(),
        )


def test_production_privacy_only_webhook_requires_recovery_baseline(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(
        "CADDIEINSIGHT_MOBILE_DEPLOYMENT_ENVIRONMENT", "production"
    )
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "store.example")
    monkeypatch.delenv("SHOPIFY_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("SHOPIFY_PRIVACY_WEBHOOK_SECRET", "privacy-secret")

    with pytest.raises(RuntimeError, match="accepted.*baseline|recovery"):
        create_app(
            _config(require_account=False),
            tmp_path / "sessions",
            start_background_workers=False,
            mobile_state_hmac=_keyring(),
        )


def _accepted_live_chain(tmp_path):
    from swinglab.web import recovery_fence_ledger as ledger_module

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    db_path = sessions / "swinglab.db"
    keyring = _keyring()
    users = UserStore(db_path, mobile_state_hmac=keyring)
    user = users.verify_email_signin("recover-review@example.com")
    raw_token, metadata = users.issue_mobile_api_token(
        user.id,
        "Recovered iPhone",
        expected_auth_epoch=user.auth_epoch,
        now=20.0,
    )
    token_hash = users._conn.execute(
        "SELECT token_hash FROM mobile_api_tokens WHERE selector = ?",
        (metadata.selector,),
    ).fetchone()[0]
    users.close()

    remote = _MemoryRemote(ledger_module)
    ledger = ledger_module.RecoveryFenceLedger(
        remote_store=remote,
        keyring=keyring,
        local_root=sessions,
        db_path=db_path,
    )
    baseline_event = _baseline_event(ledger_module)
    baseline = ledger.append_and_publish(baseline_event)
    payload = baseline_event.payload()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO mobile_recovery_baseline_journals"
            " (operation_id, phase, request_hash, lineage_id, backup_id,"
            " backup_created_at, schema_generation, manifest_sha256,"
            " baseline_db_checkpoint, record_key, record_hash, head_etag,"
            " chain_hmac_key_id, created_at, updated_at)"
            " VALUES (?, 'accepted', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                baseline_event.event_id,
                "c" * 64,
                payload["lineage_id"],
                payload["baseline_backup_id"],
                payload["minimum_backup_created_at"],
                payload["schema_generation"],
                payload["manifest_sha256"],
                payload["baseline_db_checkpoint"],
                baseline.record_key,
                baseline.record_hash,
                baseline.head_etag,
                baseline.chain_hmac_key_id,
                10.0,
                10.0,
            ),
        )
        connection.execute(
            "INSERT INTO mobile_recovery_accepted_baselines"
            " (lineage_id, baseline_backup_id, minimum_backup_created_at,"
            " manifest_sha256, schema_generation, baseline_db_checkpoint, accepted_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                payload["lineage_id"],
                payload["baseline_backup_id"],
                payload["minimum_backup_created_at"],
                payload["manifest_sha256"],
                payload["schema_generation"],
                payload["baseline_db_checkpoint"],
                10.0,
            ),
        )
        connection.commit()

    revoke = ledger_module.TokenRevokeEvent.from_raw(
        event_id="22222222-3333-4444-8555-666666666666",
        cutoff_at=30.0,
        selector=metadata.selector,
        stored_token_verifier=str(token_hash),
        keyring=keyring,
    )
    ledger.append_and_publish(revoke)
    # Recreate a database snapshot whose local checkpoint predates the newest
    # remote revocation while keeping the remote HEAD and immutable body.
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE mobile_recovery_fence_checkpoints SET head_sequence = 1,"
            " head_record_key = ?, head_record_hash = ?, head_etag = ?,"
            " chain_hmac_key_id = ? WHERE checkpoint_id = 1",
            (
                baseline.record_key,
                baseline.record_hash,
                baseline.head_etag,
                baseline.chain_hmac_key_id,
            ),
        )
        connection.commit()
    return sessions, keyring, ledger, raw_token, remote


def _configure_strict_remote(monkeypatch, remote) -> None:
    monkeypatch.setattr(
        "swinglab.web.recovery_startup.RecoveryFenceStoreSettings.from_env",
        lambda: object(),
    )
    monkeypatch.setattr(
        "swinglab.web.recovery_startup.RecoveryFenceRemoteStore",
        lambda _settings: remote,
    )


def test_production_startup_applies_new_remote_revokes_before_auth(
    tmp_path, monkeypatch
):
    sessions, keyring, _writer, raw_token, remote = _accepted_live_chain(tmp_path)
    _configure_strict_remote(monkeypatch, remote)
    monkeypatch.setenv(
        "CADDIEINSIGHT_MOBILE_DEPLOYMENT_ENVIRONMENT", "production"
    )
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    app = create_app(
        _config(require_account=True),
        sessions,
        start_background_workers=False,
        mobile_state_hmac=keyring,
    )
    try:
        assert app.state.users.authenticate_mobile_api_principal(raw_token) is None
        assert app.state.users._conn.execute(
            "SELECT head_sequence FROM mobile_recovery_fence_checkpoints"
            " WHERE checkpoint_id = 1"
        ).fetchone()[0] == 2
        assert app.state.recovery_startup.decision.startup_allowed is True
    finally:
        app.state.jobs.close()
        app.state.throttle.close()
        if app.state.mobile_keyed_throttle is not None:
            app.state.mobile_keyed_throttle.close()
        app.state.users.close()


def test_production_startup_rejects_reserved_record_without_owned_reconciler(
    tmp_path, monkeypatch
):
    from swinglab.web import recovery_fence_ledger as ledger_module

    sessions, keyring, ledger, _raw_token, remote = _accepted_live_chain(tmp_path)
    ledger.append_and_publish(
        ledger_module.ReviewAccessRevisionEvent(
            event_id=str(uuid.uuid4()),
            cutoff_at=40.0,
            provider="apple",
            lane_revision=2,
            supported_builds=(
                {
                    "application_id": "com.caddieinsight.app",
                    "platform": "ios",
                    "version": "1.0",
                    "build": "7",
                },
            ),
            window_state="closed",
            purchase_test_state="complete",
            credential_hmacs=(),
        )
    )
    monkeypatch.setenv(
        "CADDIEINSIGHT_MOBILE_DEPLOYMENT_ENVIRONMENT", "production"
    )
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")
    _configure_strict_remote(monkeypatch, remote)

    with pytest.raises(
        RuntimeError, match="recovery-fence chain could not be applied"
    ):
        create_app(
            _config(require_account=True),
            sessions,
            start_background_workers=False,
            mobile_state_hmac=keyring,
        )


def test_strict_environment_rejects_injected_recovery_ledger(tmp_path, monkeypatch):
    sessions, keyring, ledger, _raw_token, _remote = _accepted_live_chain(tmp_path)
    monkeypatch.setenv(
        "CADDIEINSIGHT_MOBILE_DEPLOYMENT_ENVIRONMENT", "production"
    )
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example")

    with pytest.raises(RuntimeError, match="development-only"):
        create_app(
            _config(require_account=True),
            sessions,
            start_background_workers=False,
            mobile_state_hmac=keyring,
            recovery_fence_ledger=ledger,
        )
