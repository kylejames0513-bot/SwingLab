"""Progress trends: per-metric series built from real session metrics.json
files (legacy payloads and NaN/null included), honest latest/best/delta,
trend-sentence gating, the trend_chart SVG contract, the /progress page
(auth in both modes, rendered data, empty states), and the conversion
moments that reuse the same sentence."""

from __future__ import annotations

import json
import time
import types
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab import trends
from swinglab.config import Config
from swinglab.diagrams import trend_chart
from swinglab.ffmpeg import VideoInfo
from swinglab.pipeline import SessionResult
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app

BRAND = {"primary_color": "#1a5c38", "accent_color": "#e8720c"}


def payload_for(swings: list[dict]) -> dict:
    return {"swings": [{"metrics": m} for m in swings], "session_stats": {}}


def stub_job(
    tmp_path: Path,
    n: int,
    swings: list[dict] | None = None,
    status: str = "done",
    report_rel: str | None = "out/report.html",
    raw: str | None = None,
):
    """A duck-typed job row + on-disk metrics.json, no web stack needed."""
    session_dir = tmp_path / f"job{n}"
    if report_rel and (swings is not None or raw is not None):
        (session_dir / "out").mkdir(parents=True, exist_ok=True)
        text = raw if raw is not None else json.dumps(payload_for(swings))
        (session_dir / "out" / "metrics.json").write_text(text)
    return types.SimpleNamespace(
        id=f"job{n}",
        session_dir=session_dir,
        status=status,
        created_at=1000.0 + n,
        report_rel=report_rel,
    )


# -- series building ---------------------------------------------------------

def test_series_one_point_per_session_in_upload_order(tmp_path, cfg):
    jobs = [
        stub_job(tmp_path, 2, [{"tempo_ratio": 2.6}, {"tempo_ratio": 2.8}]),
        stub_job(tmp_path, 1, [{"tempo_ratio": 2.2}]),  # older, listed later
    ]
    built = trends.build_trends(jobs, cfg)
    assert built.session_count == 2
    tempo = built.metrics["tempo_ratio"]
    assert [v for _, v in tempo.points] == [2.2, 2.7]  # session means, oldest first
    assert tempo.latest == 2.7
    assert tempo.delta == 0.5


def test_legacy_sessions_contribute_only_the_fields_they_have(tmp_path, cfg):
    legacy = {  # pre-program-depth metrics.json: none of the newer fields
        "backswing_s": 0.9, "downswing_s": 0.3, "tempo_ratio": 3.0,
        "head_sway_backswing_sw": 0.2, "head_sway_downswing_sw": -0.1,
        "hip_slide_backswing_sw": 0.1, "hip_slide_downswing_sw": -0.05,
    }
    modern = {**legacy, "head_dip_sw": 0.1, "lead_arm_angle_deg": 165.0}
    built = trends.build_trends(
        [stub_job(tmp_path, 1, [legacy]), stub_job(tmp_path, 2, [modern])], cfg
    )
    assert len(built.metrics["tempo_ratio"].points) == 2
    assert len(built.metrics["head_dip_sw"].points) == 1  # modern session only
    assert built.metrics["head_dip_sw"].delta is None  # one point, no delta
    assert "shoulder_tilt_impact_deg" not in built.metrics  # nobody measured it


def test_null_and_nan_values_never_reach_the_series(tmp_path, cfg):
    jobs = [
        stub_job(tmp_path, 1, [
            {"tempo_ratio": None, "head_dip_sw": 0.1},   # NaN written as null
            {"tempo_ratio": 2.4, "head_dip_sw": None},
        ]),
        # Raw NaN tokens (json.loads accepts them) must be skipped too.
        stub_job(tmp_path, 2, raw=(
            '{"swings": [{"metrics": {"tempo_ratio": NaN, "head_dip_sw": 0.3}}],'
            ' "session_stats": {}}'
        )),
    ]
    built = trends.build_trends(jobs, cfg)
    assert [v for _, v in built.metrics["tempo_ratio"].points] == [2.4]
    assert [v for _, v in built.metrics["head_dip_sw"].points] == [0.1, 0.3]


def test_unfinished_unreadable_and_empty_sessions_are_skipped(tmp_path, cfg):
    jobs = [
        stub_job(tmp_path, 1, [{"tempo_ratio": 2.5}]),
        stub_job(tmp_path, 2, [{"tempo_ratio": 9.9}], status="queued"),
        stub_job(tmp_path, 3, swings=None, report_rel="out/report.html"),  # no file
        stub_job(tmp_path, 4, raw="not json at all"),
        stub_job(tmp_path, 5, raw="[]"),        # payload not a dict
        stub_job(tmp_path, 6, raw="{}"),        # no swings -> nothing measurable
        stub_job(tmp_path, 7, [{"tempo_ratio": 2.6}], report_rel=None),  # never done
    ]
    built = trends.build_trends(jobs, cfg)
    assert built.session_count == 1
    assert [v for _, v in built.metrics["tempo_ratio"].points] == [2.5]


