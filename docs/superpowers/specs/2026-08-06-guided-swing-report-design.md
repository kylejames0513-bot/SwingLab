# Guided Swing Report Redesign

**Status:** Product design approved on 2026-08-06; written-spec review pending

**Implementation target:** The existing Python report pipeline, authenticated web
experience, and the native CaddieInsight mobile client

## 1. Summary

CaddieInsight will replace its long, repetitive single-session report with a
guided coaching journey. The main path will answer six questions in order:

1. What should I work on now?
2. Where can I see it in my own swing?
3. What did the rest of my swing show?
4. What should I practice?
5. How should I re-film?
6. How will I know whether the change worked?

The experience will lead with one plain-English priority, one primary drill, and
one measurable re-film target. A single large frame from the golfer's own video
will explain the priority. Five phase-based categories will provide the deeper
swing breakdown. Raw measurements, every swing, replay, glossary, limitations,
secondary findings, and alternative drills will remain available as optional
depth.

The current analysis engine remains the source of truth. A new versioned report
view model will present the existing measurements and coaching decisions to both
the self-contained web report and the native mobile client. The client will not
reimplement swing analysis, priority selection, confidence, drill choice, or
pass-mark logic.

## 2. Why this change is needed

The current report is trustworthy and comprehensive, but the user must assemble
the answer from repeated and visually equal sections. The generated sample
audited during design had approximately 1,973 visible words, 41 headings, six
breakdown cards, three per-swing evidence articles, and five drills. On a
390-by-844-pixel viewport, the primary action began below the opening hero and
two dense measurement-scope notes, while the full page was roughly 14,500 pixels
long.

The main usability problems are:

- The same priority appears in the Caddie Brief, the later "Start here" section,
  and the full practice plan.
- One recommended drill becomes several apparently equal drills farther down.
- Context-only values, steady values, unavailable values, and actionable findings
  use visually similar cards.
- Technical units such as shoulder widths appear before the plain-language idea
  is fully understood.
- Three- and four-panel raster images shrink on mobile instead of reorganizing.
- The green "where it should be" skeleton is a generic ankle-pinned shear, not a
  personalized ideal pose, and therefore communicates more certainty than the
  model supports.
- Visuals do not expose their phase-selection method or tracking limitations.
- Down-the-line sessions can receive a face-on-style centerline overlay even
  though their supported coaching contract is timing and rhythm only.

## 3. Goals

- Make the selected priority and cue understandable within the opening owned-report
  mobile viewport.
- Organize the whole swing into clear categories and steps for a beginner golfer.
- Connect the selected priority to one understandable moment in the golfer's own
  video.
- Preserve evidence, limitations, raw measurements, print/PDF behavior, and
  offline self-contained web reports.
- Use the same server-owned coaching structure on web and mobile.
- Make confidence and phase provenance visible without invented probabilities.
- Define a comparable re-film and pass mark in every coaching-ready report.
- Preserve the existing capture-only safety gate and DTL timing-only boundary.
- Remain backward compatible with immutable historical reports.

## 4. Non-goals

- Rewriting pose, events, metrics, coaching, or Proof Cycle logic in TypeScript.
- Claiming 3D movement, body rotation, pressure, ground force, club path, face
  angle, strike location, launch, spin, carry, or ball flight.
- Creating a generic 1-to-100 swing score.
- Predicting or drawing a personalized ideal full-body pose.
- Retaining raw pose coordinates in the mobile API.
- Reprocessing or mutating historical customer reports.
- Letting the golfer adjust event markers in version 1.
- Building a multi-layer interactive swing lab in version 1.
- Moving longitudinal Proof of Change or Proof of Transfer decisions into the
  immutable single-session report.

## 5. Product principles

1. **Action before analysis.** Lead with one change, one drill, and one target.
2. **Plain English before units.** Explain the visible idea before displaying the
   supporting number.
3. **Observed before inferred.** Show what the 2D video supports and label how the
   phase was selected.
4. **One source of coaching truth.** Web and mobile render server decisions rather
   than selecting priorities independently.
5. **Progressive disclosure without information loss.** Trustworthy detail remains
   available, but it does not interrupt the main path.
6. **Fail closed.** Missing or weak evidence becomes unavailable or re-film
   guidance, never a guessed diagnosis.
7. **Prove the next change.** Every coaching-ready result ends with a comparable
   re-film protocol and an explicit pass mark.

## 6. Report states

The persisted report outcome remains compatible with the current two-outcome
contract: `coaching_ready` or `capture_only`. The new presentation adds a trust
state inside that outcome; it does not introduce a third persisted report
outcome.

### 6.1 Clear enough to coach

The capture passes the current coaching gate and the selected priority has usable
supporting evidence. Render the full guided report: next move, focused evidence,
whole-swing categories, primary drill, re-film protocol, and optional depth.

Use this state only when the existing capture gate passes, the selected priority
metric is trustworthy, its required event exists, and a priority-specific
`EvidenceSnapshot` passes its annotation quality gate.

### 6.2 Limited evidence

The report remains `coaching_ready`, but one or more non-priority categories or
visual details are unavailable. Render the supported priority and clearly label
each unsupported category as `Not measured`. A missing secondary measurement
must not suppress a trustworthy primary action.

If the underlying metric, event, or tracking evidence for the selected priority
is unreliable, the result cannot use this state; it becomes capture-only and
requests a re-film. A media-renderer-only failure is the explicit exception
described below because the coaching evidence remains trustworthy.

This state also covers a focused-media rendering failure when the priority metric
and its evidence snapshot remain trustworthy. The main path replaces the image
with a `Visual unavailable` card containing the same text observation, phase
method, supporting measurement link, and a retry-safe explanation. It does not
substitute an unrelated swing or weaker annotation.

### 6.3 Re-film required

The result remains `capture_only`. Render capture guidance and only playback that
is already safe under the current contract. Suppress diagnosis, phase grades,
drills, corrective annotations, coach replay, proof targets, and commerce.

