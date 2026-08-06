# Guided Report Evidence Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture trustworthy per-swing evidence while landmarks exist, render one priority-matched visual or explicit unavailable variant, and publish the guided HTML/view/media bundle atomically with durable checksums, immutable job policy, safe recovery, and complete artifact lifecycle coverage.

**Architecture:** The analysis loop keeps its current metric/coaching behavior but adds in-memory `EvidenceSnapshot` objects before work frames are removed. `swinglab/focused_evidence.py` consumes the already-selected presenter rule and snapshots; it never selects a new priority. `swinglab/report_bundle.py` builds a complete bundle inside a unique same-volume staging directory using an injected HTML-writer contract, validates it through `swinglab/report_artifacts.py`, atomically renames that directory to an opaque final bundle root, and returns paths that `JobManager` exposes only after one final SQLite transaction. Legacy jobs retain the current overlay/report path and are the default until the persisted presentation gate selects `guided-report-v1`; the web-presentation plan exclusively supplies and composes the real guided writer.

**Tech Stack:** Python 3.11 dataclasses and `StrEnum`, NumPy, Pillow, existing pose/events/metrics/report modules, SQLite WAL, SHA-256, canonical JSON, pytest.

## Global Constraints

- Preserve the current one-replica local artifact and SQLite topology. Staging and final bundles stay below the same target session directory and therefore on the same volume.
- Add one `DEFAULTS["report"]` mapping and `Config.report -> dict[str, Any]`. Only `cfg.report.get("guided_presentation_enabled") is True` selects guided output by default; bare defaults and shipped `config.yaml` remain false.
- Persist `report_presentation_version` and `report_entitlements` when the job is created. Retries reuse them; a later config, account-plan, or cohort change affects future jobs only.
- Guided version constants come from `swinglab.report_view`: `REPORT_VIEW_VERSION = "report-view-v1"` and `GUIDED_REPORT_PRESENTATION_VERSION = "guided-report-v1"`. Legacy remains `premium-coach-v2`.
- Keep `metrics.json` compatible. Existing fields and legacy deliverables remain unchanged for legacy jobs; a guided job may omit the optional legacy `overlay` deliverable and adds no raw landmarks.
- Keep raw landmarks, landmark visibility, source filenames, absolute paths, work paths, and scratch identifiers out of `report-view.json`, bundle manifests, logs, and APIs.
- A focused visual is core and never Pro-gated. Slow motion and coach replay retain their separate entitlement behavior.
- A focused-renderer-only failure may become limited `UnavailableEvidence`. A priority metric/event/annotation-trust failure becomes capture-only. A view, HTML, manifest, checksum, or other required-media failure publishes no completed result.
- A non-core slow-motion, replay, capture-playback, or instructional-illustration
  artifact may be omitted only when the server-owned capability/optional-section
  state is updated to unavailable before view serialization. Never declare a
  missing optional file or silently substitute another asset.
- Guided DTL reports render timing/rhythm evidence only and never call the legacy body-reference overlay. Legacy presentation retains its current overlay for rollback and historical compatibility.
- Never label a synthetic full-body figure as an ideal or “where it should be.” Orange is observed, green is the address/start reference, and a separately labeled dashed line is the configured coaching boundary.
- Missing detected audio may use an explicitly persisted manual strike method. It never falls back to a guessed impact frame.
- Cleanup removes only a validated attempt/final bundle directory whose canonical manifest belongs to that attempt. Never broadly delete the report/session root.
- The final job transaction assigns `DONE`, `report_rel`, `report_view_rel`, `report_manifest_rel`, `report_checksums_rel`, and `structured_report`. A failed/incomplete job keeps the rels null/flag false and the existing derived allowance logic treats it as unconsumed.
- Proof Cycle remains a separately verified, non-blocking sidecar. It does not rewrite the immutable view or block a completed core bundle.
- This plan defines only the guided `ReportHtmlWriter` injection boundary. Its
  deterministic test writer validates bundle mechanics, not customer-facing
  presentation. The web-presentation plan exclusively owns the real writer and
  production composition; guided job creation fails closed until it is supplied.
- This plan supplies artifact loading/path-resolution primitives. Owned API projection and generic-file denial are implemented in the owned API/security plan.

---

## Task 1: Preserve landmark visibility and capture typed in-memory evidence snapshots

**Files:**

- Modify: `swinglab/pose.py:37-65,98-150,153-192`
- Modify: `swinglab/events.py:20-45,52-108`
- Create: `swinglab/evidence.py`
- Create: `tests/test_evidence_snapshot.py`
- Modify: `tests/test_tracking_quality.py`
- Modify: `tests/test_events.py`

**Interfaces:**

- Produces frozen `PoseObservation(landmarks: Landmarks,
  visibility: Mapping[int, float | None])`.
- Produces `PoseTracker.detect_observation(frame_path: str | Path) ->
  PoseObservation | None`. Existing `detect(...) -> Landmarks | None` remains a
  compatibility wrapper returning `observation.landmarks`.
- `PoseObservation.visibility` contains every index in `pose.TRACKED`; missing
  model scores remain null. Existing upright/core-visibility rejection happens
  before an observation is returned.
- Produces `EventFailure.INSUFFICIENT_POSE_FRAMES` and
  `EventFailure.NO_READABLE_SWING`. `EventError` gains required `.reason` while
  preserving its current customer-facing string.
