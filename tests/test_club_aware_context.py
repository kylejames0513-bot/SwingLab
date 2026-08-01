"""Club-aware aggregation keeps every comparison inside one capture context."""

from __future__ import annotations

import json
import types

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.web import digest
from swinglab.web import jobs as jobs_module
from swinglab.web.app import create_app
from swinglab.web.jobs import DONE, JobManager
from tests.test_trends import make_fake_analyze, payload_for, signup
from tests.test_web import wait_for


def _save_finished(
    manager: JobManager,
    *,
    user_id: str,
    club: str | None,
    hand: str,
    angle: str,
    tempo: float,
):
    job = manager.create_session(
        source_name="swing.mov",
        user_id=user_id,
        club=club,
        hand=hand,
        angle=angle,
    )
    out = job.session_dir / "out"
    out.mkdir()
    (out / "metrics.json").write_text(
        json.dumps(payload_for([{"tempo_ratio": tempo}])),
        encoding="utf-8",
    )
    (out / "report.html").write_text("<html>report</html>", encoding="utf-8")
    job.status = DONE
    job.report_rel = "out/report.html"
    manager._save(job)
    return job


def _priority_payload(*, meta_club: str | None = None) -> dict:
    payload = payload_for(
        [
            {
                "head_sway_backswing_sw": 0.50,
                "finish_balance_sw": 0.50,
            }
            for _ in range(3)
        ]
    )
    if meta_club is not None:
        payload["meta"] = {"club": meta_club, "angle": "face-on"}
    return payload


def _write_job_payload(job, payload: dict, *, rule_version: int | None) -> None:
    output = job.session_dir / "out"
    (output / "metrics.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    marker = (
        ""
        if rule_version is None
        else (
            '<meta name="caddieinsight-coaching-priority-rule" '
            f'content="{rule_version}">'
        )
    )
    (output / "report.html").write_text(
        f"<html><head>{marker}</head></html>", encoding="utf-8"
    )


def test_list_comparable_exact_filters_before_limit(tmp_path):
    manager = JobManager(tmp_path / "sessions", Config())
    old = _save_finished(
        manager,
        user_id="golfer-a",
        club="iron",
        hand="right",
        angle="face-on",
        tempo=2.2,
    )
    for _ in range(12):
        _save_finished(
            manager,
            user_id="golfer-a",
            club="iron",
            hand="left",
            angle="dtl",
            tempo=3.0,
        )
    _save_finished(
        manager,
        user_id="golfer-b",
        club="iron",
        hand="right",
        angle="face-on",
        tempo=2.1,
    )
    current = _save_finished(
        manager,
        user_id="golfer-a",
        club="iron",
        hand="right",
        angle="face-on",
        tempo=2.0,
    )

    comparable = manager.list_comparable(
        user_id="golfer-a",
        club="iron",
        hand="right",
        angle="face-on",
        through=current.created_at,
        limit=2,
    )

    assert [job.id for job in comparable] == [current.id, old.id]


def _upload(
    client: TestClient, *, club: str, hand: str, angle: str
) -> str:
    response = client.post(
        "/upload",
        files={"video": ("swing.mov", b"fake video", "video/quicktime")},
        data={"club": club, "hand": hand, "angle": angle},
        follow_redirects=False,
    )
    assert response.status_code == 303
    job_id = response.headers["location"].rsplit("/", 1)[-1]
    assert wait_for(client, job_id)["status"] == "done"
    return job_id


