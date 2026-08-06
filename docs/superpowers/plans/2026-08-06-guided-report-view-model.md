# Guided Report View Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the versioned `report-view-v1` contract and a pure server-owned presenter that turns the existing scoped metrics, Caddie Brief, issue order, drill choice, evidence result, and report context into one deterministic coaching or capture-only document for web and native clients.

**Architecture:** `swinglab.coaching`, `swinglab.caddie_brief`, and `swinglab.drills` remain the coaching source of truth. New frozen types and strict JSON adapters live in `swinglab/report_view.py`; pure mapping, deterministic representative selection, category/status derivation, authored practice presentation, and server-only optional-depth content live in `swinglab/report_presenter.py`. Persisted/API JSON contains only the approved `ReportViewV1`; static HTML receives a `ReportDocument` that pairs that view with typed server-only depth, so Jinja does not reconstruct coaching decisions and the persisted schema does not gain unapproved required fields.

**Tech Stack:** Python 3.11 frozen dataclasses and `StrEnum`, existing SwingLab metric/coaching/drill modules, canonical JSON, pytest.

## Global Constraints

- Keep the existing Python pipeline authoritative for pose, events, metrics, quality, Caddie Brief priority, issue severity, drill selection, and re-film targets.
- Preserve the persisted outcome vocabulary: `coaching_ready` and `capture_only`. `clear`, `limited`, and `refilm_required` are nested trust states, not a third outcome.
- Use `REPORT_VIEW_VERSION = "report-view-v1"` and `GUIDED_REPORT_PRESENTATION_VERSION = "guided-report-v1"`. Keep `swinglab.report.REPORT_FORMAT_VERSION = "caddie-brief-v1"` and `REPORT_PRESENTATION_VERSION = "premium-coach-v2"` as legacy compatibility markers.
- Every persisted contract field is required unless its annotation includes `None`; arrays serialize as present, server-ordered arrays even when empty.
- Reject an unknown view-model version. Ignore unknown fields only after a known-version payload has passed all required-field, enum, union, range, and cross-field checks.
- Never persist raw pose coordinates, source filenames, absolute paths, or environment values in `report-view.json`.
- Face-on coaching views contain exactly five ordered phases: setup, going back, transition and downswing, impact, finish. DTL contains exactly one `timing_rhythm` phase and no body-reference evidence.
- The selected priority/strength, phase status, drill, and target are server decisions. Web and mobile render them; they do not re-rank or infer them.
- Preserve one canonical full practice prescription and one canonical full pass-mark sentence. `RefilmTarget.text` is the only main-path full pass mark.
- Preserve authored safety-critical drill steps. The presenter must fail if the selected drill lacks an authored three-stage presentation; it must never slice or auto-merge `Drill.protocol`.
- Preserve `write_report_html(...)` at `swinglab/report.py:146-331` as a legacy/synthetic compatibility adapter and keep historical files readable without mutation or backfill.
- This plan does not change the public sample, the action-first final Jinja design, owned API routes, mobile UI, or cohort selection. Those consume the interfaces established here.

---

## Task 1: Freeze the typed `report-view-v1` union and reusable contract fixtures

**Files:**

- Create: `swinglab/report_view.py`
- Create: `tests/report_view_fixtures.py`
- Create: `tests/fixtures/report_view/coaching-improve-clear.json`
- Create: `tests/fixtures/report_view/coaching-protect-clear.json`
- Create: `tests/fixtures/report_view/coaching-limited-rendered.json`
- Create: `tests/fixtures/report_view/coaching-limited-visual-unavailable.json`
- Create: `tests/fixtures/report_view/capture-only.json`
- Create: `tests/test_report_view_contract.py`

**Interfaces:**

- Produces constants `REPORT_VIEW_VERSION = "report-view-v1"`,
  `GUIDED_REPORT_PRESENTATION_VERSION = "guided-report-v1"`, and
  `MAX_REPORT_VIEW_BYTES = 2 * 1024 * 1024`.
- Produces `StrEnum` classes matching the approved strings exactly:
  `ReportOutcome`, `JourneyMode`, `TrustState`, `TrackingState`, `Angle`,
  `Hand`, `PhaseId`, `PhaseStatus`, `ReasonCode`, `EvidenceKind`,
  `PhaseMethod`, `EventId`, `MeasurementUnit`, `BenchmarkRelation`,
  `TargetComparator`, `TargetWindow`, `OptionalSectionId`, `MediaRole`, and
  `Entitlement`.
- Produces frozen contract dataclasses: `Trust`, `ReportContext`, `NextMove`,
  `EventProvenance`, `MeasurementDetail`, `RenderedEvidence`,
  `UnavailableEvidence`, `PhaseSummary`, `DrillAlternative`,
  `PracticePrescription`, `RefilmTarget`, `RefilmProtocol`,
  `CaptureGuidance`, `OptionalSection`, `Capabilities`, `MediaEntry`,
  `CoachingReportView`, and `CaptureOnlyReportView`.
