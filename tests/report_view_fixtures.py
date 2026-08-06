from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).with_name("fixtures") / "report_view"

def report_view_payload(name: str = "coaching-improve-clear") -> dict[str, object]:
    return json.loads((_ROOT / f"{name}.json").read_text(encoding="utf-8"))

def report_document_fixture(name: str = "coaching-improve-clear"):
    """Late-bound helper for the ReportDocument introduced by Task 6."""
    from swinglab.report_document import ReportDocument
    from swinglab.report_view import report_view_from_dict
    return ReportDocument(view=report_view_from_dict(report_view_payload(name)))