def test_activated_progress_recurrence_and_personal_trend_use_exact_context(
    tmp_path, monkeypatch
):
    payloads = [
        payload_for([{"tempo_ratio": 2.2}]),  # iron / right / face-on
        payload_for([{"tempo_ratio": 3.8}]),  # iron / left / face-on
        payload_for([{"tempo_ratio": 3.1}]),  # driver / left / dtl
        payload_for([{"tempo_ratio": 3.2}]),  # driver / right / dtl
        payload_for([{"tempo_ratio": 2.0}]),  # latest: iron / right / face-on
    ]
    monkeypatch.setattr(
        jobs_module, "analyze_video", make_fake_analyze(payloads)
    )
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["free_per_month"] = 0
    cfg.coaching["club_aware_enabled"] = True
    app = create_app(cfg, sessions_dir=tmp_path / "sessions")
    client = TestClient(app)
    signup(client)

    _upload(client, club="iron", hand="right", angle="face-on")
    _upload(client, club="iron", hand="left", angle="face-on")
    _upload(client, club="driver", hand="left", angle="dtl")
    driver_latest = _upload(
        client, club="driver", hand="right", angle="dtl"
    )
    latest = _upload(client, club="iron", hand="right", angle="face-on")

    progress = client.get("/progress").text
    assert "Tracking context:</strong> Iron · Right-handed · Face-on" in progress
    assert "All clubs" not in progress
    assert 'href="/progress?club=iron"' in progress
    assert 'href="/progress?club=driver"' in progress
    assert "2.20:1" in progress and "2.00:1" in progress
    assert "3.80:1" not in progress and "3.20:1" not in progress
    assert "across 2 sessions" in progress

    driver = client.get("/progress?club=driver").text
    assert (
        "Tracking context:</strong> Driver · Right-handed · Down-the-line"
        in driver
    )
    assert "Baseline on the books" in driver
    assert f'/session/{driver_latest}/report' in driver
    assert "3.10:1" not in driver

    # Conversion copy uses the same latest exact context, not an all-history
    # personal trend.
    pricing = client.get("/pricing").text
    assert "2.20:1 → 2.00:1 across 2 sessions" in pricing
    assert "across 5 sessions" not in pricing

    # Recurrence on the result includes only the earlier matching iron/right/
    # face-on session, not the left-handed or DTL rows.
    status = client.get(f"/session/{latest}").text
    assert "2 comparable sessions" in status
    assert "3 comparable sessions" not in status


def test_activated_progress_fails_closed_without_authoritative_club(tmp_path):
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.coaching["club_aware_enabled"] = True
    app = create_app(cfg, sessions_dir=tmp_path / "sessions")
    client = TestClient(app)
    signup(client)
    user = app.state.users.get_by_email("kyle@example.com")
    _save_finished(
        app.state.jobs,
        user_id=user.id,
        club=None,
        hand="right",
        angle="face-on",
        tempo=2.2,
    )

    html = client.get("/progress").text

    assert "Nothing to chart yet" in html
    assert "Tracking context:" not in html
    assert "2.20:1" not in html


def test_dynamic_brief_replays_old_rule_one_after_activation(tmp_path):
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.coaching["club_aware_enabled"] = True
    app = create_app(cfg, sessions_dir=tmp_path / "sessions")
    client = TestClient(app)
    signup(client)
    user = app.state.users.get_by_email("kyle@example.com")
    job = _save_finished(
        app.state.jobs,
        user_id=user.id,
        club="driver",
        hand="right",
        angle="face-on",
        tempo=3.0,
    )
    _write_job_payload(job, _priority_payload(), rule_version=None)

    html = client.get(f"/session/{job.id}").text

    assert '<p class="coach-value"><strong>Head sway (backswing)</strong>' in html
    assert '<p class="coach-value"><strong>Finish balance</strong>' not in html


def test_dynamic_brief_replays_rule_two_after_rollback(tmp_path):
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.coaching["club_aware_enabled"] = False
    app = create_app(cfg, sessions_dir=tmp_path / "sessions")
    client = TestClient(app)
    signup(client)
    user = app.state.users.get_by_email("kyle@example.com")
    job = _save_finished(
        app.state.jobs,
        user_id=user.id,
        club="driver",
        hand="right",
        angle="face-on",
        tempo=3.0,
    )
    _write_job_payload(job, _priority_payload(), rule_version=2)

    html = client.get(f"/session/{job.id}").text

    assert '<p class="coach-value"><strong>Finish balance</strong>' in html
    assert '<p class="coach-value"><strong>Head sway (backswing)</strong>' not in html


def test_rule_two_dynamic_brief_rejects_payload_club_without_job_context(tmp_path):
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.coaching["club_aware_enabled"] = False
    app = create_app(cfg, sessions_dir=tmp_path / "sessions")
    client = TestClient(app)
    signup(client)
    user = app.state.users.get_by_email("kyle@example.com")
    job = _save_finished(
        app.state.jobs,
        user_id=user.id,
        club=None,
        hand="right",
        angle="face-on",
        tempo=3.0,
    )
    _write_job_payload(
        job, _priority_payload(meta_club="driver"), rule_version=2
    )

    html = client.get(f"/session/{job.id}").text

    assert "Your caddie's read" not in html
    assert "Finish balance" not in html


