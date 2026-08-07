"""The public sample report — the product's wow-moment, un-walled.

Renders one complete, realistic example report from synthetic session data
(the same technique the end-to-end tests use: a fake VideoInfo plus
hand-written SwingMetrics run through the REAL coaching/report machinery),
so a visitor can see exactly what they'd get before creating an account.

Honesty rules: the page carries a banner saying it is a sample, the
stand-in imagery is drawn (labeled, never pretending to be footage), the
video sections are simply omitted, and every number goes through the same
notes/flags/issue-cards/practice-plan code a real session does — nothing is
mocked downstream of the metrics.

The web app calls :func:`ensure_sample_report` once at startup; the report
is only generated when absent, and GET /sample-report serves it with no
auth (see web/app.py).
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
import tempfile

import numpy as np
from PIL import Image, ImageDraw

from . import pose
from .coaching import session_notes as make_session_notes
from .coaching import swing_notes
from .config import Config
from .drawing import draw_skeleton, load_font
from .ffmpeg import VideoInfo
from .metrics import SwingMetrics, session_stats
from .report import (
    REPORT_FORMAT_VERSION,
    REPORT_PRESENTATION_VERSION,
    write_report_html,
)
from .report_html import write_report_document_html
from .report_presenter import (
    ReportNavigation,
    build_report_document,
    prepare_report_input,
)
from .report_view import (
    BenchmarkRelation,
    CoachingReportView,
    Entitlement,
    EventId,
    EventProvenance,
    EvidenceKind,
    GUIDED_REPORT_PRESENTATION_VERSION,
    MediaEntry,
    MediaRole,
    MeasurementDetail,
    MeasurementUnit,
    PhaseId,
    PhaseMethod,
    RenderedEvidence,
    TrackingState,
)

# What the banner on top of the sample says. cta_url is the app's landing
# page, where signup lives.
BANNER_TEXT = "This is a sample session — film your own free"
BANNER_CTA = "Sign up free \N{RIGHTWARDS ARROW}"


def sample_video() -> VideoInfo:
    return VideoInfo(
        path=Path("sample-range-session.mov"),
        duration_s=21.0,
        width=1080,
        height=1920,
        fps=30.0,
        rotation=0,
        creation_time=None,
        has_audio=True,
    )


def sample_metrics() -> list[SwingMetrics]:
    """Three believable mid-handicap swings: tempo and head sway flagged,
    everything else inside the lines (so the praise strip has content and
    the deferred-issues list stays short — a realistic day one)."""
    return [
        SwingMetrics(
            swing=1, strike_s=3.2, backswing_s=0.78, downswing_s=0.34,
            tempo_ratio=2.29, head_sway_backswing_sw=0.41,
            head_sway_downswing_sw=-0.12, hip_slide_backswing_sw=0.22,
            hip_slide_downswing_sw=-0.18, target_direction=1,
            head_dip_sw=0.08, lead_arm_angle_deg=168.0,
            shoulder_tilt_address_deg=9.0, shoulder_tilt_impact_deg=13.0,
            shoulder_tilt_delta_deg=4.0, finish_balance_sw=0.06,
            stance_width_sw=0.96, downswing_hand_speed_sw_s=4.82,
        ),
        SwingMetrics(
            swing=2, strike_s=9.8, backswing_s=0.81, downswing_s=0.35,
            tempo_ratio=2.31, head_sway_backswing_sw=0.38,
            head_sway_downswing_sw=-0.10, hip_slide_backswing_sw=0.19,
            hip_slide_downswing_sw=-0.21, target_direction=1,
            head_dip_sw=0.11, lead_arm_angle_deg=171.0,
            shoulder_tilt_address_deg=8.0, shoulder_tilt_impact_deg=12.0,
            shoulder_tilt_delta_deg=4.0, finish_balance_sw=0.04,
            stance_width_sw=0.98, downswing_hand_speed_sw_s=4.91,
        ),
        SwingMetrics(
            swing=3, strike_s=16.4, backswing_s=0.84, downswing_s=0.33,
            tempo_ratio=2.55, head_sway_backswing_sw=0.33,
            head_sway_downswing_sw=-0.14, hip_slide_backswing_sw=0.24,
            hip_slide_downswing_sw=-0.16, target_direction=1,
            head_dip_sw=0.09, lead_arm_angle_deg=166.0,
            shoulder_tilt_address_deg=10.0, shoulder_tilt_impact_deg=14.0,
            shoulder_tilt_delta_deg=4.0, finish_balance_sw=0.07,
            stance_width_sw=0.95, downswing_hand_speed_sw_s=4.76,
        ),
    ]


# -- stand-in imagery ---------------------------------------------------------
# Drawn skeleton figures on labeled panels — clearly illustrations, never
# fake footage. Coordinates live in a 0..1 unit square, scaled per panel.

_POSES: dict[str, dict[int, tuple[float, float]]] = {
    "address": {
        pose.NOSE: (0.50, 0.12),
        pose.LEFT_EAR: (0.53, 0.13), pose.RIGHT_EAR: (0.47, 0.13),
        pose.LEFT_SHOULDER: (0.62, 0.30), pose.RIGHT_SHOULDER: (0.38, 0.30),
        pose.LEFT_ELBOW: (0.60, 0.46), pose.RIGHT_ELBOW: (0.40, 0.46),
        pose.LEFT_WRIST: (0.51, 0.60), pose.RIGHT_WRIST: (0.49, 0.60),
        pose.LEFT_HIP: (0.58, 0.55), pose.RIGHT_HIP: (0.42, 0.55),
        pose.LEFT_KNEE: (0.58, 0.72), pose.RIGHT_KNEE: (0.42, 0.72),
        pose.LEFT_ANKLE: (0.59, 0.90), pose.RIGHT_ANKLE: (0.41, 0.90),
    },
    "top": {
        pose.NOSE: (0.46, 0.13),
        pose.LEFT_EAR: (0.49, 0.14), pose.RIGHT_EAR: (0.43, 0.14),
        pose.LEFT_SHOULDER: (0.55, 0.31), pose.RIGHT_SHOULDER: (0.36, 0.29),
        pose.LEFT_ELBOW: (0.44, 0.20), pose.RIGHT_ELBOW: (0.32, 0.18),
        pose.LEFT_WRIST: (0.34, 0.10), pose.RIGHT_WRIST: (0.30, 0.09),
        pose.LEFT_HIP: (0.56, 0.55), pose.RIGHT_HIP: (0.41, 0.55),
        pose.LEFT_KNEE: (0.57, 0.72), pose.RIGHT_KNEE: (0.42, 0.72),
        pose.LEFT_ANKLE: (0.59, 0.90), pose.RIGHT_ANKLE: (0.41, 0.90),
    },
    "impact": {
        pose.NOSE: (0.48, 0.12),
        pose.LEFT_EAR: (0.51, 0.13), pose.RIGHT_EAR: (0.45, 0.13),
        pose.LEFT_SHOULDER: (0.60, 0.29), pose.RIGHT_SHOULDER: (0.39, 0.32),
        pose.LEFT_ELBOW: (0.63, 0.45), pose.RIGHT_ELBOW: (0.43, 0.47),
        pose.LEFT_WRIST: (0.56, 0.60), pose.RIGHT_WRIST: (0.54, 0.61),
        pose.LEFT_HIP: (0.60, 0.55), pose.RIGHT_HIP: (0.45, 0.55),
        pose.LEFT_KNEE: (0.60, 0.72), pose.RIGHT_KNEE: (0.45, 0.72),
        pose.LEFT_ANKLE: (0.61, 0.90), pose.RIGHT_ANKLE: (0.42, 0.90),
    },
    "finish": {
        pose.NOSE: (0.56, 0.12),
        pose.LEFT_EAR: (0.59, 0.13), pose.RIGHT_EAR: (0.53, 0.13),
        pose.LEFT_SHOULDER: (0.63, 0.29), pose.RIGHT_SHOULDER: (0.51, 0.30),
        pose.LEFT_ELBOW: (0.70, 0.22), pose.RIGHT_ELBOW: (0.60, 0.21),
        pose.LEFT_WRIST: (0.73, 0.12), pose.RIGHT_WRIST: (0.68, 0.11),
        pose.LEFT_HIP: (0.62, 0.55), pose.RIGHT_HIP: (0.52, 0.55),
        pose.LEFT_KNEE: (0.62, 0.72), pose.RIGHT_KNEE: (0.52, 0.73),
        pose.LEFT_ANKLE: (0.62, 0.90), pose.RIGHT_ANKLE: (0.50, 0.90),
    },
}

_PANEL_W, _PANEL_H = 320, 460
_BG = "#25302a"          # muted range-at-dusk green, clearly not a photo
_GROUND = "#1c241f"
_LABEL = "#ffffff"


def _panel_landmarks(key: str, w: int, h: int, dx: float = 0.0) -> pose.Landmarks:
    return {
        idx: np.array([(x + dx) * w, y * h], dtype=np.float64)
        for idx, (x, y) in _POSES[key].items()
    }


def _draw_panel(
    draw: ImageDraw.ImageDraw,
    x0: int,
    key: str,
    label: str,
    color: str,
    ghost: str | None = None,
    ghost_dx: float = 0.0,
) -> None:
    w, h = _PANEL_W, _PANEL_H
    draw.rectangle([x0, 0, x0 + w, h], fill=_BG)
    draw.rectangle([x0, int(h * 0.88), x0 + w, h], fill=_GROUND)
    if ghost is not None:
        lm = {
            k: v + np.array([x0, 0.0])
            for k, v in _panel_landmarks(key, w, h, dx=ghost_dx).items()
        }
        draw_skeleton(draw, lm, ghost, shoulder_width_px=0.2 * w, line_w=3)
    lm = {
        k: v + np.array([x0, 0.0])
        for k, v in _panel_landmarks(key, w, h).items()
    }
    draw_skeleton(draw, lm, color, shoulder_width_px=0.2 * w, line_w=4)
    font = load_font(22)
    draw.text((x0 + 12, 10), label, fill=_LABEL, font=font)
    draw.line([(x0 + w, 0), (x0 + w, h)], fill="#111111", width=2)


def _footer(img: Image.Image, text: str) -> None:
    draw = ImageDraw.Draw(img)
    # Auto-fit: step the size down until the caption fits inside the image —
    # the honesty label must never clip mid-sentence (the overlay is only
    # 3 panels wide, and the text length varies with translations/branding).
    max_w = img.width - 24
    size = 16
    font = load_font(size)
    while size > 11 and draw.textlength(text, font=font) > max_w:
        size -= 1
        font = load_font(size)
    draw.rectangle([0, img.height - 30, img.width, img.height], fill="#101512")
    draw.text((12, img.height - 30 + (28 - size) // 2), text,
              fill="#c9d4cc", font=font)


def draw_sample_strip(out_path: Path, swing_no: int, cfg: Config) -> Path:
    """Key-position stand-in: four labeled skeleton panels."""
    img = Image.new("RGB", (4 * _PANEL_W, _PANEL_H + 30), _BG)
    draw = ImageDraw.Draw(img)
    color = cfg.brand.get("accent_color") or "#e8720c"
    for i, key in enumerate(("address", "top", "impact", "finish")):
        _draw_panel(draw, i * _PANEL_W, key, key.capitalize(), color)
    _footer(
        img,
        f"Sample illustration — swing {swing_no}. Your report shows these "
        "frames from your own footage.",
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


def draw_sample_overlay(out_path: Path, swing_no: int, cfg: Config) -> Path:
    """Centerline-overlay stand-in: captured (accent) vs corrected (green)."""
    img = Image.new("RGB", (3 * _PANEL_W, _PANEL_H + 30), _BG)
    draw = ImageDraw.Draw(img)
    captured = cfg.overlay.get("captured_color") or "#ff8c1a"
    corrected = cfg.overlay.get("corrected_color") or "#2ecc40"
    for i, key in enumerate(("address", "top", "impact")):
        _draw_panel(
            draw, i * _PANEL_W, key, key.capitalize(), captured,
            ghost=corrected, ghost_dx=-0.04 if key == "top" else 0.0,
        )
    _footer(
        img,
        f"Sample illustration — swing {swing_no}: captured body vs corrected "
        "position. Your report draws this over your own frames.",
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


_SAMPLE_OBSERVED = "#e8720c"
_SAMPLE_REFERENCE = "#2ecc40"
_SAMPLE_SILHOUETTE = "#f3eee2"
_SAMPLE_BOUNDARY = "#f6d7a8"


def draw_sample_focused_evidence(out_path: Path, cfg: Config) -> Path:
    """Draw honest, cropped sample evidence without a pose skeleton.

    This is a diagram of the evidence vocabulary, not generated golfer
    footage and not a body correction. Filled shapes keep the golfer readable
    at report scale while the only semantic marks remain observed, starting
    reference, and coaching boundary.
    """
    del cfg  # Semantic evidence colors stay stable across brand themes.
    size = 900
    image = Image.new("RGB", (size, size), _BG)
    draw = ImageDraw.Draw(image)
    title_font = load_font(34)
    label_font = load_font(24)
    small_font = load_font(19)

    draw.text(
        (52, 42),
        "SYNTHETIC SAMPLE · CROPPED FOCUS",
        fill="#ffffff",
        font=title_font,
    )
    draw.text(
        (52, 91),
        "Head position near the top",
        fill="#c9d4cc",
        font=small_font,
    )

    # Starting reference zone: a filled, rounded region rather than a ghost
    # body. It describes only the head's opening location.
    draw.rounded_rectangle(
        (245, 165, 405, 325),
        radius=38,
        fill="#214e31",
        outline=_SAMPLE_REFERENCE,
        width=10,
    )
    draw.rectangle((72, 171, 102, 201), fill=_SAMPLE_REFERENCE)
    draw.text((118, 171), "STARTING ZONE", fill="#ffffff", font=label_font)

    # Dashed coaching boundary between the starting zone and observed head.
    boundary_x = 454
    for y in range(142, 574, 34):
        draw.line(
            (boundary_x, y, boundary_x, min(y + 18, 574)),
            fill=_SAMPLE_BOUNDARY,
            width=7,
        )
    draw.line((454, 548, 720, 548), fill=_SAMPLE_BOUNDARY, width=3)
    draw.text(
        (528, 558),
        "COACHING BOUNDARY",
        fill="#ffffff",
        font=label_font,
    )

    # Cropped, filled upper-body silhouette. No joints, landmark lines,
    # skeleton, corrected ghost, or full-body comparison is drawn.
    draw.polygon(
        ((392, 439), (505, 365), (640, 399), (732, 521),
         (704, 812), (332, 812), (324, 580)),
        fill=_SAMPLE_SILHOUETTE,
    )
    draw.polygon(
        ((485, 408), (397, 458), (249, 384), (216, 431),
         (383, 545), (548, 478)),
        fill=_SAMPLE_SILHOUETTE,
    )
    draw.polygon(
        ((562, 410), (665, 452), (771, 351), (810, 395),
         (700, 548), (525, 485)),
        fill=_SAMPLE_SILHOUETTE,
    )
    draw.ellipse((495, 202, 635, 342), fill=_SAMPLE_OBSERVED)
    draw.rectangle((710, 211, 740, 241), fill=_SAMPLE_OBSERVED)
    draw.text((754, 211), "OBSERVED", fill="#ffffff", font=label_font)
    draw.line((710, 242, 630, 267), fill=_SAMPLE_OBSERVED, width=5)

    _footer(
        image,
        "Sample illustration — not golfer footage or a predicted ideal pose.",
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)
    return out_path


# -- assembly -----------------------------------------------------------------

def build_sample_swings(sample_dir: Path, cfg: Config) -> list[dict]:
    """Swing dicts in the exact shape the pipeline hands the report writer —
    real coaching notes, drawn stand-in imagery, no video (the template
    omits absent sections gracefully)."""
    swings = []
    for m in sample_metrics():
        n = m.swing
        strip_rel = f"media/strip_s{n}.png"
        overlay_rel = f"media/overlay_s{n}.png"
        draw_sample_strip(sample_dir / strip_rel, n, cfg)
        draw_sample_overlay(sample_dir / overlay_rel, n, cfg)
        swings.append({
            "metrics": m,
            "notes": swing_notes(m, cfg),
            "strip": strip_rel,
            "overlay": overlay_rel,
            "slowmo": None,   # no synthetic footage — section omitted
            "replay": None,
        })
    return swings


def _report_is_current(report_path: Path, presentation_version: str) -> bool:
    if not report_path.is_file():
        return False
    try:
        existing = report_path.read_text(encoding="utf-8")
    except OSError:
        return False
    format_marker = (
        'name="caddieinsight-report-format" '
        f'content="{REPORT_FORMAT_VERSION}"'
    )
    presentation_marker = (
        'name="caddieinsight-report-presentation" '
        f'content="{presentation_version}"'
    )
    return format_marker in existing and presentation_marker in existing


def build_legacy_sample_report(sample_dir: Path, cfg: Config) -> Path:
    """Build or refresh the rollback-safe legacy public sample."""
    sample_dir = Path(sample_dir)
    report_path = sample_dir / "report.html"
    if _report_is_current(report_path, REPORT_PRESENTATION_VERSION):
        return report_path
    sample_dir.mkdir(parents=True, exist_ok=True)
    swings = build_sample_swings(sample_dir, cfg)
    all_metrics = [s["metrics"] for s in swings]
    stats = session_stats(all_metrics)
    notes = make_session_notes(all_metrics, stats, cfg)
    temporary_report = report_path.with_name(".report.html.tmp")
    write_report_html(
        temporary_report,
        sample_video(),
        swings,
        stats,
        notes,
        "right",
        cfg,
        club="iron",
        sample_banner={
            "text": BANNER_TEXT,
            "cta_label": BANNER_CTA,
            "cta_url": "/",
        },
    )
    temporary_report.replace(report_path)
    return report_path


def _media_entry(
    *,
    key: str,
    role: MediaRole,
    entitlement: Entitlement,
    relative_path: str,
    path: Path,
) -> MediaEntry:
    return MediaEntry(
        key=key,
        role=role,
        mime_type="image/png",
        entitlement=entitlement,
        relative_path=relative_path,
        checksum_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def build_guided_sample_swings(
    sample_dir: Path, cfg: Config
) -> tuple[list[dict[str, object]], tuple[MediaEntry, ...]]:
    """Build guided depth rows with stable strip keys and no overlays."""
    swings: list[dict[str, object]] = []
    media: list[MediaEntry] = []
    for metric in sample_metrics():
        swing_no = metric.swing
        relative_path = f"media/strip_s{swing_no}.png"
        path = draw_sample_strip(sample_dir / relative_path, swing_no, cfg)
        key = f"sample-strip-{swing_no}"
        media.append(
            _media_entry(
                key=key,
                role=MediaRole.KEY_POSITIONS,
                entitlement=Entitlement.FREE,
                relative_path=relative_path,
                path=path,
            )
        )
        swings.append(
            {
                "metrics": metric,
                "notes": swing_notes(metric, cfg),
                "strip": key,
                "slowmo": None,
                "replay": None,
            }
        )
    return swings, tuple(media)


def _sample_focused_evidence(media_key: str) -> RenderedEvidence:
    return RenderedEvidence(
        kind=EvidenceKind.HEAD_BOUNDARY,
        state="rendered",
        media_key=media_key,
        swing=1,
        phase=PhaseId.GOING_BACK,
        phase_method=PhaseMethod.HIGHEST_TRACKED_HANDS,
        timestamp_ms=2860,
        events=(
            EventProvenance(
                event=EventId.TOP,
                method=PhaseMethod.HIGHEST_TRACKED_HANDS,
                timestamp_ms=2860,
                label="Top estimate",
            ),
        ),
        tracking_state=TrackingState.CLEAR,
        tracking_reasons=(),
        render_reasons=(),
        observed_label="Head position near the top",
        reference_label="Starting head-position zone",
        boundary_label="0.35 shoulder-width coaching boundary",
        readable_swings=3,
        triggered_swings=2,
        supporting_measurement=MeasurementDetail(
            id="measurement-head_sway_backswing_sw",
            label="Head sway going back",
            plain_value="0.37 shoulder widths",
            numeric_value=0.37,
            unit=MeasurementUnit.SHOULDER_WIDTHS,
            benchmark_relation=BenchmarkRelation.ABOVE,
            benchmark_value=0.35,
            benchmark_upper_value=None,
            benchmark_label="Coaching line: 0.35 shoulder widths",
            explanation="How far the head moved from address to the top estimate.",
            limitation="A face-on 2D estimate, not a 3D center-of-mass measure.",
        ),
        observation=(
            "The head moved beyond its starting reference zone on two of "
            "three readable swings."
        ),
        alt_text=(
            "Sample illustration of swing 1 near the top. An orange head "
            "marker sits outside the green starting zone and beyond a dashed "
            "coaching boundary."
        ),
    )


def build_guided_sample_report(sample_dir: Path, cfg: Config) -> Path:
    """Build or refresh the explicit guided public-sample preview."""
    sample_dir = Path(sample_dir)
    report_path = sample_dir / "report.html"
    if _report_is_current(report_path, GUIDED_REPORT_PRESENTATION_VERSION):
        return report_path

    sample_dir.mkdir(parents=True, exist_ok=True)
    media_dir = sample_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    focused_relative_path = "media/focused-priority.png"
    focused_path = draw_sample_focused_evidence(
        sample_dir / focused_relative_path, cfg
    )
    focused_media = _media_entry(
        key="sample-focused-priority",
        role=MediaRole.PRIORITY_EVIDENCE,
        entitlement=Entitlement.CORE,
        relative_path=focused_relative_path,
        path=focused_path,
    )
    swings, strip_media = build_guided_sample_swings(sample_dir, cfg)
    all_metrics = [swing["metrics"] for swing in swings]
    stats = session_stats(all_metrics)
    notes = make_session_notes(all_metrics, stats, cfg)
    media = (focused_media, *strip_media)
    source = prepare_report_input(
        sample_video(),
        swings,
        stats,
        notes,
        "right",
        cfg,
        angle="face_on",
        club="iron",
        analysis_fps=30.0,
        visual_evidence=_sample_focused_evidence(focused_media.key),
        media=media,
        navigation=ReportNavigation(
            app_url="/",
            storefront_url=None,
            gear_collection_url=None,
        ),
    )
    document = build_report_document(source, cfg)
    if not isinstance(document.view, CoachingReportView):
        raise ValueError("guided sample requires a coaching-ready document")
    document = replace(
        document,
        view=replace(
            document.view,
            next_move=replace(
                document.view.next_move,
                eyebrow="Your next move",
            ),
        ),
    )
    temporary_report = report_path.with_name(".report.html.tmp")
    write_report_document_html(
        temporary_report,
        document,
        cfg=cfg,
        sample_banner={
            "text": BANNER_TEXT,
            "cta_label": BANNER_CTA,
            "cta_url": "/",
        },
    )
    overlay_paths = tuple(
        media_dir / f"overlay_s{swing_no}.png" for swing_no in (1, 2, 3)
    )
    backup_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{sample_dir.name}-legacy-overlays-",
            dir=sample_dir.parent,
        )
    )
    staged: list[tuple[Path, Path]] = []
    try:
        for overlay_path in overlay_paths:
            if overlay_path.is_file():
                backup_path = backup_dir / overlay_path.name
                overlay_path.replace(backup_path)
                staged.append((overlay_path, backup_path))
        temporary_report.replace(report_path)
    except BaseException:
        for overlay_path, backup_path in reversed(staged):
            if backup_path.is_file():
                backup_path.replace(overlay_path)
        try:
            backup_dir.rmdir()
        except OSError:
            pass
        raise
    for _, backup_path in staged:
        try:
            backup_path.unlink()
        except OSError:
            # The backup is outside the publicly served sample root. Retaining
            # it is safer than reporting activation failure after the atomic
            # HTML switch has completed.
            pass
    try:
        backup_dir.rmdir()
    except OSError:
        pass
    return report_path


def ensure_sample_report(sample_dir: Path, cfg: Config) -> Path:
    """Generate the configured synthetic public sample, defaulting legacy.

    Only the literal boolean True selects the guided presentation. This gate
    never changes customer-job presentation policy, and both branches replace
    only the synthetic ``sample-report/report.html`` atomically.
    """
    if cfg.report.get("guided_sample_enabled") is True:
        return build_guided_sample_report(sample_dir, cfg)
    return build_legacy_sample_report(sample_dir, cfg)
