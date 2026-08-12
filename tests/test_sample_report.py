"""The public sample report: generated at startup from synthetic session
data through the real report machinery, served with no auth, honest about
being a sample, and advertised from the landing page."""

from __future__ import annotations

from pathlib import Path
import re

import numpy as np
import pytest
from PIL import Image
from scipy import ndimage

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from swinglab import sample
from swinglab.config import Config
from swinglab.web.app import create_app


def _config_with_guided_sample(tmp_path, value: str = "true") -> Config:
    path = tmp_path / f"guided-{value.replace(chr(39), 'quote')}.yaml"
    path.write_text(
        f"report:\n  guided_sample_enabled: {value}\n",
        encoding="utf-8",
    )
    return Config.load(path)


def test_ensure_sample_report_writes_report_and_media(tmp_path):
    path = sample.ensure_sample_report(tmp_path / "sr", Config())
    assert path.is_file()
    media = sorted(p.name for p in (tmp_path / "sr" / "media").iterdir())
    assert media == [
        "overlay_s1.png", "overlay_s2.png", "overlay_s3.png",
        "strip_s1.png", "strip_s2.png", "strip_s3.png",
    ]
    html = path.read_text(encoding="utf-8")
    assert 'content="caddie-brief-v1"' in html
    assert 'content="premium-coach-v2"' in html
    # The banner says what this is, and where signup lives.
    assert sample.BANNER_TEXT in html
    assert 'href="/"' in html
    # Three swings, tempo + head sway flagged, through the REAL machinery:
    assert html.count("media/strip_s") == 3
    assert "Start here" in html
    assert "Head sway" in html and "Tempo" in html
    # The praise strip has content (most metrics are in range by design).
    assert "What&#39;s working" in html or "What's working" in html
    # No synthetic footage is faked — video sections are simply absent.
    assert "<video" not in html
    assert "Slow motion" not in html


def test_shipped_config_generates_the_guided_public_sample(tmp_path):
    cfg = Config.load(Path(__file__).resolve().parents[1] / "config.yaml")

    path = sample.ensure_sample_report(tmp_path / "shipped", cfg)
    html = path.read_text(encoding="utf-8")

    assert 'content="guided-report-v1"' in html
    assert (path.parent / "media" / "focused-priority.png").is_file()
    assert "Understand" in html
    assert "Practice" in html
    assert "Re-film" in html


def test_ensure_sample_report_is_idempotent(tmp_path):
    first = sample.ensure_sample_report(tmp_path / "sr", Config())
    marker = "<!-- untouched -->"
    first.write_text(first.read_text() + marker)
    second = sample.ensure_sample_report(tmp_path / "sr", Config())
    assert second == first
    assert marker in second.read_text()  # existing report left alone


def test_ensure_sample_report_refreshes_only_an_old_synthetic_format(tmp_path):
    sample_dir = tmp_path / "sr"
    sample_dir.mkdir()
    report = sample_dir / "report.html"
    report.write_text("<html>old synthetic sample</html>", encoding="utf-8")

    refreshed = sample.ensure_sample_report(sample_dir, Config())
    html = refreshed.read_text(encoding="utf-8")
    assert "old synthetic sample" not in html
    assert 'name="caddieinsight-report-format" content="caddie-brief-v1"' in html
    assert "Your caddie's read" in html


def test_ensure_sample_report_refreshes_an_old_presentation_only(tmp_path):
    sample_dir = tmp_path / "sr"
    sample_dir.mkdir()
    report = sample_dir / "report.html"
    report.write_text(
        '<meta name="caddieinsight-report-format" content="caddie-brief-v1">'
        "<p>schema-compatible old presentation</p>",
        encoding="utf-8",
    )

    refreshed = sample.ensure_sample_report(sample_dir, Config())
    html = refreshed.read_text(encoding="utf-8")
    assert "schema-compatible old presentation" not in html
    assert (
        'name="caddieinsight-report-presentation" '
        'content="premium-coach-v2"'
    ) in html
    assert 'class="report-intro"' in html


def test_sample_report_route_is_public(tmp_path):
    cfg = Config()
    cfg.web["require_account"] = True  # locked-down instance...
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    # ...but the sample needs no login.
    resp = client.get("/sample-report", follow_redirects=True)
    assert resp.status_code == 200
    assert sample.BANNER_TEXT in resp.text
    media = client.get("/sample-report/media/strip_s1.png")
    assert media.status_code == 200
    assert media.content[:4] == b"\x89PNG"