The capture-only main path is ordered as:

1. the primary reason the clip cannot support coaching;
2. one camera- or tracking-specific correction;
3. a short filming checklist;
4. safe playback when available; and
5. a primary `Re-film` or `Choose another video` action.

Angle mismatch, unstable tracking, insufficient pose frames, no readable swing,
and no reliable strike event each receive a distinct reason and correction. A
failed retry preserves the prior capture-only result and offers another upload or
support path; it never exposes partial coaching.

### 6.4 Trust-state algorithm and reason codes

Report-level state precedence is `refilm_required`, then `limited`, then `clear`.

- `refilm_required` is set when the current capture-quality gate requires a
  re-film, the selected priority metric is unavailable or unreliable, or a
  required event for that priority does not exist.
- `limited` is set only after the priority remains trustworthy, when a secondary
  category is unavailable, target direction limits wording, a nonessential hand
  visual is unavailable, or focused media rendering fails.
- `clear` is set when the priority and every rendered category have the required
  evidence and no limiting reason applies.

The version-1 reason-code enum is:

`secondary_metric_unavailable`, `target_direction_uncertain`,
`hand_landmarks_unreliable`, `event_estimate_limited`,
`focused_media_render_failed`, `camera_angle_mismatch`, `tracking_unstable`,
`insufficient_pose_frames`, `no_readable_swing`, `no_reliable_strike_event`, and
`priority_evidence_unreliable`.

Each code has one server-owned user label and remediation string. Clients render
those strings and do not infer severity from the code name.

## 7. Main reading order

### 7.1 Compact context header

Show club, handedness, camera view, detected-swing count,
priority-readable-swing count, and the categorical trust state in one compact
header. `Priority readable` means a swing contains the selected priority metric
and passes that priority's evidence gate; phase rows carry their own readability
count when it differs. Move the long measurement boundary into an optional "What
this video can measure" disclosure. Keep a one-line boundary near the main visual
when it materially prevents misinterpretation.

### 7.2 Your next move

The opening card contains:

- one priority or, for a clean result, one strength to protect;
- a plain-English observation;
- one coach cue;
- the categorical trust state; and
- a compact Understand, Practice, and Re-film journey preview.

The opening card must not require a technical unit to understand the conclusion.
The supporting value remains available through "See measurement."

The full primary drill prescription appears once in the canonical Practice card,
and the full pass mark appears once in the canonical Re-film card. The opening
journey preview may link to those cards and say that a drill and target are ready,
but it does not repeat their full content.

### 7.3 Three-step journey

The main report visibly groups the experience into:

1. **Understand** — focused evidence and whole-swing categories;
2. **Practice** — one prescribed drill and dosage; and
3. **Re-film** — matched setup, pass mark, and next action.

This is an information hierarchy, not a client-side state machine. The
self-contained HTML report must remain readable without JavaScript.

### 7.4 Focused visual evidence

Show one large, priority-specific visual from the golfer's own swing. The visual
must identify the swing number, phase, phase-selection method, observed marker,
reference marker or zone, tracking state, and one plain-English callout.

### 7.5 Whole-swing breakdown

Show a compact scan of supported phases. Only the active priority expands by
default. Other categories contain a one-sentence summary and explicit status.
Down-the-line reports are the exception: they render a focused timing-and-rhythm
breakdown rather than five mostly unsupported phase rows.

### 7.6 Primary practice prescription

Render the selected drill once. Its main card includes three novice-facing
stages, dosage, required setup, and a feel cue. Existing drill
protocols with more than three instructions require authored three-stage
summaries; the presenter must not truncate or automatically merge safety-critical
steps.

Alternative drills remain under "Try a different drill" and are explicitly
secondary. Secondary flagged issues remain under "Review later" and do not gain
their own default practice plan.

### 7.7 Matched re-film

End the main journey with the same club, handedness, camera angle and height,
framing, and similar-effort checklist. This card is the canonical location for
the full measurable pass mark. The opening journey preview may say that a target
is set, but it does not repeat the pass-mark sentence.

### 7.8 Optional depth

The following sections are collapsed or linked by default:

- every detected swing;
- slow motion and coach replay;
- secondary findings;
- alternative drills;
- strengths beyond the one opening strength;
- raw measurements and session context;
- glossary and full measurement boundaries; and
- optional, evidence-matched training aids.

Print/PDF expands text, tables, static evidence frames, and instructional
diagrams. Video becomes its poster frame plus a caption and owned-screen playback
reference. Printing never reveals replay or other media that the viewer's
entitlement does not allow; a locked item remains a lock explanation in print.

## 8. Whole-swing categories

### 8.1 Face-on taxonomy

| Category | Supported content |
|---|---|
| Setup | Stance-width baseline and capture context |
| Going back | Backswing duration, head sway, and hip slide |
| Transition & downswing | Tempo ratio, downswing duration, and personal hand-movement baseline |
| Impact | Estimated impact frame, head dip, lead-arm shape, and shoulder tilt |
| Finish | Finish-base stability and held-finish context |

Measurements remain scoped by the existing engine. A category renders only
values supported by the current view and readable frames.

### 8.2 Down-the-line taxonomy

Down-the-line remains timing and rhythm only. It renders the persisted address,
top, impact-estimate, and finish event labels and methods with backswing duration,
downswing duration, tempo ratio, and consistency where supported. Structured
event timestamps come from the new in-memory `EvidenceSnapshot`; no client
reconstructs them from durations. It does not render face-on body movement,
reference zones, or empty five-phase body cards. If no reliable detected-audio or
manual strike event exists, the result requests a re-film rather than fabricating
an impact estimate.

### 8.3 Status vocabulary

| Status | Meaning |
|---|---|
| Priority | The one selected change for the current practice cycle |
| Review later | A supported secondary issue intentionally deferred |
| Steady | Measured and inside the configured coaching line |
| Baseline | Useful personal context without a universal pass/fail judgment |
| Not measured | Unsupported in this view or unavailable because evidence was weak |

