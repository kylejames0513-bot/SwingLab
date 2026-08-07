from __future__ import annotations

from pathlib import Path
import re

import pytest

from tests.report_view_fixtures import (
    GUIDED_DOCUMENT_QA_FIXTURE_NAMES,
    report_document_fixture,
)
from tests.test_guided_report_accessibility import GuidedReportAudit


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_FIXTURES = ("guided-sample-preview", "legacy-sample-default")


def _relative_files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }


def _assert_structurally_readable(
    report: Path, *, guided: bool = True, expect_status: bool = True
) -> None:
    audit = GuidedReportAudit()
    audit.feed(report.read_text(encoding="utf-8"))

    assert audit.headings.count(1) == 1
    assert audit.main_count == 1
    assert audit.landmarks.get("main") == 1
    if guided:
        assert audit.landmarks.get("header") == 1
    else:
        assert audit.landmarks.get("header", 0) >= 1
    assert all(
        current <= previous + 1
        for previous, current in zip(audit.headings, audit.headings[1:])
    )
    assert audit.ids and all(audit.ids)
    assert len(audit.ids) == len(set(audit.ids))
    assert audit.image_alts and all(alt and alt.strip() for alt in audit.image_alts)
    assert audit.details_count == len(audit.disclosure_summaries)
    assert all(child == "summary" for child in audit.details_first_children)
    assert all(
        "".join(summary["text"]).strip()
        or str(summary["aria_label"] or "").strip()
        for summary in audit.disclosure_summaries
    )
    assert audit.controls
    assert all(
        "".join(control["text"]).strip()
        or str(control["aria_label"] or "").strip()
        for control in audit.controls
    )
    assert all(
        control["tag"] != "a" or str(control["href"] or "").strip()
        for control in audit.controls
    )
    if expect_status:
        assert audit.statuses
    assert all(item["hidden_icon"] for item in audit.statuses)
    assert all("".join(item["text"]).strip() for item in audit.statuses)


def test_qa_script_renders_only_declared_fixture_files(tmp_path: Path) -> None:
    from scripts.render_guided_report_qa import main

    output = tmp_path / "guided-report-qa"

    assert main(["--output", str(output)]) == 0

    expected_names = {*GUIDED_DOCUMENT_QA_FIXTURE_NAMES, *SAMPLE_FIXTURES}
    assert {path.name for path in output.iterdir()} == expected_names
    assert set(tmp_path.iterdir()) == {output}

    for name in GUIDED_DOCUMENT_QA_FIXTURE_NAMES:
        fixture_root = output / name
        document = report_document_fixture(name)
        expected_files = {
            "report.html",
            *(entry.relative_path for entry in document.media_by_key.values()),
        }
        assert _relative_files(fixture_root) == expected_files
        for entry in document.media_by_key.values():
            if entry.mime_type == "video/mp4":
                payload = (fixture_root / entry.relative_path).read_bytes()
                assert payload[4:8] == b"ftyp"
                assert b"avc1" in payload
        html = (fixture_root / "report.html").read_text(encoding="utf-8")
        declared_sources = set(re.findall(r'\b(?:src|poster)="([^"]+)"', html))
        assert declared_sources <= expected_files
        _assert_structurally_readable(
            fixture_root / "report.html",
            expect_status=not name.startswith("capture-only-"),
        )

    guided = output / "guided-sample-preview"
    assert _relative_files(guided) == {
        "report.html",
        "media/focused-priority.png",
        "media/strip_s1.png",
        "media/strip_s2.png",
        "media/strip_s3.png",
    }
    guided_html = (guided / "report.html").read_text(encoding="utf-8")
    assert 'content="guided-report-v1"' in guided_html
    _assert_structurally_readable(guided / "report.html")

    legacy = output / "legacy-sample-default"
    assert _relative_files(legacy) == {
        "report.html",
        "media/overlay_s1.png",
        "media/overlay_s2.png",
        "media/overlay_s3.png",
        "media/strip_s1.png",
        "media/strip_s2.png",
        "media/strip_s3.png",
    }
    legacy_html = (legacy / "report.html").read_text(encoding="utf-8")
    assert 'content="premium-coach-v2"' in legacy_html
    _assert_structurally_readable(
        legacy / "report.html", guided=False, expect_status=False
    )


def test_qa_script_requires_explicit_non_repository_output(
    tmp_path: Path, capsys
) -> None:
    from scripts.render_guided_report_qa import main

    with pytest.raises(SystemExit):
        main([])
    assert "the following arguments are required: --output" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["--output", str(REPOSITORY_ROOT)])
    assert "repository root" in capsys.readouterr().err

    assert not (tmp_path / "guided-report-qa").exists()


def test_qa_script_refuses_a_nonempty_output_directory(
    tmp_path: Path, capsys
) -> None:
    from scripts.render_guided_report_qa import main

    output = tmp_path / "guided-report-qa"
    output.mkdir()
    marker = output / "belongs-to-reviewer.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(SystemExit):
        main(["--output", str(output)])

    assert "must be empty" in capsys.readouterr().err
    assert marker.read_text(encoding="utf-8") == "keep"
