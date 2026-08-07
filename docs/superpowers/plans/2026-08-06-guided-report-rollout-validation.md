# Guided Swing Report Cohort Rollout and Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Activate the redesigned report for stable new-session cohorts only after privacy-safe operations, rendered accessibility evidence, beginner comprehension, and independent web/native/sample rollback gates are proven.

**Architecture:** A pure `swinglab.report_rollout` policy validates four default-off switches and selects either the existing `premium-coach-v2` presentation or `guided-report-v1` with a stable keyed bucket. `JobManager` persists that string selection and the Plan 2 `ReportEntitlementSnapshot` before analysis, so retry and later membership/configuration changes cannot rewrite the result. `/healthz` and the typed capabilities API expose only non-sensitive aggregate state. A deterministic fixture renderer and evidence validator produce the release packet; no personal swing footage is used.

**Tech Stack:** Python 3.11+, HMAC-SHA256, dataclasses, SQLite WAL, FastAPI/Pydantic, pytest, Playwright Chromium for local rendered evidence, existing Expo/Jest/Maestro checks, Markdown/JSON release evidence, and the current one-replica Railway topology.

## Global Constraints

- This plan starts only after Plans 1-5 have a green vertical slice. It does not repair presenter, artifact, API, web, or native failures by weakening their contracts.
- The bare-code and shipped configuration begin with report presentation disabled and a zero-percent cohort. Enabling code and increasing a production cohort are separate operations.
- Selection applies only to new sessions. Never mutate, backfill, rerender, or delete a completed report to change its presentation version.
- The selected presentation version and report entitlement snapshot are durable job-creation facts. Retries, restarts, upgrades, downgrades, and flag changes cannot alter them.
- A partial cohort requires a stable `SWINGLAB_SECRET`; invalid percentage, missing secret, or malformed settings fail startup before accepting uploads.
- Cohort keys are opaque user IDs for owned sessions and generated job IDs for ownerless sessions. Never hash or log an email, source filename, IP address, report copy, or swing measurement for rollout selection.
- Health and operator output may include only configuration state and aggregate counts. It must not expose a user ID, job ID, report reason, metric, media key, or outcome text.
- QA fixtures are synthetic and committed. Local screenshots, PDFs, browser traces, and moderated-session records stay under ignored `artifacts/report-qa/` and are attached to release evidence, never committed with participant names or personal footage.
- Any safety-boundary misunderstanding is a release blocker even when the numerical comprehension threshold passes.
- This plan prepares activation and rollback evidence. It does not authorize a merge, Railway deployment, cohort increase, public-sample change, TestFlight/Play publication, or store submission.

---

## Task 1: Add a strict, stable report-presentation policy

**Files:**

- Create: `swinglab/report_rollout.py`
- Create: `tests/test_report_rollout.py`
- Modify: `swinglab/config.py:17-290`
- Modify: `config.yaml:1-340`
- Modify: `tests/test_config.py`

**Interfaces:**

- `ReportPresentationSettings.from_config(cfg: Config) -> ReportPresentationSettings`
- `ReportPresentationSettings` fields are exactly `enabled: bool`,
  `cohort_percent: float`, `sample_enabled: bool`, and `native_enabled: bool`.
- `select_report_presentation_version(settings, *, cohort_key: str,
  secret: str | None) -> str`
- The function returns existing `swinglab.report.REPORT_PRESENTATION_VERSION`
  (`premium-coach-v2`) or
  `swinglab.report_view.GUIDED_REPORT_PRESENTATION_VERSION`
  (`guided-report-v1`); `report-view-v1` remains the independent JSON schema.
- A partial cohort (`0 < cohort_percent < 100`) requires a non-empty stable
  secret. Disabled or zero percent always returns `premium-coach-v2`; enabled
  and 100 percent always returns `guided-report-v1` without reading secret
  material.

- [ ] Add failing tests that import the new module and cover disabled, 0, 0.01,
  50, 99.99, and 100 percent; stable repeat selection; separation between two
  cohort keys; malformed booleans/numbers; NaN/infinity; and a missing secret for
  a partial cohort. Expected first failure: `ModuleNotFoundError` for
  `swinglab.report_rollout`.
