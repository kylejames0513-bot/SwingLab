"""Web layer tests: upload -> status -> report flow, JSON API, failure paths,
and the traversal guard. The pipeline itself is faked here (it has its own
end-to-end tests); these tests exercise the web plumbing around it.
"""

from __future__ import annotations

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


def fake_analyze_ok(video_path, out_dir=None, hand="right", manual_strikes=None,
                    cfg=None, keep_work=False, log=print):
    log("Detected 1 strike(s): 3.00s")
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


def upload(client, filename="swing.mov"):
    resp = client.post(
        "/upload",
        files={"video": (filename, b"fake video bytes", "video/quicktime")},
        data={"hand": "right", "strikes": ""},
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

    status_html = client.get(f"/session/{job_id}").text
    assert "Results ready" in status_html

    report = client.get(f"/session/{job_id}/report", follow_redirects=True)
    assert report.status_code == 200 and "fake report" in report.text

    media = client.get(f"/session/{job_id}/files/out/source/media/strip_s1.png")
    assert media.status_code == 200


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


def test_manual_strikes_are_forwarded(tmp_path, monkeypatch):
    seen = {}

    def spy(video_path, manual_strikes=None, **kwargs):
        seen["strikes"] = manual_strikes
        return fake_analyze_ok(video_path, manual_strikes=manual_strikes, **kwargs)

    monkeypatch.setattr(jobs_module, "analyze_video", spy)
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s"))
    resp = client.post(
        "/upload",
        files={"video": ("swing.mov", b"x", "video/quicktime")},
        data={"strikes": "12.5, 31.0"},
        follow_redirects=False,
    )
    job_id = resp.headers["location"].rsplit("/", 1)[-1]
    wait_for(client, job_id)
    assert seen["strikes"] == [12.5, 31.0]


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
