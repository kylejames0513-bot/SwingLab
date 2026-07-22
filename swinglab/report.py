"""report.html and metrics.json for one session."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import __version__
from .config import Config
from .ffmpeg import VideoInfo


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
        "swings": [
            {
                "metrics": s["metrics"].as_dict(),
                "notes": s["notes"],
                "deliverables": {
                    "strip": s["strip"],
                    "overlay": s["overlay"],
                    "slowmo": s["slowmo"],
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
) -> Path:
    env = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=select_autoescape(["html"]),
    )
    html = env.get_template("report.html.j2").render(
        brand=cfg.brand,
        coaching=cfg.coaching,
        video=video,
        swings=swings,
        stats=stats,
        session_notes=session_notes,
        hand=hand,
        slowmo_factor=cfg.slowmo["factor"],
    )
    out_path.write_text(html, encoding="utf-8")
    return out_path
