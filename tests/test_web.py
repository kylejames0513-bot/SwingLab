"""Web layer tests: upload -> status -> report flow, JSON API, failure paths,
the traversal guard, and the robustness features (bounded queue, per-IP and
upload-size limits, restart recovery). The pipeline itself is faked here (it
has its own end-to-end tests); these tests exercise the web plumbing around it.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.ffmpeg import VideoInfo
from swinglab.pipeline import SessionResult, ZeroStrikesError
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from swinglab.web.jobs import PROCESSING, JobManager


def fake_analyze_ok(video_path, out_dir=None, hand="right", manual_strikes=None,
                    cfg=None, keep_work=False, fast=False, log=print, progress=None):
    log("Detected 1 strike(s): 3.00s")
    if progress:
        progress(0, 1)
        progress(1, 1)
    session_dir = Path(out_dir) / Path(video_path).stem
    media = session_dir / "media"
    media.mkdir(parents=True)
    (media / "strip_s1.png").write_bytes(b"\x89PNG fake")
    report = session_dir / "report.html"
    report.write_text("<html><body>fake report</body></html>")
    (session_dir / "metrics.json").write_text("{}")
    info = VideoInfo(Path(video_path), 20.0, 854, 480, 30.0, 0, None, True)
    return SessionResult(
        session_dir=session_dir, report_path=report,
        metrics_path=session_dir / "metrics.json", video=info,
        swings=[{}], stats={},
    )


def fake_analyze_no_strikes(video_path, **kwargs):
    raise ZeroStrikesError("No ball strikes detected. Use --strikes.")


def make_blocking_fake(release: threading.Event, started: threading.Event):
    """A fake analysis that parks until the test releases it."""

    def fake(video_path, **kwargs):
        started.set()
        assert release.wait(timeout=10), "test never released the fake analysis"
        return fake_analyze_ok(video_path, **kwargs)

    return fake


def wait_for(client, job_id, timeout=5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = client.get(f"/api/session/{job_id}").json()
        if data["status"] in ("done", "failed"):
            return data
        time.sleep(0.02)
    raise TimeoutError("job never finished")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    app = create_app(Config(), sessions_dir=tmp_path / "sessions")
    return TestClient(app)


def upload(client, filename="swing.mov", extra=None):
    resp = client.post(
        "/upload",
        files={"video": (filename, b"fake video bytes", "video/quicktime")},
        data={"hand": "right", "strikes": "", **(extra or {})},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return resp.headers["location"].rsplit("/", 1)[-1]


def test_upload_page_is_branded(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    cfg = Config()
    cfg.brand["name"] = "AceCoach"
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    html = client.get("/").text
    assert "AceCoach" in html and "Filming tips" in html


def test_full_flow_upload_status_report(client):
    job_id = upload(client)
    data = wait_for(client, job_id)
    assert data["status"] == "done"
    assert data["report_url"].endswith("report.html")
    assert "metrics_url" in data
    assert (data["swings_done"], data["swings_total"]) == (1, 1)
    assert data["queue_position"] is None
    assert data["source_name"] == "swing.mov"

    status_html = client.get(f"/session/{job_id}").text
    assert "Results ready" in status_html

    report = client.get(f"/session/{job_id}/report", follow_redirects=True)
    assert report.status_code == 200 and "fake report" in report.text

    media = client.get(f"/session/{job_id}/files/out/source/media/strip_s1.png")
    assert media.status_code == 200


def test_upload_returns_json_when_asked(client):
    resp = client.post(
        "/upload",
        files={"video": ("swing.mov", b"fake video bytes", "video/quicktime")},
        headers={"Accept": "application/json"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["url"] == f"/session/{data['id']}"
    wait_for(client, data["id"])


def test_failed_job_shows_error(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_no_strikes)
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s"))
    job_id = upload(client)
    data = wait_for(client, job_id)
    assert data["status"] == "failed"
    assert "No ball strikes" in data["error"]
    html = client.get(f"/session/{job_id}").text
    assert "Analysis failed" in html and "No ball strikes" in html


def test_bad_uploads_rejected(client):
    resp = client.post(
        "/upload",
        files={"video": ("notes.txt", b"hello", "text/plain")},
        follow_redirects=False,
    )
    assert resp.status_code == 400

    resp = client.post(
        "/upload",
        files={"video": ("swing.mov", b"x", "video/quicktime")},
        data={"strikes": "abc"},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_manual_strikes_and_fast_are_forwarded(tmp_path, monkeypatch):
    seen = {}

    def spy(video_path, manual_strikes=None, fast=False, **kwargs):
        seen["strikes"] = manual_strikes
        seen["fast"] = fast
        return fake_analyze_ok(
            video_path, manual_strikes=manual_strikes, fast=fast, **kwargs
        )

    monkeypatch.setattr(jobs_module, "analyze_video", spy)
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s"))
    job_id = upload(client, extra={"strikes": "12.5, 31.0", "fast": "on"})
    wait_for(client, job_id)
    assert seen["strikes"] == [12.5, 31.0]
    assert seen["fast"] is True


def test_unknown_session_404(client):
    assert client.get("/session/nope").status_code == 404
    assert client.get("/api/session/nope").status_code == 404


def test_path_traversal_blocked(client):
    job_id = upload(client)
    wait_for(client, job_id)
    resp = client.get(f"/session/{job_id}/files/../../../etc/passwd")
    assert resp.status_code == 404


def test_finished_job_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    sessions = tmp_path / "sessions"
    client = TestClient(create_app(Config(), sessions_dir=sessions))
    job_id = upload(client)
    wait_for(client, job_id)

    fresh = TestClient(create_app(Config(), sessions_dir=sessions))  # "restart"
    data = fresh.get(f"/api/session/{job_id}").json()
    assert data["status"] == "done"
    report = fresh.get(f"/session/{job_id}/report", follow_redirects=True)
    assert "fake report" in report.text


def test_interrupted_job_requeued_after_restart(tmp_path, monkeypatch):
    """A job that was mid-analysis when the process died runs again on boot."""
    sessions = tmp_path / "sessions"
    manager = JobManager(sessions, Config())
    job = manager.create_session(source_name="swing.mov")
    (job.session_dir / "source.mov").write_bytes(b"fake video bytes")
    job.status = PROCESSING
    manager._save(job)

    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    client = TestClient(create_app(Config(), sessions_dir=sessions))  # "restart"
    data = wait_for(client, job.id)
    assert data["status"] == "done"
    assert any("re-queued" in line for line in data["log"])


def test_interrupted_job_without_source_fails_cleanly(tmp_path):
    sessions = tmp_path / "sessions"
    manager = JobManager(sessions, Config())
    job = manager.create_session(source_name="swing.mov")  # upload never landed
    job.status = PROCESSING
    manager._save(job)

    client = TestClient(create_app(Config(), sessions_dir=sessions))
    data = client.get(f"/api/session/{job.id}").json()
    assert data["status"] == "failed"
    assert "upload it again" in data["error"]


def test_legacy_status_json_sessions_are_imported(tmp_path):
    sessions = tmp_path / "sessions"
    legacy = sessions / "abc123"
    legacy.mkdir(parents=True)
    (legacy / "status.json").write_text(
        '{"status": "done", "log": ["old log line"], "report": "out/report.html",'
        ' "swings_done": 2, "swings_total": 2}'
    )
    (legacy / "out").mkdir()
    (legacy / "out" / "report.html").write_text("<html>legacy report</html>")

    client = TestClient(create_app(Config(), sessions_dir=sessions))
    data = client.get("/api/session/abc123").json()
    assert data["status"] == "done"
    assert data["swings_total"] == 2
    report = client.get("/session/abc123/report", follow_redirects=True)
    assert "legacy report" in report.text


def test_queue_is_bounded_and_positions_reported(tmp_path, monkeypatch):
    release, started = threading.Event(), threading.Event()
    monkeypatch.setattr(
        jobs_module, "analyze_video", make_blocking_fake(release, started)
    )
    cfg = Config()
    cfg.web["workers"] = 1
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    try:
        first = upload(client)
        assert started.wait(timeout=5)
        second = upload(client)

        data = client.get(f"/api/session/{second}").json()
        assert data["status"] == "queued"
        assert data["queue_position"] == 1
        html = client.get(f"/session/{second}").text
        assert "Waiting in line" in html

        health = client.get("/healthz").json()
        assert health == {"status": "ok", "queued": 1, "processing": 1}
    finally:
        release.set()
    assert wait_for(client, first)["status"] == "done"
    assert wait_for(client, second)["status"] == "done"
    assert client.get("/healthz").json() == {
        "status": "ok", "queued": 0, "processing": 0,
    }


def test_per_ip_active_job_limit(tmp_path, monkeypatch):
    release, started = threading.Event(), threading.Event()
    monkeypatch.setattr(
        jobs_module, "analyze_video", make_blocking_fake(release, started)
    )
    cfg = Config()
    cfg.web["max_active_jobs_per_ip"] = 1
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    try:
        first = upload(client)
        assert started.wait(timeout=5)
        resp = client.post(
            "/upload",
            files={"video": ("swing.mov", b"x", "video/quicktime")},
            follow_redirects=False,
        )
        assert resp.status_code == 429
    finally:
        release.set()
    wait_for(client, first)
    upload(client)  # slot freed — allowed again


def test_oversized_upload_rejected_and_discarded(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    cfg = Config()
    cfg.web["max_upload_mb"] = 10 / (1024 * 1024)  # 10-byte cap
    sessions = tmp_path / "s"
    client = TestClient(create_app(cfg, sessions_dir=sessions))
    resp = client.post(
        "/upload",
        files={"video": ("swing.mov", b"way more than ten bytes", "video/quicktime")},
        follow_redirects=False,
    )
    assert resp.status_code == 413
    assert not [p for p in sessions.iterdir() if p.is_dir()]  # session cleaned up
    assert client.get("/api/sessions").json()["sessions"] == []


def test_session_history(client):
    job_id = upload(client, filename="range-day.mov")
    wait_for(client, job_id)

    listed = client.get("/api/sessions").json()["sessions"]
    assert [s["id"] for s in listed] == [job_id]
    assert listed[0]["source_name"] == "range-day.mov"
    assert listed[0]["status"] == "done"

    html = client.get("/sessions").text
    assert "range-day.mov" in html and f"/session/{job_id}" in html


def test_expired_sessions_are_cleaned_up(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    cfg = Config()
    cfg.web["retention_days"] = 7
    sessions = tmp_path / "s"
    client = TestClient(create_app(cfg, sessions_dir=sessions))
    job_id = upload(client)
    wait_for(client, job_id)

    # age the finished job past the retention window, then "restart"
    manager = client.app.state.jobs
    with manager._lock:
        manager._conn.execute(
            "UPDATE jobs SET updated_at = updated_at - 8 * 86400 WHERE id = ?",
            (job_id,),
        )
        manager._conn.commit()
    fresh = TestClient(create_app(cfg, sessions_dir=sessions))
    assert fresh.get(f"/api/session/{job_id}").status_code == 404
    assert not (sessions / job_id).exists()
