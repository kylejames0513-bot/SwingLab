"""Web layer tests: upload -> status -> report flow, JSON API, failure paths,
the traversal guard, and the robustness features (bounded queue, per-IP and
upload-size limits, restart recovery). The pipeline itself is faked here (it
has its own end-to-end tests); these tests exercise the web plumbing around it.
"""

from __future__ import annotations

import builtins
import json
import threading
import time
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.ffmpeg import VideoInfo
from swinglab.pipeline import SessionResult, ZeroStrikesError
from swinglab.report import REPORT_FORMAT_VERSION
from swinglab.web import app as app_module, jobs as jobs_module
from swinglab.web.app import create_app
from swinglab.web.jobs import PROCESSING, JobManager


def fake_analyze_ok(video_path, out_dir=None, hand="right", manual_strikes=None,
                    cfg=None, keep_work=False, fast=False, log=print, progress=None,
                    angle="face-on", club=None, level=None, replay_locked=False):
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
    (session_dir / "metrics.json").write_text(
        json.dumps(
            {
                "swings": [{"metrics": {"tempo_ratio": 3.0}}],
                "session_stats": {},
            }
        )
    )
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
    assert "AceCoach" in html and "Filming checklist" in html


def test_open_mode_first_analysis_state_is_per_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    app = create_app(Config(), sessions_dir=tmp_path / "sessions")
    first = TestClient(app)
    fresh = TestClient(app)

    assert "Build your swing baseline" in first.get("/").text
    assert "Build your swing baseline" in fresh.get("/").text

    job_id = upload(first)
    wait_for(first, job_id)

    returning_html = first.get("/").text
    assert "Your next coaching check-in" in returning_html
    assert 'id="fast" name="fast" type="checkbox" checked' not in returning_html

    fresh_html = fresh.get("/").text
    assert "Build your swing baseline" in fresh_html
    assert 'id="fast" name="fast" type="checkbox" checked' in fresh_html


def test_failed_open_analysis_does_not_consume_first_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_no_strikes)
    client = TestClient(
        create_app(Config(), sessions_dir=tmp_path / "sessions")
    )

    assert "Build your swing baseline" in client.get("/").text
    job_id = upload(client)
    assert wait_for(client, job_id)["status"] == "failed"

    html = client.get("/").text
    assert "Build your swing baseline" in html
    assert 'id="fast" name="fast" type="checkbox" checked' in html


def test_unreliable_camera_result_stops_coaching_and_gear(tmp_path, monkeypatch):
    warning = (
        "Low confidence: this clip looks like it was filmed down the line, "
        "but it was uploaded as face-on — numbers may not mean what they say."
    )

    def fake_bad_angle(video_path, **kwargs):
        result = fake_analyze_ok(video_path, **kwargs)
        result.report_path.write_text(
            "<html><head>"
            f'<meta name="caddieinsight-report-format" content="{REPORT_FORMAT_VERSION}">'
            '<meta name="caddieinsight-report-outcome" content="coaching_ready">'
            "</head><body>unsafe coaching report</body></html>",
            encoding="utf-8",
        )
        result.metrics_path.write_text(
            json.dumps(
                {
                    "meta": {"camera_angle": "face-on"},
                    "session_notes": [warning],
                    "swings": [{"metrics": {"tempo_ratio": 2.0}}],
                }
            ),
            encoding="utf-8",
        )
        return result

    monkeypatch.setattr(jobs_module, "analyze_video", fake_bad_angle)
    monkeypatch.setattr(app_module.shop, "enabled", lambda: True)
    monkeypatch.setattr(
        app_module.shop,
        "fetch_products",
        lambda cfg: pytest.fail("unreliable measurements must not request gear"),
    )
    client = TestClient(
        create_app(Config(), sessions_dir=tmp_path / "sessions")
    )

    job_id = upload(client)
    outcome = wait_for(client, job_id)
    html = client.get(f"/session/{job_id}").text

    assert "Re-film before coaching" in html
    assert "Re-film needed · clip reviewed" in html
    assert warning in html
    assert "Practice this" not in html
    assert "Optional aid" not in html
    assert "Build your swing baseline" in client.get("/").text
    assert outcome["status"] == "done"
    assert outcome["outcome"] == "refilm_required"
    assert outcome["coaching_eligible"] is False
    assert "report_url" not in outcome
    assert "metrics_url" not in outcome
    listed = client.get("/api/sessions").json()["sessions"]
    assert listed[0]["outcome"] == "refilm_required"
    assert "Re-film needed" in client.get("/sessions").text
    report = client.get(
        f"/session/{job_id}/report", follow_redirects=False
    )
    assert report.status_code == 303
    assert report.headers["location"] == f"/session/{job_id}"
    direct_report = client.get(
        f"/session/{job_id}/files/out/source/report.html",
        follow_redirects=False,
    )
    assert direct_report.status_code == 303
    direct_metrics = client.get(
        f"/session/{job_id}/files/out/source/metrics.json",
        follow_redirects=False,
    )
    assert direct_metrics.status_code == 303
    job = client.app.state.jobs.get(job_id)
    report_path = job.session_dir / "out" / "source" / "report.html"
    metrics_path = report_path.with_name("metrics.json")
    report_path.with_name("report.html.").write_bytes(report_path.read_bytes())
    metrics_path.with_name("metrics.json.").write_bytes(
        metrics_path.read_bytes()
    )
    assert client.get(
        f"/session/{job_id}/files/out/source/report.html.",
        follow_redirects=False,
    ).status_code == 303
    assert client.get(
        f"/session/{job_id}/files/out/source/metrics.json.",
        follow_redirects=False,
    ).status_code == 303


