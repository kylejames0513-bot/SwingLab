"""End-to-end pipeline for one video.

This module is the single implementation of the analysis flow; the CLI and the
future web layer both call into it (never duplicate pipeline logic elsewhere).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image

from . import annotate, audio, events, frames, metrics, overlay, pose, report, slowmo, strip
from .coaching import DTL_SESSION_NOTE, angle_mismatch_note
from .coaching import session_notes as make_session_notes
from .coaching import swing_notes
from .config import Config
from .events import EventError
from .ffmpeg import VideoInfo, probe, require_binaries
from .metrics import ANGLE_DTL, ANGLE_FACE_ON, ANGLES


class ZeroStrikesError(RuntimeError):
    """No ball strikes found in the audio track."""


@dataclass
class SessionResult:
    session_dir: Path
    report_path: Path
    metrics_path: Path
    video: VideoInfo
    swings: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)


def _unique_dir(base: Path) -> Path:
    if not base.exists():
        return base
    n = 2
    while (candidate := base.with_name(f"{base.name}-{n}")).exists():
        n += 1
    return candidate


def _fullres_landmarks(
    tracker: pose.PoseTracker,
    fullres_path: Path,
    analysis_lm: pose.Landmarks | None,
    analysis_frame: Path,
) -> pose.Landmarks | None:
    """Pose on a full-res frame, falling back to scaled analysis landmarks."""
    lm = tracker.detect(fullres_path)
    if lm is not None or analysis_lm is None:
        return lm
    with Image.open(fullres_path) as full, Image.open(analysis_frame) as small:
        scale = full.width / small.width
    return {k: v * scale for k, v in analysis_lm.items()}


def analyze_video(
    video_path: str | Path,
    out_dir: str | Path | None = None,
    hand: str = "right",
    manual_strikes: list[float] | None = None,
    cfg: Config | None = None,
    keep_work: bool = False,
    fast: bool = False,
    log: Callable[[str], None] = print,
    progress: Callable[[int, int], None] | None = None,
    angle: str = ANGLE_FACE_ON,
    club: str | None = None,
) -> SessionResult:
    """Run the full pipeline for one video.

    ``progress`` (optional) is called with (swings_finished, swings_total) —
    once as soon as the swing count is known, then after each swing.
    ``fast`` skips motion-interpolated slow motion (the long step) for much
    quicker results.

    ``angle`` is the camera angle the golfer filmed from ("face-on" or
    "dtl"). Every lateral/angular metric is defined face-on, so a
    down-the-line session keeps timing (tempo, durations, consistency) and
    honestly reads NaN for the rest — with a session note saying so.
    ``club`` is display context only (report/metrics.json meta); it changes
    no thresholds and no numbers.
    """
    cfg = cfg or Config.load()
    if angle not in ANGLES:
        raise ValueError(f'angle must be one of {ANGLES}, got "{angle}"')
    require_binaries()
    video_path = Path(video_path)
    info = probe(video_path)
    log(
        f"{video_path.name}: {info.display_width}x{info.display_height} "
        f"@ {info.fps:.2f} fps, {info.duration_s:.1f}s"
        + (f", rotation {info.rotation}°" if info.rotation else "")
    )

    session_dir = _unique_dir(
        Path(out_dir or cfg.output_dir) / video_path.stem
    )
    media_dir = session_dir / "media"
    work_dir = session_dir / "work"
    media_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    # --- strikes ---------------------------------------------------------
    if manual_strikes:
        strikes = sorted(manual_strikes)
        log(f"Using {len(strikes)} manual strike time(s): "
            + ", ".join(f"{t:.2f}s" for t in strikes))
    else:
        if not info.has_audio:
            raise ZeroStrikesError(
                f"{video_path.name} has no audio track, so strikes cannot be "
                "detected. Pass strike times manually with --strikes."
            )
        wav = audio.extract_audio(video_path, work_dir / "audio.wav")
        strikes = audio.detect_strikes(wav, cfg)
        if not strikes:
            raise ZeroStrikesError(
                f"No ball strikes detected in {video_path.name}. If the video "
                "does contain swings, lower detection.audio_height in config, "
                "or pass times manually: --strikes \"12.5,31.0\"."
            )
        log(f"Detected {len(strikes)} strike(s): "
            + ", ".join(f"{t:.2f}s" for t in strikes))

    # --- per swing -------------------------------------------------------
    if progress:
        progress(0, len(strikes))
    tracker = pose.PoseTracker()
    swings: list[dict] = []
    all_metrics: list[metrics.SwingMetrics] = []
    skipped: list[str] = []
    try:
        for swing_no, strike_s in enumerate(strikes, start=1):
            try:
                swing = _analyze_swing(
                    video_path, strike_s, swing_no, tracker, work_dir, media_dir,
                    session_dir, hand, cfg, fast, log, angle,
                )
                swings.append(swing)
                all_metrics.append(swing["metrics"])
            except EventError as exc:
                msg = f"Swing {swing_no} at {strike_s:.2f}s skipped: {exc}"
                log(f"WARNING: {msg}")
                skipped.append(msg)
            if progress:
                progress(swing_no, len(strikes))
    finally:
        tracker.close()

    if not swings:
        raise ZeroStrikesError(
            "Strikes were detected but no swing could be analyzed (pose "
            "tracking failed in every window). Check that the golfer is fully "
            "visible; details: " + "; ".join(skipped)
        )

    # --- session outputs -------------------------------------------------
    stats = metrics.session_stats(all_metrics)
    notes = make_session_notes(all_metrics, stats, cfg)
    # Camera-angle honesty. The DTL note leads (it reframes the whole
    # report); the mismatch warning fires only when every per-swing address
    # pose that had an opinion says the footage looks like the OTHER angle —
    # conservative, so false alarms are rare.
    guesses = [
        s["apparent_angle"] for s in swings if s.get("apparent_angle")
    ]
    if guesses and all(g != angle for g in guesses):
        notes.insert(0, angle_mismatch_note(angle, guesses[0]))
        log("WARNING: " + angle_mismatch_note(angle, guesses[0]))
    if angle == ANGLE_DTL:
        notes.insert(0, DTL_SESSION_NOTE)
    meta = {"camera_angle": angle, "club": club, "hand": hand}
    report_path = report.write_report_html(
        session_dir / "report.html", info, swings, stats, notes, hand, cfg,
        angle=angle, club=club,
    )
    metrics_path = report.write_metrics_json(
        session_dir / "metrics.json", info, swings, stats, notes, cfg,
        meta=meta,
    )

    if not keep_work:
        shutil.rmtree(work_dir, ignore_errors=True)

    return SessionResult(
        session_dir=session_dir,
        report_path=report_path,
        metrics_path=metrics_path,
        video=info,
        swings=swings,
        stats=stats,
        skipped=skipped,
    )


def _analyze_swing(
    video_path: Path,
    strike_s: float,
    swing_no: int,
    tracker: pose.PoseTracker,
    work_dir: Path,
    media_dir: Path,
    session_dir: Path,
    hand: str,
    cfg: Config,
    fast: bool,
    log: Callable[[str], None],
    angle: str = ANGLE_FACE_ON,
) -> dict:
    log(f"Swing {swing_no}: analyzing strike at {strike_s:.2f}s...")
    frameset = frames.extract_window(video_path, strike_s, work_dir, swing_no, cfg)
    tracked = [tracker.detect(p) for p in frameset.paths]
    ev = events.detect_events(tracked, frameset, strike_s, cfg)
    finish_idx = frameset.index_near(ev.finish_s)
    m = metrics.compute_metrics(
        swing_no, tracked, ev, finish_idx, hand, cfg=cfg, angle=angle
    )

    # full-res key frames (deliverables only)
    key_times = {
        "address": frameset.time_of(ev.address_idx),
        "top": ev.top_s,
        "impact": ev.impact_s,
        "finish": ev.finish_s,
    }
    fullres: dict[str, Path] = {}
    for name, t in key_times.items():
        fullres[name] = frames.extract_fullres_frame(
            video_path, t, work_dir / f"full_s{swing_no}_{name}.png", cfg
        )

    strip_path = strip.make_strip(
        [fullres[k] for k in ("address", "top", "impact", "finish")],
        swing_no,
        media_dir / f"strip_s{swing_no}.png",
        cfg,
    )

    analysis_by_key = {
        "address": (tracked[ev.address_idx], frameset.paths[ev.address_idx]),
        "top": (tracked[ev.top_idx], frameset.paths[ev.top_idx]),
        "impact": (tracked[ev.impact_idx], frameset.paths[ev.impact_idx]),
    }
    overlay_lm = {
        key: _fullres_landmarks(tracker, fullres[key], a_lm, a_frame)
        for key, (a_lm, a_frame) in analysis_by_key.items()
    }
    overlay_path = overlay.make_overlay(
        {k: fullres[k] for k in ("address", "top", "impact")},
        overlay_lm,
        m.target_direction,
        media_dir / f"overlay_s{swing_no}.png",
        cfg,
    )

    log(
        f"Swing {swing_no}: rendering slow motion"
        + (" (fast mode)..." if fast else " (the long step)...")
    )
    slowmo_path = slowmo.make_slowmo(
        video_path, strike_s, media_dir / f"slowmo_s{swing_no}.mp4", cfg, fast=fast
    )

    # Annotated replay: the golfer's own footage with the tracked skeleton,
    # fading hand-path trace, and event chips burned in. Never motion-
    # interpolated (see annotate.py), so it is identical in --fast mode;
    # slowmo.annotated is the only switch.
    replay_path = None
    if cfg.slowmo["annotated"]:
        log(f"Swing {swing_no}: rendering annotated replay...")
        replay_frames = slowmo.extract_replay_frames(
            video_path, strike_s, work_dir / f"replay_s{swing_no}", cfg
        )
        replay_path = annotate.make_replay(
            replay_frames, frameset, tracked, ev, m,
            media_dir / f"replay_s{swing_no}.mp4", cfg,
        )

    return {
        "metrics": m,
        "notes": swing_notes(m, cfg),
        # report-relative paths so the session folder is portable
        "strip": str(strip_path.relative_to(session_dir)),
        "overlay": str(overlay_path.relative_to(session_dir)),
        "slowmo": str(slowmo_path.relative_to(session_dir)),
        "replay": str(replay_path.relative_to(session_dir)) if replay_path else None,
        # Camera-angle sanity check input: what the address pose looks like
        # (face-on/dtl/None). Consumed by analyze_video, never serialized.
        "apparent_angle": metrics.apparent_camera_angle(tracked[ev.address_idx]),
    }