- [ ] Include this exact boundary test before implementation:

  ```python
  def test_partial_cohort_is_stable_and_requires_secret():
      settings = ReportPresentationSettings(
          enabled=True,
          cohort_percent=25.0,
          sample_enabled=False,
          native_enabled=False,
      )
      first = select_report_presentation_version(
          settings, cohort_key="user_opaque_17", secret="stable-test-secret"
      )
      assert first in (
          REPORT_PRESENTATION_VERSION,
          GUIDED_REPORT_PRESENTATION_VERSION,
      )
      assert select_report_presentation_version(
          settings, cohort_key="user_opaque_17", secret="stable-test-secret"
      ) == first
      with pytest.raises(ReportPresentationConfigurationError):
          select_report_presentation_version(
              settings, cohort_key="user_opaque_17", secret=None
          )
  ```

- [ ] Run `python -m pytest tests/test_report_rollout.py tests/test_config.py -q`;
  expect collection failure because the policy does not exist.
- [ ] Add default-off values to `Config.DEFAULTS` and `config.yaml`:

  ```yaml
  report:
    guided_presentation_enabled: false
    guided_presentation_cohort_percent: 0.0
    guided_sample_enabled: false
    guided_native_enabled: false
  ```

- [ ] Implement the selector with a domain-separated keyed digest and no logging:

  ```python
  def select_report_presentation_version(
      settings: ReportPresentationSettings,
      *,
      cohort_key: str,
      secret: str | None,
  ) -> str:
      if not settings.enabled or settings.cohort_percent <= 0:
          return REPORT_PRESENTATION_VERSION
      if settings.cohort_percent >= 100:
          return GUIDED_REPORT_PRESENTATION_VERSION
      if not secret:
          raise ReportPresentationConfigurationError(
              "A stable application secret is required for a partial report cohort."
          )
      digest = hmac.new(
          secret.encode("utf-8"),
          f"guided-report-v1:{cohort_key}".encode("utf-8"),
          hashlib.sha256,
      ).digest()
      unit = int.from_bytes(digest[:8], "big") / float(1 << 64)
      return (
          GUIDED_REPORT_PRESENTATION_VERSION
          if unit < settings.cohort_percent / 100.0
          else REPORT_PRESENTATION_VERSION
      )
  ```

- [ ] Validate settings once during app construction. Keep `sample_enabled` and
  `native_enabled` independent from the web cohort; neither may implicitly turn
  on report generation for customer sessions.
- [ ] Run `python -m pytest tests/test_report_rollout.py tests/test_config.py -q`;
  expect all tests to pass.
- [ ] Commit only this task's files:
  `git add swinglab/report_rollout.py swinglab/config.py config.yaml tests/test_report_rollout.py tests/test_config.py`, then
  `git commit -m "feat: add guided report rollout policy"`.

## Task 2: Persist creation-time selection and prove deterministic retry

**Files:**

- Modify: `swinglab/web/jobs.py:86-110,174-215,218-288,648-790,968-1031`
- Modify: `swinglab/web/app.py:477-585,3390-3530`
- Create: `tests/test_report_rollout_jobs.py`
- Modify: `tests/test_web.py`
- Modify: `tests/test_replay_gate.py`
- Modify: `tests/test_pipeline_e2e.py`
- Modify: `tests/test_guided_report_web_composition.py` (created by Plan 3)

**Interfaces:**

- Consume Plan 2's persisted `Job` fields `report_view_rel`,
  `report_manifest_rel`, `report_checksums_rel`, and `structured_report` without
  renaming them.
- Consume Plan 2's `Job.report_presentation_version: str` and its database
  default `premium-coach-v2`; do not add a numeric shadow version.
- Consume Plan 2's typed creation-time `ReportEntitlementSnapshot` and
  `report_entitlements_json` serializer. `coach_replay` remains exactly
  `available`, `locked`, or `disabled`; do not add a second entitlement format.
- `JobManager` receives the validated settings and cohort secret alongside Plan
  2's existing `guided_html_writer` at construction; rollout wiring must preserve
  Plan 3's production writer injection.
  `create_session(..., report_presentation_version: str | None = None)` preserves
  Plan 2's explicit override. When the argument is `None`, it selects once after
  generating the job ID and before its first `_save`, using the opaque owner ID
  when present and the generated job ID only for an ownerless session.
