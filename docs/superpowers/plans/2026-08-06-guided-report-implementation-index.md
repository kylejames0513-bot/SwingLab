# Guided Swing Report Implementation Index

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved action-first swing report on web and native clients from one trustworthy, versioned server presentation contract while preserving every historical report and rollback path.

**Architecture:** The existing Python analysis, coaching, Caddie Brief, and drill modules remain the source of truth. Plans 1 and 2 add a typed `report-view-v1` presenter and durable evidence bundle. The web template and owned API consume that persisted bundle; the native client consumes only the owned API. A persisted per-job presentation assignment controls rollout without rewriting completed sessions.

**Tech Stack:** Python 3.11+, dataclasses and Pydantic, Pillow, Jinja2, FastAPI, SQLite WAL, pytest, Expo SDK 57, React Native, TypeScript, Jest, Maestro, and the existing same-volume Railway artifact topology.

## Global Constraints

- The approved specification at `docs/superpowers/specs/2026-08-06-guided-swing-report-design.md` is authoritative. If a plan and the specification conflict, stop and reconcile the plan before implementation.
- Existing `swinglab.metrics`, `swinglab.coaching`, `swinglab.caddie_brief`, and `swinglab.drills` decisions remain the golf truth. Presentation code may explain, group, or suppress unsupported evidence; it may not recalculate or invent coaching.
- `report-view-v1` is additive. Historical reports are never backfilled, mutated, or deleted to adopt the new presentation.
- Persist presentation version and entitlement snapshot before analysis begins. A retry must reproduce the same cohort, lock state, bundle shape, and report outcome even if configuration or membership changes later.
- The focused visual uses the golfer's own unmirrored frame. Orange means observed, green means the starting marker, and a separately labeled dashed line means a configured coaching boundary. It must never imply a predicted ideal pose.
- DTL reports are timing/rhythm only. They never render face-on body annotations or a body-reference overlay.
- The full priority explanation, primary drill prescription, and re-film pass mark each have exactly one canonical main-path card. Compact journey links may navigate to them but may not duplicate the full content.
- Raw pose coordinates, source filenames, report copy, private metric values, bearer credentials, and private media locations never enter logs, analytics, notifications, or unauthorized responses.
- Every artifact path is an allowlisted relative path beneath the session root. Reject absolute paths, traversal, symlinks, unknown files, and checksum mismatches.
- Web output remains self-contained and usable without JavaScript or network assets. Native report content remains network-only and is cleared on sign-out, ownership/history-epoch change, or account deletion.
- Preserve the one-replica SQLite and local `/data/sessions` production contract. Do not add Redis, object storage, another database, or a second analysis coordinator.
- Each task starts with a failing test, implements only the behavior needed to pass, runs focused regression tests, and ends in a focused commit.

---

## Ordered plans

1. [View-model contract and presenter](2026-08-06-guided-report-view-model.md)
   defines typed coaching-ready and capture-only variants, trust states, reason
   codes, category rows, authored drill presentation, compatibility adapters,
   and deterministic representative-swing selection.
2. [Evidence pipeline and durable bundle](2026-08-06-guided-report-evidence-bundle.md)
   captures transient evidence before work cleanup, renders the focused frame,
   removes the corrected overlay from new reports, and publishes validated
   HTML/JSON/media/manifests/checksums as one recoverable transaction. It owns
   an injected HTML-writer boundary and a deterministic test writer, not the
   production Jinja implementation.
3. [Web report presentation](2026-08-06-guided-report-web-presentation.md)
   replaces the stick-figure-led layout with the approved action-first reading
   flow, five face-on phase categories, one practice card, one re-film card,
   accessible disclosure, print behavior, regenerated sample fixtures, and the
   exclusive production HTML writer/composition wiring.
4. [Owned API and security](2026-08-06-guided-report-owned-api.md)
   serves typed structured or explicit legacy results, projects short-lived
   owned media links, denies internal artifacts from the generic file route,
   and freezes the OpenAPI/security contract.
5. [Native report integration](2026-08-06-guided-report-native-integration.md)
   renders the same priority, evidence, categories, drill, and target in the
   Expo client with private media, capture-only recovery, legacy fallback,
   Dynamic Type, TalkBack/VoiceOver, and end-to-end coverage.
