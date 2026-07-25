"""Drill library validation and the report's practice-plan rendering:
every coaching flag has complete drills, re-film targets are measurable and
track the config thresholds, and the right drill set lands in report.html
(with the gear link inert when shop.store_url is empty)."""

from __future__ import annotations

import re
from pathlib import Path

from swinglab.coaching import (
    FLAG_CONSISTENCY,
    FLAG_HIP_SLIDE,
    FLAG_SWAY,
    FLAG_TEMPO,
    session_flags,
)
from swinglab.config import Config
from swinglab.drills import (
    CLEAN,
    DRILLS,
    build_drills,
    gear_shop_url,
    practice_plan,
)
from swinglab.ffmpeg import VideoInfo
from swinglab.metrics import SwingMetrics, session_stats
from swinglab.report import write_report_html

ALL_KEYS = (FLAG_TEMPO, FLAG_SWAY, FLAG_HIP_SLIDE, "head-dip",
            "arm-extension", "balance", FLAG_CONSISTENCY, CLEAN)


def fake_video() -> VideoInfo:
    return VideoInfo(
        path=Path("swing.mov"),
        duration_s=20.0,
        width=1920,
        height=1080,
        fps=29.97,
        rotation=0,
        creation_time=None,
        has_audio=True,
    )


def fake_swing(n: int, tempo: float = 2.9, sway: float = 0.2,
               slide: float = 0.15) -> dict:
    m = SwingMetrics(
        swing=n,
        strike_s=3.0 * n,
        backswing_s=0.9,
        downswing_s=0.31,
        tempo_ratio=tempo,
        head_sway_backswing_sw=sway,
        head_sway_downswing_sw=-0.1,
        hip_slide_backswing_sw=slide,
        hip_slide_downswing_sw=-0.2,
        target_direction=1,
    )
    return {
        "metrics": m,
        "notes": ["note"],
        "strip": f"media/strip_s{n}.png",
        "overlay": f"media/overlay_s{n}.png",
        "slowmo": f"media/slowmo_s{n}.mp4",
    }


def render_report(tmp_path, swings, cfg) -> str:
    stats = session_stats([s["metrics"] for s in swings])
    out = write_report_html(
        tmp_path / "report.html", fake_video(), swings, stats, [], "right", cfg
    )
    return out.read_text()


# -- library completeness ----------------------------------------------------

def test_every_flag_key_has_two_to_three_drills():
    assert set(DRILLS) == set(ALL_KEYS)
    for key in ALL_KEYS:
        assert 2 <= len(DRILLS[key]) <= 3


def test_every_drill_is_complete():
    seen_ids = set()
    for drills in DRILLS.values():
        for d in drills:
            for field in ("id", "name", "aim", "dosage", "success_metric",
                          "gear_tag"):
                value = getattr(d, field)
                assert isinstance(value, str) and value.strip(), (d.id, field)
            assert d.id not in seen_ids  # ids are unique across the library
            seen_ids.add(d.id)
            assert 3 <= len(d.protocol) <= 4, d.id
            assert all(isinstance(s, str) and s.strip() for s in d.protocol)
            assert d.gear_tag.startswith("swinglab:"), d.id


def test_success_metrics_mention_a_number():
    for drills in DRILLS.values():
        for d in drills:
            assert re.search(r"\d", d.success_metric), d.id


def test_refilm_targets_track_config_thresholds():
    coach = dict(Config().coaching)
    coach["sway_warn_sw"] = 0.5
    rebuilt = build_drills(coach)
    assert any("0.50" in d.success_metric for d in rebuilt[FLAG_SWAY])
    # defaults use the shipped thresholds
    assert any("0.35" in d.success_metric for d in DRILLS[FLAG_SWAY])
    assert any("2.4" in d.success_metric for d in DRILLS[FLAG_TEMPO])


# -- plan selection ----------------------------------------------------------

def test_practice_plan_selects_fired_flags_in_order():
    plan = practice_plan([FLAG_TEMPO, FLAG_SWAY], Config())
    assert [b["flag"] for b in plan] == [FLAG_TEMPO, FLAG_SWAY]
    assert all(b["title"] and b["drills"] for b in plan)


def test_practice_plan_falls_back_to_clean():
    assert [b["flag"] for b in practice_plan([], Config())] == [CLEAN]
    # unknown flags are skipped, not rendered
    assert [b["flag"] for b in practice_plan(["nonsense"], Config())] == [CLEAN]


def test_session_flags_feed_the_plan():
    cfg = Config()
    swings = [fake_swing(1, tempo=2.0), fake_swing(2, sway=0.5)]
    stats = session_stats([s["metrics"] for s in swings])
    flags = session_flags([s["metrics"] for s in swings], stats, cfg)
    assert FLAG_TEMPO in flags and FLAG_SWAY in flags


# -- report rendering --------------------------------------------------------

def test_flagged_session_renders_matching_drills(tmp_path):
    cfg = Config()
    html = render_report(tmp_path, [fake_swing(1, tempo=2.0)], cfg)
    assert "Practice plan" in html
    for d in DRILLS[FLAG_TEMPO]:
        assert d.name in html
        assert d.dosage in html
    assert DRILLS[FLAG_TEMPO][0].success_metric in html
    # only the fired flag's set — no maintenance block on a flagged session
    assert DRILLS[CLEAN][0].name not in html


def test_clean_session_renders_maintenance_set(tmp_path):
    cfg = Config()
    html = render_report(tmp_path, [fake_swing(1)], cfg)
    assert "Practice plan" in html
    for d in DRILLS[CLEAN]:
        assert d.name in html
    assert DRILLS[FLAG_TEMPO][0].name not in html


def test_gear_link_present_when_store_url_set(tmp_path):
    cfg = Config()
    cfg.shop["store_url"] = "https://example.myshopify.com/"
    html = render_report(tmp_path, [fake_swing(1)], cfg)
    assert "Matched training aids" in html
    assert "https://example.myshopify.com/collections/swinglab-gear" in html


def test_report_fine_without_store_url(tmp_path):
    cfg = Config()  # defaults ship with store_url empty
    assert gear_shop_url(cfg) is None
    html = render_report(tmp_path, [fake_swing(1)], cfg)
    assert "Matched training aids" not in html
    assert "Practice plan" in html