Every status uses text and an icon or shape. Color is supplementary.

Status is derived only from existing flags and measurement semantics; the
presenter does not create a new diagnosis. When multiple values contribute to
one category, use this precedence:

1. `Priority` when the selected Caddie Brief focus belongs to the category;
2. `Review later` when an existing secondary issue card belongs to it;
3. `Steady` when supported scored values are present and none fired an issue;
4. `Baseline` when the category contains context-only values without a universal
   pass/fail judgment; and
5. `Not measured` when no supported value is available.

Unavailable secondary values may be mentioned inside an otherwise supported
category, but they do not silently change its status.

### 8.4 Clean and maintenance journey

A clean report uses `journey_mode: protect`. Its opening label is `Protect this`,
not `Priority`. The category containing the selected strength expands, keeps its
ordinary `Steady` status, and adds a `Strength to protect` sublabel. The server
selects one maintenance drill and measurable maintenance target through the
existing clean-result Caddie Brief. Practice and Re-film remain canonical and
appear once. Conditional acceptance tests use `strength`, `maintenance drill`,
and `maintenance target` instead of requiring a fault label.

## 9. Focused evidence rules

### 9.1 Shared visual contract

Every focused evidence object includes:

- source swing number;
- source frame or timeline;
- phase label;
- phase-selection method;
- annotation kind;
- observed label;
- reference label, when valid;
- categorical tracking state and reason codes;
- plain-English observation;
- optional supporting measurement; and
- descriptive alternative text.

### 9.2 Representative-swing selection

The focused frame represents the selected priority without cherry-picking the
most extreme swing for visual drama. Selection is deterministic:

1. Start with swings that contain the priority metric and pass its
   annotation-specific evidence gate.
2. For a threshold-based priority, prefer eligible swings that crossed the
   configured coaching line and select the value closest to the median of those
   crossings.
3. For a priority based on a session mean, select the eligible swing closest to
   that mean.
4. For a consistency or standard-deviation priority, select the eligible swing
   closest to the session median and explain the full range separately.
5. For shoulder-tilt change, use the eligible swing closest to the session mean
   delta and render both required shoulder lines from that same swing.
6. For a clean or maintenance result, select the eligible swing closest to the
   session median for the strength being protected.
7. Break exact ties with the earliest swing number.

Metric and image always come from the same selected swing. The card states how
many swings were detected, how many were readable for the priority, and how many
crossed the line when applicable. If no swing passes the visual gate but the
priority metric remains trustworthy, use the explicit `visual_unavailable`
variant and set report trust to `limited`. If the priority metric or its required
event is unreliable, request a re-film. Missing detected audio may use an
explicitly persisted manual strike event; it never falls back to a guessed frame.

### 9.3 Priority-specific annotation

| Priority family | Default evidence treatment |
|---|---|
| Head sway or hip slide | Address-relative starting marker, configured coaching boundary, observed position, and displacement arrow at the relevant phase |
| Head dip | Address head-height marker, configured coaching boundary, and observed head position on the impact estimate |
| Tempo or transition | Four-event timeline with backswing, downswing, ratio, and consistency labels |
| Lead-arm shape | Angle arc and plain-language arm-shape label on the impact estimate |
| Shoulder tilt | Shoulder line and angle label on the impact estimate |
| Finish-base stability | Stance-center positions during the finish-hold window |
| Clean or maintenance result | A representative steady measurement or rhythm timeline labeled as a strength to protect |

### 9.4 Annotation semantics

- Orange identifies the observed position or measurement.
- Green identifies the starting reference marker, not an ideal body pose.
- A separately labeled dashed boundary represents the configured coaching line
  when the selected metric has one.
- Solid and dashed line styles plus text labels duplicate the color meaning.
- The report never labels a synthetic full-body figure "where it should be."
- Target-relative language appears only when target direction is confident.
- A body reference annotation never appears on DTL footage.

For sway and slide, convert the configured shoulder-width threshold to pixels
using the selected swing's address shoulder width. Place the boundary in the
scored direction only when target direction is confident; otherwise omit the
directional geometry and language. For head dip, use vertical address-relative
geometry and no target direction. Angle visuals use the exact tracked joints
that produced the measurement. Finish stability uses the measured ankle-midpoint
positions. The renderer never draws a boundary when a required landmark is
missing or its quality gate fails.

The renderer preserves the golfer's actual handedness and does not mirror source
video. Any crop must retain every joint and marker used by the annotation. Plain
language distinguishes the starting marker from the coaching boundary, for
example: `Your head crossed the coaching boundary relative to where it started.`
The pass mark uses the same configured boundary; its exact value and unit remain
available under measurement detail.

### 9.5 Phase provenance

Use honest labels such as:

- `Address sample — opening baseline`;
- `Top estimate — highest tracked hands`;
- `Impact estimate — detected sound mapped to nearest frame`; and
- `Finish sample — configured interval after impact`.

The exact method comes from structured fields, not copy inferred in the client.

### 9.6 Tracking quality

Expose categorical states such as `Clear`, `Limited`, and `Unavailable`, backed
by reason codes. Do not expose invented probability percentages.

Hand-dependent visuals require wrist and elbow visibility and jump checks in
addition to the existing core shoulder, hip, and ankle gate. Break a hand trail
across uncertain frames instead of connecting unreliable points.

### 9.7 Instructional diagrams

Drill diagrams are instructional artwork, not measured pose output. Mirror them
for handedness where direction matters and label them `Instructional
illustration — not your measured pose`. Do not use a diagram in place of the
focused evidence frame.

## 10. Practice and commerce rules

- The primary drill is complete without a purchase.
- Its three-stage summary is authored data, not automatic text truncation.
- The full existing protocol remains available when it adds useful detail.
- Alternative drills are visually secondary and never presented as simultaneous
  requirements.
- Optional gear appears after the complete practice prescription and only on a
  coaching-ready result with a relevant approved tag.
