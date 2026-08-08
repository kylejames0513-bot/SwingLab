"""Focused, provenance-preserving evidence artifacts for one selected swing."""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

from PIL import Image, ImageDraw

from . import pose
from .config import Config
from .drawing import draw_angle_arc, draw_dashed_line, draw_displacement_arrow, draw_labeled_timeline, draw_marker, load_font, save_branded
from .evidence import EvidenceSnapshot
from .metrics import finite_float, lead_trail_sides
from .report_presenter import EvidenceCandidate, PriorityEvidenceRule, select_representative_swing
from .report_view import (Entitlement, EventId, EventProvenance, EvidenceKind, MediaEntry, MediaRole, MeasurementDetail, PhaseId, ReasonCode, RenderedEvidence, TrackingState, UnavailableEvidence)


class FocusedEvidenceRenderError(RuntimeError): pass
class UnsupportedFocusedEvidence(ValueError): pass

@dataclass(frozen=True)
class FocusedEvidenceSelection:
    rule: PriorityEvidenceRule
    snapshot: EvidenceSnapshot | None
    metric_readable_swings: int
    annotation_readable_swings: int
    triggered_swings: int | None
    fatal_reason: ReasonCode | None
    session_value: float | None = None
    tempo_ratio_std: float | None = None

@dataclass(frozen=True)
class FocusedEvidenceArtifact:
    evidence: RenderedEvidence
    media: MediaEntry
    path: Path

def _gate(snapshot: EvidenceSnapshot, metric: str) -> bool:
    gate = snapshot.annotation_gates.get(metric)
    return bool(gate and gate.readable)

def _event(snapshot: EvidenceSnapshot, event: EventId | None):
    if event is None:
        return None
    return next((item for item in snapshot.events if item.event is event), None)

def select_focused_evidence(*, rule: PriorityEvidenceRule, snapshots: Sequence[EvidenceSnapshot], stats: Mapping[str, Mapping[str, float]]) -> FocusedEvidenceSelection:
    values = [(snapshot, finite_float(getattr(snapshot.metrics, rule.metric_id, None))) for snapshot in snapshots]
    metric_rows = [(snapshot, value) for snapshot, value in values if value is not None]
    canonical = finite_float(stats.get(rule.metric_id, {}).get("mean"))
    tempo_std = finite_float(stats.get("tempo_ratio", {}).get("std"))
    if not metric_rows:
        return FocusedEvidenceSelection(rule, None, 0, 0, None, ReasonCode.PRIORITY_EVIDENCE_UNRELIABLE)
    if rule.event is not None and not any(rule.event in snapshot.event_frames and snapshot.event_landmarks.get(rule.event) is not None for snapshot, _ in metric_rows):
        return FocusedEvidenceSelection(rule, None, len(metric_rows), 0, None, ReasonCode.NO_RELIABLE_STRIKE_EVENT)
    def crossed(value: float) -> bool | None:
        if rule.benchmark is None or rule.worse_direction is None:
            return None
        return value >= rule.benchmark if rule.worse_direction == "higher" else value <= rule.benchmark
    candidates = [EvidenceCandidate(snapshot.swing, value, _gate(snapshot, rule.metric_id) and (rule.event is None or rule.event in snapshot.event_frames and snapshot.event_landmarks.get(rule.event) is not None), crossed(value)) for snapshot, value in metric_rows]
    needs_session = rule.selection_basis in {"session_mean", "shoulder_tilt_delta_mean"}
    if needs_session and canonical is None:
        return FocusedEvidenceSelection(rule, None, len(metric_rows), 0, None, ReasonCode.PRIORITY_EVIDENCE_UNRELIABLE)
    selected = select_representative_swing(candidates, basis=rule.selection_basis, session_value=canonical if needs_session else None)
    chosen = next((snapshot for snapshot, _ in metric_rows if snapshot.swing == selected), None)
    readable = sum(candidate.eligible for candidate in candidates)
    triggered = sum(candidate.crossed_line is True for candidate in candidates) if rule.selection_basis == "threshold" else None
    return FocusedEvidenceSelection(rule, chosen, len(metric_rows), readable, triggered, None, canonical, tempo_std)