def test_sample_report_route_blocks_traversal(tmp_path):
    client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s"))
    assert client.get("/sample-report/../swinglab.db").status_code == 404
    assert client.get("/sample-report/nope.html").status_code == 404


def test_landing_page_advertises_sample_and_free_tier(tmp_path):
    cfg = Config()
    cfg.web["require_account"] = True
    client = TestClient(create_app(cfg, sessions_dir=tmp_path / "s"))
    html = client.get("/").text  # logged-out landing
    assert "Explore the sample report" in html
    assert "/sample-report/" in html
    assert "No card" in html

    open_client = TestClient(create_app(Config(), sessions_dir=tmp_path / "s2"))
    upload_html = open_client.get("/").text  # open-mode hero
    assert "See a sample report first" in upload_html
    assert "Build your swing baseline" in upload_html
    assert 'id="fast" name="fast" type="checkbox" checked' in upload_html


def test_sample_uses_branded_config(tmp_path):
    from tests.test_report import branded_cfg

    path = sample.ensure_sample_report(tmp_path / "sr", branded_cfg())
    html = path.read_text()
    assert "AceCoach" in html and "CaddieInsight" not in html and "SwingLab" not in html
    assert "#123456" in html  # branded primary color


def test_explicit_guided_sample_uses_focused_visual_and_collapsed_depth(
    tmp_path,
):
    sample_dir = tmp_path / "guided"

    path = sample.build_guided_sample_report(sample_dir, Config())
    html = path.read_text(encoding="utf-8")
    media = sorted(item.name for item in (sample_dir / "media").iterdir())

    assert media == [
        "focused-priority.png",
        "strip_s1.png",
        "strip_s2.png",
        "strip_s3.png",
    ]
    assert 'content="caddie-brief-v1"' in html
    assert 'content="guided-report-v1"' in html
    assert html.count("media/focused-priority.png") == 1
    assert html.count("media/strip_s") == 3
    assert "overlay_s" not in html
    assert not list((sample_dir / "media").glob("overlay_s*.png"))
    assert sample.BANNER_TEXT in html
    for label in ("Your next move", "Understand", "Practice", "Re-film"):
        assert label in html
    assert len(re.findall(r'data-field="priority">Head sway \(backswing\)<', html)) == 1
    assert len(re.findall(r'data-field="drill-name">[^<]+<', html)) == 1
    assert len(re.findall(r'data-field="pass-mark">[^<]+<', html)) == 1
    assert "Stick outside the trail foot" in html
    assert "Keep the session at or below 0.35 shoulder widths." in html
    assert "<video" not in html
    every_swing = re.search(
        r'<details class="optional-card" '
        r'data-optional-section="every_swing"(?P<attrs>[^>]*)>',
        html,
    )
    assert every_swing is not None
    assert "open" not in every_swing.group("attrs")
    assert html.index("media/focused-priority.png") < html.index("media/strip_s1.png")


def test_focused_sample_art_is_square_solid_and_uses_only_approved_marks(
    tmp_path, monkeypatch
):
    def fail_if_skeleton_is_used(*args, **kwargs):
        raise AssertionError("focused sample must not use the skeleton renderer")

    monkeypatch.setattr(sample, "draw_skeleton", fail_if_skeleton_is_used)
    out_path = sample.draw_sample_focused_evidence(
        tmp_path / "focused.png", Config()
    )

    with Image.open(out_path) as image:
        assert image.width == image.height
        assert image.width >= 640
        pixels = np.asarray(image.convert("RGB"))
        colors = image.convert("RGB").getcolors(maxcolors=image.width * image.height)
    assert colors is not None
    counts = {color: count for count, color in colors}
    # Field-side marks: what the engine MEASURED reads as paper, the
    # reference it is compared against as the lit steel trace. This art lands
    # on a frame, so the field's rule applies rather than the paper page's.
    assert counts.get((242, 242, 243), 0) > 100  # observed — paper
    assert counts.get((148, 188, 227), 0) > 100  # starting reference — trace

    def component_sizes(color: tuple[int, int, int]) -> list[int]:
        mask = np.all(pixels == color, axis=2)
        components, count = ndimage.label(mask)
        return sorted(
            np.bincount(components.ravel())[1 : count + 1].tolist(),
            reverse=True,
        )

    silhouette = component_sizes((93, 107, 98))
    boundary = component_sizes((102, 117, 108))
    reference = component_sizes((148, 188, 227))
    assert silhouette and silhouette[0] > 150_000
    assert len(boundary) >= 8 and all(size >= 100 for size in boundary[:8])
    assert reference and reference[0] < 10_000


