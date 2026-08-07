"""Frozen persisted contract for the guided swing report view."""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import asdict, dataclass, fields, is_dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Literal, TypeVar

REPORT_VIEW_VERSION = "report-view-v1"
LEGACY_REPORT_PRESENTATION_VERSION = "premium-coach-v2"
GUIDED_REPORT_PRESENTATION_VERSION = "guided-report-v1"
MAX_REPORT_VIEW_BYTES = 2 * 1024 * 1024


class ReportViewValidationError(ValueError): pass
class UnsupportedReportViewVersion(ReportViewValidationError): pass
class UnsupportedReportPresentationVersion(ValueError): pass


class ReportPresentationVersion(StrEnum):
    LEGACY = LEGACY_REPORT_PRESENTATION_VERSION
    GUIDED = GUIDED_REPORT_PRESENTATION_VERSION


def parse_report_presentation_version(value: object) -> ReportPresentationVersion:
    """Parse the only two supported presentation routes, or fail closed."""

    if not isinstance(value, str):
        raise UnsupportedReportPresentationVersion(
            f"unknown report presentation: {value!r}"
        )
    try:
        return ReportPresentationVersion(value)
    except ValueError as exc:
        raise UnsupportedReportPresentationVersion(
            f"unknown report presentation: {value!r}"
        ) from exc


class ReportOutcome(StrEnum): COACHING_READY = "coaching_ready"; CAPTURE_ONLY = "capture_only"
class JourneyMode(StrEnum): IMPROVE = "improve"; PROTECT = "protect"; CAPTURE_RETRY = "capture_retry"
class TrustState(StrEnum): CLEAR = "clear"; LIMITED = "limited"; REFILM_REQUIRED = "refilm_required"
class TrackingState(StrEnum): CLEAR = "clear"; LIMITED = "limited"; UNAVAILABLE = "unavailable"
class Angle(StrEnum): FACE_ON = "face_on"; DTL = "dtl"
class Hand(StrEnum): RIGHT = "right"; LEFT = "left"
class PhaseId(StrEnum):
    SETUP="setup"; GOING_BACK="going_back"; TRANSITION_DOWNSWING="transition_downswing"; IMPACT="impact"; FINISH="finish"; TIMING_RHYTHM="timing_rhythm"
class PhaseStatus(StrEnum): PRIORITY="priority"; REVIEW_LATER="review_later"; STEADY="steady"; BASELINE="baseline"; NOT_MEASURED="not_measured"
class ReasonCode(StrEnum):
    SECONDARY_METRIC_UNAVAILABLE="secondary_metric_unavailable"; TARGET_DIRECTION_UNCERTAIN="target_direction_uncertain"; HAND_LANDMARKS_UNRELIABLE="hand_landmarks_unreliable"; EVENT_ESTIMATE_LIMITED="event_estimate_limited"; FOCUSED_MEDIA_RENDER_FAILED="focused_media_render_failed"; CAMERA_ANGLE_MISMATCH="camera_angle_mismatch"; TRACKING_UNSTABLE="tracking_unstable"; INSUFFICIENT_POSE_FRAMES="insufficient_pose_frames"; NO_READABLE_SWING="no_readable_swing"; NO_RELIABLE_STRIKE_EVENT="no_reliable_strike_event"; PRIORITY_EVIDENCE_UNRELIABLE="priority_evidence_unreliable"