- Produces aliases `EvidenceView = RenderedEvidence | UnavailableEvidence` and
  `ReportViewV1 = CoachingReportView | CaptureOnlyReportView`.
- Produces `report_view_to_dict(view: ReportViewV1) -> dict[str, object]`,
  `report_view_from_dict(payload: object) -> ReportViewV1`,
  `write_report_view(path: Path, view: ReportViewV1) -> Path`, and
  `load_report_view(path: Path) -> ReportViewV1`.
- `write_report_view` emits UTF-8 canonical JSON using sorted keys, compact
  separators, `ensure_ascii=False`, one trailing newline, and finite numbers.
- `report_view_from_dict` raises `UnsupportedReportViewVersion` only for an
  unknown/missing version and `ReportViewValidationError` for every malformed
  known-version payload.
- Produces shared test helpers
  `report_view_payload(name: str = "coaching-improve-clear") -> dict[str, object]`
  and, after Task 6 defines `ReportDocument`,
  `report_document_fixture(name: str = "coaching-improve-clear") -> ReportDocument`.

- [ ] Write the five golden JSON fixtures from the approved contract. Use the
  same context, priority, drill, target, and media key in all coaching variants
  so diffs isolate the intended mode/trust/evidence changes. The limited
  rendered fixture carries a secondary reason; the visual-unavailable fixture
  carries only `focused_media_render_failed`; the capture-only fixture has
  `next_move`, `visual_evidence`, `practice`, and `refilm` set to null and an
  empty `phases` array.

- [ ] Add failing round-trip and unknown-version tests:

  ```python
  @pytest.mark.parametrize(
      "name",
      (
          "coaching-improve-clear",
          "coaching-protect-clear",
          "coaching-limited-rendered",
          "coaching-limited-visual-unavailable",
          "capture-only",
      ),
  )
  def test_report_view_v1_fixtures_round_trip(name):
      payload = report_view_payload(name)
      view = report_view_from_dict(payload)
      assert report_view_to_dict(view) == payload


  def test_unknown_report_view_version_fails_closed():
      payload = report_view_payload()
      payload["version"] = "report-view-v2"
      with pytest.raises(UnsupportedReportViewVersion):
          report_view_from_dict(payload)
  ```

- [ ] Add table-driven failing validation tests for every enum, non-finite
  number, negative swing/count/timestamp, empty authored stage, duplicate media
  key, duplicate reason, duplicate phase, unsafe media `relative_path`, missing
  media reference, and each union inconsistency. Assert in particular that a
  rendered evidence object has a media key and no render reasons, unavailable
  evidence has no media key and includes `focused_media_render_failed`, clear
  coaching has rendered evidence, limited coaching never carries a fatal
  reason, and capture-only has no coaching content.

- [ ] Run `python -m pytest tests/test_report_view_contract.py -q`; expect an
  import failure because `swinglab.report_view` does not exist.

- [ ] Implement the enums and frozen dataclasses. Define the top-level union
  with literal-valued fields, not a bag of dictionaries:

  ```python
  class ReportOutcome(StrEnum):
      COACHING_READY = "coaching_ready"
      CAPTURE_ONLY = "capture_only"


  @dataclass(frozen=True)
  class CoachingReportView:
      version: Literal["report-view-v1"]
      mode: Literal["structured"]
      presentation_version: str
      outcome: Literal[ReportOutcome.COACHING_READY]
      journey_mode: Literal[JourneyMode.IMPROVE, JourneyMode.PROTECT]
      trust: Trust
      context: ReportContext
      capabilities: Capabilities
      media: tuple[MediaEntry, ...]
      optional_sections: tuple[OptionalSection, ...]
      next_move: NextMove
      visual_evidence: EvidenceView
      phases: tuple[PhaseSummary, ...]
      practice: PracticePrescription
      refilm: RefilmProtocol
      capture_guidance: None = None
  ```

- [ ] Implement one explicit parser per nested dataclass. Require the exact
  approved fields, copy known fields only, and validate cross-field invariants
  once after construction. Do not use `dataclass(**payload)` and do not let a
  Python `KeyError`, `TypeError`, or `ValueError` escape the public adapter.

- [ ] Implement path validation with `PurePosixPath`: reject empty, absolute,
  backslash-containing, drive-qualified, dot, dot-dot, or non-canonical paths.
  The parser may accept unknown sibling fields for a known version, but the
  serialized dataclass must contain only allowlisted fields.

- [ ] Implement canonical writing and the 2-MiB bounded loader. Read at most
  `MAX_REPORT_VIEW_BYTES + 1`; reject a larger file before JSON decoding.

