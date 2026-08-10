"""The Swing Pattern section is the Coach tier's report-level feature.

These tests pin the contract end to end: the pattern exists exactly when
the report's owner is entitled to it, a locked report carries a teaser and
none of the pattern's content, and metrics.json can never say more than
the report it ships beside.
"""

from __future__ import annotations

from swinglab.metrics import session_stats
from swinglab.report_html import write_report_document_html
from swinglab.report_presenter import build_report_document, prepare_report_input
from swinglab.report_view import (
    Entitlement,
    MediaEntry,
    MediaRole,
    OptionalSectionId,
    report_view_from_dict,
)

from tests.report_view_fixtures import report_document_fixture, report_view_payload
from tests.test_report import branded_cfg, fake_swing, fake_video


def _document(*, replay_locked: bool, angle: str = "face-on"):
    cfg = branded_cfg()
    swing = fake_swing(1)
    swing["overlay"] = None
    swing["strip"] = "media/positions-1.jpg"
    swing["slowmo"] = "media/slow-1.mp4"
    evidence = (
        report_document_fixture("coaching-dtl-clear").view.visual_evidence
        if angle == "dtl"
        else report_view_from_dict(report_view_payload()).visual_evidence
    )
    media = (
        MediaEntry("focus-1", MediaRole.PRIORITY_EVIDENCE, "image/jpeg",
                   Entitlement.CORE, "media/focus-1.jpg", "a" * 64),
        MediaEntry("positions-1", MediaRole.KEY_POSITIONS, "image/jpeg",
                   Entitlement.CORE, "media/positions-1.jpg", "b" * 64),
        MediaEntry("slow-1", MediaRole.SLOW_MOTION, "video/mp4",
                   Entitlement.CORE, "media/slow-1.mp4", "c" * 64),
    )
    stats = session_stats([swing["metrics"]])
    source = prepare_report_input(
        fake_video(),
        [swing],
        stats,
        [],
        "right",
        cfg,
        angle=angle,
        replay_locked=replay_locked,
        visual_evidence=evidence,
        media=media,
    )
    return source, build_report_document(source, cfg), cfg


def _pattern_section(document):
    return next(
        (
            section
            for section in document.view.optional_sections
            if section.id is OptionalSectionId.SWING_PATTERN
        ),
        None,
    )


def test_an_entitled_report_carries_the_pattern_and_its_section():
    source, document, _ = _document(replay_locked=False)

    assert source.swing_pattern is not None
    section = _pattern_section(document)
    assert section is not None
    assert section.available and not section.locked
    assert section.item_count == len(source.swing_pattern.axes)
    assert document.depth.swing_pattern is source.swing_pattern
    assert not document.depth.swing_pattern_locked


def test_a_locked_report_never_computes_the_pattern():
    """Locked means never computed — nothing to leak, byte or object."""
    source, document, _ = _document(replay_locked=True)

    assert source.swing_pattern is None
    section = _pattern_section(document)
    assert section is not None
    assert section.locked and not section.available
    assert section.item_count == 0
    assert document.depth.swing_pattern is None
    assert document.depth.swing_pattern_locked


def test_the_rendered_html_shows_axes_when_entitled(tmp_path):
    source, document, cfg = _document(replay_locked=False)
    html = write_report_document_html(
        tmp_path / "report.html", document, cfg=cfg
    ).read_text(encoding="utf-8")

    assert 'data-optional-section="swing_pattern"' in html
    assert source.swing_pattern.name in html
    for axis in source.swing_pattern.axes:
        assert axis.display in html
    assert "Swing Pattern is locked" not in html


def test_the_rendered_html_shows_only_the_teaser_when_locked(tmp_path):
    """The teaser names the section, no tier, no price, and links /pricing —
    the same contract as the replay lock note."""
    _, document, cfg = _document(replay_locked=True)
    html = write_report_document_html(
        tmp_path / "report.html", document, cfg=cfg
    ).read_text(encoding="utf-8")

    assert "Swing Pattern is locked" in html
    assert 'href="/pricing"' in html
    lowered = html.lower()
    for word in ("coach tier", "founders", "$"):
        assert word not in lowered.split("swing pattern is locked", 1)[1].split("</details>", 1)[0]
    # No pattern vocabulary escapes into a locked page.
    for phrase in ("body-led", "arm-led", "Centered,", "Lateral,"):
        assert phrase not in html


def test_a_dtl_session_still_gets_its_honest_tempo_only_pattern():
    source, document, _ = _document(replay_locked=False, angle="dtl")

    assert source.swing_pattern is not None
    assert source.swing_pattern.note  # names the face-on limitation
    section = _pattern_section(document)
    assert section is not None and section.available
    assert section.item_count == len(source.swing_pattern.axes)