class EvidenceKind(StrEnum): HEAD_BOUNDARY="head_boundary"; HIP_BOUNDARY="hip_boundary"; HEAD_HEIGHT="head_height"; TEMPO_TIMELINE="tempo_timeline"; LEAD_ARM_ANGLE="lead_arm_angle"; SHOULDER_TILT="shoulder_tilt"; FINISH_STABILITY="finish_stability"; STEADY_REFERENCE="steady_reference"
class PhaseMethod(StrEnum): OPENING_BASELINE="opening_baseline"; HIGHEST_TRACKED_HANDS="highest_tracked_hands"; DETECTED_AUDIO="detected_audio"; MANUAL_STRIKE="manual_strike"; CONFIGURED_FINISH_OFFSET="configured_finish_offset"; SESSION_TIMING="session_timing"
class EventId(StrEnum): ADDRESS="address"; TOP="top"; IMPACT="impact"; FINISH="finish"
class MeasurementUnit(StrEnum): SECONDS="seconds"; RATIO="ratio"; SHOULDER_WIDTHS="shoulder_widths"; SHOULDER_WIDTHS_PER_SECOND="shoulder_widths_per_second"; DEGREES="degrees"; COUNT="count"
class BenchmarkRelation(StrEnum): ABOVE="above"; BELOW="below"; BETWEEN="between"; CONTEXT_ONLY="context_only"; NONE="none"
class TargetComparator(StrEnum): LTE="lte"; GTE="gte"; BETWEEN="between"; ALL_LTE="all_lte"; ALL_GTE="all_gte"; COUNT_LTE="count_lte"; COUNT_GTE="count_gte"
class TargetWindow(StrEnum): SWING="swing"; SESSION="session"; CONSECUTIVE_SESSIONS="consecutive_sessions"
class OptionalSectionId(StrEnum): EVERY_SWING="every_swing"; REPLAY="replay"; SECONDARY_FINDINGS="secondary_findings"; ALTERNATIVE_DRILLS="alternative_drills"; MORE_STRENGTHS="more_strengths"; MEASUREMENTS="measurements"; GLOSSARY="glossary"; GEAR="gear"
class MediaRole(StrEnum): PRIORITY_EVIDENCE="priority_evidence"; DRILL_ILLUSTRATION="drill_illustration"; KEY_POSITIONS="key_positions"; SLOW_MOTION="slow_motion"; COACH_REPLAY="coach_replay"; VIDEO_POSTER="video_poster"; CAPTURE_PLAYBACK="capture_playback"
class Entitlement(StrEnum): CORE="core"; FREE="free"; PRO="pro"

