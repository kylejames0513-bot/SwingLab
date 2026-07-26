"""report.html and metrics.json for one session."""

from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import __version__, diagrams
from .clubs import club_label
from .coaching import issue_cards as make_issue_cards
from .coaching import praise_notes as make_praise_notes
from .coaching import session_flags
from .config import Config
from .drills import gear_shop_url, practice_plan
from .explainers import build_explainers
from .ffmpeg import VideoInfo
from .metrics import ANGLE_DTL, ANGLE_FACE_ON


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
                "metrics": s["metrics"].as_dict(),
                "notes": s["notes"],
                "deliverables": {
                    "strip": s["strip"],
                    "overlay": s["overlay"],
                    "slowmo": s["slowmo"],
                    # only when present — older consumers see no null churn
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
    sample_banner: dict | None = None,
) -> Path:
    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    all_metrics = [s["metrics"] for s in swings]
    flags = session_flags(all_metrics, stats, cfg)
    # Issue cards: one per fired flag, each with an inline-SVG sparkline of
    # the per-swing values against the flag's benchmark (self-contained HTML,
    # no external assets). Already sorted highest-severity first — the report
    # renders the first one full-size as "Start here" and defers the rest.
    cards = make_issue_cards(all_metrics, stats, cfg)
    issue_ctx = [
        {**dataclasses.asdict(c),
         "sparkline": diagrams.sparkline(
             c.per_swing, c.benchmark_value, cfg.brand, c.worse_direction)}
        for c in cards
    ]
    plan = practice_plan(flags, cfg)
    # Inline SVG diagram + CSS-only animation per drill in the plan (keyed by
    # drill id; brand colors flow in from config for white-labeling).
    drill_media = {
        d.id: {"diagram": diagrams.drill_diagram(d.id, cfg.brand),
               "animation": diagrams.drill_animation(d.id, cfg.brand)}
        for block in plan for d in block["drills"]
    }
    html = env.get_template("report.html.j2").render(
        brand=cfg.brand,
        coaching=cfg.coaching,
        video=video,
        swings=swings,
        stats=stats,
        session_notes=session_notes,
        # "What's working": every metric measured AND in range — [] hides the
        # strip entirely (never fake praise).
        praise_notes=make_praise_notes(all_metrics, cfg, stats),
        hand=hand,
        angle=angle,
        dtl=(angle == ANGLE_DTL),
        club_label=club_label(club),
        explainers=build_explainers(cfg.coaching),
        slowmo_factor=cfg.slowmo["factor"],
        flags=flags,
        issue_cards=issue_ctx,
        practice_plan=plan,
        drill_media=drill_media,
        gear_url=gear_shop_url(cfg),
        sample_banner=sample_banner,
    )
    out_path.write_text(html, encoding="utf-8")
    return out_path
