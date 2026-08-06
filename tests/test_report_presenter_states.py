from __future__ import annotations

from dataclasses import replace

import pytest

from swinglab.caddie_brief import CaddieBrief
from swinglab.coaching import IssueCard, StrengthCard
from swinglab.config import Config
from swinglab.drills import Drill
from swinglab.report_presenter import (
    REASON_COPY,
    ReportContextInput,
    ReportPresentationInput,
    build_report_view,
)
from swinglab.report_view import (
    Angle,
    CaptureOnlyReportView,
    EvidenceKind,
    EventId,
    EventProvenance,
    JourneyMode,
    MediaEntry,
    MediaRole,
    PhaseId,
    PhaseMethod,
    ReasonCode,
    RenderedEvidence,
    TrackingState,
    TrustState,
)


def drill() -> Drill:
    return Drill(
        "test-drill", "Test drill", "Keep the motion organized.",
        ("Set up.", "Rehearse.", "Swing."), "5 reps",
        "legacy text must not be parsed", "swinglab:test",
    )


def issue(flag: str = "sway", metric: str = "head_sway_backswing_sw") -> IssueCard:
    return IssueCard(
        flag, metric, "Head sway", "SW", (0.5,), 0.5, "session mean",
        "0.50 SW", 0.35, "flagged above 0.35 SW", "higher", "warn",
        "Why it matters.", "Turn around a quiet head.", ("test-drill",), ("Test drill",),
    )


def brief(*, focus: str | None = "sway", strength: str | None = None) -> CaddieBrief:
    return CaddieBrief(
        "Head sway stays steady." if strength else None, strength, focus,
        "Head sway", "0.50 SW" if focus else None,
        "flagged above 0.35 SW" if focus else None, "Why it matters.",
        "Turn around a quiet head.", drill(), None, None, 1, 0,
        focus is None, False,
    )


def evidence() -> RenderedEvidence:
    return RenderedEvidence(
        EvidenceKind.HEAD_BOUNDARY, "rendered", 1, PhaseId.GOING_BACK,
        PhaseMethod.HIGHEST_TRACKED_HANDS, 400,
        (EventProvenance(EventId.TOP, PhaseMethod.HIGHEST_TRACKED_HANDS, 400, "Top"),),
        TrackingState.CLEAR, (), (),
        "Head moved", "Address", "Stay centered", 1, 1, None,
        "The head moved going back.", "Focused head reference.", "capture",
    )


def source(*, reasons: tuple[ReasonCode, ...] = (), focus: str | None = "sway", strength: str | None = None):
    media = (MediaEntry("capture", MediaRole.CAPTURE_PLAYBACK, "image/jpeg", "core", "media/capture.jpg", "a" * 64),)
    return ReportPresentationInput(
        ReportContextInput("7i", "right", "face_on", 1, 30.0), (), {}, (),
        brief(focus=focus, strength=strength),
        (issue(),) if focus else (),
        (StrengthCard(strength, "head_sway_backswing_sw", "Head sway", "Head sway stays steady."),) if strength else (),
        drill(), (), evidence(), media, reasons, ("capture",), False, None,
    )


@pytest.mark.parametrize("reason", (
    ReasonCode.CAMERA_ANGLE_MISMATCH,
    ReasonCode.TRACKING_UNSTABLE,
    ReasonCode.INSUFFICIENT_POSE_FRAMES,
    ReasonCode.NO_READABLE_SWING,
    ReasonCode.NO_RELIABLE_STRIKE_EVENT,
    ReasonCode.PRIORITY_EVIDENCE_UNRELIABLE,
))
def test_each_fatal_capture_reason_returns_only_safe_capture_recovery(reason):
    view = build_report_view(source(reasons=(reason,)), Config())
    assert isinstance(view, CaptureOnlyReportView)
    assert view.trust.state is TrustState.REFILM_REQUIRED
    assert view.capture_guidance.primary_reason is reason
    assert view.capture_guidance.reason_label == REASON_COPY[reason].label
    assert view.capture_guidance.correction == REASON_COPY[reason].remediation
    assert view.capture_guidance.checklist
    assert view.capture_guidance.safe_media_keys == ("capture",)
    assert view.next_move is view.visual_evidence is view.practice is view.refilm is None
    assert view.phases == ()
    assert not view.capabilities.focused_evidence
    assert not view.capabilities.gear


def test_reason_order_deduplicates_camera_tracking_frame_priority_then_limited():
    view = build_report_view(source(reasons=(
        ReasonCode.FOCUSED_MEDIA_RENDER_FAILED, ReasonCode.TRACKING_UNSTABLE,
        ReasonCode.CAMERA_ANGLE_MISMATCH, ReasonCode.TRACKING_UNSTABLE,
        ReasonCode.PRIORITY_EVIDENCE_UNRELIABLE, ReasonCode.INSUFFICIENT_POSE_FRAMES,
        ReasonCode.SECONDARY_METRIC_UNAVAILABLE,
    )), Config())
    assert isinstance(view, CaptureOnlyReportView)
    assert view.trust.reasons == (
        ReasonCode.CAMERA_ANGLE_MISMATCH, ReasonCode.TRACKING_UNSTABLE,
        ReasonCode.INSUFFICIENT_POSE_FRAMES, ReasonCode.PRIORITY_EVIDENCE_UNRELIABLE,
        ReasonCode.SECONDARY_METRIC_UNAVAILABLE, ReasonCode.FOCUSED_MEDIA_RENDER_FAILED,
    )


@pytest.mark.parametrize("reason", (
    ReasonCode.SECONDARY_METRIC_UNAVAILABLE,
    ReasonCode.TARGET_DIRECTION_UNCERTAIN,
    ReasonCode.HAND_LANDMARKS_UNRELIABLE,
    ReasonCode.EVENT_ESTIMATE_LIMITED,
    ReasonCode.FOCUSED_MEDIA_RENDER_FAILED,
))
def test_secondary_or_render_reason_is_limited_after_priority_trust_passes(reason):
    view = build_report_view(source(reasons=(reason,)), Config())
    assert not isinstance(view, CaptureOnlyReportView)
    assert view.trust.state is TrustState.LIMITED
    assert view.trust.reasons == (reason,)


def test_no_reasons_is_clear_coaching():
    view = build_report_view(source(), Config())
    assert not isinstance(view, CaptureOnlyReportView)
    assert view.trust.state is TrustState.CLEAR


def test_improve_next_move_uses_selected_issue_and_server_copy_not_drill_metric():
    view = build_report_view(source(), Config())
    assert view.journey_mode is JourneyMode.IMPROVE
    assert view.next_move.priority_key == "sway"
    assert view.next_move.observation == "Why it matters."
    assert view.next_move.cue == "Turn around a quiet head."
    assert "legacy text" not in " ".join((view.next_move.title, view.next_move.observation, view.next_move.cue))


def test_protect_next_move_uses_selected_strength_and_protect_eyebrow():
    view = build_report_view(source(focus=None, strength="sway"), Config())
    assert view.journey_mode is JourneyMode.PROTECT
    assert view.next_move.eyebrow == "Protect this"
    assert view.next_move.priority_key == "sway"
    assert view.next_move.category is PhaseId.GOING_BACK
    assert view.next_move.observation == "Head sway stays steady."
    assert next(phase for phase in view.phases if phase.id is PhaseId.GOING_BACK).status.value == "steady"
    assert "legacy text" not in " ".join((view.next_move.title, view.next_move.observation, view.next_move.cue))