@dataclass(frozen=True)
class Trust: state: TrustState; label: str; reasons: tuple[ReasonCode,...]; explanation: str | None
@dataclass(frozen=True)
class ReportContext: club: str|None; club_label: str|None; hand: Hand; angle: Angle; angle_label: str; detected_swings: int; priority_readable_swings: int; analysis_fps: float|None
@dataclass(frozen=True)
class NextMove: mode: JourneyMode; priority_key: str; category: PhaseId; eyebrow: str; title: str; observation: str; cue: str; measurement_detail_id: str|None; practice_anchor: Literal["practice"]; refilm_anchor: Literal["refilm"]
@dataclass(frozen=True)
class EventProvenance: event: EventId; method: PhaseMethod; timestamp_ms: int; label: str
@dataclass(frozen=True)
class MeasurementDetail: id: str; label: str; plain_value: str; numeric_value: float|None; unit: MeasurementUnit|None; benchmark_relation: BenchmarkRelation; benchmark_value: float|None; benchmark_upper_value: float|None; benchmark_label: str|None; explanation: str; limitation: str
@dataclass(frozen=True)
class RenderedEvidence: kind: EvidenceKind; state: Literal["rendered"]; swing: int; phase: PhaseId; phase_method: PhaseMethod; timestamp_ms: int|None; events: tuple[EventProvenance,...]; tracking_state: TrackingState; tracking_reasons: tuple[ReasonCode,...]; render_reasons: tuple[ReasonCode,...]; observed_label: str; reference_label: str|None; boundary_label: str|None; readable_swings: int; triggered_swings: int|None; supporting_measurement: MeasurementDetail|None; observation: str; alt_text: str; media_key: str
@dataclass(frozen=True)
class UnavailableEvidence: kind: EvidenceKind; state: Literal["unavailable"]; swing: int; phase: PhaseId; phase_method: PhaseMethod; timestamp_ms: int|None; events: tuple[EventProvenance,...]; tracking_state: TrackingState; tracking_reasons: tuple[ReasonCode,...]; render_reasons: tuple[ReasonCode,...]; observed_label: str; reference_label: str|None; boundary_label: str|None; readable_swings: int; triggered_swings: int|None; supporting_measurement: MeasurementDetail|None; observation: str; alt_text: str; media_key: None
EvidenceView = RenderedEvidence | UnavailableEvidence
@dataclass(frozen=True)
class PhaseSummary: id: PhaseId; label: str; status: PhaseStatus; status_label: str; summary: str; readable_swings: int; measurements: tuple[MeasurementDetail,...]; unavailable_reasons: tuple[ReasonCode,...]; detail_section_id: str; expanded_by_default: bool
@dataclass(frozen=True)
class DrillAlternative: id: str; name: str; aim: str; detail_section_id: str
@dataclass(frozen=True)
class PracticePrescription: section_id: Literal["practice"]; drill_id: str; name: str; aim: str; summary_steps: tuple[str,str,str]; full_steps: tuple[str,...]; setup: str; feel_cue: str; dosage: str; equipment: str|None; illustration_media_key: str|None; illustration_label: str|None; alternatives: tuple[DrillAlternative,...]
@dataclass(frozen=True)
class RefilmTarget: text: str; metric_id: str; comparator: TargetComparator; threshold: float; upper_threshold: float|None; unit: MeasurementUnit; required_successes: int|None; required_attempts: int|None; window: TargetWindow
@dataclass(frozen=True)
class RefilmProtocol: section_id: Literal["refilm"]; checklist: tuple[str,...]; target: RefilmTarget; primary_action_label: str; preserves_club: bool; preserves_hand: Literal[True]; preserves_angle: Literal[True]; preserves_camera_height: Literal[True]; preserves_framing: Literal[True]; preserves_effort: Literal[True]
@dataclass(frozen=True)
class CaptureGuidance: primary_reason: ReasonCode; reason_label: str; explanation: str; correction: str; checklist: tuple[str,...]; safe_media_keys: tuple[str,...]; primary_action: Literal["refilm","choose_video"]; primary_action_label: str; secondary_action: Literal["choose_video","support"]|None; secondary_action_label: str|None
@dataclass(frozen=True)
class OptionalSection: id: OptionalSectionId; label: str; available: bool; locked: bool; item_count: int
@dataclass(frozen=True)
class Capabilities: structured_report: Literal[True]; focused_evidence: bool; every_swing: bool; slow_motion: bool; coach_replay: bool; measurements: bool; alternative_drills: bool; gear: bool; print: bool
@dataclass(frozen=True)
class MediaEntry: key: str; role: MediaRole; mime_type: str; entitlement: Entitlement; relative_path: str; checksum_sha256: str
@dataclass(frozen=True)
class CoachingReportView:
    version: Literal["report-view-v1"]; mode: Literal["structured"]; presentation_version: str; outcome: Literal[ReportOutcome.COACHING_READY]; journey_mode: Literal[JourneyMode.IMPROVE,JourneyMode.PROTECT]; trust: Trust; context: ReportContext; capabilities: Capabilities; media: tuple[MediaEntry,...]; optional_sections: tuple[OptionalSection,...]; next_move: NextMove; visual_evidence: EvidenceView; phases: tuple[PhaseSummary,...]; practice: PracticePrescription; refilm: RefilmProtocol; capture_guidance: None = None
@dataclass(frozen=True)
class CaptureOnlyReportView:
    version: Literal["report-view-v1"]; mode: Literal["structured"]; presentation_version: str; outcome: Literal[ReportOutcome.CAPTURE_ONLY]; journey_mode: Literal[JourneyMode.CAPTURE_RETRY]; trust: Trust; context: ReportContext; capabilities: Capabilities; media: tuple[MediaEntry,...]; optional_sections: tuple[OptionalSection,...]; next_move: None; visual_evidence: None; phases: tuple[()]; practice: None; refilm: None; capture_guidance: CaptureGuidance
ReportViewV1 = CoachingReportView | CaptureOnlyReportView

T = TypeVar("T")
def _err(message: str) -> None: raise ReportViewValidationError(message)
def _obj(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict): _err(f"{name} must be an object")
    return value
def _field(data: dict[str, Any], key: str) -> Any:
    if key not in data: _err(f"missing required field: {key}")
    return data[key]
