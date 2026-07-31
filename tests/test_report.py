"""Report rendering and metrics.json, including the white-label acceptance:
changing brand name/colors in config changes the report with no code edits."""

from __future__ import annotations

import json
import math
from pathlib import Path

from swinglab.config import Config
from swinglab.ffmpeg import VideoInfo
from swinglab.metrics import SwingMetrics, session_stats
from swinglab.report import write_metrics_json, write_report_html


def fake_video() -> VideoInfo:
    return VideoInfo(
        path=Path("swing.mov"),
        duration_s=20.0,
        width=1920,
        height=1080,
        fps=29.97,
        rotation=90,
        creation_time="2026-07-20T10:00:00Z",
        has_audio=True,
    )


def fake_swing(n: int, tempo: float = 2.9) -> dict:
    m = SwingMetrics(
        swing=n,
        strike_s=3.0 * n,
        backswing_s=0.9,
        downswing_s=0.31,
        tempo_ratio=tempo,
        head_sway_backswing_sw=0.2,
        head_sway_downswing_sw=-0.1,
        hip_slide_backswing_sw=0.15,
        hip_slide_downswing_sw=-0.2,
        target_direction=1,
    )
    return {
        "metrics": m,
        "notes": ["No flags on this swing."],
        "strip": f"media/strip_s{n}.png",
        "overlay": f"media/overlay_s{n}.png",
        "slowmo": f"media/slowmo_s{n}.mp4",
    }


def branded_cfg() -> Config:
    cfg = Config()
    cfg.brand["name"] = "AceCoach"
    cfg.brand["primary_color"] = "#123456"
    cfg.brand["accent_color"] = "#abcdef"
    cfg.brand["footer_text"] = "AceCoach footer line"
    return cfg


