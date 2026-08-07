"""Disconnect-mid-upload regression: a client that hangs up while its video
streams in must NOT leak a permanently-QUEUED job. Before the fix only
OSError was caught, so the half-written job kept its session row forever —
eating one of the visitor's per-IP slots and a monthly-quota analysis for a
video that never arrived."""

from __future__ import annotations

import asyncio

import pytest

fastapi = pytest.importorskip("fastapi")
import starlette.datastructures
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from swinglab.config import Config
from swinglab.report_bundle import GuidedReportRendererUnavailable
from swinglab.web.jobs import JobManager
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from tests.test_web import fake_analyze_ok, upload, wait_for


def _breaking_read(exc_type, chunks_before_break=1):
    """An UploadFile.read that yields some real data, then raises like a
    dropped connection does."""
    state = {"reads": 0}

    async def read(self, size=-1):
        state["reads"] += 1
        if state["reads"] <= chunks_before_break:
            return b"partial video bytes"
        raise exc_type()

    return read


def session_dirs(sessions):
    return [
        p for p in sessions.iterdir()
        if p.is_dir() and p.name != "sample-report"
    ]


def test_guided_writer_rejection_cannot_leak_an_upload_session(tmp_path):
    sessions = tmp_path / "s"
    cfg = Config()
    cfg.report["guided_presentation_enabled"] = True
    manager = JobManager(sessions, cfg)

    with pytest.raises(GuidedReportRendererUnavailable):
        manager.create_session(source_name="swing.mov")

    assert session_dirs(sessions) == []
    assert manager._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_disconnect_mid_upload_leaks_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    sessions = tmp_path / "s"
    cfg = Config()
    cfg.web["max_active_jobs_per_ip"] = 1  # the tightest slot to leak
    client = TestClient(create_app(cfg, sessions_dir=sessions))

    monkeypatch.setattr(
        starlette.datastructures.UploadFile, "read",
        _breaking_read(ClientDisconnect),
    )
    resp = client.post(
        "/upload",
        files={"video": ("swing.mov", b"fake video bytes", "video/quicktime")},
        data={"club": "iron"},
        follow_redirects=False,
    )
    assert resp.status_code == 400

    # no partial file, no job row, no queued ghost
    assert session_dirs(sessions) == []
    assert client.get("/api/sessions").json()["sessions"] == []
    health = client.get("/healthz").json()
    assert health["queued"] == 0 and health["processing"] == 0

    # the visitor's one per-IP slot was NOT consumed: a clean retry works
    monkeypatch.undo()
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    job_id = upload(client)
    assert wait_for(client, job_id)["status"] == "done"


def test_disconnect_does_not_count_against_monthly_quota(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["free_per_month"] = 1  # one analysis: any leak would block it
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    client.post(
        "/signup",
        data={"email": "kyle@example.com", "password": "longenough"},
        follow_redirects=False,
    )

    monkeypatch.setattr(
        starlette.datastructures.UploadFile, "read",
        _breaking_read(ClientDisconnect),
    )
    resp = client.post(
        "/upload",
        files={"video": ("swing.mov", b"fake video bytes", "video/quicktime")},
        data={"club": "iron"},
        follow_redirects=False,
    )
    assert resp.status_code == 400

    users = client.app.state.users
    manager = client.app.state.jobs
    user = users.get_by_email("kyle@example.com")
    assert manager.usage_this_month(user.id) == 0  # nothing was consumed

    # the single free analysis is still available
    monkeypatch.undo()
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    job_id = upload(client)
    assert wait_for(client, job_id)["status"] == "done"
    assert manager.usage_this_month(user.id) == 1


def test_cancellation_mid_upload_cleans_up_and_propagates(tmp_path, monkeypatch):
    """Server-side task cancellation (shutdown, or an ASGI server mapping a
    disconnect to CancelledError) also discards the partial job — and the
    cancellation keeps propagating as asyncio requires."""
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    sessions = tmp_path / "s"
    client = TestClient(
        create_app(Config(), sessions_dir=sessions),
        raise_server_exceptions=True,
    )
    monkeypatch.setattr(
        starlette.datastructures.UploadFile, "read",
        _breaking_read(asyncio.CancelledError),
    )
    with pytest.raises(BaseException):
        client.post(
            "/upload",
            files={"video": ("swing.mov", b"x", "video/quicktime")},
            data={"club": "iron"},
            follow_redirects=False,
        )
    assert session_dirs(sessions) == []
    assert client.get("/api/sessions").json()["sessions"] == []