- Produces frozen `EventSnapshot(event: EventId, frame_index: int,
  timestamp_ms: int, method: PhaseMethod, label: str)` and
  `AnnotationGate(metric_id: str, readable: bool,
  reasons: tuple[ReasonCode, ...])`.
- Produces frozen `EvidenceSnapshot(swing: int, metrics: SwingMetrics,
  events: tuple[EventSnapshot, ...], event_frames: Mapping[EventId, Path],
  event_landmarks: Mapping[EventId, pose.Landmarks | None],
  finish_ankle_midpoints: tuple[tuple[float, float], ...],
  annotation_gates: Mapping[str, AnnotationGate],
  tracking_quality: TrackingQuality, target_direction: int,
  target_confident: bool, shoulder_width_px: float)`.
- Produces `build_evidence_snapshot(*, swing: int, frameset: FrameSet,
  observations: Sequence[PoseObservation | None], events: SwingEvents,
  finish_idx: int, metrics: SwingMetrics,
  event_frames: Mapping[EventId, Path],
  event_landmarks: Mapping[EventId, pose.Landmarks | None],
  impact_method: PhaseMethod, tracking_quality: TrackingQuality,
  hand: str) -> EvidenceSnapshot`.

- [ ] Add failing pose tests proving `detect_observation` retains wrist/elbow
  visibility, rejects low-visibility core landmarks exactly as `detect` does,
  and leaves `detect` return values unchanged for all current callers.

- [ ] Add failing event tests asserting sparse/degenerate tracking produces
  `INSUFFICIENT_POSE_FRAMES`, while no takeaway or no tracked top-to-impact
  interval produces `NO_READABLE_SWING`; assert current exception text remains
  unchanged.

- [ ] Add failing snapshot tests for all four events and methods: address uses
  `opening_baseline`, top uses `highest_tracked_hands`, impact uses exactly the
  supplied `detected_audio` or `manual_strike`, and finish uses
  `configured_finish_offset`. Timestamps are rounded absolute video time in
  milliseconds and never reconstructed from durations.

- [ ] Add failing annotation-gate tests. Head/hip/height/shoulder/finish gates
  require their exact tracked joints at their exact event/window. Lead-arm
  evidence requires lead shoulder, elbow, and wrist visibility; timing requires
  the wrist evidence used for top plus all four events. A missing or sub-0.5
  wrist/elbow yields `hand_landmarks_unreliable`. Poor session tracking yields
  `tracking_unstable`; target uncertainty is recorded separately and does not
  make head dip or timing unreadable.

- [ ] Run `python -m pytest tests/test_evidence_snapshot.py tests/test_tracking_quality.py tests/test_events.py -q`; expect failures for the missing observation/snapshot types.

- [ ] Implement `detect_observation` by retaining the MediaPipe visibility
  values before the current `Landmarks` projection. Keep `detect` exactly:

  ```python
  def detect(self, frame_path: str | Path) -> Landmarks | None:
      observation = self.detect_observation(frame_path)
      return observation.landmarks if observation is not None else None
  ```

- [ ] Add typed `EventFailure` to each current `EventError` site. Do not import
  report types into `events.py`; `evidence.py` maps event failures to report
  reason codes at the pipeline boundary.

- [ ] Implement immutable snapshots by copying landmark arrays so a later
  tracker/frame mutation cannot change selection. Keep only four event landmark
  maps plus finish ankle-midpoints; do not retain the entire tracked sequence.

- [ ] Implement explicit required-index maps per metric. Scale the finish-hold
  midpoint path into the same full-resolution coordinate system as its finish
  frame before storing it. Reject a non-finite/nonpositive shoulder width.

- [ ] Rerun the three focused test files; expect all pass.

- [ ] Commit: `git add swinglab/pose.py swinglab/events.py swinglab/evidence.py tests/test_evidence_snapshot.py tests/test_tracking_quality.py tests/test_events.py && git commit -m "feat: capture swing evidence snapshots"`.

## Task 2: Select and render one priority-matched focused artifact

**Files:**

- Create: `swinglab/focused_evidence.py`
- Modify: `swinglab/drawing.py:63-130,151-195`
- Create: `tests/test_focused_evidence.py`
- Modify: `tests/test_drawing.py`
- Modify: `tests/test_deliverable_images.py`

**Interfaces:**

- Consumes `PriorityEvidenceRule`, `EvidenceCandidate`, and
  `select_representative_swing` from `swinglab.report_presenter`.
- Produces frozen `FocusedEvidenceSelection(rule: PriorityEvidenceRule,
  snapshot: EvidenceSnapshot | None, metric_readable_swings: int,
  annotation_readable_swings: int, triggered_swings: int | None,
  fatal_reason: ReasonCode | None)`.
- Produces `select_focused_evidence(*, rule: PriorityEvidenceRule,
  snapshots: Sequence[EvidenceSnapshot],
  stats: Mapping[str, Mapping[str, float]]) -> FocusedEvidenceSelection`.
- Produces frozen `FocusedEvidenceArtifact(evidence: RenderedEvidence,
  media: MediaEntry, path: Path)` and
  `render_focused_evidence(selection: FocusedEvidenceSelection, *,
  out_path: Path, relative_path: str, cfg: Config) -> FocusedEvidenceArtifact`.