- Capture-only reports never include gear recommendations.
- Gear copy may say it supports the drill; it may not say it fixes the swing.

Every drill that can become primary under the enabled presentation has authored
presentation data:

- exactly three nonempty `summary_steps` in setup, rehearse, and perform order;
- one `setup` string;
- one `feel_cue` string;
- one `dosage` string;
- an `equipment` string or explicit `none` value;
- the complete existing `full_steps` list; and
- handedness behavior for its instructional diagram.

Startup/preflight validation rejects enabling the redesigned presentation when
any eligible drill lacks these fields. The presenter never truncates a full
protocol to manufacture three stages. If catalog validation somehow fails after
a job has already selected the redesigned renderer, report generation fails as a
core artifact failure with no allowance consumed; it does not emit an incomplete
practice prescription.

## 11. Architecture

### 11.1 Source of truth

The existing Python pipeline continues to own video analysis, event estimates,
metrics, quality gates, Caddie Brief selection, issue severity, drill selection,
and re-film targets.

### 11.2 Report presenter

Add a pure server-side presenter that builds a versioned `ReportViewModel` from
already-scoped metrics, quality reasons, the selected Caddie Brief, evidence
media, practice data, and report context. It must not rerun pose or select a
different priority.

The pipeline adds an in-memory `EvidenceSnapshot` for each swing before work
frames and landmarks are deleted. It contains swing number, event timestamps and
method identifiers, priority-relevant frame keys, metric-specific landmark
quality results, target-direction confidence, and in-memory landmarks required
by the focused renderer. Raw landmarks never enter `report-view.json` or the
mobile API.

Add a report-bundle orchestrator that accepts scoped metrics, Caddie Brief,
practice data, and the evidence snapshots, then builds all core artifacts. Keep
`write_report_html()` as a compatibility adapter for existing tests, synthetic
callers, and legacy rendering; the production pipeline uses the bundle
orchestrator. HTML compatibility markers remain stable even though the internal
call path expands.

### 11.3 Logical components

- **Report view-model builder:** ordering, statuses, plain-language fields,
  optional sections, and shared web/mobile contract.
- **Evidence snapshot builder:** captures structured event provenance and
  metric-specific quality while analysis frames and landmarks still exist.
- **Focused evidence selector:** chooses the representative swing and phase for
  the already-selected priority using deterministic rules.
- **Focused evidence renderer:** creates the observed-only image or timing
  timeline while analysis landmarks and frames still exist.
- **Static HTML renderer:** renders one self-contained offline report from the
  view model.
- **Owned API adapter:** returns the same view model with authenticated media
  URLs for mobile.
- **Legacy adapter:** identifies sessions without the new artifact and returns an
  owned legacy-report fallback, without pretending to have structured evidence.

### 11.4 Data flow

```text
video
  -> existing pose, events, metrics, quality, coaching
  -> in-memory EvidenceSnapshot per swing
  -> selected Caddie Brief and primary drill
  -> focused evidence selector and renderer
  -> ReportViewModel v1
       -> self-contained report.html
       -> private report-view.json
       -> authenticated native report response

practice + matched re-film + history
  -> existing verified Proof Cycle sidecar and progress surface
```

Proof Cycle remains separate because it depends on later private history and must
be re-verified. The immutable single-session report defines the experiment and
target; it does not later rewrite itself with the verdict.

## 12. Report view-model contract

The persisted artifact uses a separate schema version from the HTML presentation
version. Every field below is required unless its type explicitly includes
`null`. Arrays are always present and server-ordered. A client may ignore unknown
fields inside a known version, but it rejects an unknown `version`. Adding a new
required field or changing enum meaning requires a new schema version.

### 12.1 Top-level discriminated union

```text
ReportViewV1 = CoachingReportView | CaptureOnlyReportView

BaseReportView {
  version: "report-view-v1"
  mode: "structured"
  presentation_version: string
  outcome: "coaching_ready" | "capture_only"
  journey_mode: "improve" | "protect" | "capture_retry"
  trust: Trust
  context: ReportContext
  capabilities: Capabilities
  media: MediaEntry[]
  optional_sections: OptionalSection[]
}

CoachingReportView extends BaseReportView {
  outcome: "coaching_ready"
  journey_mode: "improve" | "protect"
  trust.state: "clear" | "limited"
  next_move: NextMove
  visual_evidence: RenderedEvidence | UnavailableEvidence
  phases: PhaseSummary[]                 // one DTL timing row or five face-on rows
  practice: PracticePrescription
  refilm: RefilmProtocol
  capture_guidance: null
}

CaptureOnlyReportView extends BaseReportView {
  outcome: "capture_only"
  journey_mode: "capture_retry"
  trust.state: "refilm_required"
  next_move: null
  visual_evidence: null
  phases: []
  practice: null
  refilm: null
  capture_guidance: CaptureGuidance
}
```

New capture-only sessions persist `report-view.json` using the capture-only
variant. Therefore absence of `report-view.json` means legacy only after session
ownership and the session's historical completion state are verified.

### 12.2 Common enums and trust

```text
TrustState = "clear" | "limited" | "refilm_required"
TrackingState = "clear" | "limited" | "unavailable"
Angle = "face_on" | "dtl"
Hand = "right" | "left"
PhaseId = "setup" | "going_back" | "transition_downswing" |
          "impact" | "finish" | "timing_rhythm"
PhaseStatus = "priority" | "review_later" | "steady" |
              "baseline" | "not_measured"
ReasonCode = "secondary_metric_unavailable" |
             "target_direction_uncertain" |
             "hand_landmarks_unreliable" |
             "event_estimate_limited" |
             "focused_media_render_failed" |
             "camera_angle_mismatch" |
             "tracking_unstable" |
             "insufficient_pose_frames" |
             "no_readable_swing" |
             "no_reliable_strike_event" |
             "priority_evidence_unreliable"

Trust {
  state: TrustState
  label: string                         // server-owned user-facing label
  reasons: ReasonCode[]                 // unique, server-ordered
  explanation: string | null
}
```

