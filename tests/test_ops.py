"""Ops hardening: optional Sentry stays perfectly inert unless BOTH the
SENTRY_DSN env var is set and sentry-sdk is installed, and the web error
paths log through the logging module (uvicorn-compatible) instead of print."""

from __future__ import annotations

import logging
import sys
import types

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app, init_sentry
from tests.test_web import fake_analyze_ok


def test_no_dsn_means_no_sentry(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert init_sentry() is False


def test_dsn_without_sdk_degrades_with_a_warning(monkeypatch, caplog):
    monkeypatch.setenv("SENTRY_DSN", "https://key@sentry.example/1")
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)  # import -> ImportError
    with caplog.at_level(logging.WARNING, logger="swinglab.web"):
        assert init_sentry() is False
    assert any("sentry-sdk is not installed" in r.message for r in caplog.records)
    assert any("swinglab[ops]" in r.message for r in caplog.records)


def test_dsn_with_sdk_initializes(monkeypatch):
    calls = {}
    fake = types.ModuleType("sentry_sdk")
    fake.init = lambda **kwargs: calls.update(kwargs)
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
    monkeypatch.setenv("SENTRY_DSN", "https://key@sentry.example/1")
    assert init_sentry() is True
    assert calls["dsn"] == "https://key@sentry.example/1"
    assert calls["send_default_pii"] is False
    assert calls["include_local_variables"] is False
    assert calls["max_request_body_size"] == "never"


def test_app_creation_works_in_every_sentry_state(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    monkeypatch.setenv("SENTRY_DSN", "https://key@sentry.example/1")
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s"))
    assert client.get("/healthz").json()["status"] == "ok"


def test_missing_secret_warning_goes_through_logging(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    monkeypatch.delenv("SWINGLAB_SECRET", raising=False)
    cfg = Config()
    cfg.web["require_account"] = True
    with caplog.at_level(logging.WARNING, logger="swinglab.web"):
        create_app(cfg, sessions_dir=tmp_path / "s")
    assert any("SWINGLAB_SECRET" in r.message for r in caplog.records)


def test_unexpected_analysis_error_is_logged_with_traceback(
    tmp_path, monkeypatch, caplog
):
    def exploding(video_path, **kwargs):
        raise RuntimeError("boom: simulated bug")

    monkeypatch.setattr(jobs_module, "analyze_video", exploding)
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s"))
    with caplog.at_level(logging.ERROR, logger="swinglab.web.jobs"):
        from tests.test_web import upload, wait_for

        job_id = upload(client)
        data = wait_for(client, job_id)
    assert data["status"] == "failed"
    assert "boom: simulated bug" in data["error"]  # the job keeps its message
    logged = [r for r in caplog.records if "Unexpected error" in r.message]
    assert logged and logged[0].exc_info  # traceback attached for Sentry/ops