- [ ] Rerun `python -m pytest tests/test_report_view_contract.py -q`; expect all
  contract and fixture cases to pass.

- [ ] Commit: `git add swinglab/report_view.py tests/report_view_fixtures.py tests/fixtures/report_view tests/test_report_view_contract.py && git commit -m "feat: freeze guided report view contract"`.

## Task 2: Expose the selected strength and author the primary drill presentation

**Files:**

- Modify: `swinglab/coaching.py:186-285,500-735`
- Modify: `swinglab/caddie_brief.py:46-211,376-414,454-478`
- Modify: `swinglab/drills.py:57-69,69-486,507-528`
- Modify: `tests/test_praise_notes.py`
- Modify: `tests/test_caddie_brief.py`
- Modify: `tests/test_drills.py:90-149`
- Create: `tests/test_drill_presentation.py`

**Interfaces:**

- Produces `StrengthCard(key: str, metric: str, display_name: str, text: str)`
  and `strength_cards(all_metrics: list[SwingMetrics], cfg: Config,
  stats: dict[str, dict[str, float]] | None = None) -> list[StrengthCard]`.
- Preserves `praise_notes(...) -> list[str]` as
  `[strength.text for strength in strength_cards(...)]` with the current order
  and byte-for-byte text.
- Adds required `strength_key: str | None` to `CaddieBrief`; all three internal
  constructors set it. Improvement and re-film briefs use null. A protect brief
  uses the key of the exact first selected `StrengthCard`.
- Renames only the two colliding in-memory maintenance drill IDs:
  `rhythm-baseline-refilm` and `readability-baseline-refilm`. Historical HTML
  and metrics files are not rewritten.
- Produces frozen `DrillPresentation(summary_steps: tuple[str, str, str],
  setup: str, feel_cue: str, equipment: str | None)` and
  `build_drill_presentations(coach: Mapping[str, object]) -> dict[str, DrillPresentation]`.
- Produces `drill_presentation(drill: Drill, cfg: Config) -> DrillPresentation`;
  it raises `MissingDrillPresentation` for an un-authored selected drill.

- [ ] Add failing tests proving `strength_cards` preserves all existing praise
  text/order and `CaddieBrief.strength_key` identifies the exact strength whose
  text is in `brief.strength`. Add clean DTL and partial-baseline cases so a
  rhythm/readability maintenance key is never inferred from prose.

- [ ] Add a failing catalog test that collects every drill that can be primary:
  the first drill for each `practice_plan` family plus the two maintenance
  drills from `caddie_brief.py`. Assert every ID is unique and has exactly three
  nonempty authored stages, nonempty setup/feel, and no stage is produced by
  `drill.protocol[:3]` when the original protocol has four steps.

- [ ] Run `python -m pytest tests/test_praise_notes.py tests/test_caddie_brief.py tests/test_drills.py tests/test_drill_presentation.py -q`; expect failures for the missing typed strength and presentation registry.

- [ ] Introduce `StrengthCard` without changing the current threshold or order
  logic. Build `praise_notes` from the new typed function and update
  `build_caddie_brief` to carry the selected key rather than matching prose.