- Produces `build_unavailable_evidence(selection: FocusedEvidenceSelection,
  *, observation: str, supporting_measurement: MeasurementDetail | None) ->
  UnavailableEvidence` for renderer failure only.
- Raises `FocusedEvidenceRenderError` for Pillow/file failures. It does not catch
  metric/event/gate failures or choose another priority/swing.

- [ ] Add failing selector integration tests. Convert each eligible snapshot to
  one `EvidenceCandidate`, call the pure Task-1 selector, and assert metric/image
  come from the same swing. No visual-eligible snapshot plus trustworthy finite
  priority values produces null snapshot without a fatal reason; no trustworthy
  priority value produces `priority_evidence_unreliable`; missing required
  impact produces `no_reliable_strike_event`.

- [ ] Add failing image-semantic tests for all evidence kinds:
  head/hip boundary = green start marker, orange observed marker/displacement,
  and labeled dashed configured line only when target direction is confident;
  head height = vertical address reference with no target-direction wording;
  lead arm = exact shoulder-elbow-wrist arc; shoulder tilt = exact tracked
  shoulder line(s), with both address and impact lines from one swing for delta;
  finish stability = measured ankle-midpoint positions; tempo = four-event
  timeline with event methods/durations/ratio/consistency; steady reference =
  observed baseline labeled as a strength, never an ideal pose.

- [ ] Add failing DTL tests asserting only `tempo_timeline` renders, body
  evidence raises `UnsupportedFocusedEvidence`, and neither full-body skeleton,
  centerline, target boundary, nor toward/away phrase appears.

- [ ] Add failing quality/crop/handedness tests: source pixels are never mirrored;
  every joint/marker needed by an annotation remains inside the crop; no boundary
  is drawn when required landmarks fail; exact handed lead joints are used; alt
  text names swing, phase, observed marker, start/reference meaning, boundary
  when present, tracking state, and phase method.

- [ ] Run `python -m pytest tests/test_focused_evidence.py tests/test_drawing.py tests/test_deliverable_images.py -q`; expect import failures for the renderer.

- [ ] Add small focused drawing primitives (`draw_marker`, `draw_dashed_line`,
  `draw_displacement_arrow`, `draw_angle_arc`, `draw_labeled_timeline`) without
  changing legacy `draw_skeleton`, `sheared`, or overlay rendering.

- [ ] Implement one renderer function per `EvidenceKind`. Use
  `cfg.overlay["captured_color"]` for observed orange and
  `cfg.overlay["corrected_color"]` for the starting reference green. The green
  layer is never a sheared/full-body pose. Hash the finished file and populate
  the core `MediaEntry` with `role=priority_evidence`, `entitlement=core`, and
  the canonical relative path.

- [ ] Implement renderer failure separation. `build_unavailable_evidence` is
  callable only when `fatal_reason is None`, metric-readable count is positive,
  and snapshot selection established the required event; it adds only
  `focused_media_render_failed` to `render_reasons`.

- [ ] Rerun the focused tests; expect all pass and legacy drawing tests unchanged.

- [ ] Commit: `git add swinglab/focused_evidence.py swinglab/drawing.py tests/test_focused_evidence.py tests/test_drawing.py tests/test_deliverable_images.py && git commit -m "feat: render focused swing evidence"`.

## Task 3: Define manifests, checksums, safe published lookup, and attack-resistant validation

**Files:**

- Create: `swinglab/report_artifacts.py`
- Create: `tests/test_report_artifacts.py`

**Interfaces:**

- Produces constants `REPORT_MANIFEST_FORMAT = "report-bundle-v1"`,
  `REPORT_CHECKSUMS_FORMAT = "report-bundle-checksums-v1"`,
  `REPORT_VIEW_FILENAME = "report-view.json"`,
  `REPORT_MANIFEST_FILENAME = "report-bundle-manifest.json"`, and
  `REPORT_CHECKSUMS_FILENAME = "report-bundle-checksums.json"`.
- Produces frozen `ReportEntitlementSnapshot(coach_replay:
  Literal["available", "locked", "disabled"])` with canonical JSON
  encode/decode helpers.
- Produces frozen `ManifestArtifact(relative_path: str,
  kind: Literal["report", "report_view", "metrics", "media"],
  media_key: str | None, entitlement: Entitlement | None, required: bool)`.
- Produces frozen `ReportBundleManifest(format: Literal["report-bundle-v1"],
  attempt_id: str, presentation_version: str, outcome: ReportOutcome,
  artifacts: tuple[ManifestArtifact, ...])`.
- Produces frozen `ChecksumEntry(relative_path: str, size_bytes: int,
  sha256: str)` and `ReportBundleChecksums(format:
  Literal["report-bundle-checksums-v1"], manifest_sha256: str,
  files: tuple[ChecksumEntry, ...])`.
- Produces frozen `PublishedReportBundle(root: Path, report_path: Path,
  report_view_path: Path, manifest_path: Path, checksums_path: Path,
  view: ReportViewV1, manifest: ReportBundleManifest,
  checksums: ReportBundleChecksums)`.