6. [Cohort rollout and validation](2026-08-06-guided-report-rollout-validation.md)
   persists stable presentation assignment, exposes privacy-safe operational
   state, captures the full rendered matrix, runs moderated comprehension, and
   defines independent web/native/sample activation and rollback evidence.

## Dependency and parallelism map

| Gate | Required completion | Work unlocked |
|---|---|---|
| Contract freeze | Plan 1 complete and its schema fixtures committed | Plan 2 implementation; web/API fixture preparation |
| Durable bundle | Plans 1-2 complete, including crash/retry and lifecycle tests | Plans 3 and 4 in parallel |
| Owned structured API | Plan 4 complete, OpenAPI regenerated twice identically | Plan 5 report transport and media integration |
| Full vertical slice | Plans 1-5 focused suites pass | Plan 6 rendered, moderated, and cohort gates |
| Default-on decision | Plan 6 acceptance evidence signed | Increasing only the new-session web cohort; native/sample remain separate decisions |

Plan 3 and Plan 4 may run concurrently after the durable bundle contract is
green. Native shell, authentication, capture, upload, practice, progress, and
commerce work may continue concurrently, but native report rendering must wait
for Plan 4's exact schema and media authorization contract.

## Shared interfaces that must not drift

### Persisted bundle

```text
<session>/out/<analysis>/
  report.html
  report-view.json
  report-bundle-manifest.json
  report-bundle-checksums.json
  metrics.json
  media/
    focused-s<N>-<phase>.webp
    strip_s<N>.png
    slowmo_s<N>.mp4
    replay_s<N>.mp4        # only when entitled and successfully rendered
```

- `report-view.json` validates as exactly `version="report-view-v1"`.
- `Job.report_presentation_version` is the persisted string
  `premium-coach-v2` for legacy rendering or `guided-report-v1` for this design;
  do not use the JSON schema name as the HTML presentation marker.
- `report-bundle-manifest.json` validates as `report-bundle-v1` and lists only
  files owned by that publish attempt, with canonical path, artifact kind,
  media key, required state, and entitlement visibility. Media role/type stay
  in the typed `MediaEntry`; file size and identity stay in checksums.
- `report-bundle-checksums.json` contains SHA-256 values for every manifest file
  except itself and is checked during publication, restore validation, and
  recovery.
- Scratch attempts live under the same session volume and are never visible
  through report or generic-file routes.

The shared artifact loader is
`load_published_bundle(session_dir, *, report_rel, report_view_rel, manifest_rel,
checksums_rel) -> PublishedReportBundle`; media authorization resolves only
opaque keys through `resolve_media_path(bundle, media_key) -> Path`. `Job` stores
`report_view_rel`, `report_manifest_rel`, `report_checksums_rel`, and
`structured_report` beside its existing `report_rel` compatibility pointer.

### Report outcomes and trust

- Persisted report outcomes are `coaching_ready` or `capture_only`;
  `refilm_required` is the capture-only trust state, not a third outcome.
- Coaching trust is `clear` or `limited`; an isolated media-render failure uses
  `visual_unavailable` while retaining supported text coaching.
- Capture-only reason codes are the closed enum from the approved specification:
  `secondary_metric_unavailable`, `target_direction_uncertain`,
  `hand_landmarks_unreliable`, `event_estimate_limited`,
  `focused_media_render_failed`, `camera_angle_mismatch`,
  `tracking_unstable`, `insufficient_pose_frames`, `no_readable_swing`,
  `no_reliable_strike_event`, and `priority_evidence_unreliable`.
- An unknown version or reason code fails closed; clients do not partially infer
  a result.

### Owned mobile API

- Exact route: `GET /api/v1/sessions/{session_id}/report-view`.
- Current structured result: `{resource_version: 1, mode: "structured", report_view: ...}`.
- Historical result without a valid view artifact: `{resource_version: 1, mode: "legacy", legacy_report_url: ...}`.
- Every success is owner-authorized and `Cache-Control: no-store`; cross-account,
  missing, malformed, or unpublishable sessions remain non-enumerating.
- The API returns media capabilities/owned references, never filesystem paths,
  symlink-resolved locations, raw manifests, checksums, or pose coordinates.

## Coordination with the existing mobile plans