def _digest_job(
    tmp_path,
    n: int,
    *,
    tempo: float,
    club: str | None = "iron",
    hand: str | None = "right",
    angle: str | None = "face-on",
    metrics: list[dict] | None = None,
    rule_version: int | None = None,
):
    session_dir = tmp_path / f"job{n}"
    out = session_dir / "out"
    out.mkdir(parents=True)
    (out / "metrics.json").write_text(
        json.dumps(payload_for(metrics or [{"tempo_ratio": tempo}])),
        encoding="utf-8",
    )
    marker = (
        ""
        if rule_version is None
        else (
            '<meta name="caddieinsight-coaching-priority-rule" '
            f'content="{rule_version}">'
        )
    )
    (out / "report.html").write_text(
        f"<html><head>{marker}</head></html>", encoding="utf-8"
    )
    return types.SimpleNamespace(
        id=f"job{n}",
        session_dir=session_dir,
        status="done",
        created_at=1000.0 + n,
        report_rel="out/report.html",
        club=club,
        hand=hand,
        angle=angle,
    )


def _digest_user():
    return types.SimpleNamespace(
        id="u1",
        email="golfer@example.com",
        digest_opt_in=True,
        digest_last_sent_at=None,
        is_pro=True,
    )


def test_activated_digest_recomputes_exact_trend_and_passes_job_club(
    tmp_path, monkeypatch
):
    cfg = Config()
    cfg.coaching["club_aware_enabled"] = True
    jobs = [
        _digest_job(tmp_path, 1, tempo=2.2),
        _digest_job(tmp_path, 2, tempo=3.8, hand="left"),
        _digest_job(tmp_path, 3, tempo=2.0),
    ]
    seen = {}
    original = digest.build_caddie_brief_from_payload

    def capture(*args, **kwargs):
        seen["club"] = kwargs.get("club")
        return original(*args, **kwargs)

    monkeypatch.setattr(digest, "build_caddie_brief_from_payload", capture)
    composed = digest.compose_digest(
        _digest_user(), cfg, jobs, secret="digest-test-secret"
    )

    assert composed is not None
    _subject, body = composed
    assert seen["club"] == "iron"
    assert "Comparison context:</strong> Iron · right-handed · face-on" in body
    assert "2.20:1 → 2.00:1 across 2 sessions" in body
    assert "3.80:1" not in body and "across 3 sessions" not in body


def test_activated_digest_fails_closed_when_latest_readable_context_is_missing(
    tmp_path,
):
    cfg = Config()
    cfg.coaching["club_aware_enabled"] = True
    jobs = [
        _digest_job(tmp_path, 1, tempo=2.2),
        _digest_job(tmp_path, 2, tempo=2.0, club=None),
    ]

    assert digest.compose_digest(
        _digest_user(), cfg, jobs, secret="digest-test-secret"
    ) is None


def test_malformed_activation_value_preserves_legacy_digest(tmp_path):
    cfg = Config()
    cfg.coaching["club_aware_enabled"] = "true"
    legacy = _digest_job(
        tmp_path, 1, tempo=2.2, club=None, hand=None, angle=None
    )

    assert digest.compose_digest(
        _digest_user(), cfg, [legacy], secret="digest-test-secret"
    ) is not None


def test_digest_replays_rule_two_exact_context_after_rollback(tmp_path):
    cfg = Config()
    cfg.coaching["club_aware_enabled"] = False
    tied = [
        {"head_sway_backswing_sw": 0.50, "finish_balance_sw": 0.50}
        for _ in range(3)
    ]
    jobs = [
        _digest_job(tmp_path, 1, tempo=3.0, club="driver", metrics=tied),
        _digest_job(
            tmp_path,
            2,
            tempo=3.0,
            club="driver",
            hand="left",
            metrics=tied,
        ),
        _digest_job(
            tmp_path,
            3,
            tempo=3.0,
            club="driver",
            metrics=tied,
            rule_version=2,
        ),
    ]

    composed = digest.compose_digest(
        _digest_user(), cfg, jobs, secret="digest-test-secret"
    )

    assert composed is not None
    subject, body = composed
    assert subject == "This week: hold the finish (1 drill)"
    assert "Comparison context:</strong> Driver · right-handed · face-on" in body


def test_digest_keeps_old_rule_one_priority_after_activation(tmp_path):
    cfg = Config()
    cfg.coaching["club_aware_enabled"] = True
    tied = [
        {"head_sway_backswing_sw": 0.50, "finish_balance_sw": 0.50}
        for _ in range(3)
    ]
    legacy = _digest_job(
        tmp_path, 1, tempo=3.0, club="driver", metrics=tied
    )

    composed = digest.compose_digest(
        _digest_user(), cfg, [legacy], secret="digest-test-secret"
    )

    assert composed is not None
    subject, body = composed
    assert subject == "This week: quiet the head (1 drill)"
    assert "Comparison context:</strong> Driver · right-handed · face-on" in body