- Produces `write_report_manifest`, `write_report_checksums`,
  `validate_staged_bundle(staging_dir: Path, *, manifest_rel: str,
  checksums_rel: str) -> tuple[ReportBundleManifest, ReportBundleChecksums,
  ReportViewV1]`,
  `load_published_bundle(session_dir: Path, *, report_rel: str,
  report_view_rel: str, manifest_rel: str, checksums_rel: str) ->
  PublishedReportBundle`, and
  `resolve_media_path(bundle: PublishedReportBundle, media_key: str) -> Path`.

- [ ] Add failing canonical round-trip tests for the entitlement snapshot,
  manifest, and checksums. The checksum file excludes itself; it covers the
  manifest and every declared report/view/metrics/media artifact. Artifacts and
  checksum rows are ordered by canonical POSIX relative path.

- [ ] Add failing validation tests for absolute paths, backslashes, drive
  prefixes, dot/dot-dot traversal, duplicate/case-colliding paths, symlink file
  or parent, undeclared file, missing file, directory where a file is expected,
  wrong size/hash, changed manifest hash, invalid SHA-256, unknown kind/role/
  entitlement/version, mismatched outcome, missing core focused media, locked
  unrendered replay declared as a file, and a view media checksum that differs
  from the checksum artifact.

- [ ] Add failing safe-lookup tests: all four persisted rels must stay under the
  session root, refer to one bundle root, match canonical files by identity,
  and pass full validation. Unknown/duplicate media keys, non-media entries, and
  a post-load replaced/symlinked path are rejected by `resolve_media_path`.

- [ ] Run `python -m pytest tests/test_report_artifacts.py -q`; expect an import
  failure for `swinglab.report_artifacts`.

- [ ] Implement a single `_safe_relative_path(value: str) -> PurePosixPath`
  modeled on `swinglab/backups/core.py:350-385`. Resolve each parent one segment
  at a time and reject symlinks before hashing/opening.

- [ ] Serialize both files canonically with sorted keys, compact separators,
  one newline, and bounded reads. Validate all cross-file invariants, including
  `MediaEntry.relative_path`, media key/role/entitlement/checksum, report-view
  version/presentation/outcome, and exactly one declared report/view/metrics/
  manifest relationship.

- [ ] Implement `resolve_media_path` from the already parsed view and validated
  manifest/checksum rows. Re-resolve and recheck file identity/hash before
  returning; never accept a caller-supplied relative path.

- [ ] Rerun `python -m pytest tests/test_report_artifacts.py -q`; expect all
  canonical, malicious-path, and lookup tests to pass.

- [ ] Commit: `git add swinglab/report_artifacts.py tests/test_report_artifacts.py && git commit -m "feat: validate guided report artifacts"`.

## Task 4: Orchestrate a complete same-volume bundle and recover abandoned attempts

**Files:**

- Create: `swinglab/report_bundle.py`
- Create: `tests/report_bundle_fixtures.py`
- Create: `tests/test_report_bundle.py`
- Create: `tests/test_report_bundle_recovery.py`
- Modify: `swinglab/report.py:90-143,146-331`

**Interfaces:**

- Produces `ReportHtmlWriter(Protocol)` with
  `__call__(out_path: Path, document: ReportDocument, *, cfg: Config) -> Path`.
  It is a composition boundary, not a renderer implementation. Also produces
  `GuidedReportRendererUnavailable(CoreReportBundleError)` for a guided call
  that reaches the boundary without a composed writer.
- Produces frozen `ReportBundleAttempt(attempt_id: str, session_dir: Path,
  staging_dir: Path, work_dir: Path, media_dir: Path)` and
  `StagedReportBundle(attempt: ReportBundleAttempt, document: ReportDocument,
  report_path: Path, report_view_path: Path, manifest_path: Path,
  checksums_path: Path)`.
- Produces `begin_report_bundle(session_dir: Path, *, attempt_id: str | None =
  None) -> ReportBundleAttempt`. Directory names are
  `.report-attempt-<32 lowercase hex>`.
- Produces `build_report_bundle(attempt: ReportBundleAttempt, *, html_writer:
  ReportHtmlWriter, video: VideoInfo, swings: list[dict], stats: dict,
  session_notes: list[str],
  hand: str, cfg: Config, angle: str, club: str | None, level: str | None,
  analysis_fps: float | None, replay_locked: bool,
  evidence_snapshots: Sequence[EvidenceSnapshot],
  reason_codes: Sequence[ReasonCode]) -> StagedReportBundle`.
- Produces `publish_report_bundle(staged: StagedReportBundle) ->
  PublishedReportBundle`, `discard_report_bundle_attempt(attempt:
  ReportBundleAttempt) -> None`, and
  `cleanup_abandoned_report_bundles(session_dir: Path) -> int`.
- Final bundle directories are `report-bundle-<attempt_id>` and are created by
  one same-volume `Path.replace` of the fully validated staging directory.

- [ ] Add failing build tests for clear face-on, clear DTL, limited secondary,
  limited renderer-unavailable, and capture-only. Assert the bundle contains
  compatible `metrics.json`, document HTML, `report-view.json`, manifest,
  checksums, and exactly the declared entitled media. Capture-only has no focused
  evidence; DTL and all guided jobs have no legacy overlay.