def _str(data: dict[str, Any], key: str, nullable=False) -> str|None:
    v=_field(data,key)
    if v is None and nullable:return None
    if not isinstance(v,str) or not v: _err(f"{key} must be a nonempty string")
    return v
def _bool(data:dict[str,Any],key:str)->bool:
    v=_field(data,key)
    if not isinstance(v,bool):_err(f"{key} must be boolean")
    return v
def _int(data:dict[str,Any],key:str,minimum=0,nullable=False)->int|None:
    v=_field(data,key)
    if v is None and nullable:return None
    if isinstance(v,bool) or not isinstance(v,int) or v<minimum:_err(f"{key} must be an integer >= {minimum}")
    return v
def _num(data:dict[str,Any],key:str,nullable=False,positive=False)->float|None:
    v=_field(data,key)
    if v is None and nullable:return None
    if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or (positive and v<=0):_err(f"{key} must be a finite number")
    return v
def _enum(cls:type[T], value:object, key:str)->T:
    try:return cls(value) # type: ignore[call-arg]
    except (TypeError,ValueError):_err(f"invalid {key}: {value!r}")
def _seq(data:dict[str,Any],key:str)->list[Any]:
    v=_field(data,key)
    if not isinstance(v,list):_err(f"{key} must be an array")
    return v
def _unique(values:tuple[Any,...],key:str)->None:
    if len(values)!=len(set(values)):_err(f"duplicate {key}")
def _literal(data:dict[str,Any],key:str,*values:str)->str:
    v=_field(data,key)
    if v not in values:_err(f"invalid {key}")
    return v

def _trust(v:object)->Trust:
    d=_obj(v,"trust"); reasons=tuple(_enum(ReasonCode,x,"reason") for x in _seq(d,"reasons")); _unique(reasons,"reason")
    e=_field(d,"explanation");
    if e is not None and (not isinstance(e,str) or not e):_err("explanation must be null or nonempty string")
    return Trust(_enum(TrustState,_field(d,"state"),"trust state"),_str(d,"label"),reasons,e)
def _context(v:object)->ReportContext:
    d=_obj(v,"context"); club=_field(d,"club"); label=_field(d,"club_label")
    if club is not None and not isinstance(club,str):_err("club must be string or null")
    if label is not None and not isinstance(label,str):_err("club_label must be string or null")
    return ReportContext(club,label,_enum(Hand,_field(d,"hand"),"hand"),_enum(Angle,_field(d,"angle"),"angle"),_str(d,"angle_label"),_int(d,"detected_swings"),_int(d,"priority_readable_swings"),_num(d,"analysis_fps",True,True))
def _measurement(v:object)->MeasurementDetail:
    d=_obj(v,"measurement"); unit=_field(d,"unit")
    if unit is not None: unit=_enum(MeasurementUnit,unit,"unit")
    limitation=_field(d,"limitation")
    if not isinstance(limitation,str): _err("limitation must be a string")
    return MeasurementDetail(_str(d,"id"),_str(d,"label"),_str(d,"plain_value"),_num(d,"numeric_value",True),unit,_enum(BenchmarkRelation,_field(d,"benchmark_relation"),"benchmark_relation"),_num(d,"benchmark_value",True),_num(d,"benchmark_upper_value",True),_str(d,"benchmark_label",True),_str(d,"explanation"),limitation)
def _event(v:object)->EventProvenance:
    d=_obj(v,"event"); return EventProvenance(_enum(EventId,_field(d,"event"),"event"),_enum(PhaseMethod,_field(d,"method"),"method"),_int(d,"timestamp_ms"),_str(d,"label"))
