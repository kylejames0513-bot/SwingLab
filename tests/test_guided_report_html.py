from __future__ import annotations

from dataclasses import fields, replace
from pathlib import Path
import re

import pytest

from swinglab.config import Config
from swinglab.report import REPORT_FORMAT_VERSION, write_report_html
from swinglab.report_presenter import (
    ReportDepthContent,
    ReportDocument,
    SwingDetail,
)
from swinglab.report_view import GUIDED_REPORT_PRESENTATION_VERSION
from tests.report_view_fixtures import report_document_fixture
from tests.test_report import fake_swing, fake_video


def field_names(model: type) -> set[str]:
    return {field.name for field in fields(model)}


def render_fixture_path(
    tmp_path: Path, name: str, *, sample_banner: dict | None = None
) -> Path:
    from swinglab.report_html import write_report_document_html

    out_path = tmp_path / f"{name}.html"
    return write_report_document_html(
        out_path,
        report_document_fixture(name),
        cfg=Config(),
        sample_banner=sample_banner,
    )


def render_fixture(
    tmp_path: Path, name: str, *, sample_banner: dict | None = None
) -> str:
    return render_fixture_path(
        tmp_path, name, sample_banner=sample_banner
    ).read_text(encoding="utf-8")


def test_report_document_exposes_all_server_owned_html_depth():
    assert field_names(ReportDocument) == {"view", "depth", "media_by_key"}
    assert {
        "swings", "secondary_findings", "strengths", "measurements",
        "session_details", "glossary", "limitations", "gear", "navigation",
    } <= field_names(ReportDepthContent)
    assert {
        "key_positions_media_key", "key_positions_alt_text",
        "slow_motion_media_key", "slow_motion_caption",
        "coach_replay_media_key", "coach_replay_caption",
        "video_poster_media_key", "video_poster_alt_text",
        "print_playback_reference", "replay_locked",
        "locked_replay_explanation",
    } <= field_names(SwingDetail)


def test_guided_writer_emits_contract_markers_outcome_and_focused_media(tmp_path: Path):
    document = report_document_fixture()

    html = render_fixture(tmp_path, "coaching-improve-clear")

    header = html.encode("utf-8")[:8192]
    assert len(re.findall(
        rf'<meta\s+name="caddieinsight-report-format"\s+content="{REPORT_FORMAT_VERSION}">',
        header.decode("utf-8"),
    )) == 1
    assert len(re.findall(
        r'<meta\s+name="caddieinsight-report-presentation"\s+content="guided-report-v1">',
        header.decode("utf-8"),
    )) == 1
    assert len(re.findall(
        r'<meta\s+name="caddieinsight-report-outcome"\s+content="coaching_ready">',
        header.decode("utf-8"),
    )) == 1
    assert document.view.presentation_version == GUIDED_REPORT_PRESENTATION_VERSION
    assert document.view.visual_evidence is not None
    assert document.view.visual_evidence.media_key is not None
    assert document.media_by_key[document.view.visual_evidence.media_key].relative_path in html

    legacy_path = write_report_html(
        tmp_path / "legacy.html",
        fake_video(),
        [fake_swing(1, tempo=2.0)],
        {},
        [],
        "right",
        Config(),
    )
    assert 'name="caddieinsight-report-presentation" content="premium-coach-v2"' in legacy_path.read_text(encoding="utf-8")


def test_guided_writer_rejects_unknown_media_before_writing(tmp_path: Path):
    from swinglab.report_html import write_report_document_html

    document = report_document_fixture()
    assert document.view.visual_evidence is not None
    broken_view = replace(
        document.view,
        visual_evidence=replace(
            document.view.visual_evidence,
            media_key="missing-priority",
        ),
    )
    broken_document = replace(document, view=broken_view)
    out_path = tmp_path / "broken.html"

    with pytest.raises(
        ValueError,
        match="^guided report references unknown media key: missing-priority$",
    ):
        write_report_document_html(out_path, broken_document, cfg=Config())

    assert not out_path.exists()