def _phase_event(rule: PriorityEvidenceRule, snapshot: EvidenceSnapshot):
    event = rule.event or {PhaseId.GOING_BACK: EventId.TOP, PhaseId.IMPACT: EventId.IMPACT, PhaseId.FINISH: EventId.FINISH}.get(rule.phase, EventId.ADDRESS)
    item = _event(snapshot, event)
    if item is None:
        raise FocusedEvidenceRenderError("selected snapshot lacks event provenance")
    return event, item

def _tracking(snapshot: EvidenceSnapshot) -> tuple[TrackingState, tuple[ReasonCode, ...]]:
    return (TrackingState.LIMITED, (ReasonCode.TRACKING_UNSTABLE,)) if snapshot.tracking_quality.poor else (TrackingState.CLEAR, ())

def _crop(image: Image.Image, points: list[tuple[float, float]]) -> tuple[Image.Image, tuple[int, int]]:
    w, h = image.size
    xs, ys = zip(*points)
    margin = 90
    left, top = max(0, int(min(xs) - margin)), max(0, int(min(ys) - margin))
    right, bottom = min(w, int(max(xs) + margin)), min(h, int(max(ys) + margin))
    return image.crop((left, top, right, bottom)), (left, top)

def _point(lm, index, offset): return (float(lm[index][0] - offset[0]), float(lm[index][1] - offset[1]))

@dataclass(frozen=True)
class _RenderContext:
    image: Image.Image
    draw: ImageDraw.ImageDraw
    landmarks: pose.Landmarks
    address: pose.Landmarks
    offset: tuple[int, int]
    orange: str
    green: str
    font: object
    event: EventId
    provenance: object


@dataclass(frozen=True)
class _RenderResult:
    image: Image.Image
    observed: str
    reference: str | None
    boundary: str | None
    alt: str
    event: EventId
    provenance: object


def _prepare_render(rule: PriorityEvidenceRule, snapshot: EvidenceSnapshot, cfg: Config, required: Sequence[int], extra_points: Sequence[tuple[float, float]] = ()) -> _RenderContext:
    event, provenance = _phase_event(rule, snapshot)
    landmarks = snapshot.event_landmarks.get(event)
    address = snapshot.event_landmarks.get(EventId.ADDRESS)
    source_path = snapshot.event_frames.get(event)
    if landmarks is None or address is None or source_path is None:
        raise FocusedEvidenceRenderError("selected evidence pixels or landmarks unavailable")
    if any(index not in landmarks or index not in address for index in required):
        raise FocusedEvidenceRenderError("required landmarks unavailable for focused annotation")
    crop_points = [tuple(landmarks[index]) for index in required]
    crop_points.extend(tuple(address[index]) for index in required)
    crop_points.extend(extra_points)
    try:
        with Image.open(source_path) as source:
            image, offset = _crop(source.convert("RGB"), crop_points)
    except (OSError, ValueError) as exc:
        raise FocusedEvidenceRenderError(str(exc)) from exc
    return _RenderContext(image, ImageDraw.Draw(image), landmarks, address, offset,
                          cfg.overlay["captured_color"], cfg.overlay["corrected_color"],
                          load_font(18), event, provenance)


def _body_result(snapshot: EvidenceSnapshot, ctx: _RenderContext, observed: str, reference: str | None, boundary: str | None) -> _RenderResult:
    ctx.draw.text((12, 12), observed, fill=ctx.orange, font=ctx.font)
    alt = (f"Swing {snapshot.swing}, {ctx.event.value}, observed {observed.lower()}; "
           f"{reference.lower() if reference else 'no reference'}"
           f"{'; ' + boundary.lower() if boundary else ''}; tracking {_tracking(snapshot)[0].value}; "
           f"event method {ctx.provenance.method.value}.")
    return _RenderResult(ctx.image, observed, reference, boundary, alt, ctx.event, ctx.provenance)


def _boundary_offset_px(rule: PriorityEvidenceRule, snapshot: EvidenceSnapshot) -> float | None:
    """Convert the configured shoulder-width benchmark into a pixel offset."""
    if rule.benchmark is None or not math.isfinite(rule.benchmark):
        return None
    if snapshot.shoulder_width_px <= 0:
        return None
    return float(snapshot.target_direction) * float(snapshot.shoulder_width_px) * float(rule.benchmark)