def _evidence(v:object)->EvidenceView:
    d=_obj(v,"visual_evidence"); state=_literal(d,"state","rendered","unavailable"); reasons=tuple(_enum(ReasonCode,x,"render reason") for x in _seq(d,"render_reasons")); _unique(reasons,"render reason")
    tracking_reasons=tuple(_enum(ReasonCode,x,"tracking reason") for x in _seq(d,"tracking_reasons")); _unique(tracking_reasons,"tracking reason")
    common=[_enum(EvidenceKind,_field(d,"kind"),"evidence kind"),state,_int(d,"swing",1),_enum(PhaseId,_field(d,"phase"),"phase"),_enum(PhaseMethod,_field(d,"phase_method"),"phase method"),_int(d,"timestamp_ms",0,True),tuple(_event(x) for x in _seq(d,"events")),_enum(TrackingState,_field(d,"tracking_state"),"tracking state"),tracking_reasons,reasons,_str(d,"observed_label"),_str(d,"reference_label",True),_str(d,"boundary_label",True),_int(d,"readable_swings",1),_int(d,"triggered_swings",0,True),_measurement(_field(d,"supporting_measurement")) if _field(d,"supporting_measurement") is not None else None,_str(d,"observation"),_str(d,"alt_text")]
    media=_field(d,"media_key")
    if state=="rendered":
        if not isinstance(media,str) or not media or reasons:_err("rendered evidence requires media_key and no render reasons")
        return RenderedEvidence(*common,media)
    if media is not None or reasons != (ReasonCode.FOCUSED_MEDIA_RENDER_FAILED,) or common[7] == TrackingState.UNAVAILABLE:_err("unavailable evidence requires focused-media failure only")
    return UnavailableEvidence(*common,None)
def _phase(v:object)->PhaseSummary:
    d=_obj(v,"phase"); reasons=tuple(_enum(ReasonCode,x,"reason") for x in _seq(d,"unavailable_reasons"));_unique(reasons,"reason")
    return PhaseSummary(_enum(PhaseId,_field(d,"id"),"phase id"),_str(d,"label"),_enum(PhaseStatus,_field(d,"status"),"phase status"),_str(d,"status_label"),_str(d,"summary"),_int(d,"readable_swings"),tuple(_measurement(x) for x in _seq(d,"measurements")),reasons,_str(d,"detail_section_id"),_bool(d,"expanded_by_default"))
def _media(v:object)->MediaEntry:
    d=_obj(v,"media"); path=_str(d,"relative_path"); p=PurePosixPath(path)
    if "\\" in path or p.is_absolute() or re.match(r"^[A-Za-z]:",path) or any(x in (".","..") for x in p.parts) or p.as_posix()!=path:_err("relative_path is unsafe")
    checksum=_str(d,"checksum_sha256")
    if not re.fullmatch(r"[0-9a-f]{64}",checksum):_err("checksum_sha256 must be lowercase SHA-256")
    return MediaEntry(_str(d,"key"),_enum(MediaRole,_field(d,"role"),"media role"),_str(d,"mime_type"),_enum(Entitlement,_field(d,"entitlement"),"entitlement"),path,checksum)
def _optional(v:object)->OptionalSection:
    d=_obj(v,"optional section"); return OptionalSection(_enum(OptionalSectionId,_field(d,"id"),"optional section id"),_str(d,"label"),_bool(d,"available"),_bool(d,"locked"),_int(d,"item_count"))
def _caps(v:object)->Capabilities:
    d=_obj(v,"capabilities");
    if _field(d,"structured_report") is not True:_err("structured_report must be true")
    return Capabilities(True,*(_bool(d,k) for k in ("focused_evidence","every_swing","slow_motion","coach_replay","measurements","alternative_drills","gear","print")))
def _next(v:object)->NextMove:
    d=_obj(v,"next_move"); return NextMove(_enum(JourneyMode,_literal(d,"mode","improve","protect"),"next move mode"),_str(d,"priority_key"),_enum(PhaseId,_field(d,"category"),"category"),_str(d,"eyebrow"),_str(d,"title"),_str(d,"observation"),_str(d,"cue"),_str(d,"measurement_detail_id",True),_literal(d,"practice_anchor","practice"),_literal(d,"refilm_anchor","refilm"))
