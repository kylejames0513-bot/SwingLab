"""End-to-end pipeline for one video.

This module is the single implementation of the analysis flow; the CLI and the
future web layer both call into it (never duplicate pipeline logic elsewhere).
"""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, assert_never

from PIL import Image

from . import annotate, audio, events, frames, metrics, overlay, pose, report, slowmo, strip
from .coaching import DTL_SESSION_NOTE, TRACKING_UNSTABLE_NOTE, angle_mismatch_note
from .coaching import session_notes as make_session_notes
from .coaching import swing_notes
from .config import Config
from .events import EventError, EventFailure
from .evidence import EvidenceSnapshot, build_evidence_snapshot
from .ffmpeg import FFmpegError, VideoInfo, probe, require_binaries
from .metrics import ANGLE_DTL, ANGLE_FACE_ON, ANGLES
from .report import REPORT_PRESENTATION_VERSION
from .report_artifacts import PublishedReportBundle, ReportEntitlementSnapshot
from .report_bundle import (
    CoreReportBundleError,
    GuidedReportRendererUnavailable,
    ReportHtmlWriter,
    _delete_exact_owned_file,
    begin_report_bundle,
    build_report_bundle,
    publish_report_bundle,
)
from .report_view import (
    EventId,
    MediaRole,
    PhaseMethod,
    ReasonCode,
    ReportPresentationVersion,
    parse_report_presentation_version,
)


class ZeroStrikesError(RuntimeError):
    """No ball strikes found in the audio track."""


class VideoTooLongError(RuntimeError):
    """The clip exceeds analysis.max_video_s — refused before any work."""


@dataclass
class SessionResult:
    session_dir: Path
    report_path: Path
    metrics_path: Path
    video: VideoInfo
    swings: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    report_view_path: Path | None = None
    manifest_path: Path | None = None
    checksums_path: Path | None = None
    structured_report: bool = False
    evidence_snapshots: list[EvidenceSnapshot] = field(default_factory=list)


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


def _scaled_analysis_landmarks(
    fullres_path: Path,
    analysis_lm: pose.Landmarks | None,
    analysis_frame: Path,
) -> pose.Landmarks | None:
    """Project the accepted analysis observation onto its render frame.

    Guided visibility gates are calculated from that observation. Re-running
    the model on a full-resolution extraction could produce a different pose
    and let the rendered evidence contradict the gate that authorized it.
    """

    if analysis_lm is None:
        return None
    with Image.open(fullres_path) as full, Image.open(analysis_frame) as small:
        if small.width <= 0 or small.height <= 0:
            raise CoreReportBundleError("analysis frame has invalid dimensions")
        scale_x = full.width / small.width
        scale_y = full.height / small.height
    return {
        index: point * (scale_x, scale_y)
        for index, point in analysis_lm.items()
    }


def _guided_video_info(info: VideoInfo) -> VideoInfo:
    """Return persistable video facts without the private source path."""
    return VideoInfo(
        path=Path("uploaded-video"),
        duration_s=info.duration_s,
        width=info.width,
        height=info.height,
        fps=info.fps,
        rotation=info.rotation,
        creation_time=info.creation_time,
        has_audio=info.has_audio,
    )


def _remove_optional_partial(
    media_dir: Path,
    output: Path,
    *,
    session_anchor: Path,
) -> None:
    """Remove only one exact renderer-owned partial, or fail closed."""
    if output.parent.absolute() != media_dir.absolute():
        raise CoreReportBundleError("optional renderer output is outside its owned media root")
    _delete_exact_owned_file(
        media_dir,
        output,
        session_anchor=session_anchor,
    )