- [ ] Add `write_test_report_html(...)` in `tests/report_bundle_fixtures.py`.
  Its exact signature is `write_test_report_html(out_path: Path, document:
  ReportDocument, *, cfg: Config) -> Path`.
  It deterministically emits only a self-contained structural document with
  `REPORT_FORMAT_VERSION`, supplied presentation/outcome markers, and escaped
  fixture text. Every Plan 2 bundle/pipeline/publication test injects it. It is
  never imported from `swinglab`, treated as visual acceptance, or used by app
  composition.

- [ ] Add failing failure-policy tests. Mock only focused Pillow rendering to
  fail and expect a limited unavailable view. Mock view write, HTML write,
  manifest write, checksum write, required focused file, and validation in turn;
  expect `CoreReportBundleError`, no final root, and no completed-looking paths.
  Separately fail each non-core media renderer and assert its media entry is
  absent, its capability/optional-section state is unavailable, and every other
  declared artifact still validates; no unrelated media is substituted.

- [ ] Add failing publication tests proving the staging/final parents have the
  same resolved filesystem/volume, validation occurs before rename, one directory
  rename publishes all files, and the returned rels resolve inside the final
  root. A reader given only the old job row cannot observe the new directory.

- [ ] Add failing recovery tests for a crash before validation, after validation,
  after final rename but before DB publication, and after a corrupt attacker
  directory appears beside an attempt. Cleanup removes only a canonical
  `.report-attempt-*` or unreferenced `report-bundle-*` whose internal
  attempt/manifest IDs match; it refuses symlinks, malformed manifests, session
  root, media root, current published rels, and unrelated files.

- [ ] Run `python -m pytest tests/test_report_bundle.py tests/test_report_bundle_recovery.py tests/test_report.py -q`; expect import failures for the orchestrator.

- [ ] Implement `begin_report_bundle` with `mkdir(exist_ok=False)` below the
  resolved session root. Put both `work` and `media` inside staging so no raw or
  partial deliverable is ever beside the final bundle.

- [ ] In `build_report_bundle`, call `prepare_report_input` once to freeze the
  existing Caddie Brief/issues/drill. Map its `PriorityEvidenceRule`, select and
  render focused evidence, then use `dataclasses.replace` to add the final
  evidence/media/reasons before `build_report_document`. Do not call any priority
  builder after representative selection.

- [ ] Call only the required `html_writer` argument to create `report.html`.
  Do not import `swinglab.report_html`, create a Jinja template, or provide a
  default renderer in this plan. Validate the returned path and report markers
  exactly like any other core artifact.

- [ ] Change `write_metrics_json` deliverable assembly to include `overlay` only
  when `swing.get("overlay")` is present. Legacy output remains byte-compatible;
  guided output honestly omits a file it did not render.

- [ ] Write document/view/metrics/media, then manifest, then checksums. Call
  `validate_staged_bundle` and retain its parsed objects in the staged result.
  On a core failure, remove only the validated attempt directory; if safe
  validation cannot establish ownership, leave it for operator/recovery review
  and raise.

- [ ] Implement `publish_report_bundle` as one same-volume directory rename to
  a previously nonexistent final root, then call `load_published_bundle` for
  readback. If readback fails, leave the unreferenced final root for the scoped
  recovery function and return no published result.

- [ ] Rerun the three focused files; expect all pass.

- [ ] Commit: `git add swinglab/report_bundle.py swinglab/report.py tests/report_bundle_fixtures.py tests/test_report_bundle.py tests/test_report_bundle_recovery.py tests/test_report.py && git commit -m "feat: publish atomic guided report bundles"`.

## Task 5: Integrate snapshots/bundles into the pipeline and enforce DTL/legacy boundaries

**Files:**

- Modify: `swinglab/pipeline.py:34-43,69-269,272-385`
- Modify: `swinglab/metrics.py:371-454`
- Modify: `swinglab/overlay.py:1-155`
- Modify: `tests/test_pipeline_e2e.py:57-211`
- Modify: `tests/test_camera_angle.py`
- Modify: `tests/test_replay_gate.py:252-311`
- Modify: `tests/test_caps.py`

**Interfaces:**

- Extends `SessionResult` additively with defaults:
  `report_view_path: Path | None = None`, `manifest_path: Path | None = None`,
  `checksums_path: Path | None = None`, `structured_report: bool = False`, and
  `evidence_snapshots: list[EvidenceSnapshot] = field(default_factory=list)`.
- Extends `analyze_video(..., report_presentation_version: str =
  REPORT_PRESENTATION_VERSION, report_entitlements:
  ReportEntitlementSnapshot | None = None, guided_html_writer:
  ReportHtmlWriter | None = None) -> SessionResult`. Guided mode rejects null;
  legacy mode ignores it.
- Extends private `_analyze_swing(..., impact_method: PhaseMethod,
  guided: bool = False) -> tuple[dict, EvidenceSnapshot]` for the guided branch.
  Legacy remains internal-dictionary-compatible to `SessionResult.swings`.
- Guided capture failures map directly to reason codes, not note-string parsing:
  no audio/detected/manual event -> `no_reliable_strike_event`; sparse pose ->
  `insufficient_pose_frames`; no takeaway/readable interval ->
  `no_readable_swing`; conservative selected-angle mismatch ->
  `camera_angle_mismatch`; poor selected evidence -> `tracking_unstable` or
  `priority_evidence_unreliable`.

- [ ] Add failing end-to-end tests that run the existing fake tracker through a
  guided face-on analysis with `write_test_report_html` and assert a structured
  bundle plus evidence snapshot;
  run guided DTL and assert a timing diagram, persisted event methods, no body
  evidence, no overlay file/key, and unchanged timing metrics.