The mobile plans under `docs/superpowers/plans/2026-08-06-caddieinsight-*` remain
authoritative for shell, authentication, upload, practice, progress, commerce,
and store release. This report index makes these scoped amendments:

- Mobile Backend Foundation Task 1 owns the Pydantic base, OpenAPI exporter, and
  deterministic snapshot. Guided Report Plan 4 adds report-view models and the
  exact endpoint to those same files; it must not create a second contract stack.
- Mobile Backend Foundation Task 4 owns `/api/v1/capabilities`. Plan 4 adds the
  structured-report capability to that response; Plan 6 controls its activation.
- Expo Coaching Client Task 7's Brief portion is superseded by Guided Report Plan
  5. Its Practice, matched re-film, and Progress work remains in force. The
  resulting `BriefScreen` consumes `report-view-v1`; it must not fetch report
  HTML/metrics and invent a second coaching hierarchy.
- Beta and Store Release remains the authority for store submission. Completing
  this index does not authorize TestFlight, Play Console, App Store, Google Play,
  Railway, Shopify, or public-sample publication.

## Integration checkpoints

### Checkpoint A — contract and presenter

- [ ] Run the Plan 1 focused suite and schema-fixture validation.
- [ ] Confirm every reason code and report variant is represented by a committed
  fixture and unknown fields/versions fail closed.
- [ ] Confirm representative selection is deterministic for threshold crossing,
  session mean, consistency, shoulder-tilt delta, maintenance, and tie cases.

### Checkpoint B — durable publication

- [ ] Run Plan 2 pipeline, crash, retry, backup/restore, privacy, history-reset,
  retention, and checksum suites.
- [ ] Kill a test publish after staging, after replace, and before the final
  database commit. Restart and verify no partial report is externally visible,
  no allowance is consumed, and retry keeps the original presentation/entitlement.
- [ ] Confirm new DTL reports and all new-presentation jobs omit the corrected
  body overlay while legacy persisted reports still open.

### Checkpoint C — web and API

- [ ] Run Plans 3 and 4 independently, then together against the same fixture set.
- [ ] Diff server-owned priority, statuses, drill, and pass target across HTML
  and JSON projections; require exact semantic equality.
- [ ] Confirm the generic file route denies report-view, manifest, checksums,
  scratch, and any non-allowlisted media even to the session owner.

### Checkpoint D — native

- [ ] Regenerate `docs/api/openapi-v1.json` and
  `mobile/src/api/schema.generated.ts`; confirm no handwritten shadow types.
- [ ] Run Jest at iOS accessibility sizes and Android font scale 2.0, then the
  Maestro structured, capture-only, visual-unavailable, legacy, and expired-media
  journeys.
- [ ] Confirm sign-out and history-epoch changes remove all private report/media
  cache entries.

### Checkpoint E — release evidence

- [ ] Complete Plan 6's desktop, 390-by-844, 320-CSS-pixel, 200-percent zoom,
  large-text, reduced-motion, print, DTL, capture-only, clean, free, Pro, legacy,
  and longest-copy matrix.
- [ ] Run the moderated test with at least five beginner or improving golfers.
  Require four of five to find the priority/strength, cue, drill, and pass mark
  within 30 seconds and block release on any safety-boundary misunderstanding.
- [ ] Record source commit, GitHub merge, deployed web cohort, native build/store
  state, and public sample state as separate facts.

## Final repository gate

- [ ] Run `python -m pytest -q`; expect all Python tests to pass.
- [ ] From `mobile/`, run `npm test -- --runInBand`, `npm run lint`,
  `npm run typecheck`, and `npx expo-doctor`; expect all checks to pass.
- [ ] Export OpenAPI twice and compare bytes; expect no drift.
- [ ] Build the production container and verify `/healthz` with report presentation
  disabled and with a development cohort enabled; legacy upload/report flow stays
  operational in both cases.
- [ ] Inspect `git diff main...HEAD --name-only`; confirm there are no credentials,
  SQLite files, personal videos, generated sessions, local QA screenshots, or
  unrelated storefront changes.
- [ ] Preserve focused commits and the legacy-renderer rollback boundary. Do not
  merge, deploy, increase a cohort, change the public sample, or submit a native
  build without the separately required authorization for that action.