def _render_head_boundary(rule, snapshot, selection, cfg):
    ctx = _prepare_render(rule, snapshot, cfg, (pose.NOSE,))
    observed, start = _point(ctx.landmarks, pose.NOSE, ctx.offset), _point(ctx.address, pose.NOSE, ctx.offset)
    draw_marker(ctx.draw, start, ctx.green); draw_marker(ctx.draw, observed, ctx.orange); draw_displacement_arrow(ctx.draw, start, observed, ctx.orange)
    boundary = None
    offset = _boundary_offset_px(rule, snapshot) if snapshot.target_confident else None
    if offset is not None:
        x = start[0] + offset
        draw_dashed_line(ctx.draw, (x, 10), (x, ctx.image.height - 10), ctx.green)
        ctx.draw.text((x + 8, 20), "Configured coaching boundary", fill=ctx.green, font=ctx.font)
        boundary = "Configured coaching boundary"
    return _body_result(snapshot, ctx, "Observed head marker", "Address/start head reference", boundary)


def _render_hip_boundary(rule, snapshot, selection, cfg):
    ctx = _prepare_render(rule, snapshot, cfg, (pose.LEFT_HIP, pose.RIGHT_HIP))
    observed_raw = (ctx.landmarks[pose.LEFT_HIP] + ctx.landmarks[pose.RIGHT_HIP]) / 2
    start_raw = (ctx.address[pose.LEFT_HIP] + ctx.address[pose.RIGHT_HIP]) / 2
    observed = (float(observed_raw[0] - ctx.offset[0]), float(observed_raw[1] - ctx.offset[1]))
    start = (float(start_raw[0] - ctx.offset[0]), float(start_raw[1] - ctx.offset[1]))
    draw_marker(ctx.draw, start, ctx.green); draw_marker(ctx.draw, observed, ctx.orange); draw_displacement_arrow(ctx.draw, start, observed, ctx.orange)
    boundary = None
    offset = _boundary_offset_px(rule, snapshot) if snapshot.target_confident else None
    if offset is not None:
        x = start[0] + offset
        draw_dashed_line(ctx.draw, (x, 10), (x, ctx.image.height - 10), ctx.green)
        ctx.draw.text((x + 8, 20), "Configured coaching boundary", fill=ctx.green, font=ctx.font)
        boundary = "Configured coaching boundary"
    return _body_result(snapshot, ctx, "Observed hip midpoint", "Address/start hip reference", boundary)


def _render_head_height(rule, snapshot, selection, cfg):
    ctx = _prepare_render(rule, snapshot, cfg, (pose.NOSE,))
    observed, start = _point(ctx.landmarks, pose.NOSE, ctx.offset), _point(ctx.address, pose.NOSE, ctx.offset)
    draw_marker(ctx.draw, start, ctx.green); draw_marker(ctx.draw, observed, ctx.orange); draw_displacement_arrow(ctx.draw, start, observed, ctx.orange)
    ctx.draw.line((start[0], start[1], start[0], observed[1]), fill=ctx.green, width=3)
    return _body_result(snapshot, ctx, "Observed head-height marker", "Address head-height reference", None)


def _render_lead_arm_angle(rule, snapshot, selection, cfg):
    shoulder, elbow, wrist = lead_trail_sides(snapshot.hand)[0]
    ctx = _prepare_render(rule, snapshot, cfg, (shoulder, elbow, wrist))
    a, b, c = (_point(ctx.landmarks, index, ctx.offset) for index in (shoulder, elbow, wrist))
    ctx.draw.line((a, b, c), fill=ctx.orange, width=4)
    draw_marker(ctx.draw, b, ctx.orange); draw_angle_arc(ctx.draw, b, 180, 300, 35, ctx.orange)
    return _body_result(snapshot, ctx, "Observed lead-arm angle", "Tracked lead shoulder-elbow-wrist", None)