def test_current_capture_only_report_stays_available_but_metrics_do_not(
    tmp_path, monkeypatch
):
    warning = (
        "Tracking was unstable for this swing — numbers may be off; "
        "film with a clear view."
    )

    def fake_capture_only(video_path, **kwargs):
        result = fake_analyze_ok(video_path, **kwargs)
        result.metrics_path.write_text(
            json.dumps(
                {
                    "swings": [
                        {
                            "metrics": {"tempo_ratio": 2.0},
                            "notes": [warning],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        result.report_path.write_text(
            "<html><head>"
            f'<meta name="caddieinsight-report-format" content="{REPORT_FORMAT_VERSION}">'
            '<meta name="caddieinsight-report-outcome" content="capture_only">'
            "</head><body>safe capture details</body></html>",
            encoding="utf-8",
        )
        slowmo = result.session_dir / "media" / "slowmo_s1.mp4"
        slowmo.parent.mkdir(exist_ok=True)
        slowmo.write_bytes(b"fake slowmo")
        return result

    monkeypatch.setattr(jobs_module, "analyze_video", fake_capture_only)
    client = TestClient(
        create_app(Config(), sessions_dir=tmp_path / "sessions")
    )
    job_id = upload(client)
    outcome = wait_for(client, job_id)

    assert outcome["outcome"] == "refilm_required"
    assert "report_url" in outcome
    assert "metrics_url" not in outcome
    status = client.get(f"/session/{job_id}").text
    assert "Review capture details" in status
    report = client.get(f"/session/{job_id}/report", follow_redirects=True)
    assert report.status_code == 200
    assert "safe capture details" in report.text
    assert client.get(
        f"/session/{job_id}/files/out/source/metrics.json",
        follow_redirects=False,
    ).status_code == 303
    assert client.get(
        f"/session/{job_id}/files/out/source/media/strip_s1.png",
        follow_redirects=False,
    ).status_code == 303
    metrics = (
        client.app.state.jobs.get(job_id).session_dir
        / "out"
        / "source"
        / "metrics.json"
    )
    uppercase_metrics = metrics.with_name("METRICS.JSON")
    uppercase_metrics.write_bytes(metrics.read_bytes())
    strip = (
        client.app.state.jobs.get(job_id).session_dir
        / "out"
        / "source"
        / "media"
        / "strip_s1.png"
    )
    uppercase_strip = strip.with_name("STRIP_S1.PNG")
    uppercase_strip.write_bytes(strip.read_bytes())
    assert client.get(
        f"/session/{job_id}/files/out/source/METRICS.JSON",
        follow_redirects=False,
    ).status_code == 303
    assert client.get(
        f"/session/{job_id}/files/out/source/media/STRIP_S1.PNG",
        follow_redirects=False,
    ).status_code == 303
    assert client.get(
        f"/session/{job_id}/files/out/source/media/slowmo_s1.mp4",
        follow_redirects=False,
    ).status_code == 200
    metrics.unlink()
    restored = client.get(f"/api/session/{job_id}").json()
    assert restored["outcome"] == "refilm_required"
    assert restored["coaching_eligible"] is False
    restored_status = client.get(f"/session/{job_id}").text
    assert "Review capture details" in restored_status
    assert client.get(
        f"/session/{job_id}/report", follow_redirects=True
    ).status_code == 200


def test_capture_only_marker_vetoes_coaching_ready_metrics(
    tmp_path, monkeypatch
):
    def fake_capture_with_eligible_metrics(video_path, **kwargs):
        result = fake_analyze_ok(video_path, **kwargs)
        result.report_path.write_text(
            "<html><head>"
            f'<meta name="caddieinsight-report-format" content="{REPORT_FORMAT_VERSION}">'
            '<meta name="caddieinsight-report-outcome" content="capture_only">'
            "</head><body>capture-only report</body></html>",
            encoding="utf-8",
        )
        result.metrics_path.write_text(
            json.dumps({"swings": [{"metrics": {"tempo_ratio": 3.0}}]}),
            encoding="utf-8",
        )
        return result

    monkeypatch.setattr(
        jobs_module, "analyze_video", fake_capture_with_eligible_metrics
    )
    client = TestClient(
        create_app(Config(), sessions_dir=tmp_path / "sessions")
    )
    job_id = upload(client)
    outcome = wait_for(client, job_id)
    assert outcome["outcome"] == "refilm_required"
    assert outcome["coaching_eligible"] is False
    assert "report_url" in outcome and "metrics_url" not in outcome
    status = client.get(f"/session/{job_id}").text
    assert "Your caddie's read" not in status
    assert "Practice this" not in status
    assert "Review capture details" in status
    assert client.get(
        f"/session/{job_id}/files/out/source/metrics.json",
        follow_redirects=False,
    ).status_code == 303


def test_job_dtl_angle_scopes_no_meta_payload_and_withholds_stale_raw_metrics(
    tmp_path, monkeypatch
):
    def fake_dtl_without_meta(video_path, **kwargs):
        result = fake_analyze_ok(video_path, **kwargs)
        result.metrics_path.write_text(
            json.dumps(
                {
                    "swings": [
                        {
                            "metrics": {
                                "tempo_ratio": 3.0,
                                "head_sway_backswing_sw": 0.8,
                            }
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return result

    monkeypatch.setattr(jobs_module, "analyze_video", fake_dtl_without_meta)
    client = TestClient(
        create_app(Config(), sessions_dir=tmp_path / "sessions")
    )
    job_id = upload(client, extra={"angle": "dtl"})
    outcome = wait_for(client, job_id)

    assert outcome["angle"] == "dtl"
    assert outcome["coaching_eligible"] is True
    assert outcome["outcome"] == "coaching_ready"
    assert "metrics_url" not in outcome
    status = client.get(f"/session/{job_id}").text
    assert "Protect your tempo baseline" in status
    assert "Head sway" not in status
    assert client.get(
        f"/session/{job_id}/files/out/source/metrics.json",
        follow_redirects=False,
    ).status_code == 303


def test_severe_warning_vetoes_structural_marker_recovery(
    tmp_path, monkeypatch
):
    warning = (
        "Tracking was unstable for this swing — numbers may be off; "
        "film with a clear view."
    )

    def fake_structural_warning(video_path, **kwargs):
        result = fake_analyze_ok(video_path, **kwargs)
        result.report_path.write_text(
            "<html><head>"
            f'<meta name="caddieinsight-report-format" content="{REPORT_FORMAT_VERSION}">'
            '<meta name="caddieinsight-report-outcome" content="coaching_ready">'
            "</head><body>unsafe coaching</body></html>",
            encoding="utf-8",
        )
        result.metrics_path.write_text(
            json.dumps(
                {
                    "swings": 1,
                    "session_notes": [warning],
                }
            ),
            encoding="utf-8",
        )
        return result

    monkeypatch.setattr(jobs_module, "analyze_video", fake_structural_warning)
    client = TestClient(
        create_app(Config(), sessions_dir=tmp_path / "sessions")
    )
    job_id = upload(client)
    outcome = wait_for(client, job_id)

    assert outcome["coaching_eligible"] is False
    assert outcome["outcome"] == "refilm_required"
    assert "report_url" not in outcome
    assert "metrics_url" not in outcome
    status = client.get(f"/session/{job_id}").text
    assert "Re-film before coaching" in status
    assert warning in status


@pytest.mark.parametrize(
    "bad_metrics",
    ["{truncated", '{"swings": 1}', '{"swings": []}'],
)
def test_current_coaching_report_survives_corrupt_metrics_without_exposing_them(
    tmp_path, monkeypatch, bad_metrics
):
    def fake_coaching_with_corrupt_metrics(video_path, **kwargs):
        result = fake_analyze_ok(video_path, **kwargs)
        result.report_path.write_text(
            "<html><head>"
            f'<meta name="caddieinsight-report-format" content="{REPORT_FORMAT_VERSION}">'
            '<meta name="caddieinsight-report-outcome" content="coaching_ready">'
            "</head><body>persisted coaching report</body></html>",
            encoding="utf-8",
        )
        result.metrics_path.write_text(bad_metrics, encoding="utf-8")
        return result

    monkeypatch.setattr(
        jobs_module, "analyze_video", fake_coaching_with_corrupt_metrics
    )
    client = TestClient(
        create_app(Config(), sessions_dir=tmp_path / "sessions")
    )
    job_id = upload(client)
    outcome = wait_for(client, job_id)

    assert outcome["outcome"] == "coaching_ready"
    assert outcome["coaching_eligible"] is True
    assert "report_url" in outcome
    assert "metrics_url" not in outcome
    status = client.get(f"/session/{job_id}").text
    assert "structured metrics could not be read" in " ".join(status.split())
    report = client.get(f"/session/{job_id}/report", follow_redirects=True)
    assert report.status_code == 200
    assert "persisted coaching report" in report.text
    assert client.get(
        f"/session/{job_id}/files/out/source/metrics.json",
        follow_redirects=False,
    ).status_code == 303


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


def test_upload_rejects_cross_origin_before_creating_session(client):
    assert client.app.state.jobs.sessions_count() == 0

    response = client.post(
        "/upload",
        files={"video": ("swing.mov", b"fake video bytes", "video/quicktime")},
        headers={"Origin": "https://evil.example"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert client.app.state.jobs.sessions_count() == 0


def test_failed_job_shows_error(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_no_strikes)
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s"))
    job_id = upload(client)
    data = wait_for(client, job_id)
    assert data["status"] == "failed"
    assert "No ball strikes" in data["error"]  # the API keeps the raw error
    html = client.get(f"/session/{job_id}").text
    assert "Analysis failed" in html and "No ball strikes" in html
    # The web page translates — CLI flags and config keys never reach it.
    assert "--strikes" not in html and "config" not in html


def test_failed_job_cannot_serve_partial_result_artifacts(
    tmp_path, monkeypatch
):
    def write_results_then_fail(video_path, out_dir=None, **kwargs):
        session = Path(out_dir) / Path(video_path).stem
        media = session / "media"
        media.mkdir(parents=True)
        (session / "report.html").write_text("<html>partial report</html>")
        (session / "metrics.json").write_text(
            json.dumps({"swings": [{"metrics": {"tempo_ratio": 3.0}}]})
        )
        (media / "strip_s1.png").write_bytes(b"partial strip")
        raise RuntimeError("metrics persistence failed")

    monkeypatch.setattr(
        jobs_module, "analyze_video", write_results_then_fail
    )
    client = TestClient(
        create_app(Config(), sessions_dir=tmp_path / "sessions")
    )
    job_id = upload(client)
    assert wait_for(client, job_id)["status"] == "failed"
    for path in (
        "out/source/report.html",
        "out/source/metrics.json",
        "out/source/media/strip_s1.png",
    ):
        response = client.get(
            f"/session/{job_id}/files/{path}", follow_redirects=False
        )
        assert response.status_code == 303
        assert response.headers["location"] == f"/session/{job_id}"


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
    status = client.get("/session/abc123").text
    assert "View original report" in status
    assert 'href="/session/abc123/report"' in status
    report = client.get("/session/abc123/report", follow_redirects=True)
    assert "legacy report" in report.text


def test_done_job_without_report_has_consistent_refilm_outcome(tmp_path):
    sessions = tmp_path / "sessions"
    manager = JobManager(sessions, Config())
    job = manager.create_session(source_name="missing.mov")
    job.status = "done"
    job.report_rel = None
    manager._save(job)
    metrics = job.session_dir / "out" / "source" / "metrics.json"
    metrics.parent.mkdir(parents=True)
    metrics.write_text(
        json.dumps({"swings": [{"metrics": {"tempo_ratio": 3.0}}]})
    )
    leftover_report = metrics.parent / "report.html"
    leftover_report.write_text("<html>undeclared coaching report</html>")
    client = TestClient(create_app(Config(), sessions_dir=sessions))

    single = client.get(f"/api/session/{job.id}").json()
    listed = client.get("/api/sessions").json()["sessions"][0]
    assert single["outcome"] == "refilm_required"
    assert single["coaching_eligible"] is False
    assert "report_url" not in single and "metrics_url" not in single
    assert listed["outcome"] == single["outcome"]
    assert client.get(
        f"/session/{job.id}/files/out/source/metrics.json",
        follow_redirects=False,
    ).status_code == 303
    assert client.get(
        f"/session/{job.id}/files/out/source/report.html",
        follow_redirects=False,
    ).status_code == 303


def test_done_job_with_missing_declared_report_cannot_power_coaching(
    tmp_path
):
    sessions = tmp_path / "sessions"
    manager = JobManager(sessions, Config())
    job = manager.create_session(source_name="partial.mov")
    job.status = "done"
    job.report_rel = "out/report.html"
    metrics = job.session_dir / "out" / "metrics.json"
    metrics.parent.mkdir(parents=True)
    metrics.write_text(
        json.dumps({"swings": [{"metrics": {"tempo_ratio": 3.0}}]})
    )
    manager._save(job)
    client = TestClient(create_app(Config(), sessions_dir=sessions))

    outcome = client.get(f"/api/session/{job.id}").json()
    assert outcome["outcome"] == "refilm_required"
    assert outcome["coaching_eligible"] is False
    assert "report_url" not in outcome and "metrics_url" not in outcome
    status = client.get(f"/session/{job.id}").text
    assert "Your caddie's read" not in status
    assert "Re-film before coaching" in status
    assert client.get(
        f"/session/{job.id}/files/out/metrics.json",
        follow_redirects=False,
    ).status_code == 303


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
        assert (health["status"], health["queued"], health["processing"]) == (
            "ok", 1, 1,
        )
        assert health["disk_free_mb"] > 0  # ops signal: disk-full visibility
        assert health["sessions_count"] == 2
    finally:
        release.set()
    assert wait_for(client, first)["status"] == "done"
    assert wait_for(client, second)["status"] == "done"
    idle = client.get("/healthz").json()
    assert (idle["status"], idle["queued"], idle["processing"]) == ("ok", 0, 0)


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
    opened = []
    real_open = builtins.open

    def tracked_open(*args, **kwargs):
        handle = real_open(*args, **kwargs)
        opened.append(handle)
        return handle

    monkeypatch.setattr(app_module, "open", tracked_open, raising=False)
    cfg = Config()
    cfg.web["max_upload_mb"] = 10 / (1024 * 1024)  # 10-byte cap
    sessions = tmp_path / "s"
    client = TestClient(create_app(cfg, sessions_dir=sessions))
    real_discard = client.app.state.jobs.discard

    def discard_after_close(job):
        assert opened and all(handle.closed for handle in opened)
        real_discard(job)

    monkeypatch.setattr(client.app.state.jobs, "discard", discard_after_close)
    resp = client.post(
        "/upload",
        files={"video": ("swing.mov", b"way more than ten bytes", "video/quicktime")},
        follow_redirects=False,
    )
    assert resp.status_code == 413
    # session cleaned up (only the startup-generated sample report remains)
    assert not [
        p for p in sessions.iterdir()
        if p.is_dir() and p.name != "sample-report"
    ]
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
