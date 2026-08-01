"""Premium Today dashboard state, identity, and privacy contracts."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web import app as app_module
from swinglab.web.app import create_app
from swinglab.web.jobs import DONE, FAILED, PROCESSING, QUEUED


def make_app(tmp_path, monkeypatch, *, proof_cycle: bool = False):
    for name in (
        "RESEND_API_KEY",
        "SWINGLAB_SMTP_URL",
        "SWINGLAB_MAIL_FROM",
        "SHOPIFY_STORE_DOMAIN",
        "SHOPIFY_WEBHOOK_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.web["passwordless_login"] = False
    cfg.billing["free_per_month"] = 2
    cfg.proof_cycle["enabled"] = proof_cycle
    cfg.proof_cycle["practice_evidence_enabled"] = False
    return create_app(cfg, sessions_dir=tmp_path / "sessions")


def login_golfer(
    app,
    *,
    email: str,
    display_name: str = "Avery",
    complete: bool = True,
    pro: bool = False,
):
    users = app.state.users
    user = users.create(email, "longenough")
    if complete:
        users.upsert_golfer_profile(
            user.id,
            display_name=display_name,
            experience_mode="improve",
            handicap_range="20_to_29",
            primary_goal="consistency",
            practice_minutes=20,
            sessions_per_week=2,
            handedness="right",
            camera_angle="face-on",
            preferred_club="driver",
        )
    if pro:
        users.set_plan(user.id, "pro", "active")
    client = TestClient(app)
    response = client.post(
        "/login",
        data={"email": email, "password": "longenough"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client, user


def make_job(app, user, state: str):
    manager = app.state.jobs
    job = manager.create_session(
        source_name="private-upload-name.mov",
        hand="right",
        angle="face-on",
        club="driver",
        level="improver",
        client_ip="198.51.100.77",
        user_id=user.id,
    )
    job.log = ["PRIVATE WORKER TRACE"]
    job.error = "PRIVATE ANALYZER ERROR"
    if state == "queued":
        job.status = QUEUED
    elif state == "processing":
        job.status = PROCESSING
        job.swings_done = 1
        job.swings_total = 3
    elif state == "failed":
        job.status = FAILED
    else:
        job.status = DONE
        result_dir = job.session_dir / "out"
        result_dir.mkdir(parents=True, exist_ok=True)
        report_path = result_dir / "private-report-name.html"
        report_path.write_text("<html><body>saved report</body></html>", encoding="utf-8")
        job.report_rel = "out/private-report-name.html"
        if state == "coaching_ready":
            payload = {
                "swings": [{"metrics": {"tempo_ratio": 2.0}}],
                "session_stats": {},
            }
            (result_dir / "metrics.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
        elif state == "refilm":
            payload = {
                "session_notes": [
                    "Tracking was unstable for this swing — numbers may be off; "
                    "film with a clear view."
                ],
                "swings": [{"metrics": {"tempo_ratio": 2.0}}],
                "session_stats": {},
            }
            (result_dir / "metrics.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
        elif state != "legacy":
            raise AssertionError(f"unsupported done state: {state}")
    manager._save(job)
    return job


def test_free_and_pro_share_one_page_identity_with_truthful_member_tiles(
    tmp_path, monkeypatch
):
    app = make_app(tmp_path, monkeypatch)
    free_client, _ = login_golfer(
        app, email="free-today@example.com", display_name="Jordan"
    )
    pro_client, _ = login_golfer(
        app, email="pro-today@example.com", display_name="Kyle", pro=True
    )

    free_page = free_client.get("/today")
    pro_page = pro_client.get("/today")

    for page in (free_page, pro_page):
        assert page.status_code == 200
        assert page.headers["cache-control"] == "private, no-store"
        assert page.text.count('<h1 id="today-title">Today</h1>') == 1
        assert "Preferred club" in page.text
        assert "Driver" in page.text
        assert "20 minutes" in page.text

    assert "Personal coaching dashboard" in free_page.text
    assert "CaddieInsight Pro member" not in free_page.text
    assert "Free plan" in free_page.text
    assert "2 analyses left this month" in free_page.text

    assert "CaddieInsight Pro member" in pro_page.text
    assert "Welcome back, Kyle" in pro_page.text
    assert "Pro member" in pro_page.text
    assert "Unlimited analyses" in pro_page.text


@pytest.mark.parametrize(
    ("state", "complete", "expected_heading"),
    (
        ("setup", False, "Set up your first coaching loop"),
        ("empty", True, "Film a baseline"),
        ("queued", True, "Your swing is queued"),
        ("processing", True, "Analysis in progress"),
        ("failed", True, "Let’s get you back on track"),
        ("refilm", True, "Re-film before practicing a change"),
        ("coaching_ready", True, "Coaching ready"),
        ("legacy", True, "Review your saved session"),
    ),
)
def test_today_has_one_dominant_move_for_each_honest_state(
    tmp_path, monkeypatch, state, complete, expected_heading
):
    app = make_app(tmp_path, monkeypatch)
    client, user = login_golfer(
        app,
        email=f"{state}@example.com",
        complete=complete,
    )
    if state not in ("setup", "empty"):
        make_job(app, user, state)

    page = client.get("/today")

    assert page.status_code == 200
    assert f'data-today-state="{state}"' in page.text
    assert expected_heading in page.text
    assert page.text.count("data-primary-next-move") == 1
    assert '<h2 id="practice-title">Practice</h2>' in page.text
    assert '<h2 id="recent-title">Recent sessions</h2>' in page.text


def test_today_minimizes_recent_session_data_and_never_crosses_owners(
    tmp_path, monkeypatch
):
    app = make_app(tmp_path, monkeypatch)
    client, owner = login_golfer(app, email="owner@example.com")
    _, stranger = login_golfer(app, email="stranger@example.com")

    preserved = make_job(app, owner, "legacy")
    failed = make_job(app, owner, "failed")
    foreign = make_job(app, stranger, "coaching_ready")

    page = client.get("/today")

    assert page.status_code == 200
    assert preserved.id in page.text
    assert failed.id in page.text
    assert foreign.id not in page.text
    for private_value in (
        "private-upload-name.mov",
        "private-report-name.html",
        "PRIVATE WORKER TRACE",
        "PRIVATE ANALYZER ERROR",
        "198.51.100.77",
    ):
        assert private_value not in page.text


@pytest.mark.parametrize("report_rel", (None, "out/missing-report.html"))
def test_done_session_without_a_readable_report_gets_an_honest_fresh_start_fallback(
    tmp_path, monkeypatch, report_rel
):
    app = make_app(tmp_path, monkeypatch)
    client, user = login_golfer(app, email="restored-today@example.com")
    job = app.state.jobs.create_session(
        source_name="restored.mov",
        club="driver",
        user_id=user.id,
    )
    job.status = DONE
    job.report_rel = report_rel
    app.state.jobs._save(job)

    html = client.get("/today").text

    assert 'data-today-state="legacy"' in html
    assert "Start with a fresh coaching card" in html
    assert "does not have a current coaching card or a preserved report" in html
    assert "Open saved result" not in html


def test_today_does_not_advertise_a_report_blocked_by_the_report_route(
    tmp_path, monkeypatch
):
    app = make_app(tmp_path, monkeypatch)
    client, user = login_golfer(app, email="blocked-report-today@example.com")
    job = app.state.jobs.create_session(
        source_name="restored.mov",
        club="driver",
        user_id=user.id,
    )
    job.status = DONE
    result_dir = job.session_dir / "out"
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "report.html").write_text(
        "<html><body>legacy report</body></html>", encoding="utf-8"
    )
    (result_dir / "metrics.json").write_text("{not-json", encoding="utf-8")
    job.report_rel = "out/report.html"
    app.state.jobs._save(job)

    html = client.get("/today").text
    report = client.get(f"/session/{job.id}/report", follow_redirects=False)

    assert report.status_code == 303
    assert report.headers["location"] == f"/session/{job.id}"
    assert "Start with a fresh coaching card" in html
    assert "Open saved result" not in html
    assert "Saved report" not in html


@pytest.mark.parametrize(
    ("outcome", "expected_state", "expected_copy"),
    (
        (
            "capture_only",
            "refilm",
            "This capture is available for review, but it could not support a "
            "trustworthy coaching card.",
        ),
        (
            "coaching_ready",
            "legacy",
            "The report is preserved, but its structured coaching card is "
            "unavailable here.",
        ),
    ),
)
def test_today_classifies_current_reports_with_unreadable_metrics_honestly(
    tmp_path, monkeypatch, outcome, expected_state, expected_copy
):
    app = make_app(tmp_path, monkeypatch)
    client, user = login_golfer(
        app, email=f"{outcome}-unreadable-today@example.com"
    )
    job = app.state.jobs.create_session(
        source_name="current.mov",
        club="driver",
        user_id=user.id,
    )
    job.status = DONE
    result_dir = job.session_dir / "out"
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "report.html").write_text(
        '<html><head><meta name="caddieinsight-report-format" '
        'content="caddie-brief-v1">'
        '<meta name="caddieinsight-report-outcome" '
        f'content="{outcome}"></head><body>current result</body></html>',
        encoding="utf-8",
    )
    (result_dir / "metrics.json").write_text("{not-json", encoding="utf-8")
    job.report_rel = "out/report.html"
    app.state.jobs._save(job)

    html = client.get("/today").text
    report = client.get(f"/session/{job.id}/report", follow_redirects=False)

    assert report.status_code == 307
    assert f'data-today-state="{expected_state}"' in html
    assert expected_copy in " ".join(html.split())
    assert "predates the current" not in html


def test_pro_member_name_is_escaped_inside_the_personalized_welcome(
    tmp_path, monkeypatch
):
    app = make_app(tmp_path, monkeypatch)
    payload = '\"><img src=x onerror=alert(1)>'
    client, _ = login_golfer(
        app,
        email="escape-today@example.com",
        display_name=payload,
        pro=True,
    )

    html = client.get("/today").text

    assert payload not in html
    assert "<img src=x onerror=alert(1)>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "CaddieInsight Pro member" in html


def test_today_renders_only_the_proof_view_returned_by_verified_evidence(
    tmp_path, monkeypatch
):
    trusted_artifact = object()
    monkeypatch.setattr(
        app_module,
        "verified_proof_cycle_artifact",
        lambda *args, **kwargs: trusted_artifact,
    )
    monkeypatch.setattr(
        app_module,
        "proof_cycle_view",
        lambda artifact: (
            SimpleNamespace(
                tone="positive",
                target_name="Tempo",
                heading="Early signal — keep testing",
                summary="One matched follow-up moved in the right direction.",
                detail="This is a measurement signal, not a causal claim.",
                next_step="Make one more matching re-film.",
            )
            if artifact is trusted_artifact
            else None
        ),
    )
    app = make_app(tmp_path, monkeypatch, proof_cycle=True)
    client, user = login_golfer(app, email="proof-today@example.com")
    make_job(app, user, "coaching_ready")

    html = client.get("/today").text

    assert 'data-proof-state="positive"' in html
    assert "Early signal — keep testing" in html
    assert "One matched follow-up moved in the right direction." in html
    assert "This is a measurement signal, not a causal claim." in html
    assert "Make one more matching re-film." in html


def test_coaching_ready_preserves_practice_and_history_routes(
    tmp_path, monkeypatch
):
    app = make_app(tmp_path, monkeypatch)
    client, user = login_golfer(app, email="routes-today@example.com")
    job = make_job(app, user, "coaching_ready")

    html = client.get("/today").text

    assert 'action="/practice/checkins"' in html
    assert f'href="/session/{job.id}"' in html
    assert 'href="/#upload-form"' in html
    assert 'href="/sessions"' in html
