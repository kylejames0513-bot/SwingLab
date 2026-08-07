"""report.html and metrics.json for one session."""

from __future__ import annotations

import dataclasses
import json
import math
import os
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import __version__, diagrams
from .clubs import club_label
from .levels import level_label, level_note
from .coaching import priority_rule_version
from .config import Config
from .drills import gear_shop_url
from .explainers import build_explainers
from .ffmpeg import VideoInfo
from .metrics import ANGLE_DTL, ANGLE_FACE_ON, SwingMetrics
from .report_insights import build_swing_breakdown
from .report_presenter import build_report_document, prepare_report_input


REPORT_FORMAT_VERSION = "caddie-brief-v1"
REPORT_PRESENTATION_VERSION = "premium-coach-v2"
REPORT_OUTCOME_COACHING = "coaching_ready"
REPORT_OUTCOME_CAPTURE = "capture_only"
PRIORITY_RULE_META_NAME = "caddieinsight-coaching-priority-rule"


def persisted_report_outcome(path: Path) -> str | None:
    """Read this version's persisted outcome marker from a report header."""
    format_marker = (
        'name="caddieinsight-report-format" '
        f'content="{REPORT_FORMAT_VERSION}"'
    )
    try:
        with path.open("r", encoding="utf-8", errors="replace") as report:
            header = report.read(8192)
    except OSError:
        return None
    if format_marker not in header:
        return None
    for outcome in (REPORT_OUTCOME_COACHING, REPORT_OUTCOME_CAPTURE):
        marker = (
            'name="caddieinsight-report-outcome" '
            f'content="{outcome}"'
        )
        if marker in header:
            return outcome
    return None


def persisted_priority_rule_version(path: Path) -> int | None:
    """Read the immutable report-priority rule, defaulting old reports to v1.

    The additive marker lets dynamic result cards and weekly plans replay the
    same selection as the static report across activation and rollback. An
    explicit malformed, duplicated, or unsupported marker fails closed.
    """

    try:
        with path.open("r", encoding="utf-8", errors="replace") as report:
            header = report.read(8192)
    except OSError:
        return None
    name_marker = f'name="{PRIORITY_RULE_META_NAME}"'
    marker_count = header.count(name_marker)
    if marker_count == 0:
        return 1
    if marker_count != 1:
        return None
    for version in (1, 2):
        marker = f'<meta {name_marker} content="{version}">'
        if marker in header:
            return version
    return None


def _sanitize(value: Any) -> Any:
    """NaN is not valid JSON; write null instead."""
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    return value


def write_metrics_json(
    out_path: Path,
    video: VideoInfo,
    swings: list[dict],
    stats: dict,
    session_notes: list[str],
    cfg: Config,
    meta: dict | None = None,
) -> Path:
    def metrics_payload(swing: dict) -> dict:
        metrics = swing["metrics"]
        if isinstance(metrics, SwingMetrics):
            return metrics.as_dict()
        if isinstance(metrics, dict):
            return dict(metrics)
        raise TypeError("Every swing must contain SwingMetrics or a metrics mapping")

    payload = {
        "generator": {"name": cfg.brand["name"], "swinglab_version": __version__},
        "video": {
            "path": str(video.path),
            "duration_s": video.duration_s,
            "width": video.display_width,
            "height": video.display_height,
            "fps": video.fps,
            "rotation": video.rotation,
            "creation_time": video.creation_time,
        },
        # Session context (camera angle, club, handedness) — additive; older
        # consumers that don't know the key simply ignore it.
        **({"meta": meta} if meta else {}),
        "swings": [
            {
                "metrics": metrics_payload(s),
                "notes": s["notes"],
                "deliverables": {
                    # Each renderer is independent in guided bundles.  Legacy
                    # calls that supply the established three values retain
                    # their exact insertion order and serialized bytes.
                    **({"strip": s["strip"]} if s.get("strip") else {}),
                    **({"overlay": s["overlay"]} if s.get("overlay") else {}),
                    **({"slowmo": s["slowmo"]} if s.get("slowmo") else {}),
                    **({"replay": s["replay"]} if s.get("replay") else {}),
                },
            }
            for s in swings
        ],
        "session_stats": stats,
        "session_notes": session_notes,
        "disclaimer": cfg.brand["disclaimer"],
    }
    out_path.write_text(json.dumps(_sanitize(payload), indent=2), encoding="utf-8")
    return out_path