def _optional_guided_media(
    *,
    label: str,
    output: Path,
    media_dir: Path,
    session_anchor: Path,
    render: Callable[[], Path],
    log: Callable[[str], None],
) -> Path | None:
    """Run one independent guided renderer without inventing a substitute."""
    try:
        rendered = render()
    except Exception:
        _remove_optional_partial(
            media_dir,
            output,
            session_anchor=session_anchor,
        )
        log(f"WARNING: {label} is unavailable.")
        return None
    if not isinstance(rendered, Path) or rendered.absolute() != output.absolute():
        raise CoreReportBundleError("optional renderer returned a noncanonical owned path")
    try:
        info = os.lstat(output)
    except OSError:
        log(f"WARNING: {label} is unavailable.")
        return None
    reparse = bool(
        int(getattr(info, "st_file_attributes", 0))
        & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    )
    if stat.S_ISLNK(info.st_mode) or reparse or not stat.S_ISREG(info.st_mode):
        raise CoreReportBundleError("optional renderer output has ambiguous ownership")
    return output


_EVENT_REASON_CODES = {
    EventFailure.INSUFFICIENT_POSE_FRAMES: ReasonCode.INSUFFICIENT_POSE_FRAMES,
    EventFailure.NO_READABLE_SWING: ReasonCode.NO_READABLE_SWING,
}


