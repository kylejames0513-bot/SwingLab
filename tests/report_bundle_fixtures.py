from __future__ import annotations

import html
from dataclasses import replace
from pathlib import Path

from swinglab.config import Config
from swinglab.report import REPORT_FORMAT_VERSION
from swinglab.report_presenter import ReportDocument
from swinglab.report_view import ReasonCode
from tests.test_focused_evidence import _snapshot
from tests.test_report import branded_cfg, fake_swing, fake_video


def write_test_report_html(
    out_path: Path,
    document: ReportDocument,
    *,
    cfg: Config,
) -> Path:
    """Deterministic structural writer for bundle mechanics tests only."""
    view = document.view
    title = (
        view.next_move.title
        if view.next_move is not None
        else view.capture_guidance.reason_label
    )
    observation = (
        view.next_move.observation
        if view.next_move is not None
        else view.capture_guidance.explanation
    )
    practice = view.practice.name if view.practice is not None else ""
    body = (
        "<!doctype html>\n"
        "<html><head>\n"
        f'<meta name="caddieinsight-report-format" content="{REPORT_FORMAT_VERSION}">\n'
        f'<meta name="caddieinsight-report-presentation" content="{html.escape(view.presentation_version, quote=True)}">\n'
        f'<meta name="caddieinsight-report-outcome" content="{html.escape(view.outcome.value, quote=True)}">\n'
        f"<title>{html.escape(str(cfg.brand['name']))} report</title>\n"
        "</head><body>\n"
        f'<main data-outcome="{html.escape(view.outcome.value, quote=True)}">\n'
        f"<h1>{html.escape(str(cfg.brand['name']))}</h1>\n"
        f"<h2>{html.escape(title)}</h2>\n"
        f"<p>{html.escape(observation)}</p>\n"
        f"<p>{html.escape(practice)}</p>\n"
        "</main>\n"
        "</body></html>\n"
    )
    out_path.write_bytes(body.encode("utf-8"))
    return out_path


def guided_bundle_inputs(
    tmp_path: Path,
    *,
    angle: str = "face-on",
    reason_codes: tuple[ReasonCode, ...] = (),
    swings: list[dict] | None = None,
) -> dict[str, object]:
    selected_swings = [fake_swing(1, 2.0)] if swings is None else swings
    for swing in selected_swings:
        swing.pop("strip", None)
        swing.pop("overlay", None)
        swing.pop("slowmo", None)
        swing.pop("replay", None)
    metrics = [swing["metrics"] for swing in selected_swings if hasattr(swing.get("metrics"), "as_dict")]
    from swinglab.metrics import session_stats

    snapshots = ()
    if metrics:
        snapshots = (
            replace(_snapshot(tmp_path, swing=1), metrics=metrics[0]),
        )
    return {
        "html_writer": write_test_report_html,
        "video": fake_video(),
        "swings": selected_swings,
        "stats": session_stats(metrics) if metrics else {},
        "session_notes": ["Fixture <note> & safe"],
        "hand": "right",
        "cfg": branded_cfg(),
        "angle": angle,
        "club": None,
        "level": None,
        "analysis_fps": 20.0,
        "replay_locked": False,
        "evidence_snapshots": snapshots,
        "reason_codes": reason_codes,
    }


def add_optional_media(
    attempt,
    inputs: dict[str, object],
    *,
    strip: bool = False,
    slowmo: bool = False,
    replay: bool = False,
) -> None:
    swing = inputs["swings"][0]
    if strip:
        path = attempt.media_dir / "positions-1.png"
        path.write_bytes(b"positions")
        swing["strip"] = path
    if slowmo:
        path = attempt.media_dir / "slow-1.mp4"
        path.write_bytes(b"slow motion")
        swing["slowmo"] = path
    if replay:
        path = attempt.media_dir / "replay-1.mp4"
        path.write_bytes(b"coach replay")
        swing["replay"] = path
