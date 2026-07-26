"""Plain-English explainers: full metric coverage, threshold numbers that
track config, reference-not-target framing, and their placement in the
report tables and /progress cards."""

from __future__ import annotations

import pytest

from swinglab.config import Config
from swinglab.explainers import (
    EXPLAINERS,
    SW_GLOSS,
    build_explainers,
)
from swinglab.metrics import NUMERIC_FIELDS


def test_every_metric_has_an_explainer():
    for name in NUMERIC_FIELDS + ("strike_s",):
        e = EXPLAINERS.get(name)
        assert e is not None, name
        assert e.metric == name
        assert e.title
        assert len(e.text) > 60, name       # a real explanation, not a stub
        assert e.unit_gloss, name


def test_sw_gloss_explains_the_unit_everywhere_sw_is_used():
    assert "shoulder-widths" in SW_GLOSS
    assert "camera" in SW_GLOSS  # the point: distance-independent numbers
    for name in ("head_sway_backswing_sw", "hip_slide_backswing_sw",
                 "head_dip_sw", "finish_balance_sw"):
        assert EXPLAINERS[name].unit_gloss == SW_GLOSS


def test_benchmarks_framed_as_references_not_targets():
    tempo = EXPLAINERS["tempo_ratio"]
    assert "reference" in tempo.text
    assert "moving toward it matters more than hitting it" in tempo.text


def test_explainers_track_config_thresholds():
    cfg = Config()
    cfg.coaching["head_dip_warn_sw"] = 0.4
    cfg.coaching["tempo_target"] = 3.2
    built = build_explainers(cfg.coaching)
    assert "0.40" in built["head_dip_sw"].text
    assert "3.2:1" in built["tempo_ratio"].text


def test_report_tables_carry_details_expanders():
    from tests.test_report import branded_cfg, fake_swing, fake_video
    from swinglab.metrics import session_stats
    from swinglab.report import write_report_html
    import tempfile
    from pathlib import Path

    cfg = branded_cfg()
    swings = [fake_swing(1), fake_swing(2)]
    stats = session_stats([s["metrics"] for s in swings])
    out = write_report_html(
        Path(tempfile.mkdtemp()) / "report.html", fake_video(), swings, stats,
        [], "right", cfg,
    )
    html = out.read_text()
    # <details>-based expanders in the table headers, no JS anywhere new.
    assert html.count('<details class="mx">') >= 12
    assert EXPLAINERS["tempo_ratio"].text[:40] in html
    assert SW_GLOSS in html
    assert "Benchmarks are references, not day-one targets" in html


def test_progress_cards_reuse_the_same_strings(tmp_path, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from swinglab.web import jobs as jobs_module
    from swinglab.web.app import create_app
    from tests.test_trends import (
        SESSION_PAYLOADS,
        make_fake_analyze,
        signup,
        upload_and_wait,
    )

    monkeypatch.setattr(
        jobs_module, "analyze_video", make_fake_analyze(SESSION_PAYLOADS)
    )
    cfg = Config()
    cfg.web["require_account"] = True
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    signup(client)
    upload_and_wait(client)
    upload_and_wait(client)
    html = client.get("/progress").text
    assert "What is this?" in html
    assert EXPLAINERS["tempo_ratio"].text[:40] in html
    assert SW_GLOSS in html
