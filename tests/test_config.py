from __future__ import annotations

from pathlib import Path

from swinglab.config import DEFAULTS, Config


def test_defaults_without_file():
    cfg = Config()
    assert cfg.brand["name"] == "CaddieInsight"
    assert cfg.detection["audio_height"] == 0.30
    assert cfg.detection["relative_height"] == 0.0
    assert cfg.coaching["tempo_target"] == 3.0
    assert cfg.coaching["club_aware_enabled"] is False
    assert cfg.shopify_customer_sync == {
        "enabled": False,
        "auto_sync_new_users": True,
        "request_timeout_seconds": 10,
        "max_attempts": 5,
        "retry_base_seconds": 30,
        "retry_max_seconds": 3600,
        "retry_jitter_ratio": 0.2,
    }


def test_partial_yaml_deep_merges(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "brand:\n  name: AceCoach\n  primary_color: '#123456'\n"
        "detection:\n  audio_height: 0.5\n"
    )
    cfg = Config.load(path)
    assert cfg.brand["name"] == "AceCoach"
    assert cfg.brand["primary_color"] == "#123456"
    # untouched keys keep defaults
    assert cfg.brand["disclaimer"] == DEFAULTS["brand"]["disclaimer"]
    assert cfg.detection["audio_height"] == 0.5
    assert cfg.detection["min_gap_s"] == 4.0


def test_shopify_customer_sync_partial_yaml_keeps_safe_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "shopify_customer_sync:\n"
        "  enabled: true\n"
        "  retry_max_seconds: 120\n"
    )

    cfg = Config.load(path)

    assert cfg.shopify_customer_sync["enabled"] is True
    assert cfg.shopify_customer_sync["auto_sync_new_users"] is True
    assert cfg.shopify_customer_sync["request_timeout_seconds"] == 10
    assert cfg.shopify_customer_sync["max_attempts"] == 5
    assert cfg.shopify_customer_sync["retry_base_seconds"] == 30
    assert cfg.shopify_customer_sync["retry_max_seconds"] == 120
    assert cfg.shopify_customer_sync["retry_jitter_ratio"] == 0.2


def test_missing_explicit_defaults(tmp_path):
    empty = tmp_path / "empty.yaml"
    empty.write_text("")
    cfg = Config.load(empty)
    assert cfg.data == DEFAULTS


def test_shipped_proof_cycle_activation_keeps_practice_collection_off():
    cfg = Config.load(Path(__file__).resolve().parents[1] / "config.yaml")

    assert cfg.proof_cycle["enabled"] is True
    assert cfg.proof_cycle["practice_evidence_enabled"] is False
    assert cfg.web["history_reset_enabled"] is True
    assert cfg.coaching["club_aware_enabled"] is True