- [ ] Add failing manual/detected event tests proving `impact_method` is exact.
  A silent clip with explicit strikes uses `manual_strike`; a missing/undetected
  event in guided mode produces a capture-only bundle instead of a coaching
  frame or guessed impact. Legacy retains its current `ZeroStrikesError` CLI/web
  failure behavior while the gate is off.

- [ ] Add failing capture tests for angle mismatch, unstable tracking,
  insufficient pose frames, and no readable swing. Assert diagnosis/drills/
  overlays/replay/gear are absent and the previous capture-only outcome marker
  remains compatible.

- [ ] Add failing legacy parity tests: default `analyze_video` still renders the
  existing overlay and accepts every current fake/signature; guided is selected
  only by the explicit version. Add a direct assertion that neither guided DTL
  nor guided face-on invokes `overlay.make_overlay`.

- [ ] Add a failing direct-pipeline test for explicit guided version plus null
  writer. It raises `GuidedReportRendererUnavailable` before publication and
  leaves no final bundle. Legacy with null writer remains unchanged.

- [ ] Run `python -m pytest tests/test_pipeline_e2e.py tests/test_camera_angle.py tests/test_replay_gate.py tests/test_caps.py -q`; expect guided-argument/snapshot failures while legacy tests still pass.

- [ ] Split only the artifact roots, not the measurement sequence. The current
  pose/events/metrics/quality order at `pipeline.py:289-310` remains; guided uses
  `detect_observation` and derives the existing `tracked` list from it. Extract
  all four full-resolution event frames/landmarks and call
  `build_evidence_snapshot` before work deletion.

- [ ] Determine impact method once in `analyze_video`: manual list means
  `manual_strike`; audio detector means `detected_audio`. Pass it into every
  analyzed swing. Never infer the method from the resulting timestamp.

- [ ] In guided mode begin the attempt before extracting audio/frames, route all
  generated work/media into it, skip `overlay.make_overlay`, and call
  `build_report_bundle(html_writer=guided_html_writer, ...)` and
  `publish_report_bundle`. In legacy mode retain the current directories/calls
  exactly.

- [ ] Convert guided recoverable capture failures into an empty/partial metrics
  payload plus typed capture-only document. Keep video-too-long, invalid
  configuration, ffmpeg infrastructure failure, view/HTML/manifest/checksum
  failure, and unsafe cleanup as terminal core failures.

- [ ] Rerun the focused tests; expect guided and legacy branches to pass.

- [ ] Commit: `git add swinglab/pipeline.py swinglab/metrics.py swinglab/overlay.py tests/test_pipeline_e2e.py tests/test_camera_angle.py tests/test_replay_gate.py tests/test_caps.py && git commit -m "feat: integrate guided report evidence pipeline"`.

## Task 6: Persist immutable presentation/entitlements and commit publication once

**Files:**

- Modify: `swinglab/config.py:17-242,306-383`
- Modify: `config.yaml`
- Modify: `swinglab/web/jobs.py:85-134,174-215,218-288,649-790,970-1031,1575-1605`
- Modify: `swinglab/proof_cycle_artifact.py:57-58,162-166,378-434`
- Create: `tests/test_report_bundle_job_publication.py`
- Modify: `tests/test_proof_cycle_artifact.py`
- Modify: `tests/test_replay_gate.py:66-196`
- Modify: `tests/test_disconnect.py`
- Modify: `tests/test_accounts.py:250-435`
- Modify: `tests/test_level_context.py:55-80`

**Interfaces:**

- Adds `DEFAULTS["report"] = {"guided_presentation_enabled": False}` and
  `Config.report -> dict[str, Any]`; shipped `config.yaml` contains the same
  false value with rollback/future-job comments.
- Adds Job fields `report_presentation_version: str`,
  `report_entitlements: ReportEntitlementSnapshot`,
  `report_view_rel: str | None`, `report_manifest_rel: str | None`,
  `report_checksums_rel: str | None`, and `structured_report: bool` beside the
  existing `report_rel`.
- Adds SQLite columns `report_presentation_version TEXT NOT NULL DEFAULT
  'premium-coach-v2'`, `report_entitlements_json TEXT`, `report_view_rel TEXT`,
  `report_manifest_rel TEXT`, `report_checksums_rel TEXT`, and
  `structured_report INTEGER NOT NULL DEFAULT 0`, with additive startup
  migration checks.
- Extends `create_session(..., report_presentation_version: str | None = None)`.
  Null resolves to guided only when `cfg.report.get("guided_presentation_enabled")
  is True`; an explicit known version wins. Unknown versions raise before a
  directory/row is created.
- `create_session` captures `ReportEntitlementSnapshot` internally. It is not an
  HTTP/request parameter. `replay_locked(job)` reads only the persisted snapshot.
- Produces `_complete_job(job: Job, result: SessionResult) -> None`, which
  validates/readbacks the published bundle, then commits all terminal fields in
  one `BEGIN IMMEDIATE` transaction guarded by `status = processing`.
- Extends `JobManager.__init__(sessions_dir: Path, cfg: Config, user_store=None,
  *, guided_html_writer: ReportHtmlWriter | None = None)`. The default preserves
  all existing call sites. A job resolved to
  `guided-report-v1` is rejected before directory/row creation when the writer is
  absent; the web-presentation plan owns supplying the production writer.