- [ ] Add the authored registry with these exact selected-drill stages and
  support fields:

  | Drill ID | Stage 1 | Stage 2 | Stage 3 | Setup | Feel cue | Equipment |
  |---|---|---|---|---|---|---|
  | `tempo-three-beat-count` | Set a steady beat and begin with half-speed swings. | Take the club away on one and arrive at the top on three. | Start down on the next beat and add speed only while the count holds. | Ball teed low with room for three-quarter swings. | Let the backswing finish before anything starts down. | Swing metronome or spoken count |
  | `sway-stick-outside-trail-foot` | Place a leaning stick safely outside the trail foot, clear of the club path. | Rehearse slow turns that stay clear of the stick while pressure loads inside the trail foot. | Hit at 80 percent effort and stop if the body or club can contact the stick. | Turf station with the stick outside and behind the swing arc. | Turn into the trail hip instead of drifting over it. | Alignment stick |
  | `hip-slide-banded-turn` | Loop the band above the knees and begin with light outward tension. | Turn to the top while both sides keep even tension. | Hold for one beat, feel the trail glute loaded, then swing through. | Stable shoes and a band that does not restrict circulation. | The trail pocket turns back; it does not slide sideways. | Hip resistance band |
  | `dip-chair-drill` | Set the chair for light glute contact at address. | Rehearse to the top and a held impact while keeping the contact light. | Move the chair one hand-width back and hit at 80 percent with the same height. | Chair behind the hips and outside the club path. | Keep the chest tall through the ball. | Chair or range basket |
  | `arm-towel-under-lead` | Trap a folded towel lightly under the lead upper arm. | Make half swings and keep the towel through impact. | Build to three-quarter swings only while the towel stays until follow-through. | Half-swing station with a soft towel and clear club path. | Keep the lead arm connected and long through the strike. | Golf towel or headcover |
  | `shoulder-impact-freeze` | Note the shoulder line at address in a face-on mirror or phone. | Swing at half speed and freeze at impact for three seconds. | Confirm the trail shoulder stayed lower, then blend five freezes into one ball. | Face-on mirror or phone at hip height. | Let the trail shoulder work down through impact. | Mirror or phone tripod |
  | `balance-feet-together` | Tee the ball and begin with the feet touching. | Make smooth three-quarter swings and hold each finish for three counts. | Widen the stance gradually while preserving the same quiet finish. | Level ground, teed ball, and reduced swing speed. | Finish stacked and still enough to hold the pose. | Tee |
  | `consistency-one-count` | Choose one count from the most repeatable swing. | Hit ten wedges and ten mid-irons without changing that count. | Step out and rehearse once whenever a swing feels rushed. | Two clubs, one target, and one fixed beat. | Make every club run on the same clock. | Metronome optional |
  | `clean-baseline-refilm` | Recreate the same club, hand, camera angle, height, and framing. | Make three swings with the same count and similar effort. | Save the report as the next matched maintenance checkpoint. | The same capture station used for the baseline. | Protect the selected steady measurement, not a perfect-looking pose. | Phone support |
  | `rhythm-baseline-refilm` | Recreate the same club and DTL camera setup. | Make three swings with one count and similar effort. | Re-film face-on next time when body-movement coaching is wanted. | DTL phone at hip height with the full club motion visible. | Repeat the rhythm this angle can measure honestly. | Phone support or tripod |
  | `readability-baseline-refilm` | Set the phone face-on at hip height with the full body visible. | Use bright even light and keep other people out of frame. | Make three swings with the same club and camera position. | Stable face-on phone support and uncluttered background. | Make the motion readable before judging it. | Phone support or tripod |

- [ ] Keep `Drill.protocol` and `Drill.success_metric` unchanged for legacy
  reports. Add the presentation registry beside `build_drills`, parameterized
  only where threshold text is displayed; do not derive stages from protocols.

- [ ] Rerun the four focused test files; expect all existing text tests and new
  authored-stage tests to pass.

- [ ] Commit: `git add swinglab/coaching.py swinglab/caddie_brief.py swinglab/drills.py tests/test_praise_notes.py tests/test_caddie_brief.py tests/test_drills.py tests/test_drill_presentation.py && git commit -m "feat: author guided drill presentation"`.

## Task 3: Define priority evidence rules and deterministic representative selection

**Files:**

- Create: `swinglab/report_presenter.py`
- Create: `tests/test_report_representative_selection.py`
- Modify: `tests/test_caddie_brief.py`

**Interfaces:**

- Produces `SelectionBasis = "threshold" | "session_mean" |
  "consistency_median" | "shoulder_tilt_delta_mean" | "maintenance_median"`.
- Produces frozen `PriorityEvidenceRule(priority_key: str, metric_id: str,
  kind: EvidenceKind, phase: PhaseId, event: EventId | None,
  selection_basis: SelectionBasis, benchmark: float | None,
  worse_direction: Literal["higher", "lower"] | None)`.
- Produces frozen `EvidenceCandidate(swing: int, metric_value: float,
  eligible: bool, crossed_line: bool | None)`.
- Produces `priority_evidence_rule(brief: CaddieBrief, issues: Sequence[IssueCard],
  *, angle: str, cfg: Config) -> PriorityEvidenceRule`.
- Produces `select_representative_swing(candidates: Sequence[EvidenceCandidate],
  *, basis: SelectionBasis, session_value: float | None = None) -> int | None`.
- The selector never receives pixels or landmarks and never changes the selected
  `CaddieBrief`. Workstream 2 supplies `eligible` from annotation gates.

- [ ] Add failing mapping tests for every selected family:

  | Priority/strength key | Metric | Evidence kind | Face-on phase/event |
  |---|---|---|---|
  | `sway` | `head_sway_backswing_sw` | `head_boundary` | `going_back` / `top` |
  | `hip-slide` | `hip_slide_backswing_sw` | `hip_boundary` | `going_back` / `top` |
  | `head-dip` | `head_dip_sw` | `head_height` | `impact` / `impact` |
  | `tempo` | `tempo_ratio` | `tempo_timeline` | `transition_downswing` / null |
  | `consistency` | `tempo_ratio` | `tempo_timeline` | `transition_downswing` / null |
  | `arm-extension` | `lead_arm_angle_deg` | `lead_arm_angle` | `impact` / `impact` |
  | `shoulder-tilt` with impact card | `shoulder_tilt_impact_deg` | `shoulder_tilt` | `impact` / `impact` |
  | `shoulder-tilt` with delta card | `shoulder_tilt_delta_deg` | `shoulder_tilt` | `impact` / `impact` |
  | `balance` | `finish_balance_sw` | `finish_stability` | `finish` / `finish` |
  | protect strength | selected strength metric | `steady_reference` except tempo, which uses `tempo_timeline` | selected strength phase |

  Assert any DTL improve/protect rule becomes `tempo_timeline` in
  `timing_rhythm`; a stale face-on key in DTL raises
  `UnsupportedPriorityEvidence` instead of producing body annotation.