### 12.3 Context and next move

```text
ReportContext {
  club: string | null
  club_label: string | null
  hand: Hand
  angle: Angle
  angle_label: string
  detected_swings: integer >= 0
  priority_readable_swings: integer >= 0
  analysis_fps: number > 0 | null
}

NextMove {
  mode: "improve" | "protect"
  priority_key: string
  category: PhaseId
  eyebrow: string                       // "Work on now" or "Protect this"
  title: string
  observation: string
  cue: string
  measurement_detail_id: string | null
  practice_anchor: "practice"
  refilm_anchor: "refilm"
}
```

For an improvement journey, `priority_key` is the existing selected issue key.
For a protect journey, it is the existing selected strength or maintenance key.
Clients do not derive the eyebrow, title, or category.

### 12.4 Focused evidence variants

```text
EvidenceKind = "head_boundary" | "hip_boundary" | "head_height" |
               "tempo_timeline" | "lead_arm_angle" |
               "shoulder_tilt" | "finish_stability" |
               "steady_reference"
PhaseMethod = "opening_baseline" | "highest_tracked_hands" |
              "detected_audio" | "manual_strike" |
              "configured_finish_offset" | "session_timing"
EventId = "address" | "top" | "impact" | "finish"

EventProvenance {
  event: EventId
  method: PhaseMethod
  timestamp_ms: integer >= 0
  label: string
}

EvidenceBase {
  kind: EvidenceKind
  swing: integer >= 1
  phase: PhaseId
  phase_method: PhaseMethod
  timestamp_ms: integer >= 0 | null
  events: EventProvenance[]              // primary event only, or all timing events
  tracking_state: TrackingState
  tracking_reasons: ReasonCode[]
  render_reasons: ReasonCode[]
  observed_label: string
  reference_label: string | null
  boundary_label: string | null
  readable_swings: integer >= 1
  triggered_swings: integer >= 0 | null
  supporting_measurement: MeasurementDetail | null
  observation: string
  alt_text: string
}

RenderedEvidence extends EvidenceBase {
  kind: EvidenceKind
  state: "rendered"
  media_key: string
  render_reasons: []
}

UnavailableEvidence extends EvidenceBase {
  kind: EvidenceKind                      // intended evidence treatment
  state: "unavailable"
  media_key: null
  tracking_state: "clear" | "limited"
  render_reasons includes "focused_media_render_failed"
}
```

The unavailable variant is valid only when the priority measurement and event
remain trustworthy and media rendering alone failed. Otherwise the session uses
the capture-only variant.

### 12.5 Phase and measurement objects

```text
MeasurementUnit = "seconds" | "ratio" | "shoulder_widths" |
                  "shoulder_widths_per_second" | "degrees" | "count"
BenchmarkRelation = "above" | "below" | "between" | "context_only" |
                    "none"

MeasurementDetail {
  id: string
  label: string
  plain_value: string
  numeric_value: number | null
  unit: MeasurementUnit | null
  benchmark_relation: BenchmarkRelation
  benchmark_value: number | null
  benchmark_upper_value: number | null
  benchmark_label: string | null
  explanation: string
  limitation: string
}

PhaseSummary {
  id: PhaseId
  label: string
  status: PhaseStatus
  status_label: string
  summary: string
  readable_swings: integer >= 0
  measurements: MeasurementDetail[]
  unavailable_reasons: ReasonCode[]
  detail_section_id: string
  expanded_by_default: boolean
}
```

Face-on phase order is setup, going back, transition and downswing, impact, then
finish. Exactly one phase is expanded for `improve`; the selected strength phase
is expanded for `protect`. DTL contains exactly one `timing_rhythm` row. Clients
render the supplied order and do not recalculate status.

### 12.6 Practice and re-film objects

```text
DrillAlternative {
  id: string
  name: string
  aim: string
  detail_section_id: string
}

PracticePrescription {
  section_id: "practice"
  drill_id: string
  name: string
  aim: string
  summary_steps: [string, string, string] // exactly three nonempty authored stages
  full_steps: string[]                   // nonempty, original protocol order
  setup: string
  feel_cue: string
  dosage: string
  equipment: string | null
  illustration_media_key: string | null
  illustration_label: "Instructional illustration — not your measured pose" | null
  alternatives: DrillAlternative[]
}

TargetComparator = "lte" | "gte" | "between" | "all_lte" |
                   "all_gte" | "count_lte" | "count_gte"
TargetWindow = "swing" | "session" | "consecutive_sessions"

RefilmTarget {
  text: string
  metric_id: string
  comparator: TargetComparator
  threshold: number
  upper_threshold: number | null
  unit: MeasurementUnit
  required_successes: integer >= 1 | null
  required_attempts: integer >= 1 | null
  window: TargetWindow
}

RefilmProtocol {
  section_id: "refilm"
  checklist: string[]                    // nonempty, server order
  target: RefilmTarget
  primary_action_label: string
  preserves_club: boolean
  preserves_hand: true
  preserves_angle: true
  preserves_camera_height: true
  preserves_framing: true
  preserves_effort: true
}
```

The full pass-mark sentence exists only in `RefilmTarget.text`. Practice links to
the Re-film card but does not duplicate it.

### 12.7 Capture-only object

```text
CaptureGuidance {
  primary_reason: ReasonCode
  reason_label: string
  explanation: string
  correction: string
  checklist: string[]                    // nonempty
  safe_media_keys: string[]
  primary_action: "refilm" | "choose_video"
  primary_action_label: string
  secondary_action: "choose_video" | "support" | null
  secondary_action_label: string | null
}
```

### 12.8 Optional sections, capabilities, and media