- [ ] Add failing config/migration tests for default/shipped false, the new
  `Config.report` property, exact-True behavior, historical rows defaulting to
  legacy/unstructured, and round-trip of all new Job fields. Add gate-true and
  explicit-guided tests with no injected writer; both fail before creating a
  directory/row. The same cases succeed with `write_test_report_html` injected.

- [ ] Rewrite the replay snapshot test: create a free job, upgrade the owner,
  downgrade again, and assert that job remains locked; create a new job while
  upgraded and assert it remains available after downgrade. Disabled replay is
  persisted as `disabled`. Restart/retry produces the same answer.

- [ ] Add failing publication tests with a fake guided `SessionResult`: before
  final transaction the row is processing with null rels/false capability;
  afterward it is done with all four canonical rels/true capability. A mismatched
  view/manifest/checksum, stale status, missing file, or DB exception leaves no
  done row. Legacy completion sets only `report_rel` and false capability.

- [ ] Add failing allowance tests. Processing reserves once; guided core failure
  becomes failed and releases it; capture-only completion follows the existing
  courtesy-rejection calculation; coaching completion consumes once; a crash
  after bundle rename but before DB commit remains processing/requeued and never
  counts as a completed use twice.

- [ ] Run `python -m pytest tests/test_report_bundle_job_publication.py tests/test_replay_gate.py tests/test_disconnect.py tests/test_accounts.py tests/test_level_context.py -q`; expect schema/policy failures.

- [ ] Add the one report config mapping/property and shipped false entry. Use a
  helper `configured_report_presentation(cfg: Config) -> str`; do not scatter
  boolean checks through app/pipeline code.

- [ ] Capture entitlement at session creation using the current gate/account
  facts. Store canonical JSON with no user ID/plan/email. Update
  `replay_locked(job)` to read the snapshot and keep ownerless/open/disabled
  behavior explicit.

- [ ] In `_run`, pass new analyzer kwargs only for a guided job so current exact
  fake analyzer signatures remain compatible, including the manager's captured
  `guided_html_writer`. Before retry call
  `cleanup_abandoned_report_bundles(job.session_dir)`. After core files are
  published, call `_complete_job`; only after that guarded transaction commits,
  build the optional Proof sidecar. Proof failure remains non-blocking and does
  not alter the done row or bundle checksums. Exclude the current job ID from
  the prior-job collection so the just-completed row cannot compare to itself.

- [ ] Keep the immutable bundle closed to undeclared files. For structured jobs,
  make `proof_cycle_artifact_path(job)` resolve the separately verified private
  sidecar to `<session_dir>/proof-cycle.json`; retain the existing report-parent
  location for legacy jobs. Add path, self-exclusion, core-completion, and
  non-blocking-failure regression tests.

- [ ] Implement `_complete_job` without routing through the ordinary progress
  `_save` commit. Verify status and persisted presentation/entitlements have not
  changed, verify bundle readback for structured results, execute one guarded
  update, and commit. In-memory Job fields change only after commit succeeds.

- [ ] Keep usage derivation at `jobs.py:362-457,1053-1107`; the atomic done row
  is the durable allowance record. Add no second quota ledger for live rows.

- [ ] Rerun the focused tests; expect all pass.

- [ ] Commit: `git add swinglab/config.py config.yaml swinglab/web/jobs.py swinglab/proof_cycle_artifact.py tests/test_report_bundle_job_publication.py tests/test_proof_cycle_artifact.py tests/test_replay_gate.py tests/test_disconnect.py tests/test_accounts.py tests/test_level_context.py && git commit -m "feat: commit guided report publication"`.

## Task 7: Cover backup, restore, privacy, reset, retention, and recovery lifecycle

**Files:**

- Modify: `swinglab/backups/core.py:397-449,637-680,703-785`
- Modify: `tests/test_backups.py`
- Modify: `tests/test_shopify_privacy.py`
- Modify: `tests/test_history_reset_core.py`
- Modify: `tests/test_retention_disk.py`
- Modify: `docs/operations/backup-recovery.md`

**Interfaces:**

- Backup allowlisting reads structured rels from the snapshot jobs table and
  includes `report.html`, `metrics.json`, `report-view.json`, focused/core and
  entitled media, `report-bundle-manifest.json`,
  `report-bundle-checksums.json`, and the optional separately verified
  session-root `proof-cycle.json` associated with each done structured job.
- Restore reconciliation requires all persisted structured rels and validates
  the report bundle checksum graph before reporting the job readable.
- Privacy inventory includes the user-readable HTML/evidence and inventories
  the private view/manifest/checksums for authorized export handling; generic
  serving remains forbidden by the API/security plan.
- History reset, account deletion, and retention continue moving/removing the
  whole validated job directory, which covers final bundles and attempts.

- [ ] Add failing backup tests that create one legacy and one structured done
  job. Assert every structured core artifact/checksum is captured once, source/
  work/attempt directories are excluded, and a DB rel/checksum mismatch aborts
  backup rather than silently downgrading the job.

- [ ] Add failing restore tests for changed/missing focused evidence,
  report-view, manifest, and checksums; unsafe/symlink paths; and a structured
  DB row whose artifact set is legacy-only. Expected result is a restore error
  before the result is exposed as readable.