- [ ] Add failing pure-selection tests for threshold crossings, session mean,
  consistency median, shoulder-tilt-delta mean, and maintenance median. Include
  ineligible extremes, negative values, two exact ties, and one no-eligible case.
  The expected tie winner is always the lowest swing number.

- [ ] Run `python -m pytest tests/test_report_representative_selection.py tests/test_caddie_brief.py -q`; expect import failures for the new presenter.

- [ ] Implement the rule map from the exact table. For shoulder tilt, consume
  `IssueCard.metric` from `coaching.py:629-644`; do not choose impact-versus-delta
  again. For protect mode, consume `CaddieBrief.strength_key` from Task 2.

- [ ] Implement selection with finite eligible values only:

  ```python
  def _closest(rows: Sequence[EvidenceCandidate], target: float) -> int:
      return min(rows, key=lambda row: (abs(row.metric_value - target), row.swing)).swing


  def _median(values: Sequence[float]) -> float:
      return float(statistics.median(values))
  ```

  Threshold mode filters to eligible crossings when at least one exists and
  selects closest to their median. Session-mean and shoulder-delta modes select
  closest to the supplied mean. Consistency and maintenance select closest to
  the eligible median. Return null only when no candidate is eligible.

- [ ] Rerun the focused tests; expect every family and deterministic branch to
  pass without changing Caddie Brief priority tests.

- [ ] Commit: `git add swinglab/report_presenter.py tests/test_report_representative_selection.py tests/test_caddie_brief.py && git commit -m "feat: select representative report evidence"`.

## Task 4: Build trust, capture recovery, next move, and structured re-film targets

**Files:**

- Modify: `swinglab/report_presenter.py`
- Create: `tests/test_report_presenter_states.py`
- Create: `tests/test_report_refilm_targets.py`
- Modify: `tests/test_caddie_brief.py`

**Interfaces:**

- Produces frozen `ReasonCopy(label: str, explanation: str,
  remediation: str)` and complete `REASON_COPY: Mapping[ReasonCode, ReasonCopy]`.
- Produces frozen `ReportContextInput(club: str | None, hand: str, angle: str,
  detected_swings: int, analysis_fps: float | None)`.
- Produces frozen `ReportSwingSource` with `metrics`, `notes`, and the exact
  nullable media-key/caption/alt fields listed in Task 6.
- Produces frozen `ReportPresentationInput(context, swings, stats,
  session_notes, brief, issues, strengths, primary_drill,
  alternative_drills, visual_evidence, media, reason_codes, safe_media_keys,
  replay_locked, navigation)`.
- Produces `build_refilm_target(brief: CaddieBrief, issues: Sequence[IssueCard],
  strengths: Sequence[StrengthCard], cfg: Config) -> RefilmTarget`.
- Produces `build_report_view(source: ReportPresentationInput,
  cfg: Config) -> ReportViewV1`.

- [ ] Add failing tests for precedence: any fatal capture reason or an
  unreliable/missing selected-priority metric/event produces capture-only;
  otherwise a secondary limitation or visual render failure produces limited;
  otherwise the result is clear. Verify reason de-duplication follows this
  server order: camera, tracking, frame/event, priority, then secondary/render.

- [ ] Add one failing capture-only test per primary reason:
  `camera_angle_mismatch`, `tracking_unstable`, `insufficient_pose_frames`,
  `no_readable_swing`, `no_reliable_strike_event`, and
  `priority_evidence_unreliable`. Assert unique user label/correction strings,
  a nonempty checklist, allowlisted `safe_media_keys`, and no diagnosis, phases,
  drills, target, corrective annotation, replay, Proof target, or gear.

- [ ] Add failing improve/protect next-move tests. Improvement consumes
  `brief.focus_flag`, the selected `IssueCard`, and server copy. Protect consumes
  `brief.strength_key` and uses eyebrow `Protect this`; it keeps the containing
  phase status steady and adds a strength sublabel later in Task 5. Neither
  opening object includes `Drill.success_metric`.

