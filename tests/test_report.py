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


def test_report_html_reflects_branding_and_content(tmp_path):
    cfg = branded_cfg()
    swings = [fake_swing(1), fake_swing(2), fake_swing(3)]
    stats = session_stats([s["metrics"] for s in swings])
    out = write_report_html(
        tmp_path / "report.html", fake_video(), swings, stats,
        ["Tempo is impressively consistent across swings."], "right", cfg,
    )
    html = out.read_text()
    assert "AceCoach" in html and "SwingLab" not in html
    assert "#123456" in html and "#abcdef" in html
    assert "AceCoach footer line" in html
    assert cfg.brand["disclaimer"][:40] in html
    assert "Filming tips" in html and "hip height" in html and "on a tee" in html
    assert html.count("media/strip_s") == 3  # one row of deliverables per swing
    assert html.count("media/slowmo_s") == 3
    assert "mean ± std" in html
    assert "rotation metadata: 90°" in html


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