def _practice(v:object)->PracticePrescription:
    d=_obj(v,"practice"); summary=tuple(_str({"v":x},"v") for x in _seq(d,"summary_steps"))
    if len(summary)!=3:_err("summary_steps must contain exactly three stages")
    full=tuple(_str({"v":x},"v") for x in _seq(d,"full_steps"));
    if not full:_err("full_steps must not be empty")
    alts=tuple(DrillAlternative(_str(xd:=_obj(x,"alternative"),"id"),_str(xd,"name"),_str(xd,"aim"),_str(xd,"detail_section_id")) for x in _seq(d,"alternatives"))
    label=_str(d,"illustration_label",True)
    if label is not None and label!="Instructional illustration — not your measured pose":_err("invalid illustration_label")
    return PracticePrescription(_literal(d,"section_id","practice"),_str(d,"drill_id"),_str(d,"name"),_str(d,"aim"),summary,full,_str(d,"setup"),_str(d,"feel_cue"),_str(d,"dosage"),_str(d,"equipment",True),_str(d,"illustration_media_key",True),label,alts)
def _refilm(v:object)->RefilmProtocol:
    d=_obj(v,"refilm"); t=_obj(_field(d,"target"),"target"); checks=tuple(_str({"v":x},"v") for x in _seq(d,"checklist"))
    if not checks:_err("checklist must not be empty")
    target=RefilmTarget(_str(t,"text"),_str(t,"metric_id"),_enum(TargetComparator,_field(t,"comparator"),"comparator"),_num(t,"threshold"),_num(t,"upper_threshold",True),_enum(MeasurementUnit,_field(t,"unit"),"unit"),_int(t,"required_successes",1,True),_int(t,"required_attempts",1,True),_enum(TargetWindow,_field(t,"window"),"window"))
    if target.comparator == TargetComparator.BETWEEN:
        if target.upper_threshold is None or target.threshold >= target.upper_threshold: _err("between target requires ordered upper threshold")
    elif target.upper_threshold is not None: _err("non-between target cannot carry upper threshold")
    if target.required_successes is not None and target.required_attempts is not None and target.required_successes > target.required_attempts: _err("required_successes cannot exceed required_attempts")
    return RefilmProtocol(_literal(d,"section_id","refilm"),checks,target,_str(d,"primary_action_label"),_bool(d,"preserves_club"),_literal(d,"preserves_hand",True),_literal(d,"preserves_angle",True),_literal(d,"preserves_camera_height",True),_literal(d,"preserves_framing",True),_literal(d,"preserves_effort",True))
def _capture(v:object)->CaptureGuidance:
    d=_obj(v,"capture_guidance"); checks=tuple(_str({"v":x},"v") for x in _seq(d,"checklist"))
    if not checks:_err("checklist must not be empty")
    safe=tuple(_str({"v":x},"v") for x in _seq(d,"safe_media_keys")); second=_field(d,"secondary_action")
    if second not in (None,"choose_video","support"):_err("invalid secondary_action")
    return CaptureGuidance(_enum(ReasonCode,_field(d,"primary_reason"),"primary reason"),_str(d,"reason_label"),_str(d,"explanation"),_str(d,"correction"),checks,safe,_literal(d,"primary_action","refilm","choose_video"),_str(d,"primary_action_label"),second,_str(d,"secondary_action_label",True))

