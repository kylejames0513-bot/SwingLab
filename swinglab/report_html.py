from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

from jinja2 import (
    Environment,
    FileSystemLoader,
    StrictUndefined,
    select_autoescape,
)

from .coaching import priority_rule_version
from .config import Config
from .report import REPORT_FORMAT_VERSION
from .report_presenter import (
    REASON_COPY,
    ReportDocument,
    complete_report_navigation,
)
from .report_view import GUIDED_REPORT_PRESENTATION_VERSION
from dataclasses import replace


GUIDED_TEMPLATE = "report_guided.html.j2"
STATUS_ICONS = MappingProxyType({
    "priority": "●",
    "review_later": "△",
    "steady": "✓",
    "baseline": "◆",
    "not_measured": "—",
})
PHASE_METHOD_LABELS = MappingProxyType({
    "opening_baseline": "Address from opening setup",
    "highest_tracked_hands": "Top from highest hand position",
    "detected_audio": "Impact estimated from sound",
    "manual_strike": "Impact marked by you",
    "configured_finish_offset": "Finish after impact",
    "session_timing": "Measured swing timing",
})
REASON_LABELS = MappingProxyType({
    **{code: copy.label for code, copy in REASON_COPY.items()},
    **{code.value: copy.label for code, copy in REASON_COPY.items()},
})
REASON_REMEDIATIONS = MappingProxyType({
    **{code: copy.remediation for code, copy in REASON_COPY.items()},
    **{code.value: copy.remediation for code, copy in REASON_COPY.items()},
})


def _media_path(document: ReportDocument, key: str) -> str:
    entry = document.media_by_key.get(key)
    if entry is None:
        raise ValueError(f"guided report references unknown media key: {key}")
    return entry.relative_path


def write_report_document_html(
    out_path: Path,
    document: ReportDocument,
    *,
    cfg: Config,
    sample_banner: dict | None = None,
) -> Path:
    if document.view.presentation_version != GUIDED_REPORT_PRESENTATION_VERSION:
        raise ValueError("guided renderer requires guided-report-v1")
    navigation = complete_report_navigation(document.depth.navigation, cfg)
    document = replace(
        document,
        depth=replace(document.depth, navigation=navigation),
    )
    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(["html", "j2"]),
        undefined=StrictUndefined,
    )
    optional_by_id = {
        section.id: section for section in document.view.optional_sections
    }
    html = env.get_template(GUIDED_TEMPLATE).render(
        brand=cfg.brand,
        document=document,
        view=document.view,
        depth=document.depth,
        navigation=document.depth.navigation,
        optional_by_id=optional_by_id,
        media_path=lambda key: _media_path(document, key),
        status_icons=STATUS_ICONS,
        phase_method_labels=PHASE_METHOD_LABELS,
        reason_labels=REASON_LABELS,
        reason_remediations=REASON_REMEDIATIONS,
        report_format_version=REPORT_FORMAT_VERSION,
        priority_rule_version=priority_rule_version(cfg),
        sample_banner=sample_banner,
    )
    out_path.write_text(html, encoding="utf-8")
    return out_path