```text
OptionalSectionId = "every_swing" | "replay" | "secondary_findings" |
                    "alternative_drills" | "more_strengths" |
                    "measurements" | "glossary" | "gear"
MediaRole = "priority_evidence" | "drill_illustration" | "key_positions" |
            "slow_motion" | "coach_replay" | "video_poster" |
            "capture_playback"
Entitlement = "core" | "free" | "pro"

OptionalSection {
  id: OptionalSectionId
  label: string
  available: boolean
  locked: boolean
  item_count: integer >= 0
}

Capabilities {
  structured_report: true
  focused_evidence: boolean
  every_swing: boolean
  slow_motion: boolean
  coach_replay: boolean
  measurements: boolean
  alternative_drills: boolean
  gear: boolean
  print: boolean
}

MediaEntry {
  key: string
  role: MediaRole
  mime_type: string
  entitlement: Entitlement
  relative_path: string                 // persisted private artifact only
  checksum_sha256: string
}
```

Only durable files appear in `media`; absent or locked unrendered media is
represented through capabilities and optional-section state. Focused evidence is
`core` for every coaching-ready structured report with rendered evidence; replay
entitlement is modeled separately. The authenticated API projection removes
`relative_path` and `checksum_sha256`, then adds `url: string | null` and
`locked: boolean`. Static HTML resolves the private relative path inside the
owned report bundle. Raw `report-view.json` is never exposed through a generic
file-serving route.

### 12.9 Legacy API variant

Legacy is an API response mode, not a persisted `report-view-v1` object:

```text
LegacyReportResponse {
  api_version: "report-view-api-v1"
  mode: "legacy"
  structured_report: false
  report_url: string                    // owned authorized route
  capabilities: {
    structured_report: false
    legacy_report: true
  }
}
```

An authenticated structured response may contain the allowlisted coaching text
and supporting measurements defined above. The privacy prohibition applies to
logs, telemetry, analytics, notifications, unauthorized responses, and unrelated
API payloads—not to the authorized report response itself.

## 13. Persisted outputs and transaction boundary

Every new session assigned the redesigned presentation persists:

- the existing `metrics.json` unchanged;
- the redesigned `report.html`;
- `report-view.json` with schema version `report-view-v1`;
- one priority-focused evidence image or timing diagram for a rendered
  coaching-ready visual; and
- the existing safe media deliverables allowed by entitlement and outcome.

Capture-only sessions persist the capture-only view-model variant and no focused
evidence image. A focused evidence image is core and not Pro-gated for a
coaching-ready structured report. Slow motion, coach replay, and other media keep
their separate capability and entitlement behavior.

### 13.1 Atomic publication

1. At job creation, persist the selected report presentation version and
   entitlement snapshot. Retries reuse them; a cohort rollback changes only
   future jobs.
2. Create a unique attempt staging directory inside the target session directory
   so staging and final files share a volume.
3. Generate the view model, HTML, declared media, manifest, and checksums into the
   staging directory.
4. Validate the complete schema, report meta markers, outcome consistency,
   entitlement consistency, checksum manifest, and every relative path. Reject
   absolute paths, symlinks, traversal, undeclared files, and missing references.
5. Move validated files into their final session-relative locations with
   same-volume replace operations.
6. In the final database transaction, set the job to done, assign `report_rel`
   and structured-report capability, and record allowance consumption. No owned
   reader treats files as published before that transaction commits.
7. On crash or retry, remove only the validated attempt directory and unreferenced
   files named by that attempt manifest. Never perform a broad session-directory
   deletion.

A focused image rendering failure may degrade to the `visual_unavailable`
variant when the priority measurement and evidence remain trustworthy. A
view-model, HTML, manifest, or required-media failure is a core report failure:
publish no completed result and preserve the current no-allowance-on-failure
behavior. Capture-only uses the same staging and commit boundary.

### 13.2 Legacy overlay policy

Jobs persisted with the redesigned presentation do not generate or persist the
generic corrected centerline overlay. The focused renderer replaces it. New DTL
jobs never generate a body-reference overlay. Jobs persisted with the legacy
presentation retain the legacy deliverable behavior for rollback compatibility.
Historical overlays remain untouched and accessible only through their existing
legacy reports.

### 13.3 Artifact lifecycle

Add `report-view.json`, focused evidence, and their manifest/checksums to backup
and restore, history reset, privacy export and deletion, account deletion,
retention cleanup, and recovery verification. Privacy export may include the
user-readable report and evidence; raw private `report-view.json` remains blocked
from generic session-file serving and is exposed only through the authorized
report-view adapter. Restore verifies checksums and ownership before a structured
report becomes readable.

Proof Cycle generation remains a non-blocking, separately verified sidecar.

## 14. Owned mobile API

Provide this authenticated owned route:

`GET /api/v1/sessions/{session_id}/report-view`

For a new structured session, it returns `ReportViewModel v1` with owned media
URLs and capability flags. For an old session, it returns an explicit legacy
fallback containing the owned report URL and no invented structured fields.
The legacy response follows `LegacyReportResponse`; it never claims
`report-view-v1` support. A new capture-only session returns its structured
capture-only variant and is not mistaken for legacy.

Requirements:

- Enforce the same session ownership checks as existing report and metrics routes.
- Mark personal report responses private and non-cacheable under the mobile
  network-only report contract.
- Allow only the schema's coaching text and supporting measurement fields in the
  authorized report response. Never place email, source filenames, raw pose
  coordinates, private metric payloads, or report content in logs, telemetry,
  analytics, notifications, or unauthorized responses.
- Keep coaching and entitlement decisions server-side.
- Return a stable unsupported-version error rather than partially parsing an
  unknown view-model version.
- Deny direct generic-file access to `report-view.json`, manifests, checksums, and
  analysis scratch data.

## 15. Compatibility and migration

- Preserve the current report format, outcome, and coaching-priority meta markers
  read from the first 8 KB of `report.html`.
- Increment only the separate HTML presentation marker for the redesigned report.
- Version `report-view.json` independently as `report-view-v1`.
- Do not rewrite or backfill historical reports; their source frames and pose data
  may no longer exist.
- Let historical sessions open through the existing owned legacy report route.
- Regenerate the public synthetic sample when the presentation marker changes.
- Keep the report self-contained: no external font, stylesheet, or script
  dependency.
