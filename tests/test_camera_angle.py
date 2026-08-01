"""Camera-angle honesty: down-the-line sessions keep timing and read NaN
for every face-on-defined metric; the apparent-angle heuristic warns (both
directions, conservatively) when the footage disagrees with the chosen
setting; the upload form's angle flows through to the pipeline; and the
target-direction fallback now carries its promised low-confidence note."""

from __future__ import annotations

import math
import time

import numpy as np
import pytest

from swinglab.coaching import (
    DTL_SESSION_NOTE,
    angle_mismatch_note,
    swing_notes,
)
from swinglab.config import Config
from swinglab.events import SwingEvents
from swinglab.metrics import (
    ANGLE_DTL,
    ANGLE_FACE_ON,
    FACE_ON_ONLY_FIELDS,
    apparent_camera_angle,
    compute_metrics,
)
from tests.conftest import make_landmarks


def events_for(tracked, shoulder_width=100.0, fps=30.0):
    return SwingEvents(
        address_idx=0,
        takeaway_idx=10,
        top_idx=40,
        impact_idx=54,
        takeaway_s=10 / fps,
        top_s=40 / fps,
        impact_s=54 / fps,
        finish_s=54 / fps + 0.55,
        shoulder_width_px=shoulder_width,
        hand_baseline=np.array([500.0, 600.0]),
    )


# ---------------------------------------------------------------- DTL metrics


def test_dtl_nans_every_face_on_metric_and_keeps_timing():
    tracked = [make_landmarks() for _ in range(75)]
    tracked[70] = make_landmarks(shoulder_span=-40.0)  # confident finish
    ev = events_for(tracked)
    m = compute_metrics(1, tracked, ev, 70, "right", angle=ANGLE_DTL)
    for field_name in FACE_ON_ONLY_FIELDS:
        assert math.isnan(getattr(m, field_name)), field_name
    # Timing is camera-angle-agnostic and must survive.
    assert not math.isnan(m.tempo_ratio)
    assert m.backswing_s == pytest.approx(1.0, abs=0.01)
    assert m.downswing_s == pytest.approx(14 / 30, abs=0.01)


def test_face_on_default_measures_everything():
    tracked = [make_landmarks() for _ in range(75)]
    tracked[70] = make_landmarks(shoulder_span=-40.0)
    ev = events_for(tracked)
    m = compute_metrics(1, tracked, ev, 70, "right", angle=ANGLE_FACE_ON)
    assert not math.isnan(m.head_sway_backswing_sw)
    assert not math.isnan(m.finish_balance_sw)


def test_dtl_metrics_fire_no_lateral_flags():
    from swinglab.coaching import session_flags
    from swinglab.metrics import session_stats

    tracked = [make_landmarks() for _ in range(75)]
    ev = events_for(tracked)
    m = compute_metrics(1, tracked, ev, 70, "right", angle=ANGLE_DTL)
    flags = session_flags([m], session_stats([m]), Config())
    assert "sway" not in flags and "balance" not in flags


# ------------------------------------------------- apparent-angle heuristic


def test_apparent_angle_wide_shoulders_reads_face_on():
    lm = make_landmarks(shoulder_span=200.0)  # ratio ~0.25 of body height
    assert apparent_camera_angle(lm) == ANGLE_FACE_ON


def test_apparent_angle_stacked_shoulders_reads_dtl():
    lm = make_landmarks(shoulder_span=60.0)  # ratio ~0.075
    assert apparent_camera_angle(lm) == ANGLE_DTL


def test_apparent_angle_uncertain_pose_stays_silent():
    # The default synthetic skeleton sits in the dead zone — conservative
    # thresholds mean no opinion, no warning, no false alarm.
    assert apparent_camera_angle(make_landmarks()) is None
    assert apparent_camera_angle(None) is None


def test_angle_mismatch_note_both_directions():
    note = angle_mismatch_note(ANGLE_FACE_ON, ANGLE_DTL)
    assert "down the line" in note and "face-on" in note
    assert "may not mean what they say" in note
    reverse = angle_mismatch_note(ANGLE_DTL, ANGLE_FACE_ON)
    assert "face-on" in reverse and "down the line" in reverse
    assert "Re-film" in reverse


def test_dtl_session_note_is_honest():
    assert "tempo and rhythm only" in DTL_SESSION_NOTE
    assert "face-on" in DTL_SESSION_NOTE


# --------------------------------- target-direction fallback low confidence