def _published_guided_swings(
    swings: list[dict], published: PublishedReportBundle
) -> list[dict]:
    """Project only declared per-swing media onto their final owned paths."""
    media = {entry.key: entry for entry in published.view.media}
    projected: list[dict] = []
    for index, original in enumerate(swings, start=1):
        swing = {
            "metrics": original["metrics"],
            "notes": original["notes"],
            "apparent_angle": original.get("apparent_angle"),
        }
        for key, field, role in (
            (f"key-positions-s{index}", "strip", MediaRole.KEY_POSITIONS),
            (f"slow-motion-s{index}", "slowmo", MediaRole.SLOW_MOTION),
            (f"coach-replay-s{index}", "replay", MediaRole.COACH_REPLAY),
            (f"capture-playback-s{index}", "slowmo", MediaRole.CAPTURE_PLAYBACK),
        ):
            entry = media.get(key)
            if entry is not None and entry.role is role:
                swing[field] = published.root / Path(entry.relative_path)
        projected.append(swing)
    return projected


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
    level: str | None = None,
    replay_locked: bool = False,
    report_presentation_version: str = REPORT_PRESENTATION_VERSION,
    report_entitlements: ReportEntitlementSnapshot | None = None,
    guided_html_writer: ReportHtmlWriter | None = None,
) -> SessionResult:
    """Run the full pipeline for one video.

    ``progress`` (optional) is called with (swings_finished, swings_total) —
    once as soon as the swing count is known, then after each swing.
    ``fast`` skips motion-interpolated slow motion (the long step) for much
    quicker results.

    ``replay_locked`` is the coach-replay Pro gate (billing.replay_pro_only),
    decided by the CALLER — the web job runner sets it for free-plan owners
    at analysis time; the CLI and open instances never do (the default is
    False, so this function gates nothing on its own). When True the
    annotated replay is not rendered at all (the CPU is saved, and neither
    metrics.json nor the session folder carries a replay), and the report
    shows an honest locked note in each replay slot instead. It only means
    anything while ``slowmo.annotated`` is on — with the replay feature
    disabled outright there is nothing to lock and no note is shown.

    ``angle`` is the camera angle the golfer filmed from ("face-on" or
    "dtl"). Every lateral/angular metric is defined face-on, so a
    down-the-line session keeps timing (tempo, durations, consistency) and
    honestly reads NaN for the rest — with a session note saying so.
    ``club`` is display context only (report/metrics.json meta); it changes
    no thresholds and no numbers. ``level`` (experience level, see
    swinglab.levels) is the same kind of context: a chip and one framing
    line on the report, never an analysis input.
    """
    presentation = parse_report_presentation_version(report_presentation_version)
    if presentation is ReportPresentationVersion.GUIDED:
        guided = True
    elif presentation is ReportPresentationVersion.LEGACY:
        guided = False
    else:  # pragma: no cover - enum exhaustiveness guard
        assert_never(presentation)
    if guided and guided_html_writer is None:
        raise GuidedReportRendererUnavailable("guided report HTML writer is unavailable")
    if guided and not isinstance(report_entitlements, ReportEntitlementSnapshot):
        raise TypeError("guided report entitlement snapshot is required")

    cfg = cfg or Config.load()
    if angle not in ANGLES:
        raise ValueError(f'angle must be one of {ANGLES}, got "{angle}"')
    require_binaries()
    video_path = Path(video_path)
    try:
        info = probe(video_path)
    except FFmpegError:
        if guided:
            raise CoreReportBundleError("guided video probe failed") from None
        raise
    log(
        ("Input video" if guided else video_path.name)
        + f": {info.display_width}x{info.display_height} "
        f"@ {info.fps:.2f} fps, {info.duration_s:.1f}s"
        + (f", rotation {info.rotation}°" if info.rotation else "")
    )
    # Length cap first — before any extraction burns CPU or disk. A one-hour
    # clip means hours of work; refusing it here with a clear message beats
    # timing out (or OOMing) halfway through.
    max_video_s = float(cfg.analysis.get("max_video_s") or 0)
    if max_video_s and info.duration_s > max_video_s:
        raise VideoTooLongError(
            f"{('The input video' if guided else video_path.name)} is "
            f"{info.duration_s:.0f} seconds long — over "
            f"the {max_video_s:.0f}-second analysis limit. Trim the clip to "
            "the swings you want analyzed and try again. (Operators: the "
            "limit is analysis.max_video_s in config; 0 disables it.)"
        )
    # Analysis frame rate for this video: analysis.fps, or — with auto_fps on
    # and a high-fps source — min(source_fps, 60). Everything downstream
    # takes timing from the FrameSet, so the rate is decided exactly once.
    analysis_fps = frames.pick_analysis_fps(cfg, info.fps)
    if analysis_fps != float(cfg.analysis["fps"]):
        log(
            f"High-speed source: analyzing at {analysis_fps:.6g} fps "
            f"(analysis.auto_fps) for finer timing."
        )

    session_dir = _unique_dir(Path(out_dir or cfg.output_dir) / video_path.stem)
    attempt = None
    if guided:
        session_dir.mkdir(parents=True, exist_ok=False)
        attempt = begin_report_bundle(session_dir)
        media_dir = attempt.media_dir
        work_dir = attempt.work_dir
    else:
        media_dir = session_dir / "media"
        work_dir = session_dir / "work"
        media_dir.mkdir(parents=True, exist_ok=True)
        work_dir.mkdir(parents=True, exist_ok=True)

    # --- strikes ---------------------------------------------------------
    manual_event_source = manual_strikes is not None if guided else bool(manual_strikes)
    impact_method = (
        PhaseMethod.MANUAL_STRIKE
        if manual_event_source
        else PhaseMethod.DETECTED_AUDIO
    )
    reason_codes: list[ReasonCode] = []
    if manual_event_source:
        strikes = sorted(manual_strikes)
        log(f"Using {len(strikes)} manual strike time(s): "
            + ", ".join(f"{t:.2f}s" for t in strikes))
    else:
        if not info.has_audio:
            if guided:
                strikes = []
                reason_codes.append(ReasonCode.NO_RELIABLE_STRIKE_EVENT)
            else:
                raise ZeroStrikesError(
                    f"{video_path.name} has no audio track, so strikes cannot be "
                    "detected. Pass strike times manually with --strikes."
                )
        else:
            try:
                wav = audio.extract_audio(video_path, work_dir / "audio.wav")
                strikes = audio.detect_strikes(wav, cfg)
            except Exception:
                if guided:
                    raise CoreReportBundleError("guided strike detection failed") from None
                raise
        if not strikes:
            if guided:
                if ReasonCode.NO_RELIABLE_STRIKE_EVENT not in reason_codes:
                    reason_codes.append(ReasonCode.NO_RELIABLE_STRIKE_EVENT)
            else:
                raise ZeroStrikesError(
                    f"No ball strikes detected in {video_path.name}. If the video "
                    "does contain swings, lower detection.audio_height in config, "
                    "or pass times manually: --strikes \"12.5,31.0\"."
                )
        else:
            log(f"Detected {len(strikes)} strike(s): "
                + ", ".join(f"{t:.2f}s" for t in strikes))
    if guided and not strikes and ReasonCode.NO_RELIABLE_STRIKE_EVENT not in reason_codes:
        reason_codes.append(ReasonCode.NO_RELIABLE_STRIKE_EVENT)

    # Strike cap: analyze the FIRST N strikes, in clip order, and say so
    # honestly in the session notes — never silently drop swings.
    max_strikes = int(cfg.detection.get("max_strikes") or 0)
    strike_cap_note = None
    if max_strikes and len(strikes) > max_strikes:
        strike_cap_note = (
            f"Clip had {len(strikes)} strikes; analyzed the first "
            f"{max_strikes} (the configured per-clip limit). Split longer "
            "sessions into shorter clips for full coverage."
        )
        log(strike_cap_note)
        strikes = strikes[:max_strikes]

    # --- per swing -------------------------------------------------------
    if progress:
        progress(0, len(strikes))
    tracker: pose.PoseTracker | None = pose.PoseTracker() if strikes else None
    swings: list[dict] = []
    all_metrics: list[metrics.SwingMetrics] = []
    skipped: list[str] = []
    evidence_snapshots: list[EvidenceSnapshot] = []
    guided_replay_locked = bool(
        guided
        and report_entitlements is not None
        and report_entitlements.coach_replay == "locked"
    )
    guided_replay_available = bool(
        guided
        and report_entitlements is not None
        and report_entitlements.coach_replay == "available"
    )
    # The pattern's own snapshot field — deliberately not replay_locked,
    # which is False when the annotated renderer is merely disabled.
    guided_pattern_locked = bool(
        guided
        and report_entitlements is not None
        and report_entitlements.swing_pattern == "locked"
    )
    try:
        for swing_no, strike_s in enumerate(strikes, start=1):
            try:
                assert tracker is not None
                analyzed = _analyze_swing(
                    video_path, strike_s, swing_no, tracker, work_dir, media_dir,
                    session_dir, hand, cfg, fast, log, angle,
                    analysis_fps=analysis_fps, replay_locked=replay_locked,
                    impact_method=impact_method, guided=guided,
                    guided_replay_available=guided_replay_available,
                )
                if guided:
                    swing, snapshot = analyzed
                    evidence_snapshots.append(snapshot)
                else:
                    swing = analyzed
                swings.append(swing)
                all_metrics.append(swing["metrics"])
            except EventError as exc:
                msg = f"Swing {swing_no} at {strike_s:.2f}s skipped: {exc}"
                log(f"WARNING: {msg}")
                skipped.append(msg)
                if guided:
                    reason = _EVENT_REASON_CODES.get(
                        exc.reason, ReasonCode.NO_READABLE_SWING
                    )
                    if reason not in reason_codes:
                        reason_codes.append(reason)
            except Exception:
                if guided:
                    raise CoreReportBundleError("guided swing analysis failed") from None
                raise
            if progress:
                progress(swing_no, len(strikes))
    finally:
        if tracker is not None:
            tracker.close()

    if not swings and not guided:
        raise ZeroStrikesError(
            "Strikes were detected but no swing could be analyzed (pose "
            "tracking failed in every window). Check that the golfer is fully "
            "visible; details: " + "; ".join(skipped)
        )

    # --- session outputs -------------------------------------------------
    stats = metrics.session_stats(all_metrics)
    notes = make_session_notes(all_metrics, stats, cfg)
    if strike_cap_note:
        notes.append(strike_cap_note)
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
        if guided:
            reason_codes.append(ReasonCode.CAMERA_ANGLE_MISMATCH)
    if angle == ANGLE_DTL:
        notes.insert(0, DTL_SESSION_NOTE)
    meta = {
        "camera_angle": angle,
        "club": club,
        "level": level,
        "hand": hand,
        # The frame rate the analysis windows were actually extracted at
        # (analysis.fps, or the auto-fps pick for high-fps sources) — the
        # resolution every timing number in this file is quantized to.
        "analysis_fps": analysis_fps,
    }
    if guided:
        assert attempt is not None and guided_html_writer is not None
        staged = build_report_bundle(
            attempt,
            html_writer=guided_html_writer,
            video=_guided_video_info(info),
            swings=swings,
            stats=stats,
            session_notes=notes,
            hand=hand,
            cfg=cfg,
            angle=angle,
            club=club,
            level=level,
            analysis_fps=analysis_fps,
            replay_locked=guided_replay_locked,
            swing_pattern_locked=guided_pattern_locked,
            evidence_snapshots=evidence_snapshots,
            reason_codes=tuple(dict.fromkeys(reason_codes)),
        )
        published = publish_report_bundle(staged)
        result_swings = _published_guided_swings(swings, published)
        return SessionResult(
            session_dir=session_dir,
            report_path=published.report_path,
            metrics_path=published.root / "metrics.json",
            video=info,
            swings=result_swings,
            stats=stats,
            skipped=skipped,
            report_view_path=published.report_view_path,
            manifest_path=published.manifest_path,
            checksums_path=published.checksums_path,
            structured_report=True,
            evidence_snapshots=evidence_snapshots,
        )

    report_path = report.write_report_html(
        session_dir / "report.html", info, swings, stats, notes, hand, cfg,
        angle=angle, club=club, level=level, analysis_fps=analysis_fps,
        # The locked note only exists where a replay would otherwise have
        # been rendered — an instance with slowmo.annotated off has no
        # replay feature to sell, so it never shows one.
        replay_locked=replay_locked and bool(cfg.slowmo["annotated"]),
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
    analysis_fps: float | None = None,
    replay_locked: bool = False,
    impact_method: PhaseMethod = PhaseMethod.DETECTED_AUDIO,
    guided: bool = False,
    guided_replay_available: bool = False,
) -> dict | tuple[dict, EvidenceSnapshot]:
    log(f"Swing {swing_no}: analyzing strike at {strike_s:.2f}s...")
    frameset = frames.extract_window(
        video_path, strike_s, work_dir, swing_no, cfg, fps=analysis_fps
    )
    observations: list[pose.PoseObservation | None] = []
    if guided:
        observations = [tracker.detect_observation(p) for p in frameset.paths]
        tracked = [
            observation.landmarks if observation is not None else None
            for observation in observations
        ]
    else:
        tracked = [tracker.detect(p) for p in frameset.paths]
    ev = events.detect_events(tracked, frameset, strike_s, cfg)
    finish_idx = frameset.index_near(ev.finish_s)
    m = metrics.compute_metrics(
        swing_no, tracked, ev, finish_idx, hand, cfg=cfg, angle=angle,
        fps=frameset.fps,
    )
    # Tracking confidence: when too many frames were dropped, or a core
    # landmark teleported between adjacent frames (the lock-onto-someone-else
    # signature), this swing's notes carry an honest low-confidence line.
    quality = pose.tracking_quality(tracked, ev.shoulder_width_px)
    notes = swing_notes(m, cfg)
    if quality.poor:
        log(
            f"WARNING: Swing {swing_no}: tracking unstable "
            f"({quality.dropped_fraction:.0%} of frames dropped, max core "
            f"jump {quality.max_core_jump_sw:.2f} SW) — numbers may be off."
        )
        notes.append(TRACKING_UNSTABLE_NOTE)

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

    if not guided:
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
        **(
            {"finish": (tracked[finish_idx], frameset.paths[finish_idx])}
            if guided else {}
        ),
    }
    if guided:
        fullres_lm = {
            key: _scaled_analysis_landmarks(fullres[key], a_lm, a_frame)
            for key, (a_lm, a_frame) in analysis_by_key.items()
        }
    else:
        fullres_lm = {
            key: _fullres_landmarks(tracker, fullres[key], a_lm, a_frame)
            for key, (a_lm, a_frame) in analysis_by_key.items()
        }

    if not guided:
        overlay_path = overlay.make_overlay(
            {k: fullres[k] for k in ("address", "top", "impact")},
            fullres_lm,
            m.target_direction,
            media_dir / f"overlay_s{swing_no}.png",
            cfg,
        )

    log(
        f"Swing {swing_no}: rendering slow motion"
        + (" (fast mode)..." if fast else " (the long step)...")
    )
    slowmo_out = media_dir / f"slowmo_s{swing_no}.mp4"
    if guided:
        slowmo_path = _optional_guided_media(
            label=f"Swing {swing_no} slow motion",
            output=slowmo_out,
            media_dir=media_dir,
            session_anchor=session_dir,
            render=lambda: slowmo.make_slowmo(
                video_path, strike_s, slowmo_out, cfg, fast=fast
            ),
            log=log,
        )
    else:
        slowmo_path = slowmo.make_slowmo(
            video_path, strike_s, slowmo_out, cfg, fast=fast
        )

    # Annotated replay: the golfer's own footage with the tracked skeleton,
    # fading hand-path trace, and event chips burned in. Never motion-
    # interpolated (see annotate.py), so it is identical in --fast mode;
    # slowmo.annotated is the feature switch, and replay_locked (the
    # per-job Pro gate, decided by the caller) skips the render entirely —
    # no file, no CPU spent on it.
    replay_path: Path | None = None
    render_replay = (
        cfg.slowmo["annotated"]
        and (guided_replay_available if guided else not replay_locked)
        and (not guided or angle != ANGLE_DTL)
    )
    if render_replay:
        log(f"Swing {swing_no}: rendering annotated replay...")
        replay_out = media_dir / f"replay_s{swing_no}.mp4"

        def render_annotated_replay() -> Path:
            replay_frames = slowmo.extract_replay_frames(
                video_path, strike_s, work_dir / f"replay_s{swing_no}", cfg
            )
            return annotate.make_replay(
                replay_frames, frameset, tracked, ev, m, replay_out, cfg
            )

        if guided:
            replay_path = _optional_guided_media(
                label=f"Swing {swing_no} coach replay",
                output=replay_out,
                media_dir=media_dir,
                session_anchor=session_dir,
                render=render_annotated_replay,
                log=log,
            )
        else:
            replay_path = render_annotated_replay()

    swing = {
        "metrics": m,
        "notes": notes,
        # Camera-angle sanity check input: what the address pose looks like
        # (face-on/dtl/None). Consumed by analyze_video, never serialized.
        "apparent_angle": metrics.apparent_camera_angle(tracked[ev.address_idx]),
    }
    if guided:
        if angle != ANGLE_DTL:
            strip_out = media_dir / f"strip_s{swing_no}.png"
            strip_path = _optional_guided_media(
                label=f"Swing {swing_no} key-position strip",
                output=strip_out,
                media_dir=media_dir,
                session_anchor=session_dir,
                render=lambda: strip.make_strip(
                    [fullres[k] for k in ("address", "top", "impact", "finish")],
                    swing_no,
                    strip_out,
                    cfg,
                ),
                log=log,
            )
            if strip_path is not None:
                swing["strip"] = strip_path
        if slowmo_path is not None:
            swing["slowmo"] = slowmo_path
        if replay_path is not None:
            swing["replay"] = replay_path
        snapshot = build_evidence_snapshot(
            swing=swing_no,
            frameset=frameset,
            observations=observations,
            events=ev,
            finish_idx=finish_idx,
            metrics=m,
            event_frames={EventId(key): path for key, path in fullres.items()},
            event_landmarks={
                EventId(key): landmarks for key, landmarks in fullres_lm.items()
            },
            impact_method=impact_method,
            tracking_quality=quality,
            hand=hand,
        )
        return swing, snapshot

    return {
        "metrics": m,
        "notes": notes,
        # report-relative paths so the session folder is portable
        "strip": str(strip_path.relative_to(session_dir)),
        "overlay": str(overlay_path.relative_to(session_dir)),
        "slowmo": str(slowmo_path.relative_to(session_dir)),
        "replay": str(replay_path.relative_to(session_dir)) if replay_path else None,
        "apparent_angle": swing["apparent_angle"],
    }