def test_latest_best_delta_respect_metric_direction(tmp_path, cfg):
    jobs = [
        stub_job(tmp_path, 1, [{
            "tempo_ratio": 2.2, "head_sway_backswing_sw": 0.5, "backswing_s": 0.8,
        }]),
        stub_job(tmp_path, 2, [{
            "tempo_ratio": 2.9, "head_sway_backswing_sw": 0.2, "backswing_s": 0.9,
        }]),
        stub_job(tmp_path, 3, [{
            "tempo_ratio": 2.6, "head_sway_backswing_sw": 0.3, "backswing_s": 0.7,
        }]),
    ]
    built = trends.build_trends(jobs, cfg)
    tempo = built.metrics["tempo_ratio"]
    assert (tempo.latest, tempo.best, tempo.delta) == (2.6, 2.9, 0.4)  # higher = better
    sway = built.metrics["head_sway_backswing_sw"]
    assert (sway.latest, sway.best, sway.delta) == (0.3, 0.2, -0.2)  # lower = better
    duration = built.metrics["backswing_s"]
    assert duration.best is None  # a duration has no "best" — never invent one
    assert duration.benchmark is None


def test_flag_fire_counts_across_sessions(tmp_path, cfg):
    quick = {"tempo_ratio": 2.0}                  # fires tempo (< 2.4)
    swayed = {"tempo_ratio": 2.1, "head_sway_backswing_sw": 0.5}  # tempo + sway
    clean = {"tempo_ratio": 3.0, "head_sway_backswing_sw": 0.1}
    built = trends.build_trends(
        [stub_job(tmp_path, 1, [quick]), stub_job(tmp_path, 2, [swayed]),
         stub_job(tmp_path, 3, [clean])],
        cfg,
    )
    assert built.flag_counts == {"tempo": 2, "sway": 1}


# -- the trend sentence ------------------------------------------------------

def test_trend_sentence_needs_two_sessions_of_the_same_metric(tmp_path, cfg):
    assert trends.trend_sentence(trends.build_trends([], cfg)) is None
    one = trends.build_trends([stub_job(tmp_path, 1, [{"tempo_ratio": 2.2}])], cfg)
    assert trends.trend_sentence(one) is None
    # Two sessions that never measured the same benchmarked metric: still None
    # — a sentence must never be stitched from different metrics.
    disjoint = trends.build_trends(
        [stub_job(tmp_path, 2, [{"tempo_ratio": 2.2}]),
         stub_job(tmp_path, 3, [{"head_dip_sw": 0.1}])],
        cfg,
    )
    assert trends.trend_sentence(disjoint) is None


def test_trend_sentence_uses_real_numbers_only(tmp_path, cfg):
    built = trends.build_trends(
        [stub_job(tmp_path, 1, [{"tempo_ratio": 2.2}]),
         stub_job(tmp_path, 2, [{"tempo_ratio": 2.8}])],
        cfg,
    )
    sentence = trends.trend_sentence(built)
    assert sentence == "Tempo has moved 2.20:1 \N{RIGHTWARDS ARROW} 2.80:1 across 2 sessions"

    held = trends.build_trends(
        [stub_job(tmp_path, 3, [{"head_sway_backswing_sw": 0.30}]),
         stub_job(tmp_path, 4, [{"head_sway_backswing_sw": 0.30}])],
        cfg,
    )
    assert trends.trend_sentence(held) == (
        "Head sway (backswing) has held at 0.30 SW across 2 sessions"
    )


# -- trend_chart SVG contract ------------------------------------------------

def test_trend_chart_is_wellformed_branded_and_selfcontained():
    svg = trend_chart([2.2, 2.6, 2.8], 2.4, BRAND, worse="lower")
    root = ET.fromstring(svg)  # raises on malformed XML
    assert root.tag.endswith("svg")
    assert root.get("viewBox") == "0 0 320 120"
    assert root.get("role") == "img" and root.get("aria-label")
    assert BRAND["primary_color"] in svg and BRAND["accent_color"] in svg
    for needle in ("xlink:href", "url(", "<image", "@import", "<script", "<defs"):
        assert needle not in svg
    assert svg.count("<circle") == 3           # one dot per session
    assert "stroke-dasharray" in svg           # the benchmark line
    assert "<rect" in svg                      # the benchmark band
    assert "<polyline" in svg


def test_trend_chart_marks_sessions_on_the_bad_side():
    svg = trend_chart([0.2, 0.5], 0.35, BRAND, worse="higher")
    assert f'fill="{BRAND["accent_color"]}"' in svg   # 0.5 breaches
    assert f'fill="{BRAND["primary_color"]}"' in svg  # 0.2 doesn't


def test_trend_chart_degrades_honestly():
    assert trend_chart([], 1.0, BRAND) == ""
    single = trend_chart([2.5], None, BRAND)
    ET.fromstring(single)
    assert "<polyline" not in single and single.count("<circle") == 1
    assert "<rect" not in single               # no benchmark, no band
    flat = trend_chart([1.0, 1.0], None, BRAND)  # zero range must not divide by 0
    ET.fromstring(flat)


# -- the /progress page ------------------------------------------------------