- [ ] Add failing target tests for all issue families and protect mode. Use these
  structured mappings without parsing `Drill.success_metric`:

  | Priority | Metric/comparator | Threshold/window | Attempts |
  |---|---|---|---|
  | tempo | `tempo_ratio`, `count_gte` | `coaching.tempo_warn_below`, `session` | 4 of 5 |
  | consistency | `tempo_ratio_std`, `lte` | `coaching.tempo_std_praise`, `session` | null |
  | sway | `head_sway_backswing_sw`, `all_lte` | `coaching.sway_warn_sw`, `session` | null |
  | hip slide | `hip_slide_backswing_sw`, `all_lte` | `coaching.sway_warn_sw`, `session` | null |
  | head dip | `head_dip_sw`, `all_lte` | `coaching.head_dip_warn_sw`, `session` | null |
  | lead arm | `lead_arm_angle_deg`, `count_gte` | `coaching.lead_arm_warn_deg`, `session` | 4 of 5 |
  | shoulder tilt impact | `shoulder_tilt_impact_deg`, `all_gte` | `coaching.shoulder_tilt_impact_min_deg`, `session` | null |
  | shoulder tilt delta | `shoulder_tilt_delta_deg`, `all_gte` | 0 degrees, `session` | null |
  | finish | `finish_balance_sw`, `all_lte` | `coaching.finish_balance_warn_sw`, `session` | null |

  Protect mode uses the selected targetable strength metric and its established
  configured coaching line. `strength_cards` may select a protect strength only
  when `build_refilm_target` has an explicit mapping for that metric. A
  context-only setup or hand-movement baseline is never given a manufactured
  tolerance; if no targetable strength exists, presenter/catalog validation
  fails instead of inventing a maintenance target.

- [ ] Run `python -m pytest tests/test_report_presenter_states.py tests/test_report_refilm_targets.py tests/test_caddie_brief.py -q`; expect failures for missing presenter builders.

- [ ] Implement all eleven reason-code labels/remediations in one immutable map.
  Keep code names out of user copy. Fatal reasons are exactly the six tested
  capture reasons; `secondary_metric_unavailable`,
  `target_direction_uncertain`, `hand_landmarks_unreliable`,
  `event_estimate_limited`, and `focused_media_render_failed` are limited only
  after priority trust passes.

- [ ] Implement state construction as an exhaustive branch on the typed union.
  Build capture-only first and return; a coaching builder must never temporarily
  construct null practice/evidence and fill it later.

- [ ] Implement `build_refilm_target` from the table and format its text from the
  same numeric threshold fields. Assert the text's number equals the structured
  threshold in tests after config retuning. Add a regression test that supplies
  only a context-only `StrengthCard` and expects `UnsupportedRefilmTarget`, with
  no synthesized `between` target.

- [ ] Rerun the three focused files; expect all states and target mappings to
  pass.

- [ ] Commit: `git add swinglab/report_presenter.py tests/test_report_presenter_states.py tests/test_report_refilm_targets.py tests/test_caddie_brief.py && git commit -m "feat: present guided report states"`.

## Task 5: Map supported metrics into phase summaries and status precedence

**Files:**

- Modify: `swinglab/report_presenter.py`
- Create: `tests/test_report_presenter_phases.py`
- Modify: `tests/test_report_insights.py`
- Modify: `tests/test_camera_angle.py`

**Interfaces:**

- Produces `build_phase_summaries(source: ReportPresentationInput,
  cfg: Config) -> tuple[PhaseSummary, ...]`.
- Produces `measurement_detail(metric_id: str, metrics: Sequence[SwingMetrics],
  stats: Mapping[str, Mapping[str, float]], cfg: Config) -> MeasurementDetail | None`.
- Produces stable detail IDs `measurement-<metric_id>` and phase detail IDs
  `phase-<phase_id>`.
- Face-on metric ownership is fixed:
  setup = stance baseline; going back = backswing duration, head sway, hip
  slide; transition/downswing = tempo, downswing duration, hand-movement
  baseline; impact = estimated event, head dip, lead-arm shape, shoulder tilt;
  finish = finish-base stability. DTL owns only backswing duration, downswing
  duration, tempo ratio, consistency, and event provenance already supplied by
  its timing evidence.
- User-facing phase labels are fixed to `Setup`, `Going back`,
  `Transition & downswing`, `Impact`, and `Finish`; the sole DTL label is
  `Timing & rhythm`. Clients render these server-owned labels unchanged.

- [ ] Add failing face-on tests that assert exactly five rows and the exact
  plain-language labels in canonical order, every supported measurement appears
  once, context-only values are baseline rather than steady, unavailable values
  remain explicit, and the selected priority row alone is expanded in improve
  mode.

- [ ] Add failing status-precedence tests: priority outranks a secondary issue;
  review later outranks steady; scored supported values with no issue are
  steady; context-only supported values are baseline; an empty category is not
  measured. One unavailable secondary value inside a supported category does
  not change that category's status.

