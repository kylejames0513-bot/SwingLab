"""Experience-level context: stored on the job, forwarded to the pipeline,
written to metrics.json meta, shown as a chip and ONE framing line on the
report — display framing only, never a threshold input (the same contract
as the club context in swinglab.clubs)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.levels import LEVEL_LABELS, level_label, level_note
from swinglab.metrics import session_stats
from swinglab.report import REPORT_PRESENTATION_VERSION, write_report_html
from swinglab.report_artifacts import ReportEntitlementSnapshot
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from swinglab.web.jobs import JobManager
from tests.test_report import branded_cfg, fake_swing, fake_video
from tests.test_web import fake_analyze_ok, wait_for


def test_level_labels_cover_the_form_options():
    assert set(LEVEL_LABELS) == {"new", "improving", "experienced"}
    assert level_label("new") == "New to golf"
    assert level_label(None) is None and level_label("") is None
    assert level_label("scratch") is None
    # Framing lines: encouraging for "new", numbers-forward for
    # "experienced", none for "improving" (the default voice already fits).
    assert "normal" in level_note("new")
    assert "Benchmarks" in level_note("experienced")
    assert level_note("improving") is None
    assert level_note(None) is None


def test_job_round_trips_level_through_sqlite(tmp_path):
    manager = JobManager(tmp_path / "s", Config())
    job = manager.create_session(source_name="range.mov", level="new")
    assert manager.get(job.id).level == "new"
    # Unset stays honestly unset.
    plain = manager.get(manager.create_session(source_name="x.mov").id)
    assert plain.level is None


def test_old_database_without_level_column_migrates(tmp_path):
    import sqlite3

    sessions = tmp_path / "s"
    sessions.mkdir()
    conn = sqlite3.connect(sessions / "swinglab.db")
    conn.execute(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT NOT NULL,"
        " created_at REAL NOT NULL, updated_at REAL NOT NULL,"
        " source_name TEXT, hand TEXT NOT NULL DEFAULT 'right',"
        " strikes TEXT, fast INTEGER NOT NULL DEFAULT 0, client_ip TEXT,"
        " error TEXT, report_rel TEXT,"
        " swings_done INTEGER NOT NULL DEFAULT 0,"
        " swings_total INTEGER NOT NULL DEFAULT 0,"
        " log TEXT NOT NULL DEFAULT '[]')"
    )
    conn.execute(
        "INSERT INTO jobs (id, status, created_at, updated_at)"
        " VALUES ('old1', 'done', 0, 0)"
    )
    conn.commit()
    conn.close()

    manager = JobManager(sessions, Config())  # migrates in place
    veteran = manager.get("old1")
    assert veteran is not None and veteran.level is None
    assert veteran.report_presentation_version == REPORT_PRESENTATION_VERSION
    assert veteran.report_entitlements == ReportEntitlementSnapshot("available")
    assert veteran.report_view_rel is None
    assert veteran.report_manifest_rel is None
    assert veteran.report_checksums_rel is None
    assert veteran.structured_report is False

    columns = {
        row[1] for row in manager._conn.execute("PRAGMA table_info(jobs)")
    }
    assert {
        "report_presentation_version",
        "report_entitlements_json",
        "report_view_rel",
        "report_manifest_rel",
        "report_checksums_rel",
        "structured_report",
    } <= columns


def test_upload_forwards_level_to_the_pipeline(tmp_path, monkeypatch):
    seen = {}

    def spy(video_path, level=None, **kwargs):
        seen["level"] = level
        return fake_analyze_ok(video_path, **kwargs)

    monkeypatch.setattr(jobs_module, "analyze_video", spy)
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s"))
    resp = client.post(
        "/upload",
        files={"video": ("swing.mov", b"fake", "video/quicktime")},
        data={"level": "new", "club": "iron"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    job_id = resp.headers["location"].rsplit("/", 1)[-1]
    wait_for(client, job_id)
    assert seen["level"] == "new"
    assert client.get(f"/api/session/{job_id}").json()["level"] == "new"


def test_upload_rejects_unknown_level_and_allows_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s"))
    resp = client.post(
        "/upload",
        files={"video": ("swing.mov", b"fake", "video/quicktime")},
        data={"level": "tour-card-holder", "club": "iron"},
        follow_redirects=False,
    )
    assert resp.status_code == 400

    resp = client.post(
        "/upload",
        files={"video": ("swing.mov", b"fake", "video/quicktime")},
        data={"level": "", "club": "iron"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    job_id = resp.headers["location"].rsplit("/", 1)[-1]
    wait_for(client, job_id)
    assert client.get(f"/api/session/{job_id}").json()["level"] is None


def _report_html(level):
    cfg = branded_cfg()
    swings = [fake_swing(1)]
    stats = session_stats([s["metrics"] for s in swings])
    out = write_report_html(
        Path(tempfile.mkdtemp()) / "report.html", fake_video(), swings, stats,
        [], "right", cfg, level=level,
    )
    return out.read_text()


def test_report_frames_by_level_without_touching_numbers():
    new_html = _report_html("new")
    assert "New to golf" in new_html
    assert "Watch the trend across sessions" in new_html

    experienced_html = _report_html("experienced")
    assert "Experienced" in experienced_html
    assert "fixed flag thresholds" in experienced_html

    # "Improving" gets the chip but no extra framing — the default voice
    # already is that register.
    improving_html = _report_html("improving")
    assert "Improving" in improving_html
    assert "Watch the trend across sessions" not in improving_html

    # No level: no chip, no row, no framing.
    plain_html = _report_html(None)
    assert ">Experience<" not in plain_html
    assert "Watch the trend across sessions" not in plain_html