def _render_shoulder_tilt(rule, snapshot, selection, cfg):
    ctx = _prepare_render(rule, snapshot, cfg, (pose.LEFT_SHOULDER, pose.RIGHT_SHOULDER))
    ctx.draw.line((_point(ctx.address, pose.LEFT_SHOULDER, ctx.offset), _point(ctx.address, pose.RIGHT_SHOULDER, ctx.offset)), fill=ctx.green, width=4)
    ctx.draw.line((_point(ctx.landmarks, pose.LEFT_SHOULDER, ctx.offset), _point(ctx.landmarks, pose.RIGHT_SHOULDER, ctx.offset)), fill=ctx.orange, width=4)
    return _body_result(snapshot, ctx, "Observed impact shoulder line", "Address shoulder line", None)


def _render_finish_stability(rule, snapshot, selection, cfg):
    if not snapshot.finish_ankle_midpoints:
        raise FocusedEvidenceRenderError("finish midpoint path unavailable")
    ctx = _prepare_render(rule, snapshot, cfg, (pose.LEFT_ANKLE, pose.RIGHT_ANKLE), snapshot.finish_ankle_midpoints)
    path = [(float(x - ctx.offset[0]), float(y - ctx.offset[1])) for x, y in snapshot.finish_ankle_midpoints]
    if len(path) > 1:
        ctx.draw.line(path, fill=ctx.orange, width=3)
    for point in path:
        draw_marker(ctx.draw, point, ctx.orange, 5)
    draw_marker(ctx.draw, path[0], ctx.green, 6)
    draw_marker(ctx.draw, path[-1], ctx.orange, 7)
    return _body_result(snapshot, ctx, "Observed finish endpoint", "Finish-start ankle midpoint", None)


def _render_steady_reference(rule, snapshot, selection, cfg):
    if "hip" in rule.metric_id:
        required = (pose.LEFT_HIP, pose.RIGHT_HIP)
    elif "arm" in rule.metric_id:
        required = lead_trail_sides(snapshot.hand)[0]
    elif "shoulder" in rule.metric_id:
        required = (pose.LEFT_SHOULDER, pose.RIGHT_SHOULDER)
    elif "finish" in rule.metric_id:
        required = (pose.LEFT_ANKLE, pose.RIGHT_ANKLE)
    else:
        required = (pose.NOSE,)
    ctx = _prepare_render(rule, snapshot, cfg, required)
    observed, start = _point(ctx.landmarks, required[0], ctx.offset), _point(ctx.address, required[0], ctx.offset)
    draw_marker(ctx.draw, start, ctx.green); draw_marker(ctx.draw, observed, ctx.orange); draw_displacement_arrow(ctx.draw, start, observed, ctx.orange)
    return _body_result(snapshot, ctx, "Observed steady baseline strength", "Measured phase reference", None)