def write_report_html(
    out_path: Path,
    video: VideoInfo,
    swings: list[dict],
    stats: dict,
    session_notes: list[str],
    hand: str,
    cfg: Config,
    angle: str = ANGLE_FACE_ON,
    club: str | None = None,
    level: str | None = None,
    sample_banner: dict | None = None,
    analysis_fps: float | None = None,
    replay_locked: bool = False,
) -> Path:
    """``analysis_fps`` is the rate the analysis windows were extracted at
    (auto-fps may lift it above analysis.fps for high-fps sources); shown in
    the session table when provided so readers know the timing resolution.

    ``replay_locked`` means the annotated coach replay was deliberately not
    rendered because the session's owner is on the free plan (the
    billing.replay_pro_only gate): the replay slot beside each slow-mo shows
    an honest locked note with a /pricing link instead of the video. False
    (the default — CLI runs, open instances, Pro owners, gate off) renders
    exactly what exists and never mentions the gate."""
    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(["html", "j2"]),
    )
    source = prepare_report_input(
        video, swings, stats, session_notes, hand, cfg, angle=angle, club=club,
        level=level, analysis_fps=analysis_fps, replay_locked=replay_locked,
    )
    # Establish the typed boundary even while the legacy Jinja adapter remains
    # the shipping renderer. The next presentation workstream consumes it.
    build_report_document(source, cfg)
    all_metrics = [SwingMetrics(**dict(item.metrics)) for item in source.swings]
    coaching_stats = source.stats
    render_swings = [
        {**swing, "metrics": metric}
        for swing, metric in zip(swings, all_metrics)
    ]
    selected_priority_rule = priority_rule_version(cfg)
    caddie_brief = source.brief
    coaching_allowed = bool(
        caddie_brief is not None and not caddie_brief.refilm_required
    )
    # Issue cards: one per fired flag, each with an inline-SVG sparkline of
    # the per-swing values against the flag's benchmark (self-contained HTML,
    # no external assets). Already sorted highest-severity first — the report
    # renders the first one full-size as "Start here" and defers the rest.
    cards = list(source.issues) if coaching_allowed else []
    if not coaching_allowed:
        flags = []
    elif selected_priority_rule == 2:
        # Rule 2 intentionally aligns the brief, report cards, practice plan,
        # gear match, and Proof target on one club-aware priority.
        flags = [card.flag for card in cards]
    else:
        # The compatibility floor must remain inert: rule 1 preserves the
        # established raw flag order used by historical practice plans.
        flags = [block["flag"] for block in source.practice_blocks]
    issue_ctx = [
        {**dataclasses.asdict(c),
         "sparkline": diagrams.sparkline(
             c.per_swing, c.benchmark_value, cfg.brand, c.worse_direction)}
        for c in cards
    ]
    plan = [dict(block) for block in source.practice_blocks] if coaching_allowed else []
    limited_baseline = False
    if (
        coaching_allowed
        and caddie_brief is not None
        and caddie_brief.clean
        and caddie_brief.drill is not None
        and plan
        and plan[0]["drills"][0] != caddie_brief.drill
    ):
        limited_baseline = True
        plan[0] = {
            **plan[0],
            "title": (
                "Rhythm-only maintenance"
                if caddie_brief.drill.gear_tag == "swinglab:tempo"
                else "Complete the baseline"
            ),
            "drills": [caddie_brief.drill],
        }
    # Inline SVG diagram + CSS-only animation per drill in the plan (keyed by
    # drill id; brand colors flow in from config for white-labeling).
    drill_media = {
        d.id: {"diagram": diagrams.drill_diagram(d.id, cfg.brand),
               "animation": diagrams.drill_animation(d.id, cfg.brand)}
        for block in plan for d in block["drills"]
    }
    selected_club_label = club_label(club)
    swing_breakdown = (
        build_swing_breakdown(
            all_metrics,
            cfg,
            angle=angle,
            selected_club=selected_club_label,
        )
        if coaching_allowed
        else []
    )
    club_aware_enabled = selected_priority_rule == 2
    club_priority_changes_ties = bool(
        club_aware_enabled
        and angle != ANGLE_DTL
        and club in {"driver", "fairway-wood", "hybrid", "iron"}
    )
    html = env.get_template("report.html.j2").render(
        brand=cfg.brand,
        report_format_version=REPORT_FORMAT_VERSION,
        report_presentation_version=REPORT_PRESENTATION_VERSION,
        priority_rule_version=selected_priority_rule,
        report_outcome=(
            REPORT_OUTCOME_COACHING
            if coaching_allowed
            else REPORT_OUTCOME_CAPTURE
        ),
        coaching=cfg.coaching,
        video=video,
        swings=render_swings,
        stats=coaching_stats,
        session_notes=session_notes,
        # "What's working": every metric measured AND in range — [] hides the
        # strip entirely (never fake praise).
        praise_notes=(
            [card.text for card in source.strengths]
            if coaching_allowed
            else []
        ),
        hand=hand,
        angle=angle,
        dtl=(angle == ANGLE_DTL),
        club_label=selected_club_label,
        club_aware_enabled=club_aware_enabled,
        club_priority_changes_ties=club_priority_changes_ties,
        # Experience-level framing (swinglab.levels): a chip plus one line
        # above the metrics — reframing only, never a threshold change.
        level_label=level_label(level),
        level_note=level_note(level),
        explainers=build_explainers(cfg.coaching),
        slowmo_factor=cfg.slowmo["factor"],
        caddie_brief=caddie_brief,
        swing_breakdown=swing_breakdown,
        flags=flags,
        issue_cards=issue_ctx,
        practice_plan=plan,
        limited_baseline=limited_baseline,
        drill_media=drill_media,
        gear_url=gear_shop_url(cfg),
        storefront_url=(cfg.shop.get("store_url") or "").rstrip("/"),
        app_url=(os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/"),
        sample_banner=sample_banner,
        analysis_fps=analysis_fps,
        replay_locked=replay_locked and coaching_allowed,
    )
    out_path.write_text(html, encoding="utf-8")
    return out_path
