"""The public sample's freshness gate.

The cached ``sample-report/report.html`` lives on the deployed volume and
outlives every code deploy, so the gate in ``swinglab.sample`` — not the
deploy — decides whether a visitor sees the current design or a fossil.

It used to check two version markers. The 2026-08-08 rebrand changed the
guided template's whole palette and type stack without changing anything
schema-level, so there was no version to bump, the gate said "current", and
production served a pre-rebrand cream / system-ui page against the green-grey
app shell for a day. These tests pin the content-addressed replacement: a
changed template regenerates, an unchanged one is left alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from swinglab import sample
from swinglab.config import Config
from swinglab.report import REPORT_FORMAT_VERSION, REPORT_PRESENTATION_VERSION
from swinglab.report_html import GUIDED_TEMPLATE
from swinglab.report_view import GUIDED_REPORT_PRESENTATION_VERSION

_REAL_TEMPLATE_DIR = Path(sample.__file__).parent / "templates"
_SENTINEL = "<!-- cached copy, must survive an unchanged template -->"


def _guided_cfg(tmp_path: Path) -> Config:
    """Shipped-shape config with the guided sample branch selected.

    Only the literal boolean True selects it (ensure_sample_report is
    deliberately strict about truthy strings), so this goes through YAML
    rather than poking cfg.report directly.
    """
    path = tmp_path / "guided.yaml"
    path.write_text("report:\n  guided_sample_enabled: true\n", encoding="utf-8")
    return Config.load(path)


def _edit_template(monkeypatch, tmp_path: Path, template_name: str) -> None:
    """Point the freshness gate at an edited copy of one template.

    Only the *gate* reads ``sample._TEMPLATE_DIR``; both renderers build their
    own Jinja loaders against the real package directory. That split is what
    makes this a clean simulation of "somebody edited the template": the
    signature moves and the rendered HTML does not, so a passing assertion is
    about the cache decision alone and cannot be satisfied by an incidental
    content diff.

    The edit itself is a CSS comment appended to the template — the smallest
    honest stand-in for the rebrand, which was a pile of palette edits inside
    exactly this file with no version change anywhere.
    """
    edited_dir = tmp_path / "edited-templates"
    edited_dir.mkdir(exist_ok=True)
    original = (_REAL_TEMPLATE_DIR / template_name).read_bytes()
    (edited_dir / template_name).write_bytes(
        original + b"\n<!-- --paper: #fffdf8 -> --sl-bg: #eef2ef -->\n"
    )
    monkeypatch.setattr(sample, "_TEMPLATE_DIR", edited_dir)


def _marker_value(html: str) -> str:
    prefix = f'<meta name="{sample.SAMPLE_RENDER_MARKER}" content="'
    assert prefix in html, "generated sample carries no render signature"
    start = html.index(prefix) + len(prefix)
    return html[start : html.index('"', start)]


# -- the marker itself --------------------------------------------------------

@pytest.mark.parametrize("guided", (False, True))
def test_generated_sample_carries_a_render_marker_in_its_head(tmp_path, guided):
    cfg = _guided_cfg(tmp_path) if guided else Config()

    report = sample.ensure_sample_report(tmp_path / "sr", cfg)
    html = report.read_text(encoding="utf-8")

    # In the head, beside the version markers it supersedes — not appended to
    # the end of the document, where a substring check would still pass but
    # the head-window conventions the rest of the codebase relies on break.
    assert html.index(f'name="{sample.SAMPLE_RENDER_MARKER}"') < html.index("</head>")
    assert len(_marker_value(html)) == 32
    expected = sample._render_signature(
        GUIDED_TEMPLATE if guided else sample._LEGACY_TEMPLATE,
        GUIDED_REPORT_PRESENTATION_VERSION if guided else REPORT_PRESENTATION_VERSION,
        cfg,
    )
    assert _marker_value(html) == expected


# -- unchanged template: leave the cache alone --------------------------------

@pytest.mark.parametrize("guided", (False, True))
def test_unchanged_template_leaves_the_cached_sample_untouched(tmp_path, guided):
    cfg = _guided_cfg(tmp_path) if guided else Config()
    sample_dir = tmp_path / "sr"
    first = sample.ensure_sample_report(sample_dir, cfg)
    first.write_text(first.read_text(encoding="utf-8") + _SENTINEL, encoding="utf-8")

    second = sample.ensure_sample_report(sample_dir, cfg)

    # Regenerating on every boot would be its own bug: the sample costs three
    # rendered PNGs and a full report render, and it sits on the startup path.
    assert _SENTINEL in second.read_text(encoding="utf-8")


# -- changed template: regenerate ---------------------------------------------

@pytest.mark.parametrize(
    "guided,template_name",
    ((False, "report.html.j2"), (True, GUIDED_TEMPLATE)),
)
def test_edited_template_regenerates_the_cached_sample(
    tmp_path, monkeypatch, guided, template_name
):
    cfg = _guided_cfg(tmp_path) if guided else Config()
    sample_dir = tmp_path / "sr"
    first = sample.ensure_sample_report(sample_dir, cfg)
    stale_signature = _marker_value(first.read_text(encoding="utf-8"))
    first.write_text(first.read_text(encoding="utf-8") + _SENTINEL, encoding="utf-8")

    _edit_template(monkeypatch, tmp_path, template_name)
    second = sample.ensure_sample_report(sample_dir, cfg)
    html = second.read_text(encoding="utf-8")

    assert _SENTINEL not in html
    assert _marker_value(html) != stale_signature


def test_a_palette_edit_with_no_version_change_is_still_stale(tmp_path):
    """The exact shape of the production bug, stated as an assertion.

    A cached file whose two version markers both match the current constants
    is what the old gate accepted unconditionally. The rebrand produced
    precisely that file. It must now read as stale.
    """
    cfg = Config()
    report = tmp_path / "report.html"
    report.write_text(
        '<meta name="caddieinsight-report-format" '
        f'content="{REPORT_FORMAT_VERSION}">\n'
        '<meta name="caddieinsight-report-presentation" '
        f'content="{REPORT_PRESENTATION_VERSION}">\n'
        f'<meta name="{sample.SAMPLE_RENDER_MARKER}" content="{"0" * 32}">\n'
        "<style>:root { --paper: #fffdf8 }</style>",
        encoding="utf-8",
    )
    current = sample._render_signature(
        sample._LEGACY_TEMPLATE, REPORT_PRESENTATION_VERSION, cfg
    )

    assert not sample._report_is_current(
        report, REPORT_PRESENTATION_VERSION, current
    )
    # ...and the same file with the right digest is current, so the assertion
    # above is failing on the digest and not on some unrelated missing marker.
    assert sample._report_is_current(
        report, REPORT_PRESENTATION_VERSION, "0" * 32
    )


def test_a_report_cached_by_the_old_gate_is_replaced_on_the_next_boot(tmp_path):
    """Deploy-day behaviour, and the reason no manual cache purge is required.

    The copy on the Railway volume was written before this marker existed, so
    it carries the two version markers and nothing else — exactly what the old
    gate produced. It has to read as stale on the first boot after this ships,
    or the fix does not actually reach production until the next edit.
    """
    cfg = _guided_cfg(tmp_path)
    sample_dir = tmp_path / "sr"
    stale = sample.ensure_sample_report(sample_dir, cfg)
    pre_marker_html = "\n".join(
        line
        for line in stale.read_text(encoding="utf-8").splitlines()
        if sample.SAMPLE_RENDER_MARKER not in line
    )
    stale.write_text(pre_marker_html + "\n<!-- pre-rebrand -->", encoding="utf-8")
    assert 'content="guided-report-v1"' in pre_marker_html  # versions still match

    refreshed = sample.ensure_sample_report(sample_dir, cfg)

    html = refreshed.read_text(encoding="utf-8")
    assert "<!-- pre-rebrand -->" not in html
    assert sample.SAMPLE_RENDER_MARKER in html


def test_a_brand_palette_change_in_config_also_regenerates(tmp_path):
    """Brand colours are rendered into the report's CSS custom properties, so
    a config-only rebrand drifts the sample exactly the way a template edit
    does — and would otherwise be cached forever."""
    sample_dir = tmp_path / "sr"
    first = sample.ensure_sample_report(sample_dir, Config())
    first.write_text(first.read_text(encoding="utf-8") + _SENTINEL, encoding="utf-8")

    rebranded = Config()
    rebranded.brand["primary_color"] = "#0f3d28"
    second = sample.ensure_sample_report(sample_dir, rebranded)

    html = second.read_text(encoding="utf-8")
    assert _SENTINEL not in html
    assert "#0f3d28" in html


# -- the signature function itself --------------------------------------------

def test_render_signature_is_stable_and_tracks_a_single_byte(
    tmp_path, monkeypatch
):
    cfg = Config()

    def signature() -> str:
        return sample._render_signature(
            GUIDED_TEMPLATE, GUIDED_REPORT_PRESENTATION_VERSION, cfg
        )

    baseline = signature()
    assert signature() == baseline  # pure function of its inputs

    edited_dir = tmp_path / "one-byte"
    edited_dir.mkdir()
    (edited_dir / GUIDED_TEMPLATE).write_bytes(
        (_REAL_TEMPLATE_DIR / GUIDED_TEMPLATE).read_bytes() + b" "
    )
    monkeypatch.setattr(sample, "_TEMPLATE_DIR", edited_dir)
    assert signature() != baseline


def test_the_two_branches_cannot_share_a_signature(tmp_path):
    """Legacy and guided both cache to the same report.html path, so a
    collision would let one branch's output satisfy the other's gate."""
    cfg = Config()

    assert sample._render_signature(
        sample._LEGACY_TEMPLATE, REPORT_PRESENTATION_VERSION, cfg
    ) != sample._render_signature(
        GUIDED_TEMPLATE, GUIDED_REPORT_PRESENTATION_VERSION, cfg
    )


def test_a_missing_template_fails_loudly_rather_than_freezing_the_cache(
    tmp_path, monkeypatch
):
    """A degraded constant digest here would freeze the cache permanently —
    the same invisible failure this gate exists to end."""
    monkeypatch.setattr(sample, "_TEMPLATE_DIR", tmp_path / "does-not-exist")

    with pytest.raises(OSError):
        sample._render_signature(
            GUIDED_TEMPLATE, GUIDED_REPORT_PRESENTATION_VERSION, Config()
        )


# -- the constraint this fix had to work around -------------------------------

def test_the_guided_presentation_version_is_not_bumped_by_this_fix():
    """``report_html.write_report_document_html`` and the frozen
    ``ReportPresentationVersion`` enum both key on this literal, and every
    persisted report view on the volume already carries it. The freshness
    gate is content-addressed precisely so it never needs this to move."""
    assert GUIDED_REPORT_PRESENTATION_VERSION == "guided-report-v1"
