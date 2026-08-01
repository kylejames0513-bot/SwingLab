"""Compatibility coverage for the versioned club-aware priority policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from swinglab.caddie_brief import (
    build_caddie_brief,
    build_caddie_brief_from_payload,
)
from swinglab.coaching import (
    FLAG_BALANCE,
    FLAG_HEAD_DIP,
    FLAG_HIP_SLIDE,
    FLAG_SWAY,
    FLAG_TEMPO,
    issue_cards,
    priority_rule_version,
)
from swinglab.config import Config
from swinglab.ffmpeg import VideoInfo
from swinglab.metrics import ANGLE_DTL, SwingMetrics, session_stats
from swinglab.proof_cycle import ProofSession, ProofTarget
from swinglab.proof_cycle_artifact import (
    ARTIFACT_VERSION,
    build_proof_cycle_artifact,
    load_proof_cycle_artifact,
    verified_proof_cycle_artifact,
    write_proof_cycle_artifact,
)
from swinglab.report import (
    REPORT_FORMAT_VERSION,
    persisted_priority_rule_version,
    write_report_html,
)


def configured(enabled: object) -> Config:
    cfg = Config()
    cfg.coaching["club_aware_enabled"] = enabled
    return cfg


def metric(
    number: int,
    *,
    sway: float = 0.10,
    tempo: float = 3.0,
    hip_slide: float = 0.10,
    head_dip: float = 0.10,
    balance: float = 0.10,
) -> SwingMetrics:
    return SwingMetrics(
        swing=number,
        strike_s=float(number * 3),
        backswing_s=0.9,
        downswing_s=0.3,
        tempo_ratio=tempo,
        head_sway_backswing_sw=sway,
        head_sway_downswing_sw=0.05,
        hip_slide_backswing_sw=hip_slide,
        hip_slide_downswing_sw=0.05,
        target_direction=1,
        head_dip_sw=head_dip,
        lead_arm_angle_deg=175.0,
        shoulder_tilt_address_deg=8.0,
        shoulder_tilt_impact_deg=18.0,
        shoulder_tilt_delta_deg=10.0,
        finish_balance_sw=balance,
    )


def tied_rows() -> list[SwingMetrics]:
    return [
        metric(
            number,
            sway=0.50,
            tempo=2.0,
            hip_slide=0.50,
            head_dip=0.50,
            balance=0.50,
        )
        for number in range(1, 4)
    ]


@pytest.mark.parametrize(
    ("club", "expected"),
    [
        ("driver", FLAG_BALANCE),
        ("fairway-wood", FLAG_HEAD_DIP),
        ("hybrid", FLAG_HEAD_DIP),
        ("iron", FLAG_SWAY),
        ("wedge", FLAG_SWAY),
        (None, FLAG_SWAY),
        ("unsupported", FLAG_SWAY),
    ],
)
def test_rule_two_changes_only_supported_club_ties(club, expected):
    rows = tied_rows()
    cards = issue_cards(
        rows, session_stats(rows), configured(True), club=club
    )

    assert cards[0].flag == expected
    if club == "iron":
        assert [card.flag for card in cards[:2]] == [FLAG_SWAY, FLAG_HIP_SLIDE]


@pytest.mark.parametrize("malformed", [False, None, "true", 1, 0])
def test_club_aware_gate_requires_the_exact_boolean_true(malformed):
    cfg = configured(malformed)
    rows = tied_rows()

    assert priority_rule_version(cfg) == 1
    assert issue_cards(rows, session_stats(rows), cfg, club="driver")[0].flag == FLAG_SWAY


def test_rule_two_never_promotes_a_warning_over_a_major_issue():
    rows = [
        metric(1, sway=0.50, balance=0.20),
        metric(2, sway=0.50, balance=0.10),
        metric(3, sway=0.50, balance=0.10),
    ]
    cards = issue_cards(
        rows, session_stats(rows), configured(True), club="driver"
    )

    assert cards[0].flag == FLAG_SWAY
    balance = next(card for card in cards if card.flag == FLAG_BALANCE)
    assert cards[0].severity == "major"
    assert balance.severity == "warn"


def test_dtl_stays_tempo_only_even_for_driver():
    rows = tied_rows()
    brief = build_caddie_brief(
        rows,
        session_stats(rows),
        configured(True),
        angle=ANGLE_DTL,
        club="driver",
    )

    assert brief is not None
    assert brief.focus_flag == FLAG_TEMPO


def test_payload_brief_prefers_authoritative_club_over_metadata():
    payload = {
        "meta": {"club": "driver", "angle": "face-on"},
        "swings": [{"metrics": row.as_dict()} for row in tied_rows()],
        "session_stats": {},
    }
    cfg = configured(True)

    metadata_brief = build_caddie_brief_from_payload(payload, cfg)
    authoritative_brief = build_caddie_brief_from_payload(
        payload, cfg, club="iron"
    )
    missing_authoritative_brief = build_caddie_brief_from_payload(
        payload, cfg, club=None
    )

    assert metadata_brief is not None and authoritative_brief is not None
    assert missing_authoritative_brief is not None
    assert metadata_brief.focus_flag == FLAG_BALANCE
    assert authoritative_brief.focus_flag == FLAG_SWAY
    assert missing_authoritative_brief.focus_flag == FLAG_SWAY


def _video() -> VideoInfo:
    return VideoInfo(
        path=Path("swing.mov"),
        duration_s=20.0,
        width=1920,
        height=1080,
        fps=30.0,
        rotation=0,
        creation_time=None,
        has_audio=True,
    )


def _report_swing(row: SwingMetrics) -> dict:
    return {
        "metrics": row,
        "notes": [],
        "strip": f"media/strip-{row.swing}.png",
        "overlay": f"media/overlay-{row.swing}.png",
        "slowmo": f"media/slowmo-{row.swing}.mp4",
    }


def test_report_brief_cards_plan_and_scope_copy_share_one_priority(tmp_path):
    cfg = configured(True)
    rows = tied_rows()
    swings = [_report_swing(row) for row in rows]
    html = write_report_html(
        tmp_path / "report.html",
        _video(),
        swings,
        session_stats(rows),
        [],
        "right",
        cfg,
        club="driver",
    ).read_text(encoding="utf-8")

    assert "Fix first" in html and "Finish balance" in html
    assert "This week:\n  <strong>Finish balance</strong>" in html
    practice_start = html.index("<h2>Practice plan</h2>")
    assert html.index("<h3>Finish balance</h3>", practice_start) < html.index(
        "<h3>Head sway</h3>", practice_start
    )
    assert "used only to order issues with the same" in html
    assert "2D body movement in the camera view" in html
    assert "club path" in html and "ball flight" in html
    assert REPORT_FORMAT_VERSION == "caddie-brief-v1"
    assert ARTIFACT_VERSION == 1


def test_rule_one_report_preserves_the_established_practice_plan_order(tmp_path):
    cfg = configured(False)
    rows = [
        metric(1, sway=0.40, tempo=2.0),
        metric(2, sway=0.10, tempo=2.0),
        metric(3, sway=0.10, tempo=2.0),
    ]
    html = write_report_html(
        tmp_path / "report.html",
        _video(),
        [_report_swing(row) for row in rows],
        session_stats(rows),
        [],
        "right",
        cfg,
        club="driver",
    ).read_text(encoding="utf-8")

    # The Caddie Brief already led with the major tempo card before this
    # floor, while the legacy practice plan followed raw session_flags order.
    # Rule 1 deliberately preserves that compatibility behavior byte-for-
    # semantics; rule 2 is where the surfaces become one ordered policy.
    assert "Fix first" in html and "Tempo" in html
    practice_start = html.index("<h2>Practice plan</h2>")
    assert html.index("<h3>Head sway</h3>", practice_start) < html.index(
        "<h3>Tempo</h3>", practice_start
    )


def test_report_priority_rule_marker_is_additive_and_fails_closed(tmp_path):
    report = tmp_path / "report.html"
    rows = tied_rows()
    write_report_html(
        report,
        _video(),
        [_report_swing(row) for row in rows],
        session_stats(rows),
        [],
        "right",
        configured(True),
        club="driver",
    )
    assert persisted_priority_rule_version(report) == 2

    report.write_text("<html><head></head></html>", encoding="utf-8")
    assert persisted_priority_rule_version(report) == 1

    report.write_text(
        '<meta name="caddieinsight-coaching-priority-rule" content="3">',
        encoding="utf-8",
    )
    assert persisted_priority_rule_version(report) is None

    report.write_text(
        '<meta name="caddieinsight-coaching-priority-rule" content="1">'
        '<meta name="caddieinsight-coaching-priority-rule" content="2">',
        encoding="utf-8",
    )
    assert persisted_priority_rule_version(report) is None


def test_proof_target_explicitly_persists_supported_rule_version():
    rows = tied_rows()
    cfg = configured(True)
    card = issue_cards(rows, session_stats(rows), cfg, club="driver")[0]
    session = ProofSession.from_swing_metrics(
        session_id="baseline",
        user_id="golfer",
        club="driver",
        hand="right",
        angle="face-on",
        swings=rows,
    )

    target = ProofTarget.from_issue_card(session, card, rule_version=2)

    assert target.rule_version == 2
    with pytest.raises(ValueError, match="priority rule"):
        ProofTarget.from_issue_card(session, card, rule_version=3)
    with pytest.raises(ValueError, match="priority rule"):
        ProofTarget.from_issue_card(session, card, rule_version=True)


@dataclass
class FakeJob:
    id: str
    session_dir: Path
    created_at: float
    hand: str = "right"
    angle: str = "face-on"
    club: str | None = "driver"
    user_id: str | None = "golfer"
    status: str = "done"
    report_rel: str | None = "out/source/report.html"


def make_job(
    tmp_path: Path,
    job_id: str,
    created_at: float,
    rows: list[SwingMetrics],
) -> FakeJob:
    job = FakeJob(job_id, tmp_path / job_id, created_at)
    output = job.session_dir / "out" / "source"
    output.mkdir(parents=True)
    (output / "report.html").write_text("<html>report</html>", encoding="utf-8")
    payload = {
        # Deliberately contradictory: the authenticated job row is authoritative.
        "meta": {"club": "iron", "hand": "left", "angle": "dtl"},
        "swings": [{"metrics": row.as_dict()} for row in rows],
        "session_stats": {},
    }
    (output / "metrics.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return job


@pytest.mark.parametrize(
    ("initial_gate", "replay_gate", "rule_version", "metric_name"),
    [
        (False, True, 1, "head_sway_backswing_sw"),
        (True, False, 2, "finish_balance_sw"),
    ],
)
def test_proof_artifacts_replay_the_stored_rule_across_gate_changes(
    tmp_path, initial_gate, replay_gate, rule_version, metric_name
):
    cfg = configured(initial_gate)
    baseline = make_job(tmp_path, "baseline", 1.0, tied_rows())
    baseline_artifact = build_proof_cycle_artifact(baseline, [], cfg)
    assert baseline_artifact.target is not None
    assert baseline_artifact.target.rule_version == rule_version
    assert baseline_artifact.target.metric == metric_name
    write_proof_cycle_artifact(baseline, baseline_artifact)

    refilm_rows = [
        metric(
            number,
            sway=0.40,
            tempo=2.0,
            hip_slide=0.40,
            head_dip=0.40,
            balance=0.40,
        )
        for number in range(1, 4)
    ]
    refilm = make_job(tmp_path, "refilm", 2.0, refilm_rows)
    refilm_artifact = build_proof_cycle_artifact(refilm, [baseline], cfg)
    assert refilm_artifact.target == baseline_artifact.target
    write_proof_cycle_artifact(refilm, refilm_artifact)

    cfg.coaching["club_aware_enabled"] = replay_gate
    assert verified_proof_cycle_artifact(baseline, [], cfg) == baseline_artifact
    assert (
        verified_proof_cycle_artifact(refilm, [baseline], cfg)
        == refilm_artifact
    )


def test_unknown_persisted_rule_version_fails_closed(tmp_path):
    cfg = configured(True)
    baseline = make_job(tmp_path, "baseline", 1.0, tied_rows())
    artifact = build_proof_cycle_artifact(baseline, [], cfg)
    path = write_proof_cycle_artifact(baseline, artifact)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["target"]["rule_version"] = 3
    data["target_fingerprint"] = hashlib.sha256(
        json.dumps(
            data["target"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    path.write_text(json.dumps(data), encoding="utf-8")

    assert load_proof_cycle_artifact(baseline) is None
    assert verified_proof_cycle_artifact(baseline, [], cfg) is None