- Preserve print/PDF behavior and expand optional detail in print.

## 16. Privacy and security

- Treat video, evidence images, reports, and structured report content as private
  account data.
- Authorize every report-view and media request against session ownership.
- Do not expose filesystem paths or unsigned public media locations.
- Keep personal report JSON and media network-only in the native client.
- Do not persist report content, metrics, or evidence images to analytics,
  AsyncStorage, crash reports, or notification payloads.
- Do not include raw pose coordinates in persisted report-view artifacts or API
  responses.
- Continue deleting analysis scratch frames and pose work data under the existing
  retention contract after durable deliverables are published.

## 17. Error and recovery behavior

| Condition | User-visible behavior | System behavior |
|---|---|---|
| Priority evidence is trustworthy and complete | Full guided report | Publish all core artifacts |
| Secondary category is unavailable | Show `Not measured` with a short reason | Keep supported primary coaching |
| Focused image render fails but priority metrics remain trustworthy | Show text explanation and `Visual unavailable` | Publish validated text-only view model |
| Priority evidence is not trustworthy | Re-film guidance only | Persist capture-only outcome; suppress coaching |
| Core view-model or HTML render fails | Clear analysis failure; no partial result | Publish nothing and do not consume allowance |
| Old session lacks `report-view.json` | Open legacy report | Return explicit legacy fallback |
| Owned media authorization expires or fails | Retry control and unavailable state | Never downgrade to a public URL |
| Unknown report-view version | Update-required or unsupported-result message | Fail closed; do not partially interpret fields |

## 18. Accessibility and responsive behavior

- Web output targets WCAG 2.2 AA. Normal text contrast is at least 4.5:1;
  large text, status icons, boundaries, and essential graphical objects are at
  least 3:1 against adjacent colors.
- The visual order and screen-reader order are priority, observation, cue, drill,
  and pass mark.
- Use one level-one heading and a logical nested heading structure.
- Provide descriptive alternative text for every focused evidence image and
  instructional diagram.
- Never encode status or observed/reference meaning with color alone.
- Support keyboard navigation, a visible high-contrast focus indicator at least
  two CSS pixels thick that is not obscured, minimum 44-by-44-CSS-pixel web and
  44-by-44-point native touch targets, reduced motion, and large text.
- Web content reflows at 320 CSS pixels and at 200 percent zoom without horizontal
  page scrolling, except inside explicitly labeled data-table and media regions.
- Native layouts support all iOS Dynamic Type accessibility sizes and Android
  font scale through 2.0 without clipped text, overlapping controls, or hidden
  actions.
- Web optional sections use native `details`/`summary` or buttons with
  `aria-expanded` and `aria-controls`. Native controls expose the equivalent
  expanded state. Focus stays on the disclosure control after expansion, and the
  newly revealed section follows it in reading order.
- Do not autoplay replay media.
- Do not require a horizontal swipe to access a phase; horizontal enhancement may
  exist only when every phase remains reachable through ordinary controls.
- On mobile, render a single large evidence frame rather than shrinking a
  multi-panel composite.
- Keep technical tables in accessible scroll regions on screen and fully expanded
  in print.
- Preserve the existing offline and no-JavaScript HTML requirement.

The opening-fold requirement applies only at default text size in a 390-by-844
CSS-pixel content viewport with native safe-area insets or browser content chrome
already accounted for, and without the public sample marketing banner. At large
text sizes, correct reflow, complete content, and reachable actions take priority
over keeping the cue above the fold.

## 19. Test strategy

### 19.1 Unit tests

- Build every trust state and report outcome.
- Validate report-level state precedence and every reason-code mapping.
- Map each supported priority family to the correct evidence treatment.
- Select the representative swing for threshold, mean, consistency,
  shoulder-tilt-delta, and maintenance cases with deterministic tie-breaking.
- Map face-on metrics into the five categories.
- Keep DTL timing-only and omit face-on categories and annotations.
- Hide toward/away language when target direction is uncertain.
- Require wrist/elbow quality for hand-dependent evidence.
- Select one primary drill and preserve authored three-stage instructions.
- Keep technical values optional while retaining them in the view model.
- Produce explicit legacy fallback for sessions without `report-view.json`.
- Build the capture-only variant with each supported recovery reason.

### 19.2 Contract and compatibility tests

- Validate coaching-ready improve, coaching-ready protect, limited rendered,
  limited visual-unavailable, and capture-only `report-view-v1` variants.
- Reject unknown schema versions.
- Preserve existing HTML format, outcome, and priority markers.
- Increment and detect the presentation marker independently.
- Keep self-contained HTML free of external stylesheets, fonts, and scripts.
- Ensure print expands optional evidence and measurements.
- Ensure print replaces video with an allowed poster/caption and never reveals
  locked media.
- Verify redesigned jobs omit the legacy corrected overlay and DTL jobs never
  generate a body-reference overlay.
- Verify same-volume staging, manifest validation, crash cleanup, idempotent
  retry, final database publication, and no allowance consumption on failure.
- Verify backup/restore, privacy export/deletion, history reset, retention, and
  recovery checksums cover the new artifacts.

### 19.3 Security and privacy tests

- Deny report-view and media access across accounts.
- Verify private/no-store response behavior where required by the mobile contract.
- Assert logs, telemetry, analytics, notifications, unauthorized responses, and
  unrelated API payloads exclude raw pose data, source filenames, email, report
  content, and private metric values. Assert the authorized response contains
  only the allowlisted report-view fields.
- Verify owned media URLs cannot be replaced with public fallback URLs.
- Deny generic-file access to raw report-view, manifest, checksum, and scratch
  artifacts.

### 19.4 Accessibility and visual tests

- Validate heading order, landmark order, alternative text, status text, focus,
  and touch-target size.
- Render and inspect desktop, 390-by-844 mobile, large-text mobile, reduced-motion,
  and print/PDF states.
