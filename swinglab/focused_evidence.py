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
    del stats
    values = [(snapshot, finite_float(getattr(snapshot.metrics, rule.metric_id, None))) for snapshot in snapshots]
    metric_rows = [(snapshot, value) for snapshot, value in values if value is not None]
    if not metric_rows:
        return FocusedEvidenceSelection(rule, None, 0, 0, None, ReasonCode.PRIORITY_EVIDENCE_UNRELIABLE)
    if rule.event is not None and not any(rule.event in snapshot.event_frames and snapshot.event_landmarks.get(rule.event) is not None for snapshot, _ in metric_rows):
        return FocusedEvidenceSelection(rule, None, len(metric_rows), 0, None, ReasonCode.NO_RELIABLE_STRIKE_EVENT)
    def crossed(value: float) -> bool | None:
        if rule.benchmark is None or rule.worse_direction is None:
            return None
        return value >= rule.benchmark if rule.worse_direction == "higher" else value <= rule.benchmark
    candidates = [EvidenceCandidate(snapshot.swing, value, _gate(snapshot, rule.metric_id) and (rule.event is None or rule.event in snapshot.event_frames and snapshot.event_landmarks.get(rule.event) is not None), crossed(value)) for snapshot, value in metric_rows]
    selected = select_representative_swing(candidates, basis=rule.selection_basis, session_value=None if rule.selection_basis not in {"session_mean", "shoulder_tilt_delta_mean"} else sum(value for _, value in metric_rows) / len(metric_rows))
    chosen = next((snapshot for snapshot, _ in metric_rows if snapshot.swing == selected), None)
    readable = sum(candidate.eligible for candidate in candidates)
    return FocusedEvidenceSelection(rule, chosen, len(metric_rows), readable, None, None)

def _phase_event(rule: PriorityEvidenceRule, snapshot: EvidenceSnapshot):
    event = rule.event or EventId.ADDRESS
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

def _render_body(rule: PriorityEvidenceRule, snapshot: EvidenceSnapshot, cfg: Config) -> tuple[Image.Image, str, str | None, str | None, str]:
    event, _ = _phase_event(rule, snapshot)
    lm = snapshot.event_landmarks.get(event)
    address = snapshot.event_landmarks.get(EventId.ADDRESS)
    path = snapshot.event_frames.get(event)
    if lm is None or address is None or path is None:
        raise FocusedEvidenceRenderError("selected evidence pixels or landmarks unavailable")
    required = [pose.NOSE]
    if rule.kind is EvidenceKind.HIP_BOUNDARY: required = [pose.LEFT_HIP, pose.RIGHT_HIP]
    elif rule.kind is EvidenceKind.LEAD_ARM_ANGLE: required = list(lead_trail_sides(snapshot.hand)[0])
    elif rule.kind is EvidenceKind.SHOULDER_TILT: required = [pose.LEFT_SHOULDER, pose.RIGHT_SHOULDER]
    elif rule.kind is EvidenceKind.FINISH_STABILITY: required = [pose.LEFT_ANKLE, pose.RIGHT_ANKLE]
    points = [tuple(lm[index]) for index in required] + [tuple(address[index]) for index in required if index in address]
    try:
        with Image.open(path) as source: image, offset = _crop(source.convert("RGB"), points)
    except (OSError, ValueError) as exc: raise FocusedEvidenceRenderError(str(exc)) from exc
    draw, orange, green = ImageDraw.Draw(image), cfg.overlay["captured_color"], cfg.overlay["corrected_color"]
    font = load_font(18)
    observed, reference, boundary = "Observed marker", "Address/start reference", None
    if rule.kind in (EvidenceKind.HEAD_BOUNDARY, EvidenceKind.STEADY_REFERENCE, EvidenceKind.HEAD_HEIGHT):
        obs, start = _point(lm, pose.NOSE, offset), _point(address, pose.NOSE, offset)
        draw_marker(draw, start, green); draw_marker(draw, obs, orange); draw_displacement_arrow(draw, start, obs, orange)
        if rule.kind is EvidenceKind.HEAD_HEIGHT:
            draw.line((start[0], start[1], start[0], obs[1]), fill=green, width=3); reference = "Address head-height reference"
        elif snapshot.target_confident and rule.kind is EvidenceKind.HEAD_BOUNDARY:
            x = start[0] + snapshot.target_direction * snapshot.shoulder_width_px * .35; draw_dashed_line(draw, (x, 10), (x, image.height - 10), green); boundary = "Configured boundary"
    elif rule.kind is EvidenceKind.HIP_BOUNDARY:
        obs = tuple((lm[pose.LEFT_HIP] + lm[pose.RIGHT_HIP]) / 2); start = tuple((address[pose.LEFT_HIP] + address[pose.RIGHT_HIP]) / 2)
        obs, start = (obs[0]-offset[0], obs[1]-offset[1]), (start[0]-offset[0], start[1]-offset[1]); draw_marker(draw,start,green); draw_marker(draw,obs,orange); draw_displacement_arrow(draw,start,obs,orange)
        if snapshot.target_confident: x=start[0]+snapshot.target_direction*snapshot.shoulder_width_px*.35; draw_dashed_line(draw,(x,10),(x,image.height-10),green); boundary="Configured boundary"
    elif rule.kind is EvidenceKind.LEAD_ARM_ANGLE:
        shoulder, elbow, wrist = lead_trail_sides(snapshot.hand)[0]
        a,b,c = _point(lm, shoulder, offset), _point(lm, elbow, offset), _point(lm, wrist, offset)
        draw.line((a,b,c), fill=orange, width=4); draw_marker(draw,b,orange); draw_angle_arc(draw,b,180,300,35,orange); reference="Tracked lead shoulder-elbow-wrist"
    elif rule.kind is EvidenceKind.SHOULDER_TILT:
        for source, color in ((address,green),(lm,orange)):
            draw.line((_point(source,pose.LEFT_SHOULDER,offset),_point(source,pose.RIGHT_SHOULDER,offset)),fill=color,width=4)
        reference="Address shoulder line"
    elif rule.kind is EvidenceKind.FINISH_STABILITY:
        points = [(x-offset[0], y-offset[1]) for x,y in snapshot.finish_ankle_midpoints]
        for point in points: draw_marker(draw,point,orange,5)
        if points: draw_marker(draw,points[0],green,6)
        reference="Finish-start ankle midpoint"
    draw.text((12,12), observed, fill=orange, font=font)
    return image, observed, reference, boundary, f"Swing {snapshot.swing} {event.value}: observed marker with address/start reference"

