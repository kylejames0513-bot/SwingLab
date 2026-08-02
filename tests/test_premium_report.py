"""Premium report presentation without changing the report artifact contract."""

from __future__ import annotations

import re
from pathlib import Path

from swinglab.metrics import session_stats
from swinglab.report import write_report_html
from tests.test_report import branded_cfg, fake_swing, fake_video


TEMPLATE = Path("swinglab/templates/report.html.j2")


def render_report(
    tmp_path, *, tempo: float = 2.0, notes=(), sample_banner=None
) -> str:
    cfg = branded_cfg()
    cfg.shop["store_url"] = ""
    swing = fake_swing(1, tempo=tempo)
    output = write_report_html(
        tmp_path / "report.html",
        fake_video(),
        [swing],
        session_stats([swing["metrics"]]),
        list(notes),
        "right",
        cfg,
        club="iron",
        sample_banner=sample_banner,
    )
    return output.read_text(encoding="utf-8")


def test_report_remains_offline_self_contained_and_contract_marked(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    html = render_report(tmp_path)

    assert 'name="caddieinsight-report-format" content="caddie-brief-v1"' in html
    assert 'name="caddieinsight-report-presentation" content="premium-coach-v2"' in html
    assert 'name="caddieinsight-report-outcome" content="coaching_ready"' in html
    assert 'name="caddieinsight-coaching-priority-rule" content="' in html
    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html
    assert "<script" not in html.lower()
    assert "<link" not in html.lower()
    assert 'onclick="window.print()"' in html

    media_sources = re.findall(r'(?:src)="([^"]+)"', html)
    assert media_sources
    assert all(
        not source.startswith(("http://", "https://", "//"))
        for source in media_sources
    )


def test_report_hierarchy_leads_priority_into_evidence_practice_and_refilm(
    tmp_path,
):
    html = render_report(tmp_path)

    assert html.count("<h1") == 1
    assert '<h1 id="report-title">Swing report</h1>' in html
    assert 'class="report-context"' in html
    assert 'class="brief"' in html
    assert 'class="swing-evidence"' in html
    assert 'class="practice-section"' in html
    assert "Matched re-film target" in html
    assert "Matched follow-up" in html
    assert "Pass mark" in html
    assert "Make the follow-up comparable." in html
    assert "same club" in html and "camera angle and height" in html

    hierarchy = (
        html.index("Swing report</h1>"),
        html.index("Your caddie's read"),
        html.index("Swing evidence"),
        html.index("<h2>Start here</h2>"),
        html.index("<h2>Practice plan</h2>"),
    )
    assert hierarchy == tuple(sorted(hierarchy))


def test_report_has_accessible_evidence_tables_media_and_print_css(tmp_path):
    html = render_report(tmp_path)
    source = TEMPLATE.read_text(encoding="utf-8")

    assert 'href="#report-main">Skip to report</a>' in html
    assert 'role="region" aria-label="Swing timing measurements"' in html
    assert 'role="region" aria-label="Body movement measurements"' in html
    assert (
        '<caption class="visually-hidden">Session context and source details</caption>'
        in html
    )
    assert 'aria-label="Slow motion playback for swing 1"' in html
    assert "@media (prefers-reduced-motion: reduce)" in source
    assert "@media print" in source
    assert "print-color-adjust: exact" in source
    assert ".report-actions, .video-row, .replay-locked-note" in source
    assert "details.measurements:not([open]) > *:not(summary)" in source
    assert "break-inside: avoid" in source


def test_report_escapes_untrusted_text_in_premium_sections(tmp_path):
    dangerous = '<script>alert("swing")</script>'
    html = render_report(tmp_path, notes=(dangerous,))

    assert dangerous not in html
    assert "&lt;script&gt;alert(&#34;swing&#34;)&lt;/script&gt;" in html


def test_capture_only_evidence_never_claims_a_coaching_summary(tmp_path):
    html = render_report(
        tmp_path,
        notes=("Tracking was unstable; numbers may be off.",),
    )

    assert 'name="caddieinsight-report-outcome" content="capture_only"' in html
    assert "capture-quality decision above" in " ".join(html.split())
    assert "source views behind the coaching summary" not in html


def test_print_keeps_sample_disclosure_and_uses_contrast_safe_tokens(tmp_path):
    html = render_report(
        tmp_path,
        sample_banner={
            "text": "This is a sample session",
            "cta_label": "Start free",
            "cta_url": "/",
        },
    )
    source = TEMPLATE.read_text(encoding="utf-8")

    assert '<div class="sample-banner">' in html
    assert "This is a sample session" in html
    assert ".sample-banner, .report-actions" not in source
    assert ".sample-banner a { display: none !important; }" in source
    assert "--ink-muted: #5d685f" in source
    assert "--accent-ink: #944600" in source
    assert ".card.sev-major .sev { color: var(--accent-ink); }" in source
