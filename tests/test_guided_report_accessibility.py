from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re

import pytest

from tests.test_guided_report_html import render_fixture


class GuidedReportAudit(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headings: list[int] = []
        self.main_count = 0
        self.ids: list[str] = []
        self.image_alts: list[str | None] = []
        self.details_first_children: list[str | None] = []
        self.statuses: list[dict[str, object]] = []
        self._details_stack: list[dict[str, object]] = []
        self._active_status: dict[str, object] | None = None
        self._status_nodes: list[bool] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        values = dict(attrs)
        if re.fullmatch(r"h[1-6]", tag):
            self.headings.append(int(tag[1]))
        if tag == "main":
            self.main_count += 1
        if "id" in values:
            self.ids.append(values["id"] or "")
        if tag == "img":
            self.image_alts.append(values.get("alt"))
        if self._details_stack and self._details_stack[-1]["first"] is None:
            self._details_stack[-1]["first"] = tag
        if tag == "details":
            self._details_stack.append({"first": None})
        classes = set((values.get("class") or "").split())
        if "status" in classes:
            item: dict[str, object] = {"text": [], "hidden_icon": False}
            self.statuses.append(item)
            self._active_status = item
            self._status_nodes = [False]
        elif self._active_status is not None:
            hidden = values.get("aria-hidden") == "true"
            if hidden:
                self._active_status["hidden_icon"] = True
            self._status_nodes.append(hidden or self._status_nodes[-1])

    def handle_endtag(self, tag: str) -> None:
        if tag == "details":
            item = self._details_stack.pop()
            self.details_first_children.append(item["first"])
        if self._active_status is not None:
            self._status_nodes.pop()
            if not self._status_nodes:
                self._active_status = None

    def handle_data(self, data: str) -> None:
        if (
            self._active_status is not None
            and self._status_nodes
            and not self._status_nodes[-1]
        ):
            self._active_status["text"].append(data)


def _style_source(html: str) -> str:
    match = re.search(r"<style>(.*?)</style>", html, flags=re.DOTALL)
    assert match is not None
    return match.group(1)


def _hex_color(css: str, token: str) -> str:
    match = re.search(rf"--{re.escape(token)}:\s*(#[0-9a-fA-F]{{6}})", css)
    assert match is not None, f"missing fixed color token --{token}"
    return match.group(1)


def _luminance(color: str) -> float:
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92
        if value <= 0.04045
        else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first: str, second: str) -> float:
    lighter, darker = sorted(
        (_luminance(first), _luminance(second)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


@pytest.mark.parametrize(
    "fixture", ("coaching-improve-clear", "capture-only-angle")
)
def test_guided_report_has_structural_accessibility(
    tmp_path: Path, fixture: str
) -> None:
    audit = GuidedReportAudit()
    audit.feed(render_fixture(tmp_path, fixture))

    assert audit.headings.count(1) == 1
    assert all(
        current <= previous + 1
        for previous, current in zip(audit.headings, audit.headings[1:])
    )
    assert audit.main_count == 1
    assert audit.ids and all(audit.ids)
    assert len(audit.ids) == len(set(audit.ids))
    assert audit.image_alts and all(alt and alt.strip() for alt in audit.image_alts)
    assert all(child == "summary" for child in audit.details_first_children)
    if fixture == "coaching-improve-clear":
        assert audit.statuses
    assert all(item["hidden_icon"] for item in audit.statuses)
    assert all("".join(item["text"]).strip() for item in audit.statuses)


def test_guided_report_is_offline_and_progressively_enhanced(
    tmp_path: Path,
) -> None:
    html = render_fixture(tmp_path, "coaching-improve-clear")
    lowered = html.lower()

    assert "<script" not in lowered
    assert "<link" not in lowered
    assert "@import" not in lowered
    assert "url(http://" not in lowered
    assert "url(https://" not in lowered
    assert "autoplay" not in lowered
    sources = re.findall(r'\b(?:src|poster)="([^"]+)"', html)
    assert sources
    assert all(
        not source.startswith(("http://", "https://", "//"))
        for source in sources
    )


def test_fixed_text_control_and_focus_tokens_meet_contrast(
    tmp_path: Path,
) -> None:
    css = _style_source(render_fixture(tmp_path, "coaching-improve-clear"))
    backgrounds = (_hex_color(css, "paper"), _hex_color(css, "canvas"))

    for token in ("ink", "muted", "accent-text"):
        color = _hex_color(css, token)
        assert all(_contrast(color, background) >= 4.5 for background in backgrounds)
    for token in ("control-border", "focus"):
        color = _hex_color(css, token)
        assert all(_contrast(color, background) >= 3 for background in backgrounds)


def test_inline_css_defines_accessible_reflow_motion_and_print_contract(
    tmp_path: Path,
) -> None:
    css = _style_source(render_fixture(tmp_path, "coaching-improve-clear"))
    compact = re.sub(r"\s+", " ", css)

    assert ":focus-visible" in css
    assert re.search(r"outline:\s*[2-9](?:\.\d+)?px\s+solid\s+var\(--focus\)", css)
    assert "outline-offset:" in css
    assert re.search(r"\.report-control\s*,\s*details\s*>\s*summary\s*\{[^}]*min-width:\s*44px", css, re.DOTALL)
    assert re.search(r"\.report-control\s*,\s*details\s*>\s*summary\s*\{[^}]*min-height:\s*44px", css, re.DOTALL)
    assert "overflow-wrap: anywhere" in css
    assert "grid-template-columns: 1fr" in css
    assert "prefers-reduced-motion: reduce" in css
    assert "overscroll-behavior-inline: contain" in css
    assert "@media print" in css
    assert "details:not([open]) > :not(summary)" in compact
    assert "details::details-content" in css
    assert "content-visibility: visible" in css
    assert ".print-only" in css and ".screen-only" in css


def test_measurements_table_has_labeled_horizontal_region(tmp_path: Path) -> None:
    html = render_fixture(tmp_path, "pro-unlocked")

    assert re.search(
        r'<div class="table-scroll" role="region" '
        r'aria-label="[^"]+" tabindex="0">',
        html,
    )
    assert '<caption>Session details and measurements</caption>' in html
