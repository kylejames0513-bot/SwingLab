"""Club context: stored on the job, forwarded to the pipeline, written to
metrics.json meta, shown as chips on report/history, and filterable on
/progress — display context only, never a threshold input."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.clubs import CLUB_LABELS, club_label
from swinglab.config import Config
from swinglab.metrics import session_stats
from swinglab.report import write_metrics_json, write_report_html
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from swinglab.web.jobs import JobManager
from tests.test_report import branded_cfg, fake_swing, fake_video
from tests.test_trends import (
    make_fake_analyze,
    payload_for,
    signup,
)
from tests.test_web import fake_analyze_ok, wait_for


def test_club_labels_cover_the_form_options():
    assert set(CLUB_LABELS) == {
        "driver", "fairway-wood", "hybrid", "iron", "wedge",
    }
    assert club_label("iron") == "Iron"
    assert club_label(None) is None and club_label("") is None
    assert club_label("mystery") is None


def test_job_round_trips_club_and_angle_through_sqlite(tmp_path):
    manager = JobManager(tmp_path / "s", Config())
    job = manager.create_session(
        source_name="range.mov", club="iron", angle="dtl"
    )
    loaded = manager.get(job.id)
    assert loaded.club == "iron"
    assert loaded.angle == "dtl"
    # And a job created without either keeps honest defaults.
    plain = manager.get(manager.create_session(source_name="x.mov").id)
    assert plain.club is None and plain.angle == "face-on"


def test_upload_forwards_club_and_shows_chip(tmp_path, monkeypatch):
    seen = {}

    def spy(video_path, angle="face-on", club=None, **kwargs):
        seen["club"] = club
        return fake_analyze_ok(video_path, angle=angle, club=club, **kwargs)

    monkeypatch.setattr(jobs_module, "analyze_video", spy)
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s"))
    resp = client.post(
        "/upload",
        files={"video": ("swing.mov", b"fake", "video/quicktime")},
        data={"club": "wedge"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    job_id = resp.headers["location"].rsplit("/", 1)[-1]
    wait_for(client, job_id)
    assert seen["club"] == "wedge"
    assert client.get(f"/api/session/{job_id}").json()["club"] == "wedge"
    sessions_html = client.get("/sessions").text
    assert "Wedge" in sessions_html  # the chip in the history list


def test_upload_rejects_unknown_club_and_allows_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s"))
    resp = client.post(
        "/upload",
        files={"video": ("swing.mov", b"fake", "video/quicktime")},
        data={"club": "putter-on-the-range"},
        follow_redirects=False,
    )
    assert resp.status_code == 400

    resp = client.post(
        "/upload",
        files={"video": ("swing.mov", b"fake", "video/quicktime")},
        data={"club": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    job_id = resp.headers["location"].rsplit("/", 1)[-1]
    wait_for(client, job_id)
    assert client.get(f"/api/session/{job_id}").json()["club"] is None


def test_metrics_json_meta_round_trip():
    swing = fake_swing(1)
    out = write_metrics_json(
        Path(tempfile.mkdtemp()) / "metrics.json", fake_video(), [swing],
        session_stats([swing["metrics"]]), [], Config(),
        meta={"camera_angle": "face-on", "club": "iron", "hand": "right"},
    )
    data = json.loads(out.read_text())
    assert data["meta"] == {
        "camera_angle": "face-on", "club": "iron", "hand": "right",
    }
    # Without meta the key is absent — legacy consumers see no churn.
    plain = write_metrics_json(
        Path(tempfile.mkdtemp()) / "metrics.json", fake_video(), [swing],
        session_stats([swing["metrics"]]), [], Config(),
    )
    assert "meta" not in json.loads(plain.read_text())


def test_report_header_shows_club_chip():
    cfg = branded_cfg()
    swings = [fake_swing(1)]
    stats = session_stats([s["metrics"] for s in swings])
    out = write_report_html(
        Path(tempfile.mkdtemp()) / "report.html", fake_video(), swings, stats,
        [], "right", cfg, club="fairway-wood",
    )
    html = out.read_text()
    assert "Fairway wood" in html
    # No club -> no chip, no empty row.
    out2 = write_report_html(
        Path(tempfile.mkdtemp()) / "report.html", fake_video(), swings, stats,
        [], "right", cfg,
    )
    assert ">Club<" not in out2.read_text()


def _upload_with_club(client, club):
    resp = client.post(
        "/upload",
        files={"video": ("swing.mov", b"fake video bytes", "video/quicktime")},
        data={"club": club},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    job_id = resp.headers["location"].rsplit("/", 1)[-1]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if client.get(f"/api/session/{job_id}").json()["status"] in (
            "done", "failed",
        ):
            return job_id
        time.sleep(0.02)
    raise TimeoutError("job never finished")


def test_progress_club_filter(tmp_path, monkeypatch):
    payloads = [
        payload_for([{"tempo_ratio": 2.2}]),
        payload_for([{"tempo_ratio": 2.7}]),
        payload_for([{"tempo_ratio": 3.4}]),
        payload_for([{"tempo_ratio": 3.6}]),
    ]
    monkeypatch.setattr(jobs_module, "analyze_video", make_fake_analyze(payloads))
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["free_per_month"] = 0  # unlimited for this test
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    signup(client)
    _upload_with_club(client, "iron")
    _upload_with_club(client, "iron")
    _upload_with_club(client, "driver")
    _upload_with_club(client, "driver")

    html = client.get("/progress").text
    assert "All clubs" in html and "Iron" in html and "Driver" in html

    iron_html = client.get("/progress?club=iron").text
    assert "Iron only" in iron_html          # the active filter is labeled
    assert "2.70:1" in iron_html             # latest iron session
    assert "3.60:1" not in iron_html         # driver sessions filtered out

    # Unknown club filters fall back to everything (no error, no lie).
    assert client.get("/progress?club=mashie").status_code == 200


def test_progress_hides_filter_with_single_club(tmp_path, monkeypatch):
    payloads = [
        payload_for([{"tempo_ratio": 2.2}]),
        payload_for([{"tempo_ratio": 2.7}]),
    ]
    monkeypatch.setattr(jobs_module, "analyze_video", make_fake_analyze(payloads))
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["free_per_month"] = 0  # unlimited for this test
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    signup(client)
    _upload_with_club(client, "iron")
    _upload_with_club(client, "iron")
    html = client.get("/progress").text
    assert "All clubs" not in html  # one club: nothing to filter