- `JobManager._run` passes the persisted version and entitlement snapshot to
  `analyze_video`; it never calls current membership to change either decision.

- [ ] Add a failing migration test that opens a pre-feature jobs database, starts
  `JobManager`, and asserts old rows load as `premium-coach-v2` while new rows
  persist their selected string. Expected failure: the boolean-only Plan 2 gate
  does not yet apply a stable partial cohort.
- [ ] Add failing job tests for a 0-percent legacy job, a 100-percent v1 job, a
  stable partial-cohort owned job, and an ownerless job selected by its generated
  ID. Assert that `Job.as_dict()` does not expose the bucket key or secret.
  Update Plan 3's composition fixture to set the cohort to 100 percent while it
  continues proving the rollout constructor passes `write_report_document_html`
  to the guided analyzer path.
- [ ] Add a failing deterministic-retry test. Create a free-account v1 job, record
  its version and entitlement snapshot, fail the first render after staging,
  upgrade the account and change the cohort to 0, restart the manager, and retry.
  The second call to `analyze_video` must receive the original version and
  entitlement snapshot; no partial artifact or allowance receipt may exist after
  the first failure.
- [ ] Run
  `python -m pytest tests/test_report_rollout_jobs.py tests/test_replay_gate.py tests/test_pipeline_e2e.py tests/test_guided_report_web_composition.py -q`;
  expect failures for missing persistence and analysis arguments.
- [ ] Reuse Plan 2's additive string column and migration. Keep its database
  default `premium-coach-v2` so historical and imported legacy rows stay legacy.
  Persist the selected value in `_save`, `_from_row`, legacy import, and
  `as_dict` only as the non-sensitive `report_presentation_version`.
- [ ] Select with `cohort_key=job.user_id or job.id` exactly once before the
  first save. Never use email, filename, IP address, club, angle, or a swing
  result as the cohort key.
- [ ] Move replay/structured-report entitlement selection from analysis-time
  membership lookup to the Plan 2 creation snapshot. Preserve
  `JobManager.replay_locked(job)` as a compatibility adapter that reads the
  persisted snapshot; no current-plan lookup remains in `_run`.
- [ ] Pass the stored values explicitly:

  ```python
  result = analyze_video(
      video_path,
      out_dir=job.session_dir / "out",
      hand=job.hand,
      manual_strikes=job.strikes,
      cfg=self.cfg,
      fast=job.fast,
      log=log,
      progress=progress,
      angle=job.angle,
      club=job.club,
      level=job.level,
      report_presentation_version=job.report_presentation_version,
      report_entitlements=job.report_entitlements,
  )
  ```

- [ ] On restart, clean only files named by the failed attempt manifest, then
  requeue with the persisted selection. Do not use broad directory deletion and
  do not alter completed legacy or v1 bundles.
- [ ] Run the focused command again; expect all tests to pass. Then run
  `python -m pytest tests/test_web.py tests/test_replay_gate.py tests/test_history_reset_core.py tests/test_backups.py -q`;
  expect no legacy regression.
- [ ] Commit:
  `git add swinglab/web/jobs.py swinglab/web/app.py tests/test_report_rollout_jobs.py tests/test_web.py tests/test_replay_gate.py tests/test_pipeline_e2e.py tests/test_guided_report_web_composition.py`, then
  `git commit -m "feat: persist guided report assignment"`.

## Task 3: Expose privacy-safe rollout health and native capability state

**Files:**

- Modify: `swinglab/web/jobs.py`
- Modify: `swinglab/web/app.py:4076-4350,4614-4697`
- Modify: `swinglab/api/contracts.py` (created by Mobile Backend Foundation Task 1)
- Modify: `swinglab/api/mobile_routes.py` (created by Mobile Backend Foundation Task 2)
- Modify: `swinglab/web/mobile_resources.py` (created by Mobile Backend Foundation Task 4)
- Create: `tests/test_report_rollout_ops.py`
- Modify: `tests/test_ops.py`
- Modify: `tests/test_mobile_capabilities.py`
- Modify: `tests/test_access_log_privacy.py`
- Modify: `docs/api/openapi-v1.json`

**Interfaces:**

- `JobManager.report_presentation_snapshot() -> dict[str, int]` returns aggregate
  counts for `legacy`, `structured`, `structured_failed`, and
  `structured_processing`; no row identifiers or report contents.
