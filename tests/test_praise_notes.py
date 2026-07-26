"""Praise notes ("What's working") and the one-thing-first report band:
every in-range metric gets a short positive line, zero in-range metrics get
silence (never fake praise), and the report leads with a single Start-here
card while deferring the rest behind a <details>."""

from __future__ import annotations

import tempfile
from pathlib import Path

from swinglab.coaching import praise_notes
from swinglab.config import Config
from swinglab.metrics import SwingMetrics, session_stats
from tests.test_metrics_depth import all_flags_metrics, make_metrics
from tests.test_report import branded_cfg, fake_video


def test_clean_session_praises_every_measured_family():
    ms = [make_metrics(1), make_metrics(2)]  # everything in range, std 0
    notes = praise_notes(ms, Config())
    text = " ".join(notes)
    assert len(notes) == 8  # 7 metric families + tempo consistency
    assert "Head sway" in text
    assert "Tempo holds" in text
    assert "Hip slide" in text
    assert "Head dip" in text
    assert "Lead arm" in text
    assert "Shoulder tilt" in text
    assert "Finish drift" in text
    assert "consistent swing to swing" in text


def test_all_flagged_session_gets_zero_praise():
    ms = all_flags_metrics()  # every family breached at least once
    assert praise_notes(ms, Config()) == []


def test_unmeasured_metrics_are_never_praised():
    # A legacy-shaped swing: only timing + sway measured, the rest NaN.
    nan = float("nan")
    ms = [make_metrics(
        1,
        head_dip_sw=nan, lead_arm_angle_deg=nan,
        shoulder_tilt_impact_deg=nan, shoulder_tilt_delta_deg=nan,
        finish_balance_sw=nan,
    )]
    notes = praise_notes(ms, Config())
    text = " ".join(notes)
    assert "Head dip" not in text and "Lead arm" not in text
    assert "Finish drift" not in text and "Shoulder tilt" not in text
    assert "Tempo holds" in text          # measured + in range: praised
    assert "consistent" not in text       # one swing: no consistency claim


def test_single_breach_kills_that_familys_praise():
    ms = [make_metrics(1), make_metrics(2, head_sway_backswing_sw=0.5)]
    text = " ".join(praise_notes(ms, Config()))
    assert "Head sway" not in text  # one bad swing = not in range
    assert "Tempo holds" in text


def test_praise_uses_worst_value_honestly():
    ms = [
        make_metrics(1, head_sway_backswing_sw=0.10),
        make_metrics(2, head_sway_backswing_sw=0.31),
    ]
    notes = praise_notes(ms, Config())
    sway_note = next(n for n in notes if "Head sway" in n)
    assert "0.31" in sway_note  # the peak, not the flattering mean


def test_praise_tracks_config_thresholds():
    cfg = Config()
    cfg.coaching["sway_warn_sw"] = 0.05  # retuned: 0.1 SW now breaches
    ms = [make_metrics(1)]
    text = " ".join(praise_notes(ms, cfg))
    assert "Head sway" not in text and "Hip slide" not in text


# ------------------------------------------------------- report structure


def _render(swings) -> str:
    cfg = branded_cfg()
    stats = session_stats([s["metrics"] for s in swings])
    from swinglab.report import write_report_html

    out = write_report_html(
        Path(tempfile.mkdtemp()) / "report.html", fake_video(), swings, stats,
        [], "right", cfg,
    )
    return out.read_text()


def _swing_dict(m: SwingMetrics) -> dict:
    from swinglab.coaching import swing_notes

    return {
        "metrics": m,
        "notes": swing_notes(m, Config()),
        "strip": f"media/strip_s{m.swing}.png",
        "overlay": f"media/overlay_s{m.swing}.png",
        "slowmo": f"media/slowmo_s{m.swing}.mp4",
        "replay": None,
    }


def test_report_leads_with_one_card_and_defers_the_rest():
    ms = all_flags_metrics()  # 8 flags -> 8 cards
    html = _render([_swing_dict(m) for m in ms])
    assert "Start here" in html
    assert "Fix one thing at a time. This week:" in html
    assert "More to work on later (7)" in html
    assert 'class="card start-here' in html or "start-here" in html
    # Every deferred card's content is preserved, just inside a <details>.
    assert html.count('<div class="card sev-') == 8


def test_report_single_issue_has_no_deferred_section():
    html = _render([_swing_dict(make_metrics(1, head_dip_sw=0.4))])
    assert "Start here" in html
    assert "More to work on later" not in html


def test_report_praise_strip_present_only_when_earned():
    clean_html = _render([_swing_dict(make_metrics(1))])
    assert "What's working" in clean_html
    assert "Nothing flagged this session" in clean_html

    flagged_html = _render([_swing_dict(m) for m in all_flags_metrics()])
    # all_flags_metrics breaches every family -> zero praise -> no strip.
    assert "What's working" not in flagged_html
