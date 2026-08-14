"""Progress dashboard 1c/1d: the four stat tiles, the proof-cycle verdict
card's measured pair, the priority-history list, the noise-floor band, the
session-axis labels + legend, and the numbered film CTA.

Every rendered number here is either a persisted proof-cycle measurement,
persisted policy, or a derivation from real session samples — these tests
pin both that the honest values appear and that the dishonest states stay
hidden (no band without an engine MDE, no history without sidecars, no
"matched" claims outside an exact context).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab.config import Config
from swinglab.diagrams import trend_chart
from swinglab.ffmpeg import VideoInfo
from swinglab.pipeline import SessionResult
from swinglab.proof_cycle import (
    ProofMeasurement,
    ProofRefilm,
    ProofTarget,
    SessionContext,
)
from swinglab.proof_cycle_artifact import (
    PersistedComparison,
    ProofCycleArtifact,
    ProofCyclePolicy,
    build_proof_cycle_artifact,
    load_proof_cycle_artifact,
    proof_cycle_target_fingerprint,
    write_proof_cycle_artifact,
)
from swinglab.web import app as app_module
from swinglab.web import jobs as jobs_module
from swinglab.web.app import (
    create_app,
    last_filmed_text,
    practice_streak_weeks,
    proof_history_rows,
)

BRAND = {"primary_color": "#1a5c38", "accent_color": "#e8720c"}


# -- pure helpers ------------------------------------------------------------

def ts(year: int, month: int, day: int) -> float:
    return datetime(year, month, day, 12, 0).timestamp()


def test_practice_streak_counts_consecutive_iso_weeks():
    # 2026-07-28 / 08-04 / 08-11 are consecutive ISO weeks (31, 32, 33).
    assert practice_streak_weeks(
        [ts(2026, 7, 28), ts(2026, 8, 4), ts(2026, 8, 11)]
    ) == 3
    # Two sessions in one week still count that week once.
    assert practice_streak_weeks(
        [ts(2026, 8, 10), ts(2026, 8, 11), ts(2026, 8, 4)]
    ) == 2


def test_practice_streak_breaks_at_a_skipped_week_and_handles_empty():
    # Week 32 was skipped: only the latest week counts.
    assert practice_streak_weeks([ts(2026, 7, 28), ts(2026, 8, 11)]) == 1
    assert practice_streak_weeks([]) == 0
    assert practice_streak_weeks(["not-a-number"]) == 0


def test_last_filmed_text_buckets_days():
    now = ts(2026, 8, 13)
    assert last_filmed_text(now - 3600, now=now) == "today"
    assert last_filmed_text(now - 90000, now=now) == "1 day ago"
    assert last_filmed_text(now - 3 * 86400, now=now) == "3 days ago"


# -- trend_chart noise band --------------------------------------------------

def test_trend_chart_band_kwargs_draw_a_marked_rect():
    svg = trend_chart(
        [0.50, 0.40, 0.30], 0.30, BRAND, worse="higher",
        band_center=0.50, band_half_width=0.05,
    )
    assert 'data-band="noise"' in svg
    # Same no-defs/no-url SVG contract as every other generated diagram.
    assert "<defs" not in svg and "url(" not in svg


def test_trend_chart_draws_no_band_by_default_or_for_degenerate_widths():
    assert 'data-band' not in trend_chart([0.50, 0.40], 0.30, BRAND)
    assert 'data-band' not in trend_chart(
        [0.50, 0.40], 0.30, BRAND, band_center=0.5, band_half_width=0.0
    )
    assert 'data-band' not in trend_chart(
        [0.50, 0.40], 0.30, BRAND,
        band_center=float("nan"), band_half_width=0.05,
    )


# -- proof_history_rows against real sidecars --------------------------------

@dataclass
class FakeJob:
    id: str
    session_dir: Path
    created_at: float
    hand: str = "right"
    angle: str = "face-on"
    club: str | None = "Driver"
    user_id: str | None = "golfer-1"
    status: str = "done"
    report_rel: str | None = "out/report.html"


def proof_cfg() -> Config:
    configured = Config()
    configured.proof_cycle["enabled"] = True
    return configured


def sway_row(head_sway: float) -> dict:
    return {
        "tempo_ratio": 3.0,
        "head_sway_backswing_sw": head_sway,
        "hip_slide_backswing_sw": 0.10,
        "head_dip_sw": 0.10,
        "lead_arm_angle_deg": 175.0,
        "shoulder_tilt_impact_deg": 20.0,
        "shoulder_tilt_delta_deg": 10.0,
        "finish_balance_sw": 0.10,
    }


def make_sidecar_job(
    tmp_path: Path, job_id: str, created_at: float, head_sway: float
) -> FakeJob:
    job = FakeJob(
        id=job_id, session_dir=tmp_path / job_id, created_at=created_at
    )
    out = job.session_dir / "out"
    out.mkdir(parents=True)
    (out / "report.html").write_text("<html>report</html>")
    payload = {
        "swings": [{"metrics": sway_row(head_sway)} for _ in range(3)],
        "session_stats": {},
    }
    (out / "metrics.json").write_text(json.dumps(payload))
    return job


def test_history_rows_baseline_only_is_unproven_or_active(tmp_path):
    configured = proof_cfg()
    baseline = make_sidecar_job(tmp_path, "baseline", 1.0, 0.50)
    write_proof_cycle_artifact(
        baseline, build_proof_cycle_artifact(baseline, [], configured)
    )
    fingerprint = proof_cycle_target_fingerprint(
        load_proof_cycle_artifact(baseline).target
    )

    rows = proof_history_rows(
        [baseline], configured,
        active_fingerprint=None, sample_job_ids=["baseline"],
    )
    assert len(rows) == 1
    assert rows[0]["state"] == "Unproven"
    assert rows[0]["verdict_held"] is False
    assert rows[0]["subline"] == "Never re-filmed \N{EM DASH} no verdict claimed"
    assert rows[0]["ordinal"] == "S01"
    assert rows[0]["name"] == "Head sway (backswing)"

    active_rows = proof_history_rows(
        [baseline], configured,
        active_fingerprint=fingerprint, sample_job_ids=["baseline"],
    )
    assert active_rows[0]["state"] == "Active"
    assert active_rows[0]["subline"].startswith("Priority set")
    # The pass mark quoted is the persisted benchmark_text, verbatim.
    assert "flagged above" in active_rows[0]["subline"]


def test_history_rows_held_after_two_matched_refilms(tmp_path):
    configured = proof_cfg()
    baseline = make_sidecar_job(tmp_path, "baseline", 1.0, 0.50)
    write_proof_cycle_artifact(
        baseline, build_proof_cycle_artifact(baseline, [], configured)
    )
    refilm1 = make_sidecar_job(tmp_path, "refilm1", 2.0, 0.20)
    write_proof_cycle_artifact(
        refilm1, build_proof_cycle_artifact(refilm1, [baseline], configured)
    )
    refilm2 = make_sidecar_job(tmp_path, "refilm2", 3.0, 0.20)
    write_proof_cycle_artifact(
        refilm2,
        build_proof_cycle_artifact(refilm2, [baseline, refilm1], configured),
    )

    jobs = [baseline, refilm1, refilm2]
    rows = proof_history_rows(
        jobs, configured,
        active_fingerprint=None,
        sample_job_ids=[j.id for j in jobs],
    )

    # One chain, one row — grouped by target fingerprint, and the LATEST
    # verdict (improved after 2 accepted matched re-films) wins.
    assert len(rows) == 1
    assert rows[0]["state"] == "Held"
    assert rows[0]["verdict_held"] is True
    assert rows[0]["subline"] == "Held across 2 matched re-films"
    assert rows[0]["ordinal"] == "S01"


def test_history_rows_are_empty_for_pre_sidecar_sessions(tmp_path):
    configured = proof_cfg()
    legacy = make_sidecar_job(tmp_path, "legacy", 1.0, 0.50)
    # No sidecar was ever written — older sessions are never retrofitted.
    assert proof_history_rows(
        [legacy], configured,
        active_fingerprint=None, sample_job_ids=["legacy"],
    ) == []


# -- the rendered page -------------------------------------------------------

def payload_for(swings: list[dict]) -> dict:
    return {"swings": [{"metrics": m} for m in swings], "session_stats": {}}


def make_fake_analyze(payloads: list[dict]):
    state = {"i": 0}

    def fake(video_path, out_dir=None, hand="right", manual_strikes=None,
             cfg=None, keep_work=False, fast=False, log=print, progress=None,
             angle="face-on", club=None, level=None, replay_locked=False):
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


DASH_PAYLOADS = [
    payload_for([{"tempo_ratio": 2.2, "head_sway_backswing_sw": 0.41}]),
    payload_for([{"tempo_ratio": 2.7, "head_sway_backswing_sw": 0.29}]),
]


def make_dash_app(tmp_path, monkeypatch, *, club_aware=True, proof=False):
    monkeypatch.setattr(
        jobs_module, "analyze_video", make_fake_analyze(list(DASH_PAYLOADS))
    )
    cfg = Config()
    cfg.web["require_account"] = True
    cfg.billing["free_per_month"] = 5
    cfg.coaching["club_aware_enabled"] = club_aware
    cfg.proof_cycle["enabled"] = proof
    return create_app(cfg, sessions_dir=tmp_path / "sessions")


def signup(client, email="kyle@example.com"):
    resp = client.post(
        "/signup", data={"email": email, "password": "longenough"},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def upload_and_wait(client, *, club="iron", hand="right", angle="face-on"):
    resp = client.post(
        "/upload",
        files={"video": ("swing.mov", b"fake video bytes", "video/quicktime")},
        data={"club": club, "hand": hand, "angle": angle},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    job_id = resp.headers["location"].rsplit("/", 1)[-1]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = client.get(f"/api/session/{job_id}").json()["status"]
        if status in ("done", "failed"):
            assert status == "done"
            return job_id
        time.sleep(0.02)
    raise TimeoutError("job never finished")


def test_stat_strip_axis_legend_and_numbered_cta_in_exact_context(
    tmp_path, monkeypatch
):
    client = TestClient(make_dash_app(tmp_path, monkeypatch))
    signup(client)
    upload_and_wait(client)
    upload_and_wait(client)

    html = client.get("/progress").text

    # The stat strip: matched-session count, headline metric with % change,
    # practice streak. All derived from the two real sessions.
    assert "Matched sessions" in html
    assert "Practice streak" in html
    assert "since session 01" in html      # +23% for tempo 2.2 -> 2.7
    assert "last filmed today" in html
    # No proof cycle configured: no verdicts tile, no history, no band.
    assert "Verdicts held" not in html
    assert "Priority history" not in html
    assert '<rect data-band="noise"' not in html
    assert "Noise floor band" not in html
    # Session axis + legend on the headline chart only.
    assert "S01" in html and "S02" in html
    assert "Matched measurement" in html
    assert "Pass mark" in html
    # The numbered film CTA (1d's block button, 1c's CTA).
    assert "Film session 3" in html


def test_legacy_club_mode_omits_matched_claims(tmp_path, monkeypatch):
    client = TestClient(
        make_dash_app(tmp_path, monkeypatch, club_aware=False)
    )
    signup(client)
    upload_and_wait(client)
    upload_and_wait(client)

    html = client.get("/progress").text

    # Legacy mode can mix hands/angles, so every "matched" element is
    # omitted rather than rendered as a false claim.
    assert "Matched sessions" not in html
    assert "Matched measurement" not in html
    assert "Practice streak" not in html
    assert "Priority history" not in html
    # The dashboard itself still renders, with the numbered CTA.
    assert "2.70:1" in html
    assert "Film session 3" in html


def make_comparison_artifact(user_id: str) -> ProofCycleArtifact:
    """A comparison-stage artifact shaped exactly like the worker writes:
    baseline 0.41 SW, accepted re-film 0.29 SW, improved at 2 of 2."""
    metric = "head_sway_backswing_sw"
    baseline_context = SessionContext(
        session_id="baseline-job", user_id=user_id,
        club="iron", hand="right", angle="face-on",
    )
    target = ProofTarget(
        source_flag="sway",
        metric=metric,
        display_name="Head sway (backswing)",
        unit="SW",
        worse_direction="higher",
        aggregation="mean",
        benchmark_value=0.30,
        benchmark_text="flagged above 0.30 SW",
        drill_ids=("wall-drill",),
        drill_names=("Wall drill",),
        baseline_context=baseline_context,
        baseline=ProofMeasurement(
            metric=metric, aggregation="mean",
            value=0.41, mean=0.41, std=0.02, readable_swings=3,
        ),
        baseline_completed=True,
        baseline_coaching_eligible=True,
        baseline_warning=None,
        rule_version=1,
    )
    refilm = ProofRefilm(
        context=SessionContext(
            session_id="refilm-job", user_id=user_id,
            club="iron", hand="right", angle="face-on",
        ),
        measurement=ProofMeasurement(
            metric=metric, aggregation="mean",
            value=0.29, mean=0.29, std=0.02, readable_swings=3,
        ),
        completed=True,
        coaching_eligible=True,
        warning=None,
    )
    return ProofCycleArtifact(
        source_session_id="refilm-job",
        source_metrics_sha256=None,
        stage="comparison",
        target=target,
        refilm=refilm,
        comparison=PersistedComparison(
            verdict="improved",
            hard_failures=(),
            notes=(),
            minimum_detectable_effect=0.05,
            maximum_refilm_spread=0.05,
            directional_change=0.12,
            accepted_refilm_count=2,
        ),
        policy=ProofCyclePolicy(
            noise_floor=0.03,
            minimum_readable_swings=3,
            minimum_refilms_for_improved=2,
            maximum_refilm_spread=None,
        ),
    )


def test_verdict_card_numbers_and_noise_band_from_verified_artifact(
    tmp_path, monkeypatch
):
    app = make_dash_app(tmp_path, monkeypatch, proof=True)
    client = TestClient(app)
    signup(client)
    upload_and_wait(client)
    upload_and_wait(client)
    user = app.state.users.get_by_email("kyle@example.com")
    artifact = make_comparison_artifact(user.id)
    monkeypatch.setattr(
        app_module,
        "verified_proof_cycle_artifact",
        lambda *args, **kwargs: artifact,
    )

    html = client.get("/progress").text

    # The measured pair, verbatim from the persisted baseline and re-film.
    assert "0.41 SW" in html and "0.29 SW" in html
    assert 'class="proof-pair"' in html
    # N of M: measured count, persisted policy, and the verdict word.
    assert 'class="proof-count"' in html
    assert "Improved" in html
    # The verified view's cautious copy is still there underneath.
    assert "Matched improvement confirmed" in html
    # The noise band renders on the target metric's chart, with its legend
    # row, using the persisted MDE (0.41 ± 0.05).
    assert '<rect data-band="noise"' in html
    assert "Noise floor band" in html


def test_baseline_stage_artifact_keeps_the_words_only_card(
    tmp_path, monkeypatch
):
    app = make_dash_app(tmp_path, monkeypatch, proof=True)
    client = TestClient(app)
    signup(client)
    upload_and_wait(client)
    upload_and_wait(client)
    user = app.state.users.get_by_email("kyle@example.com")
    full = make_comparison_artifact(user.id)
    baseline_only = ProofCycleArtifact(
        source_session_id="baseline-job",
        source_metrics_sha256=None,
        stage="baseline",
        target=full.target,
        refilm=None,
        comparison=None,
        policy=full.policy,
    )
    monkeypatch.setattr(
        app_module,
        "verified_proof_cycle_artifact",
        lambda *args, **kwargs: baseline_only,
    )

    html = client.get("/progress").text

    # Words-only card: no measured pair, no confirmations line.
    assert "Proof target set" in html
    assert 'class="proof-pair"' not in html
    assert 'class="proof-count"' not in html
    # But the baseline-stage band still renders with the same MDE the
    # comparison stage will use: max(noise_floor, baseline std) = 0.03.
    assert '<rect data-band="noise"' in html
    assert "Noise floor band" in html


def test_priority_history_renders_stored_chains(tmp_path, monkeypatch):
    app = make_dash_app(tmp_path, monkeypatch, proof=True)
    client = TestClient(app)
    signup(client)
    first_job = upload_and_wait(client)
    upload_and_wait(client)
    user = app.state.users.get_by_email("kyle@example.com")
    full = make_comparison_artifact(user.id)
    target = ProofTarget(
        **{
            **full.target.__dict__,
            "baseline_context": SessionContext(
                session_id=first_job, user_id=user.id,
                club="iron", hand="right", angle="face-on",
            ),
        }
    )
    stored = ProofCycleArtifact(
        source_session_id=first_job,
        source_metrics_sha256=None,
        stage="baseline",
        target=target,
        refilm=None,
        comparison=None,
        policy=full.policy,
    )
    monkeypatch.setattr(
        app_module,
        "load_proof_cycle_artifact",
        lambda job: stored if job.id == first_job else None,
    )

    html = client.get("/progress").text

    assert "Priority history" in html
    assert "Head sway (backswing)" in html
    assert "Never re-filmed" in html
    assert "Unproven" in html
    assert "S01" in html
    # The verdicts tile counts the same grouped chains: 0 held of 1 worked.
    assert "Verdicts held" in html
    assert "0 of 1" in html
    assert "priorities worked in this context" in html
