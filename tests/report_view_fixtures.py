from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).with_name("fixtures") / "report_view"

def report_view_payload(name: str = "coaching-improve-clear") -> dict[str, object]:
    return json.loads((_ROOT / f"{name}.json").read_text(encoding="utf-8"))

def report_document_fixture(name: str = "coaching-improve-clear"):
    """A complete typed document whose depth references the view fixture."""
    from swinglab.report_presenter import (
        FindingDetail,
        GearDetail,
        GlossaryEntry,
        LabelValue,
        ReportDepthContent,
        ReportDocument,
        ReportNavigation,
        StrengthDetail,
        SwingDetail,
    )
    from swinglab.report_view import report_view_from_dict

    view = report_view_from_dict(report_view_payload(name))
    media = {entry.key: entry for entry in view.media}
    depth = ReportDepthContent(
        swings=(SwingDetail(
            1, "Swing 1", ("Readable swing",), (), "focus-1",
            "Key positions for swing 1", None, "Slow-motion swing 1", None,
            "Coach replay for swing 1", False, None, None,
            "Video poster for swing 1", "Playback: media/focus-1.jpg",
        ),),
        secondary_findings=(FindingDetail(
            "tempo", "Tempo", "A secondary timing note.", "Timing supports contact.",
            "Finish going back.", ("head-rise",), "secondary-findings",
        ),),
        strengths=(StrengthDetail(
            "balance", "Balanced finish", "Your finish stayed steady.", (),
        ),),
        measurements=(LabelValue("head-rise", "Head rise", "1.2 shoulder widths"),),
        session_details=(LabelValue("angle", "Camera angle", "Face-on"),),
        glossary=(GlossaryEntry("Shoulder width", "A body-scaled distance."),),
        limitations=("Single-camera estimates.",),
        gear=(GearDetail("tempo-aid", "Tempo aid", "Optional timing aid.", "/gear/tempo"),),
        navigation=ReportNavigation("/", "/shop", "/collections/swinglab-gear"),
    )
    return ReportDocument(view=view, depth=depth, media_by_key=media)