def test_guided_sample_evidence_copy_is_explicit_and_not_an_ideal_pose(
    tmp_path,
):
    html = sample.build_guided_sample_report(
        tmp_path / "guided", Config()
    ).read_text(encoding="utf-8")
    image = re.search(
        r'<img src="media/focused-priority\.png" alt="([^"]+)">', html
    )

    assert image is not None
    alt = image.group(1).lower()
    assert "sample illustration" in alt
    # The alt text names the colours, so it is part of the palette: it has to
    # describe the marks that are actually drawn.
    assert "white head marker" in alt
    assert "pale blue starting zone" in alt
    assert "dashed coaching boundary" in alt
    for banned in ("ideal", "perfect pose", "corrected body"):
        assert banned not in alt


def test_guided_sample_activation_is_strict_and_rolls_back_to_legacy(
    tmp_path,
):
    sample_dir = tmp_path / "public-sample"
    enabled = _config_with_guided_sample(tmp_path, "true")

    guided = sample.ensure_sample_report(sample_dir, enabled)
    guided_html = guided.read_text(encoding="utf-8")
    assert 'content="guided-report-v1"' in guided_html
    assert "media/focused-priority.png" in guided_html
    focused = sample_dir / "media" / "focused-priority.png"
    assert focused.is_file()

    rolled_back = sample.ensure_sample_report(sample_dir, Config())
    legacy_html = rolled_back.read_text(encoding="utf-8")
    assert 'content="premium-coach-v2"' in legacy_html
    assert "media/focused-priority.png" not in legacy_html
    assert not focused.exists()


def test_failed_guided_switch_keeps_legacy_report_and_its_media(
    tmp_path, monkeypatch
):
    sample_dir = tmp_path / "public-sample"
    legacy = sample.build_legacy_sample_report(sample_dir, Config())
    before = legacy.read_bytes()

    def fail_guided_render(*args, **kwargs):
        raise RuntimeError("synthetic guided render failed")

    monkeypatch.setattr(sample, "write_report_document_html", fail_guided_render)

    with pytest.raises(RuntimeError, match="guided render failed"):
        sample.build_guided_sample_report(sample_dir, Config())

    assert legacy.read_bytes() == before
    assert all(
        (sample_dir / "media" / f"overlay_s{swing}.png").is_file()
        for swing in (1, 2, 3)
    )


def test_guided_switch_keeps_failed_overlay_cleanup_outside_public_root(
    tmp_path, monkeypatch
):
    sample_dir = tmp_path / "public-sample"
    sample.build_legacy_sample_report(sample_dir, Config())
    original_unlink = Path.unlink

    def fail_staged_overlay_cleanup(path: Path, *args, **kwargs):
        if "legacy-overlays" in path.parent.name:
            raise PermissionError("injected backup cleanup failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_staged_overlay_cleanup)

    guided = sample.build_guided_sample_report(sample_dir, Config())

    assert 'content="guided-report-v1"' in guided.read_text(encoding="utf-8")
    assert not list(sample_dir.rglob("overlay_s*.png"))
    staged_backups = list(tmp_path.glob(".*-legacy-overlays-*/overlay_s*.png"))
    assert len(staged_backups) == 3


@pytest.mark.parametrize("value", ("'true'", "1", "'1'"))
def test_truthy_non_boolean_sample_flags_keep_legacy_output(tmp_path, value):
    cfg = _config_with_guided_sample(tmp_path, value)

    report = sample.ensure_sample_report(tmp_path / f"sample-{value}", cfg)

    assert 'content="premium-coach-v2"' in report.read_text(encoding="utf-8")


def test_guided_sample_generation_is_byte_for_byte_idempotent(tmp_path):
    sample_dir = tmp_path / "guided"
    first = sample.build_guided_sample_report(sample_dir, Config())
    marker = "<!-- guided untouched -->"
    first.write_text(first.read_text(encoding="utf-8") + marker, encoding="utf-8")

    second = sample.build_guided_sample_report(sample_dir, Config())

    assert second == first
    assert marker in second.read_text(encoding="utf-8")