def test_report_html_reflects_branding_and_content(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example-golf.test")
    cfg = branded_cfg()
    cfg.shop["store_url"] = "https://example-golf.test"
    swings = [fake_swing(1), fake_swing(2), fake_swing(3)]
    stats = session_stats([s["metrics"] for s in swings])
    out = write_report_html(
        tmp_path / "report.html", fake_video(), swings, stats,
        ["Tempo is impressively consistent across swings."], "right", cfg,
    )
    html = out.read_text(encoding="utf-8")
    assert "AceCoach" in html and "CaddieInsight" not in html and "SwingLab" not in html
    assert "#123456" in html and "#abcdef" in html
    assert "AceCoach footer line" in html
    assert cfg.brand["disclaimer"][:40] in html
    assert "Filming tips" in html and "hip height" in html and "on a tee" in html
    assert html.count("media/strip_s") == 3  # one row of deliverables per swing
    assert html.count("media/slowmo_s") == 3
    assert "mean ± std" in html
    assert "rotation metadata: 90°" in html
    assert 'aria-label="Report navigation"' in html
    assert 'href="https://example-golf.test">Home</a>' in html
    assert 'href="https://app.example-golf.test/">Analyze</a>' in html
    assert 'href="https://app.example-golf.test/sessions">History</a>' in html
    assert "fonts.googleapis.com" not in html


def test_cli_report_stays_offline_and_has_no_broken_app_links(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    cfg = branded_cfg()
    cfg.shop["store_url"] = ""
    swing = fake_swing(1)
    out = write_report_html(
        tmp_path / "report.html",
        fake_video(),
        [swing],
        session_stats([swing["metrics"]]),
        [],
        "right",
        cfg,
    )
    html = out.read_text(encoding="utf-8")
    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html
    assert 'href="/"' not in html
    assert 'href="/sessions"' not in html
    assert 'aria-label="Report navigation"' not in html


def test_report_leads_with_coaching_and_collapses_raw_measurements(tmp_path):
    cfg = branded_cfg()
    swings = [fake_swing(1, tempo=2.0)]
    stats = session_stats([s["metrics"] for s in swings])
    out = write_report_html(
        tmp_path / "report.html", fake_video(), swings, stats, [], "right", cfg
    )
    html = out.read_text(encoding="utf-8")
    assert 'name="caddieinsight-report-outcome" content="coaching_ready"' in html
    assert "Your caddie's read" in html
    assert "Fix first" in html
    assert "Practice this" in html
    assert '<details class="measurements">' in html
    assert html.index("Your caddie's read") < html.index(">Session</h2>")
    assert html.index("Your caddie's read") < html.index(">Metrics</h2>")


def test_report_brief_keeps_quality_warning_above_collapsed_details(tmp_path):
    cfg = branded_cfg()
    swing = fake_swing(1, tempo=2.0)
    swing["metrics"].target_confident = False
    warning = "Low confidence: target direction could not be read."
    swing["notes"] = [warning]
    stats = session_stats([swing["metrics"]])
    out = write_report_html(
        tmp_path / "report.html", fake_video(), [swing], stats, [], "right", cfg
    )
    html = out.read_text(encoding="utf-8")
    assert "Measurement note" in html and warning in html
    assert html.index("Measurement note") < html.index('<details class="measurements">')


def test_report_brief_surfaces_root_camera_warning_before_dtl_scope_note(tmp_path):
    cfg = branded_cfg()
    swing = fake_swing(1, tempo=2.0)
    swing["notes"] = ["Let the backswing finish before starting down."]
    swing["replay"] = "media/replay_s1.mp4"
    warning = (
        "Low confidence: this clip looks like it was filmed face-on, but it "
        "was uploaded as down the line — numbers may not mean what they say."
    )
    stats = session_stats([swing["metrics"]])
    out = write_report_html(
        tmp_path / "report.html",
        fake_video(),
        [swing],
        stats,
        [warning, "Pick one count and rehearse it."],
        "right",
        cfg,
        angle="dtl",
        replay_locked=True,
    )
    html = out.read_text(encoding="utf-8")
    assert "Re-film before coaching" in html and warning in html
    assert html.index(warning) < html.index('<details class="measurements">')
    assert "<h2>Practice plan</h2>" not in html
    assert "<h2>Start here</h2>" not in html
    assert "Browse optional training aids" not in html
    assert "Upgrade to Pro" not in html
    assert "Let the backswing finish" not in html
    assert "Pick one count" not in html
    assert "<summary>See capture details</summary>" in html
    assert "<h2>Metrics</h2>" not in html
    assert "media/strip_s1.png" not in html
    assert "media/overlay_s1.png" not in html
    assert "media/replay_s1.mp4" not in html
    assert "media/slowmo_s1.mp4" in html


def test_report_with_no_coachable_fields_is_capture_only(tmp_path):
    cfg = branded_cfg()
    metric = SwingMetrics(
        swing=1,
        strike_s=3.0,
        backswing_s=0.9,
        downswing_s=0.3,
        tempo_ratio=float("nan"),
        head_sway_backswing_sw=float("nan"),
        head_sway_downswing_sw=float("nan"),
        hip_slide_backswing_sw=float("nan"),
        hip_slide_downswing_sw=float("nan"),
        target_direction=1,
    )
    swing = {
        "metrics": metric,
        "notes": [],
        "strip": "media/strip_s1.png",
        "overlay": "media/overlay_s1.png",
        "slowmo": "media/slowmo_s1.mp4",
        "replay": "media/replay_s1.mp4",
    }
    out = write_report_html(
        tmp_path / "report.html",
        fake_video(),
        [swing],
        session_stats([metric]),
        [],
        "right",
        cfg,
    )
    html = out.read_text(encoding="utf-8")
    assert 'name="caddieinsight-report-outcome" content="capture_only"' in html
    assert "Re-film before coaching" in html
    assert "did not produce enough readable motion data" in html
    assert "<summary>See capture details</summary>" in html
    assert "<h2>Practice plan</h2>" not in html
    assert "media/strip_s1.png" not in html
    assert "media/overlay_s1.png" not in html
    assert "media/replay_s1.mp4" not in html
    assert "media/slowmo_s1.mp4" in html


def test_clean_dtl_report_uses_only_rhythm_maintenance(tmp_path):
    cfg = branded_cfg()
    metric = SwingMetrics(
        swing=1,
        strike_s=3.0,
        backswing_s=0.9,
        downswing_s=0.3,
        tempo_ratio=3.0,
        head_sway_backswing_sw=0.80,
        head_sway_downswing_sw=float("nan"),
        hip_slide_backswing_sw=0.80,
        hip_slide_downswing_sw=float("nan"),
        target_direction=1,
    )
    swing = {
        "metrics": metric,
        "notes": [],
        "slowmo": "media/slowmo_s1.mp4",
    }
    out = write_report_html(
        tmp_path / "report.html",
        fake_video(),
        [swing],
        session_stats([metric]),
        [],
        "right",
        cfg,
        angle="dtl",
    )
    html = out.read_text(encoding="utf-8")
    assert "Protect your tempo baseline" in html
    assert "Rhythm baseline re-film" in html
    assert "Rhythm-only maintenance" in html
    assert "Keep every measured tempo ratio" in html
    assert "Compare tempo, sway and slide" not in html
    assert "switch the next baseline clip to face-on" in html
    assert "Head sway (backswing)" not in html
    assert "Hip slide (backswing)" not in html
    assert "Start here" not in html
    assert "0.80" not in html


def test_partial_face_on_report_does_not_claim_full_clean_baseline(tmp_path):
    cfg = branded_cfg()
    metric = SwingMetrics(
        swing=1,
        strike_s=3.0,
        backswing_s=0.9,
        downswing_s=0.3,
        tempo_ratio=3.0,
        head_sway_backswing_sw=float("nan"),
        head_sway_downswing_sw=float("nan"),
        hip_slide_backswing_sw=float("nan"),
        hip_slide_downswing_sw=float("nan"),
        target_direction=1,
    )
    swing = {
        "metrics": metric,
        "notes": [],
        "slowmo": "media/slowmo_s1.mp4",
    }
    out = write_report_html(
        tmp_path / "report.html",
        fake_video(),
        [swing],
        session_stats([metric]),
        [],
        "right",
        cfg,
    )
    html = out.read_text(encoding="utf-8")
    normalized = " ".join(html.split())
    assert "Protect your tempo baseline" in html
    assert "Rhythm baseline re-film" in html
    assert "did not produce a complete baseline" in normalized
    assert "complete body-motion baseline" in html
    assert "Compare tempo, sway and slide" not in html


def test_report_issue_card_leads_with_triggering_swing_not_safe_mean(tmp_path):
    cfg = branded_cfg()
    swings = [fake_swing(1), fake_swing(2)]
    swings[0]["metrics"].head_sway_backswing_sw = 0.50
    swings[1]["metrics"].head_sway_backswing_sw = 0.10
    stats = session_stats([swing["metrics"] for swing in swings])
    out = write_report_html(
        tmp_path / "report.html",
        fake_video(),
        swings,
        stats,
        [],
        "right",
        cfg,
    )
    html = out.read_text(encoding="utf-8")
    assert "worst swing" in html
    assert "0.50 SW" in html
    assert "session mean</span><strong>0.30 SW" not in html


def test_metrics_json_valid_and_nan_becomes_null(tmp_path):
    cfg = Config()
    swing = fake_swing(1, tempo=float("nan"))
    out = write_metrics_json(
        tmp_path / "metrics.json", fake_video(), [swing],
        session_stats([swing["metrics"]]), [], cfg,
    )
    data = json.loads(out.read_text())  # must be strictly valid JSON
    assert data["swings"][0]["metrics"]["tempo_ratio"] is None
    assert data["video"]["width"] == 1080  # rotation-aware display width
    assert data["video"]["height"] == 1920
    assert data["disclaimer"].startswith("Automated estimates")
    assert not math.isnan(data["session_stats"]["backswing_s"]["mean"])