- [ ] Add failing protect tests asserting the selected phase is expanded, keeps
  ordinary `steady` status, and its summary contains the server-owned
  `Strength to protect` sublabel. Do not emit a `priority` status for protect.

- [ ] Add failing DTL tests asserting exactly one `timing_rhythm` row, no
  face-on metric IDs or body phases, and no stale face-on card from a supplied
  metric object. Target-direction uncertainty removes toward/away wording from
  measurement explanation and adds `target_direction_uncertain` without making
  tempo unmeasured.

- [ ] Run `python -m pytest tests/test_report_presenter_phases.py tests/test_report_insights.py tests/test_camera_angle.py -q`; expect failures for the new phase builder.

- [ ] Implement declarative metric metadata in `report_presenter.py`: label,
  phase, unit, benchmark relation/value accessor, plain formatter, explanation,
  and limitation. Reuse finite values and configured coaching lines; do not call
  pose/events or create a score.

- [ ] Implement status precedence from `source.brief`, `source.issues`, and
  measurement semantics. Do not parse `IssueCard.display_name`, status prose,
  or notes to decide status.

- [ ] Preserve `build_swing_breakdown` in `swinglab/report_insights.py:391-413`
  for the legacy renderer. Add parity assertions for shared factual values but
  do not force its old six-card taxonomy into the new five-phase contract.

- [ ] Rerun the focused tests; expect all phase, status, protect, uncertainty,
  and DTL cases to pass.

- [ ] Commit: `git add swinglab/report_presenter.py tests/test_report_presenter_phases.py tests/test_report_insights.py tests/test_camera_angle.py && git commit -m "feat: map guided report phases"`.

## Task 6: Build the server-only report document and preserve the legacy adapter

**Files:**

- Modify: `swinglab/report_presenter.py`
- Modify: `swinglab/report.py:34-38,146-331`
- Create: `tests/test_report_document.py`
- Modify: `tests/test_report.py:62-403`
- Modify: `tests/test_premium_report.py:16-136`
- Modify: `tests/report_view_fixtures.py`

**Interfaces:**

- Produces frozen `ReportNavigation(app_url: str | None,
  storefront_url: str | None, gear_collection_url: str | None)`.
- Produces frozen `LabelValue(key: str, label: str, value: str)`,
  `GlossaryEntry(term: str, definition: str)`, and
  `GearDetail(key: str, label: str, description: str, url: str)`.
- Produces frozen `FindingDetail(key: str, title: str, summary: str, why: str,
  cue: str, measurement_detail_ids: tuple[str, ...], detail_section_id: str)`.
- Produces frozen `StrengthDetail(key: str, title: str, summary: str,
  measurement_detail_ids: tuple[str, ...])`.
- Produces frozen `SwingDetail` with these exact fields:
  `swing`, `summary`, `notes`, `measurements`,
  `key_positions_media_key`, `key_positions_alt_text`,
  `slow_motion_media_key`, `slow_motion_caption`,
  `coach_replay_media_key`, `coach_replay_caption`, `replay_locked`,
  `locked_replay_explanation`, `video_poster_media_key`,
  `video_poster_alt_text`, and `print_playback_reference`.
- Produces frozen `ReportDepthContent(swings, secondary_findings, strengths,
  measurements, session_details, glossary, limitations, gear, navigation)`.
- Produces frozen `ReportDocument(view: ReportViewV1,
  depth: ReportDepthContent, media_by_key: Mapping[str, MediaEntry])`.
- Produces
  `prepare_report_input(video: VideoInfo, swings: Sequence[dict[str, object]],
  stats: Mapping[str, Mapping[str, float]], session_notes: Sequence[str],
  hand: str, cfg: Config, *, angle: str = ANGLE_FACE_ON,
  club: str | None = None, level: str | None = None,
  analysis_fps: float | None = None, replay_locked: bool = False,
  visual_evidence: EvidenceView | None = None,
  media: Sequence[MediaEntry] = (), reason_codes: Sequence[ReasonCode] = (),
  safe_media_keys: Sequence[str] = (),
  navigation: ReportNavigation | None = None) -> ReportPresentationInput`.
- Produces `build_report_document(source: ReportPresentationInput,
  cfg: Config) -> ReportDocument`. The web-presentation plan exclusively owns
  `write_report_document_html(...)` and the guided Jinja template; this plan
  provides their complete typed input and does not define a competing renderer.
- Keeps the full existing `write_report_html(...)` signature. Its output retains
  the existing format, presentation, outcome, and priority-rule markers.

- [ ] Add failing document tests using `report_document_fixture()`. Assert every
  optional-section state points to typed depth with the same count, every depth
  media key exists in `media_by_key`, every video has a distinct poster key or
  an explicit null, and print playback references/captions are server-owned.