- `/healthz.report_presentation` returns `enabled`, `cohort_percent`,
  `sample_enabled`, `native_enabled`, and the aggregate count keys above.
- `/api/v1/capabilities` advertises `report_view_v1` only when
  `native_enabled is True` and the structured owned route is installed. App
  composition passes the validated setting through Plan 4's
  `report_view_v1_available` boundary. It never exposes the cohort percentage
  or operator-only aggregate counts.

- [ ] Add failing SQL aggregation tests for an empty database and mixed legacy,
  structured-done, structured-failed, and structured-processing rows. Assert the
  returned dictionary has exactly the four allowed keys.
- [ ] Add failing `/healthz` tests for default-off state, 25-percent development
  state, and aggregate counts. Recursively assert the payload contains no user
  ID, job ID, source name, report text, metric, reason code, media key, path, or
  secret.
- [ ] Add failing capabilities tests showing `report_view_v1=False` by default,
  true only under the native switch, and unaffected by whether the current user
  happens to be inside the web cohort.
- [ ] Run
  `python -m pytest tests/test_report_rollout_ops.py tests/test_ops.py tests/test_mobile_capabilities.py tests/test_access_log_privacy.py -q`;
  expect failures for missing snapshot/capability fields.
- [ ] Implement one bounded aggregate SQL query. Do not scan artifact JSON or
  inspect coaching outcomes to build health state.
- [ ] Extend typed capability models rather than returning an untyped dictionary.
  The report capability is a server-owned availability signal, not an entitlement
  or guarantee that a particular historical session is structured.
- [ ] Pass `settings.native_enabled` into Plan 4's
  `report_view_v1_available` composition input. Do not add another config lookup
  inside the API route or serializer.
- [ ] Add redaction tests for the Plan 4 `grant` query on report-media URLs and
  ensure access/error logs never include the grant, bearer token, session media
  key, or query string.
- [ ] Run the focused command again; expect all tests to pass. Regenerate
  `docs/api/openapi-v1.json` twice and compare bytes.
- [ ] Commit:
  `git add swinglab/web/jobs.py swinglab/web/app.py swinglab/api/contracts.py swinglab/api/mobile_routes.py swinglab/web/mobile_resources.py tests/test_report_rollout_ops.py tests/test_ops.py tests/test_mobile_capabilities.py tests/test_access_log_privacy.py docs/api/openapi-v1.json`, then
  `git commit -m "feat: expose guided report rollout health"`.

## Task 4: Run and review the deterministic rendered QA matrix

**Files:**

- Consume without duplicating: `scripts/render_guided_report_qa.py` from Plan 3
- Consume without duplicating: `docs/quality/guided-report-rendered-review.md` from Plan 3
- Consume without duplicating: `tests/test_guided_report_accessibility.py`
- Consume without duplicating: `tests/test_guided_report_browser.py`
- Consume without duplicating: `tests/test_guided_report_qa_script.py`
- Consume: `tests/fixtures/report_view/*.json` and `tests/report_view_fixtures.py`
- Create: `docs/qa/guided-report-rendered-release-protocol.md`

**Interfaces:**

- Plan 3 remains the sole owner of Playwright dependencies, fixture rendering,
  fold/reflow assertions, screenshots/PDF generation, and the developer review
  checklist. This plan reruns and signs that output; it does not create a second
  renderer, case registry, or browser harness.
- `python scripts/render_guided_report_qa.py --output <explicit-untracked-path>`
  renders only committed synthetic fixtures and refuses the repository root.
- The release protocol pairs Plan 3's web matrix with Plan 5's native matrix and
  records source commit, generated fixture name, state, evidence filename,
  reviewer label, date, pass/fail, and safety-boundary misunderstanding.
- Required browser states remain desktop, 390-by-844 default/longest copy,
  320-CSS-pixel reflow, 200-percent zoom, large text, reduced motion, keyboard,
  screen reader, no JavaScript, and print. Required semantic states remain
  improve, protect, DTL, limited, visual-unavailable, capture-only, free, Pro,
  guided sample preview, and historical legacy.

- [ ] Run
  `python -m pytest tests/test_guided_report_accessibility.py tests/test_guided_report_browser.py tests/test_guided_report_qa_script.py -q`;
  expect all Plan 3 browser/static tests to pass before collecting evidence.
