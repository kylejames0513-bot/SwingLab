"""Retention and disk safety: web.delete_source_after_done (drop the raw
upload once the report exists; deliverables stay) and the /healthz disk
gauges. The retention_days cleanup itself is covered in test_web.py."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import DEFAULTS, Config
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from swinglab.web.jobs import JobManager
from tests.test_web import fake_analyze_no_strikes, fake_analyze_ok, upload, wait_for


def source_files(session_dir):
    return list(session_dir.glob("source.*"))


def test_shipped_config_and_code_defaults_differ_deliberately():
    """DEFAULTS keep everything forever (white-label operators opt in);
    the SHIPPED config.yaml turns retention + source deletion on."""
    assert DEFAULTS["web"]["retention_days"] == 0
    assert DEFAULTS["web"]["delete_source_after_done"] is False
    from pathlib import Path

    shipped = Config.load(Path(__file__).parent.parent / "config.yaml")
    assert shipped.web["retention_days"] == 180
    assert shipped.web["delete_source_after_done"] is True


def test_source_deleted_after_done_deliverables_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    cfg = Config()
    cfg.web["delete_source_after_done"] = True
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    job_id = upload(client)
    data = wait_for(client, job_id)
    assert data["status"] == "done"

    job = client.app.state.jobs.get(job_id)
    assert source_files(job.session_dir) == []  # raw upload gone
    # deliverables still served
    report = client.get(f"/session/{job_id}/report", follow_redirects=True)
    assert report.status_code == 200 and "fake report" in report.text
    assert client.get(
        f"/session/{job_id}/files/out/source/media/strip_s1.png"
    ).status_code == 200
    # the trade-off is stated in the session log, not hidden
    assert any("fresh upload" in line for line in data["log"])


def test_source_kept_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s"))
    job_id = upload(client)
    wait_for(client, job_id)
    job = client.app.state.jobs.get(job_id)
    assert len(source_files(job.session_dir)) == 1


def test_failed_job_source_also_deleted_when_configured(tmp_path, monkeypatch):
    """FAILED is terminal (restart only re-queues queued/processing work) and
    failed uploads don't count against quota — so with the deletion switch on,
    a failed job's source is dropped too. Otherwise refused clips (e.g.
    over-length videos) would accumulate on disk for free."""
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_no_strikes)
    cfg = Config()
    cfg.web["delete_source_after_done"] = True
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    job_id = upload(client)
    data = wait_for(client, job_id)
    assert data["status"] == "failed"
    job = client.app.state.jobs.get(job_id)
    assert source_files(job.session_dir) == []
    assert any("upload again to retry" in line for line in data["log"])


def test_failed_job_keeps_source_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_no_strikes)
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s"))
    job_id = upload(client)
    assert wait_for(client, job_id)["status"] == "failed"
    job = client.app.state.jobs.get(job_id)
    assert len(source_files(job.session_dir)) == 1


def test_healthz_reports_disk_and_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s"))
    before = client.get("/healthz").json()
    assert before["sessions_count"] == 0
    assert isinstance(before["disk_free_mb"], int) and before["disk_free_mb"] > 0

    job_id = upload(client)
    wait_for(client, job_id)
    assert client.get("/healthz").json()["sessions_count"] == 1


def test_done_job_with_deleted_source_survives_restart(tmp_path, monkeypatch):
    """The restart pass only re-queues QUEUED/PROCESSING jobs, so a DONE job
    whose source was deleted keeps serving results after a restart."""
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    cfg = Config()
    cfg.web["delete_source_after_done"] = True
    sessions = tmp_path / "s"
    client = TestClient(create_app(cfg, sessions_dir=sessions))
    job_id = upload(client)
    wait_for(client, job_id)

    fresh = TestClient(create_app(cfg, sessions_dir=sessions))  # "restart"
    data = fresh.get(f"/api/session/{job_id}").json()
    assert data["status"] == "done"
    assert "fake report" in fresh.get(
        f"/session/{job_id}/report", follow_redirects=True
    ).text