- At default text size, verify the complete priority title, observation, and cue
  appear in the opening 390-by-844 owned-report content viewport without the
  public sample banner, using the longest supported fixture copy.
- At large text, verify reflow and reachable actions without enforcing the same
  fold position.
- Verify the full priority explanation, primary drill prescription, and pass mark
  each have one canonical main-path card; compact journey links are allowed.
- Verify no wide multi-panel evidence composite is the default mobile visual.
- Verify WCAG contrast, 200-percent web zoom, 320-CSS-pixel reflow, focus,
  disclosure state, Dynamic Type, Android 2.0 font scale, and touch targets.

### 19.5 End-to-end journeys

- Coaching-ready face-on report with each evidence family.
- Coaching-ready DTL timing report.
- Limited-evidence report with unavailable secondary categories.
- Limited report with an explicit visual-unavailable card.
- Capture-only re-film report.
- Capture-only angle mismatch, unstable tracking, insufficient pose frames, no
  readable swing, no reliable strike event, retry failure, and choose-video path.
- Clean/maintenance report.
- Free and Pro entitlement states.
- New structured native report and old legacy native fallback.
- Report generation failure with no partial output and no allowance consumed.

### 19.6 Moderated comprehension and visual review

Before broad rollout, run the rendered owned mobile report with at least five
beginner or improving golfers who did not work on the feature. Without expanding
optional detail, ask each participant to identify the priority or strength, the
coach cue, the drill, and the re-film pass mark. At least four of five must locate
all four within 30 seconds and describe the orange observed marker, green starting
marker, and dashed coaching boundary without calling them a predicted ideal pose.

Record task time, errors, and misunderstandings. Any safety-boundary
misunderstanding is a release blocker regardless of pass count. The product owner
signs a rendered-review checklist covering desktop, default mobile, longest-copy
mobile, large text, reduced motion, DTL, capture-only, clean, free, Pro, legacy,
and print states. Store screenshots and the checklist with release evidence.

## 20. Acceptance criteria

- At least four of five moderated beginner or improving golfers identify the
  priority or strength, cue, drill, and pass mark within 30 seconds without
  expanding optional detail, with no safety-boundary misunderstanding.
- At default text size, the complete priority title, observation, and cue appear
  within the opening 390-by-844 owned-report content viewport, excluding the
  public sample banner and using the longest supported fixture copy.
- The full priority explanation, primary drill prescription, and pass mark each
  have one canonical main-path card; compact journey links do not duplicate the
  full content.
- A clear coaching report displays one large priority-specific frame or timing
  visual with phase provenance and categorical tracking state. A limited report
  whose media renderer alone failed displays the explicit visual-unavailable
  replacement; unreliable priority evidence requests a re-film.
- No synthetic full-body "where it should be" overlay remains in the new report.
- Face-on reports use the five approved categories; DTL reports remain timing and
  rhythm only.
- Unsupported and uncertain measurements are unavailable or re-film guidance,
  never guesses.
- Web and mobile render the same server-owned priority, statuses, drill, and
  target from `report-view-v1`.
- Historical reports remain readable without mutation or backfill.
- Raw measurements, limitations, every swing, and print/PDF depth remain
  available.
- Privacy, compatibility, accessibility, unit, contract, and end-to-end suites
  pass.
- The product owner signs the defined rendered-review checklist and the release
  retains its screenshots and comprehension-test record.

## 21. Rollout and rollback

1. Add the view model, focused evidence artifact, and tests behind an internal
   report-presentation capability.
2. Validate the synthetic sample and owned development sessions without changing
   existing customer reports.
3. Enable the redesigned web report for a monitored cohort while preserving the
   legacy renderer as rollback.
4. Enable the native structured report after the owned API, authorization, and
   legacy fallback pass end-to-end testing.
5. Make the new presentation the default only after desktop, mobile, print,
   accessibility, DTL, capture-only, and legacy validation.

Rollback switches new-session rendering to the prior presentation. Existing new
artifacts are additive and remain readable; historical artifacts are untouched.
Report source state, GitHub merge state, deployed web state, native-app release
state, and any public sample change are reported separately.

## 22. Implementation decomposition

This is one product design but must not become one oversized implementation
patch. After written-spec approval, create one implementation index and six
ordered plans:

1. **View-model contract and presenter** — typed variants, status mapping,
   authored drill presentation, deterministic representative selection, and
   compatibility adapters.
2. **Evidence pipeline and durable bundle** — `EvidenceSnapshot`, focused
   renderer, DTL gating, atomic publication, manifests, checksums, artifact
   lifecycle, and allowance behavior.
3. **Web report presentation** — action-first Jinja structure, phase categories,
   progressive disclosure, print, sample regeneration, accessibility, and
   rendered QA.
4. **Owned API and security** — structured/legacy responses, ownership, private
   media projection, generic-file denial, logs/telemetry boundaries, and API
   contract tests.
5. **Native report integration** — typed transport, action-first components,
   private media handling, capture-only and legacy paths, accessibility, and
   native end-to-end coverage.
6. **Cohort rollout and validation** — persisted presentation selection,
   observability, moderated comprehension, visual checklist, web rollback, and
   native release reporting.

Plans 1 and 2 establish the dependency for plans 3 through 5. Web presentation
and API security may proceed in parallel after that contract stabilizes; native
integration follows the owned API. Other mobile shell, authentication, capture,
upload, and navigation work may continue in parallel, but the existing mobile
Brief/report task must be amended to consume `report-view-v1` rather than freezing
an HTML-only or client-invented coaching contract.

Each plan uses test-first checkpoints, focused commits, and an independently
reversible release boundary.

## 23. Deferred follow-ons

- Same-session "closer versus farther from start" comparison when enough readable
  swings exist.
- User review or adjustment of detected impact and top markers.
- Interactive replay layers that can toggle skeleton, trail, annotation, and
  phase labels.
- A separately versioned coach-share Proof of Change export.
- Broader on-course transfer evidence beyond the existing Proof Cycle contract.