def make_fake_analyze(payloads: list[dict]):
    """Successive uploads write successive metrics.json payloads."""
    state = {"i": 0}

    def fake(video_path, out_dir=None, hand="right", manual_strikes=None,
             cfg=None, keep_work=False, fast=False, log=print, progress=None):
        payload = payloads[min(state["i"], len(payloads) - 1)]
        state["i"] += 1
        session_dir = Path(out_dir) / Path(video_path).stem
        session_dir.mkdir(parents=True)
        report = session_dir / "report.html"
        report.write_text("<html>fake report</html>")
        (session_dir / "metrics.json").write_text(json.dumps(payload))
        info = VideoInfo(Path(video_path), 20.0, 854, 480, 30.0, 0, None, True)
        return SessionResult(
            session_dir=session_dir, report_path=report,
            metrics_path=session_dir / "metrics.json", video=info,
            swings=payload.get("swings") or [{}], stats={},
        )

    return fake


SESSION_PAYLOADS = [
    payload_for([{"tempo_ratio": 2.2, "head_sway_backswing_sw": 0.42}]),
    payload_for([{"tempo_ratio": 2.7, "head_sway_backswing_sw": 0.30}]),
]


@pytest.fixture
def accounts_app(tmp_path, monkeypatch):
    monkeypatch.setattr(
        jobs_module, "analyze_video", make_fake_analyze(SESSION_PAYLOADS)
    )
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["free_per_month"] = 2
    return create_app(cfg, sessions_dir=tmp_path / "sessions")


def signup(client, email="kyle@example.com"):
    resp = client.post(
        "/signup", data={"email": email, "password": "longenough"},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def upload_and_wait(client):
    resp = client.post(
        "/upload",
        files={"video": ("swing.mov", b"fake video bytes", "video/quicktime")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    job_id = resp.headers["location"].rsplit("/", 1)[-1]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if client.get(f"/api/session/{job_id}").json()["status"] in ("done", "failed"):
            return job_id
        time.sleep(0.02)
    raise TimeoutError("job never finished")


def test_progress_requires_login(accounts_app):
    resp = TestClient(accounts_app).get("/progress", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_progress_404_without_accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(
        jobs_module, "analyze_video", make_fake_analyze(SESSION_PAYLOADS)
    )
    open_app = create_app(Config(), sessions_dir=tmp_path / "s")  # open mode
    assert TestClient(open_app).get("/progress").status_code == 404


def test_progress_empty_states_are_honest(accounts_app):
    client = TestClient(accounts_app)
    signup(client)
    html = client.get("/progress").text
    assert "Nothing to chart yet" in html and "<svg" not in html

    upload_and_wait(client)
    html = client.get("/progress").text
    assert "Baseline on the books" in html   # one session: no fake trend lines
    assert "Re-film this week" in html and "<svg" not in html


def test_progress_renders_cards_flags_and_cta(accounts_app):
    client = TestClient(accounts_app)
    signup(client)
    upload_and_wait(client)
    upload_and_wait(client)
    html = client.get("/progress").text
    assert "Tempo" in html and "Head sway (backswing)" in html
    assert '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 120"' in html
    assert "2.70:1" in html                       # latest, mono stat
    assert "+0.50:1" in html                      # delta vs first
    assert "flagged below 2.4:1" in html          # the benchmark line
    assert "has moved 2.20:1" in html             # the trend sentence
    assert "What keeps getting flagged" in html   # flags-frequency strip
    assert "Re-film this week" in html            # the CTA back to upload
    assert 'href="/progress"' in html             # nav link present when logged in

    sessions_html = client.get("/sessions").text
    assert 'href="/progress"' in sessions_html    # linked from history too


# -- conversion moments ------------------------------------------------------

def test_blocked_upload_page_shows_own_trend_and_pro_cta(accounts_app, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_x")
    client = TestClient(accounts_app)
    signup(client)
    upload_and_wait(client)
    upload_and_wait(client)  # free_per_month = 2 -> quota exhausted
    html = client.get("/").text
    assert "You've used this month's analyses" in html
    assert "has moved 2.20:1" in html              # their own numbers
    assert "Keep the film rolling" in html         # the Pro CTA


def test_blocked_upload_page_stays_generic_without_trend_data(tmp_path, monkeypatch):
    from tests.test_web import fake_analyze_ok  # writes an empty metrics.json

    monkeypatch.setattr(jobs_module, "analyze_video", fake_analyze_ok)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_x")
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["free_per_month"] = 1
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "sessions"))
    signup(client)
    upload_and_wait(client)
    html = client.get("/").text
    assert "You've used this month's analyses" in html
    assert "Upgrade to Pro" in html                # the generic line
    assert "Keep the film rolling" not in html     # no data, no personal pitch


def test_pricing_personal_line_only_with_two_sessions(accounts_app):
    client = TestClient(accounts_app)
    assert "has moved" not in client.get("/pricing").text  # logged out: nothing

    signup(client)
    upload_and_wait(client)
    assert "has moved" not in client.get("/pricing").text  # one session: nothing

    upload_and_wait(client)
    html = client.get("/pricing").text
    assert "Tempo has moved 2.20:1" in html                # two sessions: theirs