def _validate(view:ReportViewV1)->None:
    keys=tuple(m.key for m in view.media);_unique(keys,"media key")
    if isinstance(view,CoachingReportView):
        if view.journey_mode != view.next_move.mode:_err("journey_mode must match next_move mode")
        if view.trust.state not in (TrustState.CLEAR,TrustState.LIMITED):_err("coaching trust state invalid")
        if view.trust.state==TrustState.CLEAR and not isinstance(view.visual_evidence,RenderedEvidence):_err("clear coaching requires rendered evidence")
        if view.trust.state==TrustState.LIMITED and any(x in (ReasonCode.NO_READABLE_SWING,ReasonCode.NO_RELIABLE_STRIKE_EVENT,ReasonCode.PRIORITY_EVIDENCE_UNRELIABLE) for x in view.trust.reasons):_err("limited coaching cannot carry fatal reasons")
        if isinstance(view.visual_evidence,RenderedEvidence) and view.visual_evidence.media_key not in keys:_err("missing media reference")
        if view.practice.illustration_media_key is not None and view.practice.illustration_media_key not in keys:_err("missing media reference")
        if view.capture_guidance is not None:_err("coaching has no capture guidance")
        phase_ids=tuple(phase.id for phase in view.phases); _unique(phase_ids,"phase")
        if view.context.angle == Angle.FACE_ON:
            expected=(PhaseId.SETUP,PhaseId.GOING_BACK,PhaseId.TRANSITION_DOWNSWING,PhaseId.IMPACT,PhaseId.FINISH)
            if phase_ids != expected:_err("face-on coaching requires ordered five-phase layout")
        else:
            if phase_ids != (PhaseId.TIMING_RHYTHM,):_err("DTL coaching requires timing-rhythm layout")
            if view.visual_evidence.kind not in (EvidenceKind.TEMPO_TIMELINE,):_err("DTL coaching cannot use body-reference evidence")
    else:
        if view.trust.state!=TrustState.REFILM_REQUIRED or view.journey_mode!=JourneyMode.CAPTURE_RETRY or view.phases:_err("capture-only union inconsistency")
        if any(x not in keys for x in view.capture_guidance.safe_media_keys):_err("missing media reference")

def report_view_from_dict(payload:object)->ReportViewV1:
    try:
        d=_obj(payload,"report view"); version=d.get("version")
        if version != REPORT_VIEW_VERSION: raise UnsupportedReportViewVersion(f"unsupported report view version: {version!r}")
        if _literal(d,"mode","structured")!="structured":_err("invalid mode")
        outcome=_enum(ReportOutcome,_field(d,"outcome"),"outcome"); common=[REPORT_VIEW_VERSION,"structured",_str(d,"presentation_version"),outcome,_enum(JourneyMode,_field(d,"journey_mode"),"journey_mode"),_trust(_field(d,"trust")),_context(_field(d,"context")),_caps(_field(d,"capabilities")),tuple(_media(x) for x in _seq(d,"media")),tuple(_optional(x) for x in _seq(d,"optional_sections"))]
        if outcome==ReportOutcome.COACHING_READY:
            view=CoachingReportView(*common,_next(_field(d,"next_move")),_evidence(_field(d,"visual_evidence")),tuple(_phase(x) for x in _seq(d,"phases")),_practice(_field(d,"practice")),_refilm(_field(d,"refilm")),_field(d,"capture_guidance"))
        else:
            for key in ("next_move","visual_evidence","practice","refilm"):
                if _field(d,key) is not None:_err("capture-only has no coaching content")
            view=CaptureOnlyReportView(*common,None,None,tuple(_phase(x) for x in _seq(d,"phases")),None,None,_capture(_field(d,"capture_guidance")))
        _validate(view); return view
    except UnsupportedReportViewVersion: raise
    except ReportViewValidationError: raise
    except (KeyError,TypeError,ValueError,OverflowError) as exc: raise ReportViewValidationError(str(exc)) from exc

def _jsonable(value:Any)->Any:
    if isinstance(value,StrEnum): return value.value
    if is_dataclass(value): return {f.name:_jsonable(getattr(value,f.name)) for f in fields(value)}
    if isinstance(value,tuple): return [_jsonable(x) for x in value]
    return value
def report_view_to_dict(view:ReportViewV1)->dict[str,object]:
    if not isinstance(view,(CoachingReportView,CaptureOnlyReportView)):raise ReportViewValidationError("expected ReportViewV1")
    _validate(view); return _jsonable(view)
def write_report_view(path:Path,view:ReportViewV1)->Path:
    encoded=json.dumps(report_view_to_dict(view),sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False)+"\n"
    path.write_bytes(encoded.encode("utf-8")); return path
def load_report_view(path:Path)->ReportViewV1:
    with path.open("rb") as handle: raw=handle.read(MAX_REPORT_VIEW_BYTES+1)
    if len(raw)>MAX_REPORT_VIEW_BYTES:_err("report view exceeds maximum size")
    try:return report_view_from_dict(json.loads(raw.decode("utf-8")))
    except (UnicodeDecodeError,json.JSONDecodeError) as exc:raise ReportViewValidationError("invalid report view JSON") from exc