def _render_tempo(snapshot: EvidenceSnapshot, cfg: Config) -> tuple[Image.Image, str]:
    image = Image.new("RGB", (800, 220), "white"); draw = ImageDraw.Draw(image); font = load_font(18)
    event_rows = [(event.label, event.timestamp_ms) for event in snapshot.events]
    draw_labeled_timeline(draw,event_rows,y=100,color=cfg.overlay["captured_color"],font=font)
    draw.text((30, 15), "Observed timing timeline", fill=cfg.overlay["captured_color"], font=font)
    return image, f"Swing {snapshot.swing} timing timeline: observed address, top, impact, and finish events."

def render_focused_evidence(selection: FocusedEvidenceSelection, *, out_path: Path, relative_path: str, cfg: Config, angle: str = "face_on") -> FocusedEvidenceArtifact:
    snapshot, rule = selection.snapshot, selection.rule
    if snapshot is None: raise FocusedEvidenceRenderError("no selected snapshot")
    if angle == "dtl" and rule.kind is not EvidenceKind.TEMPO_TIMELINE: raise UnsupportedFocusedEvidence("DTL supports timing evidence only")
    if "\\" in relative_path or PurePosixPath(relative_path).is_absolute() or ".." in PurePosixPath(relative_path).parts: raise FocusedEvidenceRenderError("unsafe relative media path")
    if rule.kind is EvidenceKind.TEMPO_TIMELINE:
        image, alt = _render_tempo(snapshot,cfg); observed, reference, boundary = "Observed timing events", "Event timing reference", None
        event, provenance = EventId.ADDRESS, _event(snapshot, EventId.ADDRESS)
    else:
        image, observed, reference, boundary, alt = _render_body(rule,snapshot,cfg); event, provenance = _phase_event(rule,snapshot)
    try: saved = save_branded(image,out_path,cfg)
    except (OSError, ValueError) as exc: raise FocusedEvidenceRenderError(str(exc)) from exc
    tracking, reasons = _tracking(snapshot)
    evidence = RenderedEvidence(rule.kind,"rendered",snapshot.swing,rule.phase,provenance.method,provenance.timestamp_ms,tuple(EventProvenance(item.event,item.method,item.timestamp_ms,item.label) for item in snapshot.events),tracking,reasons,(),observed,reference,boundary,selection.annotation_readable_swings,selection.triggered_swings,None,"Focused evidence from the selected swing.",alt,"priority-evidence")
    digest = hashlib.sha256(saved.read_bytes()).hexdigest()
    media = MediaEntry("priority-evidence",MediaRole.PRIORITY_EVIDENCE,"image/png",Entitlement.CORE,relative_path,digest)
    return FocusedEvidenceArtifact(evidence,media,saved)

def build_unavailable_evidence(selection: FocusedEvidenceSelection, *, observation: str, supporting_measurement: MeasurementDetail | None) -> UnavailableEvidence:
    if selection.fatal_reason is not None or selection.metric_readable_swings <= 0 or selection.snapshot is None:
        raise ValueError("renderer-only fallback requires an already trustworthy selected snapshot")
    rule, snapshot = selection.rule, selection.snapshot
    _, provenance = _phase_event(rule,snapshot)
    tracking, reasons = _tracking(snapshot)
    return UnavailableEvidence(rule.kind,"unavailable",snapshot.swing,rule.phase,provenance.method,provenance.timestamp_ms,tuple(EventProvenance(item.event,item.method,item.timestamp_ms,item.label) for item in snapshot.events),tracking,reasons,(ReasonCode.FOCUSED_MEDIA_RENDER_FAILED,),"Observed evidence", "Address/start reference",None,selection.annotation_readable_swings,selection.triggered_swings,supporting_measurement,observation,f"Swing {snapshot.swing} focused evidence could not be rendered.",None)