def _render_tempo(rule, snapshot, selection, cfg):
    image = Image.new("RGB", (900, 260), "white"); draw = ImageDraw.Draw(image); font = load_font(16)
    color = cfg.overlay["captured_color"]
    event_rows = [(event.label, event.timestamp_ms) for event in snapshot.events]
    draw_labeled_timeline(draw, event_rows, y=135, color=color, font=font)
    method_labels = [f"{event.label}: {event.method.value}" for event in snapshot.events]
    for index, label in enumerate(method_labels):
        draw.text((30 + (index % 2) * 430, 38 + (index // 2) * 24), label, fill=color, font=font)
    consistency = f"{selection.tempo_ratio_std:.2f}" if selection.tempo_ratio_std is not None else "unavailable"
    facts = f"backswing {snapshot.metrics.backswing_s:.2f}s; downswing {snapshot.metrics.downswing_s:.2f}s; ratio {snapshot.metrics.tempo_ratio:.2f}; consistency {consistency}"
    draw.text((30, 12), "Observed timing timeline", fill=color, font=font)
    draw.text((30, 220), facts, fill=color, font=font)
    methods = ", ".join(method_labels)
    provenance = _event(snapshot, EventId.ADDRESS)
    if provenance is None:
        raise FocusedEvidenceRenderError("timing evidence lacks address provenance")
    alt = f"Swing {snapshot.swing} timing timeline with observed timing reference: {methods}; {facts}. Tracking {_tracking(snapshot)[0].value}."
    return _RenderResult(image, "Observed timing events", "Event timing reference", None, alt, EventId.ADDRESS, provenance)
RENDERERS = {
    EvidenceKind.HEAD_BOUNDARY: _render_head_boundary, EvidenceKind.HIP_BOUNDARY: _render_hip_boundary,
    EvidenceKind.HEAD_HEIGHT: _render_head_height, EvidenceKind.TEMPO_TIMELINE: _render_tempo,
    EvidenceKind.LEAD_ARM_ANGLE: _render_lead_arm_angle, EvidenceKind.SHOULDER_TILT: _render_shoulder_tilt,
    EvidenceKind.FINISH_STABILITY: _render_finish_stability, EvidenceKind.STEADY_REFERENCE: _render_steady_reference,
}

def render_focused_evidence(selection: FocusedEvidenceSelection, *, out_path: Path, relative_path: str, cfg: Config, angle: str = "face_on") -> FocusedEvidenceArtifact:
    snapshot, rule = selection.snapshot, selection.rule
    if snapshot is None: raise FocusedEvidenceRenderError("no selected snapshot")
    if angle == "dtl" and rule.kind is not EvidenceKind.TEMPO_TIMELINE: raise UnsupportedFocusedEvidence("DTL supports timing evidence only")
    if "\\" in relative_path or PurePosixPath(relative_path).is_absolute() or ".." in PurePosixPath(relative_path).parts: raise FocusedEvidenceRenderError("unsafe relative media path")
    result = RENDERERS[rule.kind](rule, snapshot, selection, cfg)
    try: saved = save_branded(result.image,out_path,cfg); digest = hashlib.sha256(saved.read_bytes()).hexdigest()
    except (OSError, ValueError) as exc: raise FocusedEvidenceRenderError(str(exc)) from exc
    tracking, reasons = _tracking(snapshot)
    evidence = RenderedEvidence(rule.kind,"rendered",snapshot.swing,rule.phase,result.provenance.method,result.provenance.timestamp_ms,tuple(EventProvenance(item.event,item.method,item.timestamp_ms,item.label) for item in snapshot.events),tracking,reasons,(),result.observed,result.reference,result.boundary,selection.annotation_readable_swings,selection.triggered_swings,None,"Focused evidence from the selected swing.",result.alt,"priority-evidence")
    media = MediaEntry("priority-evidence",MediaRole.PRIORITY_EVIDENCE,"image/png",Entitlement.CORE,relative_path,digest)
    return FocusedEvidenceArtifact(evidence,media,saved)

def build_unavailable_evidence(selection: FocusedEvidenceSelection, *, observation: str, supporting_measurement: MeasurementDetail | None) -> UnavailableEvidence:
    if selection.fatal_reason is not None or selection.metric_readable_swings <= 0 or selection.annotation_readable_swings <= 0 or selection.snapshot is None:
        raise ValueError("renderer-only fallback requires an already trustworthy selected snapshot")
    rule, snapshot = selection.rule, selection.snapshot
    if rule.kind is EvidenceKind.TEMPO_TIMELINE:
        if len(snapshot.events) != 4:
            raise ValueError("timing fallback requires four-event provenance")
    else:
        event, _ = _phase_event(rule, snapshot)
        if event not in snapshot.event_frames or snapshot.event_landmarks.get(event) is None or not _gate(snapshot, rule.metric_id):
            raise ValueError("renderer-only fallback requires established annotation evidence")
    _, provenance = _phase_event(rule,snapshot)
    tracking, reasons = _tracking(snapshot)
    return UnavailableEvidence(rule.kind,"unavailable",snapshot.swing,rule.phase,provenance.method,provenance.timestamp_ms,tuple(EventProvenance(item.event,item.method,item.timestamp_ms,item.label) for item in snapshot.events),tracking,reasons,(ReasonCode.FOCUSED_MEDIA_RENDER_FAILED,),"Observed evidence", "Address/start reference",None,selection.annotation_readable_swings,selection.triggered_swings,supporting_measurement,observation,f"Swing {snapshot.swing} focused evidence could not be rendered.",None)