- [ ] Generate a fresh explicit temporary QA root with
  `python scripts/render_guided_report_qa.py --output <qa-root>`. Assert every
  directory contains only declared synthetic media and that no report contains
  an email, source filename, user ID, bearer/grant value, absolute path, or raw
  pose field.
- [ ] Capture and inspect every state in
  `docs/quality/guided-report-rendered-review.md`. Use browser measurements for
  the opening fold, 320-pixel reflow, 200-percent zoom, focus, target size, and
  print; do not substitute source-string assertions for rendered evidence.
- [ ] Verify the complete priority title, observation, and cue end within the
  390-by-844 default-text owned-report viewport without a sample banner. At large
  text, require reflow/reachable actions and do not enforce the default fold.
- [ ] Verify print expands optional text/tables/static frames, replaces allowed
  video with its associated poster/caption/reference, and keeps locked media
  locked. Verify the default mobile visual is one large focused frame, not a
  multi-panel strip.
- [ ] Render the guided sample into the explicit QA root while
  `cfg.report.get("guided_sample_enabled") is not True` for the running app. Confirm the
  preview is guided but the public `/sample-report/` remains on the prior
  presentation until the separate sample switch is deliberately enabled.
- [ ] Write `docs/qa/guided-report-rendered-release-protocol.md` with the exact
  web/native state registry, evidence fields, reviewer-label privacy rule, and
  stop conditions. Generated PNG/PDF/JSON evidence stays outside Git and is
  attached to the release record.
- [ ] Commit only the protocol:
  `git add docs/qa/guided-report-rendered-release-protocol.md`, then
  `git commit -m "docs: define guided report rendered release review"`.

## Task 5: Make comprehension and release evidence machine-checkable

**Files:**

- Create: `docs/qa/guided-report-comprehension-protocol.md`
- Create: `docs/qa/guided-report-render-checklist.md`
- Create: `docs/qa/guided-report-evidence-schema.json`
- Create: `scripts/validate_guided_report_evidence.py`
- Create: `tests/test_guided_report_evidence.py`

**Interfaces:**

- The protocol uses participant labels `P01` through `P05` or higher, never
  names/contact information.
- Each participant record contains task seconds, four booleans for finding the
  priority/strength, cue, drill, and pass mark, and three booleans for correctly
  describing orange observed, green starting marker, and dashed coaching
  boundary.
- Validation passes only with at least five participants, at least four complete
  four-part discoveries in 30 seconds or less, and zero safety-boundary
  misunderstandings.
- The rendered checklist covers desktop, default mobile, 320 pixels, 200-percent
  zoom, longest-copy mobile, large text, reduced motion, DTL, capture-only,
  visual-unavailable, clean, free, Pro, legacy, and print.

- [ ] Write failing validator tests for four participants, three-of-five task
  success, a 31-second result, missing semantic fields, duplicate participant
  labels, a safety-boundary misunderstanding, an incomplete rendered matrix, and
  one fully passing packet. Expected first failure: validator module absent.
- [ ] Run `python -m pytest tests/test_guided_report_evidence.py -q`; expect import
  failure.
- [ ] Write the protocol as a neutral usability script. The moderator shows the
  unopened optional sections, asks the golfer to point out the four main-path
  items, then asks what each visual treatment means without teaching the answer.
  Record errors and exact misunderstanding categories, not free-form personal
  notes.
- [ ] Implement the validator with explicit errors and no silent defaults:

  ```python
  passed_main_path = sum(
      row["seconds"] <= 30
      and all(row[key] for key in ("priority", "cue", "drill", "pass_mark"))
      for row in participants
  )
  if passed_main_path < 4:
      errors.append("Fewer than four participants completed the main path in 30 seconds.")
  if any(not all(row[key] for key in ("orange_observed", "green_start", "dashed_boundary")) for row in participants):
      errors.append("A safety-boundary misunderstanding blocks release.")
  ```

- [ ] Validate JSON against the committed schema before evaluating acceptance.
  Reject additional properties so report text, names, emails, metric values, or
  other personal notes cannot drift into the evidence file.
- [ ] Run the focused tests; expect all pass. Validate one synthetic passing
  example and one intentionally blocked example from a temporary directory.
