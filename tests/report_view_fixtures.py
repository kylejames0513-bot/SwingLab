from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path


_ROOT = Path(__file__).with_name("fixtures") / "report_view"


def report_view_payload(name: str = "coaching-improve-clear") -> dict[str, object]:
    return json.loads((_ROOT / f"{name}.json").read_text(encoding="utf-8"))


def report_document_fixture(name: str = "coaching-improve-clear"):
    """Return a typed document variant built from the canonical coaching view."""
    from swinglab.report_presenter import (
        FindingDetail,
        GearDetail,
        GlossaryEntry,
        LabelValue,
        ReportDepthContent,
        ReportDocument,
        ReportNavigation,
        StrengthDetail,
        SwingDetail,
    )
    from swinglab.report_view import (
        Angle,
        BenchmarkRelation,
        CoachingReportView,
        EventId,
        EventProvenance,
        EvidenceKind,
        JourneyMode,
        MeasurementUnit,
        PhaseId,
        PhaseMethod,
        PhaseStatus,
        ReasonCode,
        RenderedEvidence,
        TargetComparator,
        TrackingState,
        TrustState,
        UnavailableEvidence,
        report_view_from_dict,
        report_view_to_dict,
    )

    base_view = report_view_from_dict(report_view_payload())
    assert isinstance(base_view, CoachingReportView)
    assert isinstance(base_view.visual_evidence, RenderedEvidence)
    assert base_view.visual_evidence.supporting_measurement is not None
    view = base_view

    if name == "coaching-improve-clear":
        pass
    elif name == "coaching-improve-clear-long-copy":
        view = replace(
            base_view,
            next_move=replace(
                base_view.next_move,
                title="Keep your head centered while your turn builds behind the ball",
                observation=(
                    "Across the readable swings, your head moved upward while your "
                    "shoulders were still completing the backswing."
                ),
                cue=(
                    "Let your chest finish the turn around a quiet head before you "
                    "change direction."
                ),
            ),
            practice=replace(
                base_view.practice,
                name="Slow wall-turn checkpoint",
                aim="Build a complete shoulder turn without lifting away from address height.",
                summary_steps=(
                    "Set your trail side a hand-width from the wall.",
                    "Turn to the top at half speed without touching the wall.",
                    "Pause, then return to address with the same head height.",
                ),
                full_steps=(
                    "Set your trail side a hand-width from the wall.",
                    "Cross your arms and settle into your normal address posture.",
                    "Turn to the top at half speed without touching the wall.",
                    "Pause for one count, then return with the same head height.",
                ),
                setup="Use your normal 7-iron posture beside a wall.",
                feel_cue="Chest turns; head stays quiet.",
                dosage="Three sets of five slow rehearsals",
                equipment="A wall or alignment stick",
                illustration_label=(
                    "Instructional illustration — not your measured pose"
                ),
            ),
            refilm=replace(
                base_view.refilm,
                checklist=(
                    "Use the same 7 iron and face-on camera angle.",
                    "Match the original camera height and full-body framing.",
                    "Make three swings at the same comfortable effort.",
                ),
                target=replace(
                    base_view.refilm.target,
                    text=(
                        "On two of three swings, keep head rise at or below 0.5 "
                        "shoulder widths."
                    ),
                ),
            ),
        )
    elif name == "coaching-protect-clear":
        protected_measurement = replace(
            base_view.visual_evidence.supporting_measurement,
            plain_value="0.2 shoulder widths",
            numeric_value=0.2,
            benchmark_relation=BenchmarkRelation.BELOW,
        )
        view = replace(
            base_view,
            journey_mode=JourneyMode.PROTECT,
            next_move=replace(
                base_view.next_move,
                mode=JourneyMode.PROTECT,
                eyebrow="Protect this",
                observation="Your head stayed steady while your turn completed.",
                cue="Repeat the same balanced turn without adding effort.",
            ),
            visual_evidence=replace(
                base_view.visual_evidence,
                kind=EvidenceKind.STEADY_REFERENCE,
                observed_label="Head stayed steady",
                boundary_label="Stay within this boundary",
                supporting_measurement=protected_measurement,
                observation="The steady head pattern is visible through the backswing.",
                alt_text="Steady head reference at the top of swing 1.",
            ),
            phases=tuple(
                replace(
                    phase,
                    status=PhaseStatus.STEADY,
                    status_label="Steady",
                    summary=(
                        "Keep this ordinary steady pattern."
                        if phase.id == PhaseId.GOING_BACK
                        else phase.summary
                    ),
                    measurements=(),
                    expanded_by_default=phase.id == PhaseId.GOING_BACK,
                )
                for phase in base_view.phases
            ),
        )
    elif name == "coaching-improve-limited":
        phases = []
        for phase in base_view.phases:
            if phase.id == PhaseId.IMPACT:
                phases.append(
                    replace(
                        phase,
                        status=PhaseStatus.NOT_MEASURED,
                        status_label="Not measured",
                        summary="Impact timing could not be measured from this clip.",
                        readable_swings=0,
                        measurements=(),
                        unavailable_reasons=(ReasonCode.EVENT_ESTIMATE_LIMITED,),
                        expanded_by_default=False,
                    )
                )
            else:
                phases.append(replace(phase, readable_swings=1))
        view = replace(
            base_view,
            trust=replace(
                base_view.trust,
                state=TrustState.LIMITED,
                label="Limited read",
                reasons=(ReasonCode.SECONDARY_METRIC_UNAVAILABLE,),
                explanation="One secondary metric could not be measured reliably.",
            ),
            context=replace(
                base_view.context,
                club=None,
                club_label=None,
                detected_swings=2,
                priority_readable_swings=1,
            ),
            next_move=replace(base_view.next_move, measurement_detail_id=None),
            visual_evidence=replace(
                base_view.visual_evidence,
                tracking_state=TrackingState.LIMITED,
                tracking_reasons=(ReasonCode.HAND_LANDMARKS_UNRELIABLE,),
                readable_swings=1,
                triggered_swings=None,
                supporting_measurement=None,
                reference_label=None,
                boundary_label=None,
                observation="The upward move is visible in the one readable swing.",
            ),
            phases=tuple(phases),
            practice=replace(
                base_view.practice,
                summary_steps=("Set up.", "Turn slowly.", "Stay quiet."),
                full_steps=("Set up.", "Turn slowly.", "Stay quiet."),
                illustration_media_key=None,
                illustration_label=None,
                alternatives=(),
            ),
            refilm=replace(
                base_view.refilm,
                checklist=("Use face-on framing.",),
                primary_action_label="Re-film when you are back online",
            ),
        )
    elif name == "coaching-improve-visual-unavailable":
        evidence = base_view.visual_evidence
        unavailable = UnavailableEvidence(
            kind=evidence.kind,
            state="unavailable",
            swing=evidence.swing,
            phase=evidence.phase,
            phase_method=evidence.phase_method,
            timestamp_ms=evidence.timestamp_ms,
            events=evidence.events,
            tracking_state=evidence.tracking_state,
            tracking_reasons=evidence.tracking_reasons,
            render_reasons=(ReasonCode.FOCUSED_MEDIA_RENDER_FAILED,),
            observed_label=evidence.observed_label,
            reference_label=evidence.reference_label,
            boundary_label=evidence.boundary_label,
            readable_swings=evidence.readable_swings,
            triggered_swings=evidence.triggered_swings,
            supporting_measurement=evidence.supporting_measurement,
            observation=evidence.observation,
            alt_text="Focused visual unavailable for swing 1.",
            media_key=None,
        )
        view = replace(
            base_view,
            trust=replace(
                base_view.trust,
                state=TrustState.LIMITED,
                label="Visual unavailable",
                reasons=(ReasonCode.FOCUSED_MEDIA_RENDER_FAILED,),
                explanation=(
                    "The focused image could not be produced, but the observation "
                    "and measurement remain available."
                ),
            ),
            visual_evidence=unavailable,
        )
    elif name == "coaching-dtl-clear":
        measurement = replace(
            base_view.visual_evidence.supporting_measurement,
            id="tempo-ratio",
            label="Tempo ratio",
            plain_value="3.2 to 1",
            numeric_value=3.2,
            unit=MeasurementUnit.RATIO,
            benchmark_relation=BenchmarkRelation.BETWEEN,
            benchmark_value=2.8,
            benchmark_upper_value=3.2,
            benchmark_label="Target 2.8 to 3.2",
            explanation="Address-to-top and top-to-impact events are compared.",
            limitation="Event timing is estimated from this phone video.",
        )
        events = (
            EventProvenance(
                EventId.ADDRESS, PhaseMethod.OPENING_BASELINE, 120, "Address"
            ),
            EventProvenance(
                EventId.TOP, PhaseMethod.HIGHEST_TRACKED_HANDS, 760, "Top"
            ),
            EventProvenance(
                EventId.IMPACT, PhaseMethod.DETECTED_AUDIO, 1090, "Impact"
            ),
            EventProvenance(
                EventId.FINISH,
                PhaseMethod.CONFIGURED_FINISH_OFFSET,
                1680,
                "Finish",
            ),
        )
        timing_phase = replace(
            base_view.phases[1],
            id=PhaseId.TIMING_RHYTHM,
            label="Timing and rhythm",
            status=PhaseStatus.PRIORITY,
            status_label="Priority",
            summary="The transition changes direction faster than the backswing builds.",
            measurements=(measurement,),
            detail_section_id="timing-rhythm",
            expanded_by_default=True,
        )
        view = replace(
            base_view,
            context=replace(
                base_view.context,
                angle=Angle.DTL,
                angle_label="Down-the-line",
            ),
            next_move=replace(
                base_view.next_move,
                priority_key="tempo",
                category=PhaseId.TIMING_RHYTHM,
                title="Smooth the change of direction",
                observation="Your transition is quick compared with your backswing.",
                cue="Let the backswing finish before the club starts down.",
                measurement_detail_id="tempo-ratio",
            ),
            visual_evidence=replace(
                base_view.visual_evidence,
                kind=EvidenceKind.TEMPO_TIMELINE,
                phase=PhaseId.TIMING_RHYTHM,
                phase_method=PhaseMethod.SESSION_TIMING,
                timestamp_ms=1090,
                events=events,
                observed_label="Tempo measured at 3.2 to 1",
                reference_label="Address event",
                boundary_label="Target rhythm band",
                readable_swings=2,
                triggered_swings=2,
                supporting_measurement=measurement,
                observation="The persisted event sequence shows the change of direction.",
                alt_text="Timing timeline for swing 1 from address through finish.",
            ),
            phases=(timing_phase,),
            practice=replace(
                base_view.practice,
                drill_id="counted-tempo",
                name="Counted tempo rehearsal",
                aim="Give the backswing time to finish before starting down.",
                summary_steps=(
                    "Count one-two-three going back.",
                    "Pause for the change of direction.",
                    "Count one through impact.",
                ),
                full_steps=(
                    "Make two rehearsals without a ball.",
                    "Count one-two-three going back.",
                    "Change direction, then count one through impact.",
                ),
                setup="Use your normal down-the-line camera setup.",
                feel_cue="Long going back, smooth change, then through.",
                dosage="Five rehearsals, then three swings",
                equipment="Metronome optional",
                illustration_label=(
                    "Instructional illustration — not your measured pose"
                ),
            ),
            refilm=replace(
                base_view.refilm,
                checklist=(
                    "Keep the down-the-line camera position.",
                    "Use the same 7 iron and comfortable effort.",
                ),
                target=replace(
                    base_view.refilm.target,
                    text="Repeat a tempo ratio between 2.8 and 3.2 to 1.",
                    metric_id="tempo-ratio",
                    comparator=TargetComparator.BETWEEN,
                    threshold=2.8,
                    upper_threshold=3.2,
                    unit=MeasurementUnit.RATIO,
                ),
            ),
        )
    else:
        view = report_view_from_dict(report_view_payload(name))

    # Every named variant crosses the same persisted serialization boundary.
    view = report_view_from_dict(report_view_to_dict(view))
    media = {entry.key: entry for entry in view.media}
    depth = ReportDepthContent(
        swings=(
            SwingDetail(
                1,
                "Swing 1",
                ("Readable swing",),
                (),
                "focus-1",
                "Key positions for swing 1",
                None,
                "Slow-motion swing 1",
                None,
                "Coach replay for swing 1",
                False,
                None,
                None,
                "Video poster for swing 1",
                "Playback: media/focus-1.jpg",
            ),
        ),
        secondary_findings=(
            FindingDetail(
                "tempo",
                "Tempo",
                "A secondary timing note.",
                "Timing supports contact.",
                "Finish going back.",
                ("head-rise",),
                "secondary-findings",
            ),
        ),
        strengths=(
            StrengthDetail(
                "balance",
                "Balanced finish",
                "Your finish stayed steady.",
                (),
            ),
        ),
        measurements=(
            LabelValue("head-rise", "Head rise", "1.2 shoulder widths"),
        ),
        session_details=(LabelValue("angle", "Camera angle", "Face-on"),),
        glossary=(
            GlossaryEntry("Shoulder width", "A body-scaled distance."),
        ),
        limitations=(
            "Single-camera measurements describe motion in the image plane.",
            "Event timing can be limited by frame rate and audio quality.",
        ),
        gear=(
            GearDetail(
                "tempo-aid",
                "Tempo aid",
                "Optional timing aid.",
                "/gear/tempo",
            ),
        ),
        navigation=ReportNavigation("/", "/shop", "/collections/swinglab-gear"),
    )
    if name == "coaching-improve-limited":
        depth = replace(
            depth,
            navigation=replace(depth.navigation, app_url=None),
        )
    elif name == "coaching-dtl-clear":
        depth = replace(
            depth,
            session_details=(
                replace(depth.session_details[0], value="Down-the-line"),
            ),
        )
    return ReportDocument(view=view, depth=depth, media_by_key=media)