def test_target_fallback_flags_low_confidence_in_notes():
    # Identical frames everywhere: no shoulder rotation, no hand travel —
    # the inference hits its arbitrary fallback and must say so.
    tracked = [make_landmarks() for _ in range(75)]
    ev = events_for(tracked)
    m = compute_metrics(1, tracked, ev, 70, "right")
    assert m.target_confident is False
    notes = swing_notes(m, Config())
    assert any("Low confidence" in n and "target" in n for n in notes)


def test_confident_target_gets_no_low_confidence_note():
    tracked = [make_landmarks() for _ in range(75)]
    tracked[70] = make_landmarks(shoulder_span=-40.0)
    ev = events_for(tracked)
    m = compute_metrics(1, tracked, ev, 70, "right")
    assert m.target_confident is True
    assert not any("Low confidence" in n for n in swing_notes(m, Config()))


def test_dtl_swings_skip_the_target_confidence_note():
    # Down the line every lateral number is NaN — the sign caveat would
    # caveat nothing, so it stays out.
    tracked = [make_landmarks() for _ in range(75)]
    ev = events_for(tracked)
    m = compute_metrics(1, tracked, ev, 70, "right", angle=ANGLE_DTL)
    assert m.target_confident is False
    assert not any("Low confidence" in n for n in swing_notes(m, Config()))


# ------------------------------------------------------------ DTL rendering


def test_dtl_report_shows_no_face_on_reference_numbers(tmp_path):
    """A DTL report's Impact & finish table is all unmeasured ("—"), so the
    reference row would be the only numbers in it — it must be omitted.
    Face-on reports keep it."""
    from swinglab.metrics import session_stats
    from swinglab.report import write_report_html
    from tests.test_metrics_depth import make_metrics
    from tests.test_report import fake_video

    nan = float("nan")
    dtl_overrides = {f: nan for f in FACE_ON_ONLY_FIELDS}

    def render(name, angle, **overrides):
        m = make_metrics(1, **overrides)
        swings = [{
            "metrics": m, "notes": swing_notes(m, Config()),
            "strip": None, "overlay": None, "slowmo": None, "replay": None,
        }]
        out = write_report_html(
            tmp_path / name, fake_video(), swings,
            session_stats([m]), [], "right", Config(), angle=angle,
        )
        return out.read_text()

    dtl_html = render("dtl.html", ANGLE_DTL, **dtl_overrides)
    assert '<tr class="bench-row">' not in dtl_html
    face_html = render("face.html", ANGLE_FACE_ON)
    assert '<tr class="bench-row">' in face_html


# ------------------------------------------------------------ web plumbing


def test_upload_angle_flows_through_to_pipeline(tmp_path, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from swinglab.web import jobs as jobs_module
    from swinglab.web.app import create_app
    from tests.test_web import fake_analyze_ok

    seen = {}

    def spy(video_path, angle="face-on", club=None, **kwargs):
        seen["angle"] = angle
        return fake_analyze_ok(video_path, angle=angle, club=club, **kwargs)

    monkeypatch.setattr(jobs_module, "analyze_video", spy)
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s"))
    resp = client.post(
        "/upload",
        files={"video": ("swing.mov", b"fake", "video/quicktime")},
        data={"angle": "dtl", "club": "iron"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    job_id = resp.headers["location"].rsplit("/", 1)[-1]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        data = client.get(f"/api/session/{job_id}").json()
        if data["status"] in ("done", "failed"):
            break
        time.sleep(0.02)
    assert seen["angle"] == "dtl"
    assert data["angle"] == "dtl"  # persisted on the job + API

    # Restart: the angle survives the database round trip.
    fresh = TestClient(create_app(Config(), sessions_dir=tmp_path / "s"))
    assert fresh.get(f"/api/session/{job_id}").json()["angle"] == "dtl"


def test_upload_rejects_unknown_angle(tmp_path, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from swinglab.web import jobs as jobs_module
    from swinglab.web.app import create_app
    from tests.test_web import fake_analyze_ok

    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s"))
    resp = client.post(
        "/upload",
        files={"video": ("swing.mov", b"fake", "video/quicktime")},
        data={"angle": "overhead-drone", "club": "iron"},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_upload_form_shows_angle_choice_honestly(tmp_path, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from swinglab.web import jobs as jobs_module
    from swinglab.web.app import create_app
    from tests.test_web import fake_analyze_ok

    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    html = TestClient(create_app(Config(), sessions_dir=tmp_path / "s")).get("/").text
    assert 'name="angle"' in html
    assert 'value="face-on" checked' in html   # face-on is the default
    assert "tempo &amp; rhythm only" in html   # DTL option tells the truth
    assert "face-on or down the line" not in html  # the old false promise
