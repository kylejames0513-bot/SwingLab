"""Durable resumable mobile uploads.

This suite starts with the server-owned upload/retry policy bounds (validated
strictly at app composition time) and grows into the create/chunk/complete/abort
contract. Capacity guards ship at 0 while the feature is off and must be
measured, strictly-positive values before ``mobile_resumable_upload_enabled``
turns on.
"""

from __future__ import annotations

import pytest

from swinglab.config import Config
from swinglab.web.mobile_resources import validate_mobile_resource_settings


def _web(**overrides):
    web = dict(Config().web)
    web.update(overrides)
    return web


def test_shipped_defaults_are_off_with_zero_capacity_guards() -> None:
    settings = validate_mobile_resource_settings(_web())
    assert settings.resumable_upload_enabled is False
    assert settings.analysis_retry_window_seconds == 86400
    assert settings.analysis_retry_max_attempts == 2
    assert settings.upload_global_max_reserved_bytes == 0
    assert settings.upload_min_filesystem_free_bytes == 0


def test_enabling_upload_requires_positive_capacity_guards() -> None:
    # Global reserved bytes still 0 -> rejected.
    with pytest.raises(ValueError):
        validate_mobile_resource_settings(
            _web(
                mobile_resumable_upload_enabled=True,
                mobile_upload_min_filesystem_free_bytes=1024,
            )
        )
    # Min filesystem free still 0 -> rejected.
    with pytest.raises(ValueError):
        validate_mobile_resource_settings(
            _web(
                mobile_resumable_upload_enabled=True,
                mobile_upload_global_max_reserved_bytes=1024,
            )
        )


def test_enabling_upload_with_positive_guards_succeeds() -> None:
    settings = validate_mobile_resource_settings(
        _web(
            mobile_resumable_upload_enabled=True,
            mobile_upload_global_max_reserved_bytes=10 * 1024 * 1024 * 1024,
            mobile_upload_min_filesystem_free_bytes=1 * 1024 * 1024 * 1024,
        )
    )
    assert settings.resumable_upload_enabled is True
    assert settings.upload_global_max_reserved_bytes == 10 * 1024 * 1024 * 1024


@pytest.mark.parametrize(
    "overrides",
    [
        {"mobile_analysis_retry_window_seconds": 0},
        {"mobile_analysis_retry_window_seconds": -1},
        {"mobile_analysis_retry_max_attempts": 0},
        {"mobile_analysis_retry_max_attempts": 11},
        {"mobile_upload_global_max_reserved_bytes": -1},
        {"mobile_upload_min_filesystem_free_bytes": (1 << 43) + 1},
        {"mobile_analysis_retry_max_attempts": True},
    ],
)
def test_out_of_range_or_wrong_type_values_rejected(overrides) -> None:
    with pytest.raises(ValueError):
        validate_mobile_resource_settings(_web(**overrides))


def test_shipped_config_yaml_composes() -> None:
    cfg = Config.load("config.yaml")
    settings = validate_mobile_resource_settings(cfg.web)
    assert settings.resumable_upload_enabled is False
    assert settings.analysis_retry_max_attempts == 2