- [ ] Add failing privacy tests asserting the session artifact inventory names
  HTML/evidence/view/manifest/checksums, contains no artifact bytes/content in
  SQLite, and excludes raw landmark fields. Account deletion removes their
  owning job directory and row.

- [ ] Add failing history-reset and retention tests with structured bundles plus
  abandoned attempts. Assert the current two-phase rename/journal covers the
  entire job tree, monthly receipts preserve coaching/capture usage once, restart
  recovery finishes cleanup, and no bundle remains addressable afterward.

- [ ] Run `python -m pytest tests/test_backups.py tests/test_shopify_privacy.py tests/test_history_reset_core.py tests/test_retention_disk.py tests/test_report_bundle_recovery.py -q`; expect lifecycle failures for the new rels/files.

- [ ] Extend `_artifact_sources` and `_verify_artifact_database_mapping` to read
  the additive columns when present while retaining legacy snapshot support.
  For a structured row, call the same `load_published_bundle` validator against
  the live/scratch artifact root before yielding/accepting files.

- [ ] Preserve backup format compatibility: new files are additive artifact
  entries under the existing backup format; old bundles remain restorable. A
  restored database row with `structured_report = 1` requires the complete new
  graph and exact checksums.

- [ ] Keep privacy inventory path-only. Add assertions rather than a second copy
  algorithm; `users.py:2093-2152` already inventories contained files without
  storing their content.

- [ ] Document the new files, checksum verification, same-volume attempt/final
  roots, recovery refusal rules, and the boundary between private view JSON and
  user-readable report/evidence.

- [ ] Rerun the lifecycle test command; expect all pass.

- [ ] Commit: `git add swinglab/backups/core.py tests/test_backups.py tests/test_shopify_privacy.py tests/test_history_reset_core.py tests/test_retention_disk.py docs/operations/backup-recovery.md && git commit -m "feat: retain guided report bundles safely"`.

## Task 8: Run the evidence/bundle release gate

**Files:**

- Modify only files already listed if a verification failure is found.

**Interfaces:**

- Produces the only persisted structured artifact/lookup contract consumed by
  web presentation, owned API/security, native integration, and rollout plans.
- This gate proves evidence capture, transactionality, lifecycle behavior, and
  renderer injection with `write_test_report_html`. It does not claim the final
  guided HTML exists; Plan 3 owns its renderer, production composition, rendered
  QA, and integration test against this same boundary.

- [ ] Run the focused gate:
  `python -m pytest tests/test_evidence_snapshot.py tests/test_focused_evidence.py tests/test_report_artifacts.py tests/test_report_bundle.py tests/test_report_bundle_recovery.py tests/test_report_bundle_job_publication.py tests/test_pipeline_e2e.py tests/test_replay_gate.py tests/test_backups.py tests/test_shopify_privacy.py tests/test_history_reset_core.py tests/test_retention_disk.py -q`.
  Expected result: all pass.

- [ ] Run compatibility suites:
  `python -m pytest tests/test_report.py tests/test_premium_report.py tests/test_metrics.py tests/test_events.py tests/test_camera_angle.py tests/test_caps.py tests/test_disconnect.py tests/test_accounts.py tests/test_web.py -q`.
  Expected result: all pass with the report gate false.

- [ ] Run `python -m pytest -q`. Expected result: the full existing suite plus
  all guided contract/evidence/bundle tests pass.

- [ ] Run a guided synthetic face-on and DTL analysis in a temporary output
  directory. Validate each with `load_published_bundle`, recompute every hash,
  and inspect the file list. Expected: face-on has one focused artifact; DTL has
  one timing artifact; neither has a guided legacy overlay; no work/raw pose is
  declared.

- [ ] Simulate a renderer-only failure and a core writer failure. Expected:
  renderer-only publishes limited/unavailable; core writer publishes no done
  row and `usage_this_month` does not count a completed analysis.

- [ ] Run `git diff --check`, then search with
  `rg -n "where it should be|raw_landmark|source_name|email" swinglab/evidence.py swinglab/focused_evidence.py swinglab/report_artifacts.py swinglab/report_bundle.py`.
  Expected: no ideal-pose claim and no persisted private/raw field.

- [ ] Commit any focused gate correction with
  `git commit -m "test: verify guided report evidence bundles"`.

## Evidence-bundle plan completion gate

- [ ] Confirm event provenance, annotation-specific gates, deterministic
  representative selection, DTL restriction, target uncertainty, handedness,
  and renderer-only degradation all have direct tests.
- [ ] Confirm every structured done row readbacks one complete view/manifest/
  checksum graph and every declared media key resolves safely.
- [ ] Confirm presentation/entitlement selection survives account/config changes
  and retry without re-evaluation.
- [ ] Confirm no failure path creates a done row, partial public result, duplicate
  allowance use, broad cleanup, or Proof-cycle rewrite of immutable artifacts.
- [ ] Confirm backup/restore, privacy inventory/deletion, history reset, account
  deletion through its existing job-tree path, retention, and restart cleanup
  cover the new artifacts.
- [ ] Record implementation commits and verification evidence. Report source,
  GitHub merge, deployed web, native release, public sample, cohort activation,
  and rollback states separately; this plan alone makes none of them live.