- [ ] Commit:
  `git add docs/qa/guided-report-comprehension-protocol.md scripts/validate_guided_report_evidence.py tests/test_guided_report_evidence.py`, then
  `git commit -m "docs: add guided report acceptance protocol"`.

## Task 6: Document independent activation and rollback gates

**Files:**

- Create: `docs/operations/guided-report-rollout.md`
- Create: `tests/test_guided_report_rollout_runbook.py`
- Modify: `README.md`
- Modify: `tests/test_foundation_contracts.py`

**Interfaces:**

- Web rollback sets `report.guided_presentation_enabled=false` or
  `report.guided_presentation_cohort_percent=0` for future jobs. It does not make
  existing v1 bundles unreadable.
- Native rollback sets `report.guided_native_enabled=false`; new builds suppress
  the native guided-report entry/navigation. It does not relabel or downgrade a
  structured guided job to `mode="legacy"`, and existing valid structured bundles
  remain readable through the owned API. Legacy mode remains reserved for verified
  historical jobs that lack the structured-report assignment.
- Public sample activation uses only `report.guided_sample_enabled`; it is
  reported separately and never follows the customer cohort automatically.
- Release evidence records source commit, GitHub `main`, Railway deployment and
  health, active web cohort, native build/store state per platform, and public
  sample state as separate facts.

- [ ] Add a failing runbook-contract test that asserts all three switches,
  preflight, activation, rollback, post-rollback verification, data-retention
  behavior, and separate-state reporting headings exist. Expected first failure:
  missing runbook.
- [ ] Run
  `python -m pytest tests/test_guided_report_rollout_runbook.py tests/test_foundation_contracts.py -q`;
  expect the missing-document failure.
- [ ] Write the runbook with this bounded sequence:

  1. Deploy code with all report switches off.
  2. Verify migrations, `/healthz`, legacy upload/report, backup, and restore.
  3. Enable v1 for synthetic sample in a non-public development environment.
  4. Complete and validate the rendered/moderated evidence packet.
  5. Enable a small stable owned-web cohort and monitor aggregate generation
     failures, artifact recovery, and user support reports.
  6. Increase only after the observation window and acceptance gate pass.
  7. Enable native capability only after the exact released client build passes
     structured, capture-only, media-expiry, and legacy journeys.
  8. Change the public sample only as a separate publication decision.

- [ ] Document rollback verification: create a new session after cohort 0 and
  confirm `premium-coach-v2`; reopen one prior `guided-report-v1` session and one
  historical legacy session; verify both remain owned/readable; verify
  `/healthz`; verify no bundle deletion, allowance change, or background backfill
  occurred.
- [ ] Document stop conditions: core render failure, checksum/recovery failure,
  cross-account or generic-file exposure, native bearer/grant transport failure,
  accessibility blocker, comprehension threshold miss, or any visual-boundary
  misunderstanding.
- [ ] Run the focused tests; expect all pass.
- [ ] Commit:
  `git add docs/operations/guided-report-rollout.md tests/test_guided_report_rollout_runbook.py README.md tests/test_foundation_contracts.py`, then
  `git commit -m "docs: add guided report rollout runbook"`.

## Plan completion gate

- [ ] Run `python -m pytest -q`; expect the complete Python suite to pass.
- [ ] Export OpenAPI twice with `python scripts/export_openapi.py`; compare bytes
  and expect an exact match with `docs/api/openapi-v1.json`.
- [ ] Run the complete Playwright matrix and validate its evidence packet. Attach
  the ignored screenshots/PDFs/JSON to the release record.
- [ ] Run native Jest, lint, typecheck, Expo doctor, and Maestro structured,
  capture-only, legacy, and media-expiry journeys against the same backend commit.
- [ ] Build the production container, start it with presentation disabled, and
  verify `/healthz`, one legacy upload, one legacy report open, and clean shutdown.
- [ ] Repeat locally with a 100-percent development cohort; verify one v1 bundle,
  then return configuration to default-off before committing.
- [ ] Confirm `git status --short` contains no personal media, QA artifacts,
  database files, credentials, or unrelated storefront/mobile-plan edits.
- [ ] Record the implementation commit and verification evidence. Describe the
  feature as code-complete only; do not call it merged, deployed, live, enabled,
  published, or store-released without separately verified evidence for each state.