- [ ] Add a failing test that passes the existing `fake_video`, `fake_swing`,
  stats, and notes through `prepare_report_input`; assert its brief focus,
  ordered issues, selected drill, alternatives, strength, and target match the
  current direct builders. This is the compatibility constructor used by the
  sample and bundle; neither duplicates report assembly.

- [ ] Add failing document-boundary tests asserting the priority title, full
  primary prescription, full pass-mark text, renderer-owned navigation, and
  every explicit media/poster reference are present once in the typed document.
  Assert all `MediaEntry.relative_path` values remain relative. HTML concerns
  stay in the web-presentation plan.

- [ ] Add regression tests around `write_report_html` for its current signature,
  self-contained output, `caddie-brief-v1`, `premium-coach-v2`, outcome marker,
  priority-rule marker, capture-only suppression, DTL scope, and replay lock.

- [ ] Run `python -m pytest tests/test_report_document.py tests/test_report.py tests/test_premium_report.py -q`; expect failures only for new document interfaces.

- [ ] Move the pure assembly currently in `report.py:175-275` into
  `prepare_report_input`. Scope DTL metrics before building stats/brief/issues;
  build the Caddie Brief once; preserve rule-1/rule-2 behavior; pass typed
  results to `build_report_document`. `write_report_html` may adapt those typed
  values back into the current legacy template until the web-presentation plan
  replaces the template.

- [ ] Build `ReportDepthContent` entirely on the server. Associate media by
  explicit key from the supplied `MediaEntry` list; never synthesize a filename
  from role or swing number in Jinja. A locked replay has a null replay key and
  nonempty lock explanation. A missing poster stays null and prints the owned
  playback reference/caption without revealing another media item.

- [ ] Stop at the typed renderer boundary: expose `ReportDocument` and
  `build_report_document`, with no Jinja import, template, HTML writer, sample
  banner argument, or environment read in this workstream. The web-presentation
  plan consumes this boundary and owns all rendering behavior.

- [ ] Rerun the focused files; expect all new document tests and existing report
  compatibility tests to pass.

- [ ] Commit: `git add swinglab/report_presenter.py swinglab/report.py tests/test_report_document.py tests/test_report.py tests/test_premium_report.py tests/report_view_fixtures.py && git commit -m "refactor: add guided report document presenter"`.

## Task 7: Run the view-model compatibility gate

**Files:**

- Modify only files already listed if a compatibility failure is found.

**Interfaces:**

- Produces a stable contract for the evidence-bundle, web, owned API, and native
  plans. Those plans consume these names; they do not fork the schema.

- [ ] Run the focused gate:
  `python -m pytest tests/test_report_view_contract.py tests/test_drill_presentation.py tests/test_report_representative_selection.py tests/test_report_presenter_states.py tests/test_report_refilm_targets.py tests/test_report_presenter_phases.py tests/test_report_document.py tests/test_report.py tests/test_premium_report.py tests/test_caddie_brief.py tests/test_drills.py tests/test_report_insights.py tests/test_camera_angle.py -q`.
  Expected result: all pass.

- [ ] Run `python -m pytest -q`. Expected result: the full existing Python suite
  plus new view-model tests pass.

- [ ] Serialize each golden fixture twice through `report_view_from_dict` and
  `report_view_to_dict`; compare bytes. Expected result: deterministic identical
  canonical JSON and no raw coordinate/path/source-name field.

- [ ] Search the diff with
  `rg -n "where it should be|personalized ideal|raw_landmark|source_name" swinglab/report_view.py swinglab/report_presenter.py`.
  Expected result: no ideal-pose claim, raw-landmark field, or source filename in
  the new contract/document.

- [ ] Review `git diff --check` and `git diff --stat`. Expected result: no
  whitespace errors and no edits to mobile plans, approved specs, public sample,
  API routes, or production rollout configuration.

- [ ] Commit any focused compatibility correction with
  `git commit -m "test: verify guided report view model"`.

## View-model plan completion gate

- [ ] Confirm every `report-view-v1` field from specification sections 12.1
  through 12.8 is represented and validated, and the server-only depth does not
  alter persisted/API JSON.
- [ ] Confirm all eleven reason codes have one server-owned label and remediation
  and that fatal/limited precedence is covered by tests.
- [ ] Confirm every evidence family, representative selection mode, face-on
  phase, DTL timing row, primary drill, and re-film target has a direct unit test.
- [ ] Confirm `write_report_html` remains callable by current synthetic/test
  callers and retains legacy markers.
- [ ] Record the implementation commits and test output. Do not call the guided
  report published, deployed, live, sample-refreshed, or mobile-released; those
  are separate plans and release states.
