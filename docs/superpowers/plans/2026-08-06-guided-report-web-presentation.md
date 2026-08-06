# Guided Report Web Presentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the approved `report-view-v1` coaching journey as one self-contained, accessible, printable web report; prepare a default-off guided public-sample path; and retain the current HTML renderer and sample as rollback defaults.

**Architecture:** Workstreams 1 and 2 first produce a typed `ReportDocument` containing the persisted `ReportViewV1`, server-only optional-depth content, a validated media-key map, and an injected `ReportHtmlWriter` boundary exercised with a deterministic test writer. This plan exclusively adds the real strict Jinja writer and `report_guided.html.j2`, then injects that writer at the FastAPI `JobManager` composition root; it never recomputes a priority, status, drill, target, evidence choice, entitlement, or media path. The current `write_report_html(...)` function and `report.html.j2` stay intact for historical/legacy rendering, while the bundle orchestrator calls the injected guided writer only for jobs already assigned `guided-report-v1`.

**Tech Stack:** Python 3.11+, dataclasses, Jinja2 with `StrictUndefined` and autoescape, inline HTML/CSS, Playwright Chromium for layout/print checks, Pillow for the existing synthetic sample imagery, and pytest.

## Global Constraints

- Workstreams 1 and 2 are prerequisites. Do not start this plan until `swinglab/report_view.py`, `swinglab/report_presenter.py`, `ReportDocument`, and validated focused-media entries exist.
- Render only fields supplied by `ReportDocument`. Web code may map an enum to an icon or display label, but it must not infer coaching severity, phase status, evidence eligibility, a drill, a pass mark, an entitlement, or a media filename.
- Preserve `REPORT_FORMAT_VERSION = "caddie-brief-v1"`, the outcome marker, and the coaching-priority marker in the first 8 KiB. Emit `document.view.presentation_version` unchanged; guided Jinja never selects or rewrites it.
- Keep `swinglab.report.REPORT_PRESENTATION_VERSION = "premium-coach-v2"` as the legacy marker and leave `swinglab/templates/report.html.j2` available as the rollback renderer.
- The guided report remains offline and readable without page JavaScript: no remote font, stylesheet, script, image, or media dependency; no client-side state machine; no autoplay.
- Coaching-ready output has exactly one canonical priority/strength card, one canonical primary-practice card, and one canonical re-film/pass-mark card. Compact journey links may not repeat the full drill or target.
- Face-on renders the five server-ordered phases; down-the-line renders only the supplied `timing_rhythm` row. The template does not synthesize missing phase rows.
- Capture-only suppresses diagnosis, phases, corrective evidence, drills, targets, coach replay, and commerce. It renders only the server-supplied reason, correction, checklist, safe playback, and actions.
- Never render the legacy corrected full-body overlay in the guided report or public sample. A focused image is observed evidence, a starting reference, or a coaching boundary—not a predicted ideal pose.
- Print expands text, tables, static evidence, and instructional diagrams. Video prints its associated allowed poster, caption, and owned-screen playback reference. Locked media remains a lock explanation and never resolves to a hidden path.
- Target WCAG 2.2 AA: 4.5:1 normal text, 3:1 large text and essential graphical/control boundaries, one `h1`, logical headings, text plus icon/shape statuses, descriptive alt text, 44-by-44 CSS-pixel controls, a visible unobscured focus indicator at least two CSS pixels thick, reduced motion, 320 CSS-pixel reflow, and 200 percent zoom.
- The 390-by-844 opening-fold check excludes the public sample banner and applies only at default text size. At large text, complete reflow and reachable actions take precedence over the fold.
- Keep the public sample on `premium-coach-v2` by default. This plan may render an explicit guided preview in a temporary directory, but only the strict boolean `cfg.report.get("guided_sample_enabled") is True` may change the served sample; code completion, deployment, customer-cohort activation, and sample activation remain separate facts.
- Keep final product-owner sign-off, moderated comprehension, cohort activation, and rollback operations in workstream 6. This plan produces deterministic fixtures, automated gates, screenshots/PDFs, and the review checklist those rollout steps consume.
- Do not modify the approved design spec or any native/mobile plan while implementing this plan.

## Existing Boundaries to Preserve

- `swinglab/report.py:34-38` owns the current format, presentation, outcome, and priority-marker constants; `swinglab/report.py:146-330` is the public legacy compatibility writer.
- `swinglab/templates/report.html.j2:1-1314` is the current self-contained legacy template and rollback surface. Its current print rules are at lines 191-213 and 677-735.
- `swinglab/sample.py:200-259` draws the existing synthetic strips/overlays; `swinglab/sample.py:262-310` performs presentation-aware atomic sample refresh.
- `swinglab/web/app.py:629-633,1378-1392` generates and serves the public sample. The route and traversal boundary do not need to change.
- `tests/test_report.py:62-369` and `tests/test_premium_report.py:36-143` freeze legacy branding, marker, hierarchy, print, escaping, and capture-only behavior.
- `tests/test_sample_report.py:17-123` freezes public sample generation, idempotency, marker refresh, routing, traversal blocking, and branding.
- `pyproject.toml:21-43` contains optional development dependencies; `.github/workflows/ci.yml:34-49` installs test dependencies and runs pytest.

---

## Task 1: Freeze the HTML-facing document contract and add the guided renderer seam

**Files:**

- Consume without redefining: `swinglab/report_view.py`
- Consume without redefining: `swinglab/report_presenter.py`
- Consume without redefining: `swinglab/report_bundle.py`
- Create: `swinglab/report_html.py`
- Create: `swinglab/templates/report_guided.html.j2`
- Modify: `swinglab/web/app.py:82-121,475-510`
- Modify: `tests/report_view_fixtures.py` (created by workstream 1)
- Create: `tests/test_guided_report_html.py`
- Create: `tests/test_guided_report_web_composition.py`
- Regression test unchanged: `tests/test_premium_report.py:36-58`

**Interfaces:**

- Consumes `ReportDocument(view: ReportViewV1, depth: ReportDepthContent, media_by_key: Mapping[str, MediaEntry])`.
- Requires `ReportDepthContent` fields `swings`, `secondary_findings`, `strengths`, `measurements`, `session_details`, `glossary`, `limitations`, `gear`, and `navigation`.
- Requires `SwingDetail.video_poster_media_key` and `SwingDetail.video_poster_alt_text` in addition to the key-position, slow-motion, and replay associations. The contract test fails if either print field is absent.
- Consumes `tests.report_view_fixtures.report_document_fixture(name="coaching-improve-clear") -> ReportDocument` and extends its named variants; it does not duplicate the persisted schema as ad hoc dictionaries.
- Implements `swinglab.report_bundle.ReportHtmlWriter` structurally. Plan 2 owns only that protocol and its deterministic test writer; this plan is the sole owner of customer-facing Jinja and production renderer composition.
- Produces `write_report_document_html(out_path: Path, document: ReportDocument, *, cfg: Config, sample_banner: dict | None = None) -> Path` in `swinglab.report_html`.
- Produces `GUIDED_TEMPLATE = "report_guided.html.j2"`. It imports `GUIDED_REPORT_PRESENTATION_VERSION = "guided-report-v1"` from `swinglab.report_view` and rejects a different presentation before writing.
- Injects `write_report_document_html` through `JobManager(..., guided_html_writer=write_report_document_html)` only at `swinglab.web.app.create_app`. `JobManager`, `analyze_video`, and `build_report_bundle` retain Plan 2's fail-closed null-writer behavior; no test writer enters app composition.
- Resolves every Jinja media reference exclusively through `document.media_by_key[key].relative_path`. A missing key raises `ValueError("guided report references unknown media key: <key>")`; Jinja never constructs `media/...` paths.

- [ ] Add a failing contract test before renderer code. It must freeze the HTML depth and poster associations explicitly:

  ~~~python
  from dataclasses import fields

  from swinglab.report_presenter import (
      ReportDepthContent,
      ReportDocument,
      SwingDetail,
  )

  def field_names(model: type) -> set[str]:
      return {field.name for field in fields(model)}

  def test_report_document_exposes_all_server_owned_html_depth():
      assert field_names(ReportDocument) == {"view", "depth", "media_by_key"}
      assert {
          "swings", "secondary_findings", "strengths", "measurements",
          "session_details", "glossary", "limitations", "gear", "navigation",
      } <= field_names(ReportDepthContent)
      assert {
          "key_positions_media_key", "key_positions_alt_text",
          "slow_motion_media_key", "slow_motion_caption",
          "coach_replay_media_key", "coach_replay_caption",
          "video_poster_media_key", "video_poster_alt_text",
          "print_playback_reference", "replay_locked",
          "locked_replay_explanation",
      } <= field_names(SwingDetail)
  ~~~

- [ ] Add a failing renderer test that calls `write_report_document_html` with `report_document_fixture()` and expects one format marker, `guided-report-v1`, the supplied outcome, and the supplied relative focused-media path in the first render. Also assert the legacy writer still emits `premium-coach-v2`.
- [ ] Add a failing media-integrity test that replaces a rendered evidence `media_key` with `"missing-priority"` and expects the exact `ValueError` above before `out_path` exists.
- [ ] In `tests/test_guided_report_html.py`, add `render_fixture_path(tmp_path, name, *, sample_banner=None) -> Path` and `render_fixture(tmp_path, name, *, sample_banner=None) -> str`. Both call `report_document_fixture(name)` and the guided writer; later static/browser tests import these helpers rather than inventing new render setup.
- [ ] Add a failing production-composition test in `tests/test_guided_report_web_composition.py`. Build `Config()` with `cfg.report["guided_presentation_enabled"] = True`, replace only `swinglab.web.jobs.analyze_video` with a recording fake, call `create_app(..., start_shopify_sync_worker=False)`, create a session without an explicit presentation override, and run that job synchronously. Assert the persisted assignment is `guided-report-v1` and the analyzer receives `guided_html_writer is swinglab.report_html.write_report_document_html`, never `tests.report_bundle_fixtures.write_test_report_html`. The fake may raise after recording so this test exercises composition without duplicating Plan 2's bundle mechanics; Plan 2's pipeline test already proves that exact object is forwarded to `build_report_bundle(html_writer=...)`.
- [ ] Run `python -m pytest tests/test_guided_report_html.py tests/test_guided_report_web_composition.py tests/test_premium_report.py -q`. Expected first failure: `ModuleNotFoundError: No module named 'swinglab.report_html'` or the production composition still constructs `JobManager` without a guided writer.
- [ ] Implement the strict writer in `swinglab/report_html.py`. Keep presentation-only labels centralized and immutable:

  ~~~python
  from __future__ import annotations

  from pathlib import Path
  from types import MappingProxyType

  from jinja2 import (
      Environment,
      FileSystemLoader,
      StrictUndefined,
      select_autoescape,
  )

  from .coaching import priority_rule_version
  from .config import Config
  from .report import REPORT_FORMAT_VERSION
  from .report_presenter import ReportDocument
  from .report_view import GUIDED_REPORT_PRESENTATION_VERSION

  GUIDED_TEMPLATE = "report_guided.html.j2"
  STATUS_ICONS = MappingProxyType({
      "priority": "●",
      "review_later": "△",
      "steady": "✓",
      "baseline": "◆",
      "not_measured": "—",
  })
  PHASE_METHOD_LABELS = MappingProxyType({
      "opening_baseline": "Address from opening setup",
      "highest_tracked_hands": "Top from highest hand position",
      "detected_audio": "Impact estimated from sound",
      "manual_strike": "Impact marked by you",
      "configured_finish_offset": "Finish after impact",
      "session_timing": "Measured swing timing",
  })

  def _media_path(document: ReportDocument, key: str) -> str:
      entry = document.media_by_key.get(key)
      if entry is None:
          raise ValueError(
              f"guided report references unknown media key: {key}"
          )
      return entry.relative_path

  def write_report_document_html(
      out_path: Path,
      document: ReportDocument,
      *,
      cfg: Config,
      sample_banner: dict | None = None,
  ) -> Path:
      if (
          document.view.presentation_version
          != GUIDED_REPORT_PRESENTATION_VERSION
      ):
          raise ValueError("guided renderer requires guided-report-v1")
      env = Environment(
          loader=FileSystemLoader(Path(__file__).parent / "templates"),
          autoescape=select_autoescape(["html", "j2"]),
          undefined=StrictUndefined,
      )
      optional_by_id = {
          section.id: section
          for section in document.view.optional_sections
      }
      html = env.get_template(GUIDED_TEMPLATE).render(
          brand=cfg.brand,
          document=document,
          view=document.view,
          depth=document.depth,
          navigation=document.depth.navigation,
          optional_by_id=optional_by_id,
          media_path=lambda key: _media_path(document, key),
          status_icons=STATUS_ICONS,
          phase_method_labels=PHASE_METHOD_LABELS,
          report_format_version=REPORT_FORMAT_VERSION,
          priority_rule_version=priority_rule_version(cfg),
          sample_banner=sample_banner,
      )
      out_path.write_text(html, encoding="utf-8")
      return out_path
  ~~~

- [ ] Create a minimal valid `report_guided.html.j2` shell with all compatibility markers before style/content so they remain inside the first 8 KiB. Use one `main`, one `h1` selected from `next_move.title` or `capture_guidance.reason_label`, and no external resource tags:

  ~~~jinja2
  <!doctype html>
  <html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="caddieinsight-report-format"
          content="{{ report_format_version }}">
    <meta name="caddieinsight-report-presentation"
          content="{{ view.presentation_version }}">
    <meta name="caddieinsight-report-outcome"
          content="{{ view.outcome }}">
    <meta name="caddieinsight-coaching-priority-rule"
          content="{{ priority_rule_version }}">
    <title>{{ brand.name }} — Swing report</title>
    <style>
      :root {
        --brand-primary: {{ brand.primary_color }};
        --brand-accent: {{ brand.accent_color }};
        --ink: #17201a;
        --muted: #5d685f;
        --paper: #fffdf8;
        --canvas: #f3f1eb;
        --accent-text: #944600;
        --control-border: #66736b;
        --focus: #005fcc;
      }
      * { box-sizing: border-box; }
      body { margin: 0; color: var(--ink); background: var(--canvas); }
      main { width: min(100% - 28px, 72rem); margin: auto; }
    </style>
  </head>
  <body>
    {% if sample_banner %}
    <aside class="sample-banner" aria-label="Sample report">
      <span>{{ sample_banner.text }}</span>
      <a href="{{ sample_banner.cta_url }}">{{ sample_banner.cta_label }}</a>
    </aside>
    {% endif %}
    <main id="report-main">
      {% if view.outcome == "coaching_ready" %}
      <h1>{{ view.next_move.title }}</h1>
      {% else %}
      <h1>{{ view.capture_guidance.reason_label }}</h1>
      {% endif %}
    </main>
  </body>
  </html>
  ~~~

- [ ] Wire the production composition root after the writer exists; do not add a default inside Plan 2's protocol, bundle builder, analyzer, or `JobManager`:

  ~~~python
  from ..report_html import write_report_document_html

  manager = JobManager(
      sessions_dir,
      cfg,
      user_store=users,
      guided_html_writer=write_report_document_html,
  )
  ~~~

- [ ] Rerun `python -m pytest tests/test_guided_report_html.py tests/test_guided_report_web_composition.py tests/test_premium_report.py -q`. Expected result: the real writer satisfies Plan 2's injected boundary, guided app-created jobs retain it, all renderer-seam tests pass, and legacy output remains byte-contract compatible.
- [ ] Commit: `git add swinglab/report_html.py swinglab/templates/report_guided.html.j2 swinglab/web/app.py tests/report_view_fixtures.py tests/test_guided_report_html.py tests/test_guided_report_web_composition.py && git commit -m "feat: compose guided report HTML renderer"`.

## Task 2: Render the coaching-ready action-first journey once

**Files:**

- Modify: `swinglab/templates/report_guided.html.j2`
- Modify: `tests/report_view_fixtures.py`
- Modify: `tests/test_guided_report_html.py`

**Interfaces:**

- Consumes only `view.context`, `view.trust`, `view.next_move`, `view.visual_evidence`, server-ordered `view.phases`, `view.practice`, and `view.refilm` for the main path.
- Uses `PhaseSummary.expanded_by_default` verbatim. It does not compare phase IDs to the priority to decide which row opens.
- Uses `RenderedEvidence.media_key` through `media_path(...)`; `UnavailableEvidence` renders no `img` and retains its observation, method, tracking state, supporting-measurement link, and render-failure explanation.
- Produces stable hooks `data-report-block="next-move"`, `"understand"`, `"practice"`, and `"refilm"` plus `data-field="priority"`, `"observation"`, `"cue"`, `"drill-name"`, and `"pass-mark"` for structural and rendered checks.

- [ ] Extend `report_document_fixture(name=...)` with exact named cases: `coaching-improve-clear-long-copy`, `coaching-protect-clear`, `coaching-improve-limited`, `coaching-improve-visual-unavailable`, and `coaching-dtl-clear`. Use `dataclasses.replace` on Plan 1's typed fixture so every variant still passes `report_view_to_dict`/`report_view_from_dict`.
- [ ] Add failing tests for the complete order and canonical-card count. The long-copy fixture must assert:

  ~~~python
  def positions(html: str, *needles: str) -> tuple[int, ...]:
      return tuple(html.index(needle) for needle in needles)

  def test_coaching_report_has_one_action_first_journey(tmp_path):
      document = report_document_fixture(
          name="coaching-improve-clear-long-copy"
      )
      html = render_guided(tmp_path, document)
      order = positions(
          html,
          'data-report-block="next-move"',
          'data-report-block="understand"',
          'data-report-block="practice"',
          'data-report-block="refilm"',
      )
      assert order == tuple(sorted(order))
      assert html.count('data-canonical="priority"') == 1
      assert html.count('data-canonical="practice"') == 1
      assert html.count('data-canonical="refilm"') == 1
      assert html.count(document.view.practice.name) == 1
      assert html.count(document.view.refilm.target.text) == 1
  ~~~

- [ ] Add failing improve/protect tests: improve displays `next_move.eyebrow`; protect displays `Protect this` and `Strength to protect` while its phase keeps the supplied ordinary `Steady` label. Neither test may search metrics or recompute status.
- [ ] Add a failing focused-evidence test requiring swing number, phase, human-readable phase method, tracking-state text, detected/readable/triggered swing counts, observed label, optional reference/boundary labels, plain-language callout, nonempty `alt`, and a `See measurement` disclosure when `supporting_measurement` is present. Omit the triggered count only when the supplied value is null; never infer it from metrics.
- [ ] Add a failing DTL provenance test that renders each supplied `EventProvenance` label, timestamp, and method in order. Assert the HTML contains the persisted address/top/impact/finish timestamps and does not calculate any timestamp from backswing/downswing duration.
- [ ] Add a failing visual-unavailable test requiring the literal heading `Visual unavailable`, the same observation and method/tracking text, no focused `img`, and the supplied reason/explanation. It must not substitute any `depth.swings` image.
- [ ] Add a failing phase test requiring exactly five face-on `details.phase-card` elements in supplied order, exactly one `open` row for improve/protect, visible status label plus an `aria-hidden` icon, and one DTL `timing_rhythm` row with no face-on phase labels. The limited fixture must show `Not measured` plus the server-supplied phase summary/reason.
- [ ] Run `python -m pytest tests/test_guided_report_html.py -q`. Expected failures: missing guided journey blocks and evidence/phase markup.
- [ ] Replace the shell body with a compact context header and canonical next-move card. The preview links name the three steps but never include `practice.name` or `refilm.target.text`:

  ~~~jinja2
  <a class="skip-link report-control" href="#report-main">Skip to report</a>
  <header class="report-header">
    <span class="report-brand">{{ brand.name | upper }}</span>
    <dl class="context-list" aria-label="Swing context">
      {% if view.context.club_label %}
      <div><dt>Club</dt><dd>{{ view.context.club_label }}</dd></div>
      {% endif %}
      <div><dt>Hand</dt><dd>{{ view.context.hand | title }}</dd></div>
      <div><dt>Camera</dt><dd>{{ view.context.angle_label }}</dd></div>
      <div><dt>Detected</dt><dd>{{ view.context.detected_swings }}</dd></div>
      <div>
        <dt>Priority readable</dt>
        <dd>{{ view.context.priority_readable_swings }}</dd>
      </div>
      <div><dt>Trust</dt><dd>{{ view.trust.label }}</dd></div>
    </dl>
  </header>
  <main id="report-main">
    <section class="next-move" data-report-block="next-move"
             data-canonical="priority" aria-labelledby="report-title">
      <p class="eyebrow">{{ view.next_move.eyebrow }}</p>
      <h1 id="report-title" data-field="priority">
        {{ view.next_move.title }}
      </h1>
      <p class="observation" data-field="observation">
        {{ view.next_move.observation }}
      </p>
      <p class="coach-cue" data-field="cue">
        <strong>Coach cue:</strong> {{ view.next_move.cue }}
      </p>
      <ol class="journey-preview" aria-label="Your report journey">
        <li><a class="report-control" href="#understand">Understand</a></li>
        <li><a class="report-control" href="#practice">Practice</a></li>
        <li><a class="report-control" href="#refilm">Re-film</a></li>
      </ol>
    </section>
  ~~~

- [ ] Render `#understand` with one large evidence treatment followed by vertical phase disclosures. Use the server's `expanded_by_default` and status copy exactly:

  ~~~jinja2
  <section id="understand" data-report-block="understand"
           aria-labelledby="understand-heading">
    <p class="step-label">1 · Understand</p>
    <h2 id="understand-heading">See it in your swing</h2>
    {% if view.visual_evidence.state == "rendered" %}
    <figure class="focused-evidence">
      <img src="{{ media_path(view.visual_evidence.media_key) }}"
           alt="{{ view.visual_evidence.alt_text }}">
      <figcaption>
        <p>
          Swing {{ view.visual_evidence.swing }} ·
          {{ view.visual_evidence.phase | replace("_", " ") | title }} ·
          {{ phase_method_labels[view.visual_evidence.phase_method] }}
        </p>
        <p><strong>Observed:</strong>
          {{ view.visual_evidence.observed_label }}</p>
        {% if view.visual_evidence.reference_label %}
        <p><strong>Starting reference:</strong>
          {{ view.visual_evidence.reference_label }}</p>
        {% endif %}
        {% if view.visual_evidence.boundary_label %}
        <p><strong>Coaching boundary:</strong>
          {{ view.visual_evidence.boundary_label }}</p>
        {% endif %}
        <p><strong>Tracking:</strong>
          {{ view.visual_evidence.tracking_state | title }}</p>
        <p>{{ view.visual_evidence.observation }}</p>
      </figcaption>
    </figure>
    {% else %}
    <article class="visual-unavailable" role="note">
      <h3>Visual unavailable</h3>
      <p>{{ view.visual_evidence.observation }}</p>
      <p><strong>Phase selected by:</strong>
        {{ phase_method_labels[view.visual_evidence.phase_method] }}</p>
      <p><strong>Tracking:</strong>
        {{ view.visual_evidence.tracking_state | title }}</p>
      {% if view.trust.explanation %}<p>{{ view.trust.explanation }}</p>{% endif %}
    </article>
    {% endif %}

    <div class="phase-list" aria-label="Whole-swing breakdown">
      {% for phase in view.phases %}
      <details class="phase-card phase-card--{{ phase.status }}"
               {% if phase.expanded_by_default %}open{% endif %}>
        <summary>
          <span>{{ phase.label }}</span>
          <span class="status">
            <span aria-hidden="true">{{ status_icons[phase.status] }}</span>
            {{ phase.status_label }}
          </span>
        </summary>
        <p>{{ phase.summary }}</p>
        <p>{{ phase.readable_swings }} readable swing{% if phase.readable_swings != 1 %}s{% endif %}</p>
      </details>
      {% endfor %}
    </div>
  </section>
  ~~~

- [ ] Put a technical value only inside its matching measurement disclosure. Link `next_move.measurement_detail_id` and `visual_evidence.supporting_measurement.id` to the same server-supplied ID; do not copy the value into the opening card.
- [ ] Add a collapsed `What this video can measure` disclosure from server-owned limitations and keep one short boundary beside focused evidence: observed/reference marks are 2D phone-video evidence, not a personalized ideal pose.
- [ ] For `tempo_timeline` evidence, render `visual_evidence.events` as a semantic ordered list with each event's supplied `label`, `timestamp_ms`, and `phase_method_labels[event.method]`. Do not infer or reorder events.
- [ ] Render one canonical `#practice` card from `PracticePrescription`: drill name, aim, three authored `summary_steps` in order, setup, feel cue, dosage, equipment, optional instructional illustration labeled `Instructional illustration — not your measured pose`, a disclosure for `full_steps` only when it adds content, and a secondary `Try a different drill` disclosure for alternatives.
- [ ] Render one canonical `#refilm` card from `RefilmProtocol`: supplied checklist order, `target.text` exactly once, primary action label, and explicit same-club/hand/angle/height/framing/effort confirmations. Practice links to `#refilm` without repeating `target.text`. When `navigation.app_url` is absent, render the action label as an offline note rather than a dead `href`.
- [ ] Rerun `python -m pytest tests/test_guided_report_html.py -q`. Expected result: all coaching-ready, protect, limited, visual-unavailable, and DTL cases pass.
- [ ] Commit: `git add swinglab/templates/report_guided.html.j2 tests/report_view_fixtures.py tests/test_guided_report_html.py && git commit -m "feat: render guided coaching journey"`.

## Task 3: Render capture recovery and optional depth without entitlement leaks

**Files:**

- Modify: `swinglab/templates/report_guided.html.j2`
- Modify: `tests/report_view_fixtures.py`
- Modify: `tests/test_guided_report_html.py`

**Interfaces:**

- Capture-only consumes only `view.capture_guidance`, `capture_guidance.safe_media_keys`, matching `depth.swings` captions/posters, `document.media_by_key`, context, trust, navigation, and brand/disclaimer copy.
- Optional depth consumes `view.optional_sections` as the availability/lock index and typed `depth` payloads. It never derives an optional section from array length alone.
- `depth.swings` owns the association among a swing number, media keys, alt text, captions, replay lock explanation, video poster, and print playback reference.
- Locked media keys are absent from `document.media_by_key`. Template branches on `locked`/`replay_locked` before calling `media_path(...)`.

- [ ] Extend fixtures with `capture-only-angle`, `capture-only-tracking`, `capture-only-no-readable-swing`, `free-locked`, and `pro-unlocked`. Each must round-trip its `view` through the Plan 1 serializer/parser.
- [ ] Add a failing parameterized capture-only test for each supported primary reason. Assert reason label, explanation, correction, checklist order, primary/secondary action labels, and safe playback are present; assert `data-report-block="understand"`, `practice`, `refilm`, phase status text, corrective evidence, coach replay, `Pass mark`, and every gear URL are absent.
- [ ] Add a failing retry-failure fixture/test: the prior capture-only reason remains visible, the primary action remains reachable, and the support/choose-video secondary action appears without partial coaching.
- [ ] Add failing optional-depth tests for every section ID: `every_swing`, `replay`, `secondary_findings`, `alternative_drills`, `more_strengths`, `measurements`, `glossary`, and `gear`. Available content must use the matching typed payload; unavailable content stays absent; locked content shows only a lock explanation.
- [ ] Add a failing free/Pro test that gives the locked fixture a sentinel replay filename in no public field and asserts the filename never appears in HTML. The unlocked fixture must resolve the exact media-map path and caption.
- [ ] Add a failing safety test proving `secondary_findings` cannot create another practice plan and `gear` appears after the complete primary practice prescription. Capture-only must never render `depth.gear` even if a malicious fixture includes it.
- [ ] Run `python -m pytest tests/test_guided_report_html.py -q`. Expected failures: capture recovery and optional disclosures are absent.
- [ ] Add a separate capture-only body branch in Jinja. Keep it short and ordered:

  ~~~jinja2
  {% if view.outcome == "capture_only" %}
  <section class="capture-recovery" data-report-block="capture-recovery"
           aria-labelledby="report-title">
    <p class="eyebrow">{{ view.trust.label }}</p>
    <h1 id="report-title">{{ view.capture_guidance.reason_label }}</h1>
    <p>{{ view.capture_guidance.explanation }}</p>
    <div class="capture-correction">
      <h2>Change this for the next video</h2>
      <p>{{ view.capture_guidance.correction }}</p>
    </div>
    <section aria-labelledby="capture-checklist">
      <h2 id="capture-checklist">Quick filming checklist</h2>
      <ul>
        {% for item in view.capture_guidance.checklist %}
        <li>{{ item }}</li>
        {% endfor %}
      </ul>
    </section>
    <div class="capture-actions">
      {% if navigation.app_url %}
      <a class="button report-control" href="{{ navigation.app_url }}">
        {{ view.capture_guidance.primary_action_label }}
      </a>
      {% if view.capture_guidance.secondary_action_label %}
      <a class="text-action report-control" href="{{ navigation.app_url }}">
        {{ view.capture_guidance.secondary_action_label }}
      </a>
      {% endif %}
      {% else %}
      <p class="offline-action" role="note">
        {{ view.capture_guidance.primary_action_label }} in CaddieInsight
        when you are back online.
      </p>
      {% endif %}
    </div>
  </section>
  {% endif %}
  ~~~

- [ ] Render safe capture playback only when its key appears in `capture_guidance.safe_media_keys` and the matching `SwingDetail` provides its caption/poster association. Do not render notes, metrics, annotations, or replay from that swing.
- [ ] Add one native `details`/`summary` disclosure per available or locked optional section. Use the server label and item count in `summary`. Preserve this content mapping:

  - `every_swing` → `depth.swings` key positions, notes, and swing-level measurements.
  - `replay` → slow motion and allowed coach replay; locked replay → `locked_replay_explanation`.
  - `secondary_findings` → `depth.secondary_findings` only.
  - `alternative_drills` → `view.practice.alternatives` only.
  - `more_strengths` → `depth.strengths` only.
  - `measurements` → `depth.session_details`, `depth.measurements`, and `depth.limitations` in accessible tables/lists.
  - `glossary` → `depth.glossary` as a description list.
  - `gear` → `depth.gear` after Practice, only when coaching-ready and the section is available/unlocked.

- [ ] Give every table a visible or visually hidden caption; wrap technical tables in `role="region"`, an explicit `aria-label`, and `tabindex="0"`. Use row/column `scope` and preserve `MeasurementDetail.explanation` and `limitation`.
- [ ] Rerun `python -m pytest tests/test_guided_report_html.py -q`. Expected result: all capture-only, optional-depth, free/Pro, and no-leak assertions pass.
- [ ] Commit: `git add swinglab/templates/report_guided.html.j2 tests/report_view_fixtures.py tests/test_guided_report_html.py && git commit -m "feat: add guided report depth and capture recovery"`.

## Task 4: Enforce accessibility, reflow, no-JavaScript, and print behavior in a real browser

**Files:**

- Modify: `swinglab/templates/report_guided.html.j2`
- Create: `tests/test_guided_report_accessibility.py`
- Create: `tests/test_guided_report_browser.py`
- Modify: `pyproject.toml:21-43`
- Modify: `.github/workflows/ci.yml:34-49`

**Interfaces:**

- Static accessibility tests consume rendered HTML plus the inline CSS source; they do not bless markup merely because a class name exists.
- Browser tests call `write_report_document_html` with Plan 1's fixtures and load the resulting local `file:` URL. They make no network requests.
- Playwright is a development/CI dependency only. Production packaging and the report artifact remain plain Python/Jinja/HTML.
- Browser assertions cover the exact 390-by-844 content viewport, 320 CSS-pixel reflow, large text, reduced motion, keyboard focus, 44-pixel targets, no-JavaScript readability, print expansion, allowed posters, and locked-media print behavior.

- [ ] Add `"playwright>=1.52"` to the `dev` dependency list in `pyproject.toml`. Install the browser locally with `python -m playwright install chromium`.
- [ ] Add `python -m playwright install --with-deps chromium` after the package-install step in `.github/workflows/ci.yml`. Keep the existing single pytest command so missing Chromium fails CI instead of silently skipping browser tests.
- [ ] Write a failing structural accessibility test with a small `HTMLParser` audit helper. It must collect headings, landmarks, image alt attributes, disclosure summaries, controls, and IDs; assert exactly one `h1`, no heading-level jump, one `main`, nonempty unique IDs, every `img` has nonempty `alt`, every `details` begins with a `summary`, and each status has visible text plus an `aria-hidden` icon.
- [ ] Add failing self-contained/no-JavaScript assertions:

  ~~~python
  def test_guided_report_is_offline_and_progressively_enhanced(tmp_path):
      html = render_fixture(tmp_path, "coaching-improve-clear")
      lowered = html.lower()
      assert "<script" not in lowered
      assert "<link" not in lowered
      assert "@import" not in lowered
      assert "url(http://" not in lowered
      assert "url(https://" not in lowered
      assert "autoplay" not in lowered
      sources = re.findall(r'\b(?:src|poster)="([^"]+)"', html)
      assert sources
      assert all(
          not source.startswith(("http://", "https://", "//"))
          for source in sources
      )
  ~~~

- [ ] Add failing contrast tests for every fixed text/control token on `--paper` and `--canvas`. Reuse the relative-luminance formula already established in `tests/test_premium_accessibility.py:19-35`. Require `--ink`, `--muted`, and `--accent-text` to reach 4.5:1 and `--control-border`/`--focus` to reach 3:1. Brand colors may decorate; they may not become the sole small-text or focus color.
- [ ] Add failing CSS-source tests for `:focus-visible` with at least a two-pixel outline and offset, `min-width`/`min-height: 44px` on `.report-control` and `summary`, `overflow-wrap`, single-column narrow-screen grids, `prefers-reduced-motion: reduce`, a labeled horizontal table region, and print rules that expand closed details.
- [ ] Add a failing Playwright fold/reflow test. The complete long-copy priority title, observation, and cue must end within the 844-pixel content viewport at 390 CSS pixels wide without a sample banner:

  ~~~python
  from playwright.sync_api import sync_playwright

  def test_long_copy_opening_fold_and_320px_reflow(tmp_path):
      report = render_fixture_path(
          tmp_path, "coaching-improve-clear-long-copy"
      )
      with sync_playwright() as playwright:
          browser = playwright.chromium.launch()
          page = browser.new_page(
              viewport={"width": 390, "height": 844}
          )
          page.goto(report.as_uri())
          cue_box = page.locator('[data-field="cue"]').bounding_box()
          assert cue_box is not None
          assert cue_box["y"] + cue_box["height"] <= 844
          page.set_viewport_size({"width": 320, "height": 844})
          assert page.evaluate(
              "document.documentElement.scrollWidth <= "
              "document.documentElement.clientWidth"
          )
          browser.close()
  ~~~

- [ ] Add a failing large-text test that injects `:root { font-size: 200% !important; }` after load, then asserts no horizontal page scroll and that `#practice` and `#refilm` remain visible/reachable. Do not impose the default-size fold assertion at large text.
- [ ] Add a failing target/focus test. Measure every `.report-control` and `summary` bounding box as at least 44 by 44 CSS pixels. Tab to the skip link and the first disclosure, then assert computed outline width is at least two pixels and the focused box is inside the viewport.
- [ ] Add a failing reading-order test that compares the DOM positions of `data-field="priority"`, `"observation"`, `"cue"`, `"drill-name"`, and `"pass-mark"`. Add a disclosure test that presses Space on a focused `summary`, asserts it remains `document.activeElement`, and asserts its revealed content is the next content in DOM order.
- [ ] Add a failing reduced-motion test using `page.emulate_media(reduced_motion="reduce")`. Assert computed `scroll-behavior` is `auto` and every decorative animation/transition reports either `none` or no more than `0.01s`.
- [ ] Add a failing no-JavaScript browser test by opening the report in a context with `java_script_enabled=False`. Assert priority, cue, evidence alt text, phase summaries, practice, re-film target, and every native disclosure remain reachable.
- [ ] Add a failing print test that calls `page.emulate_media(media="print")` and `page.pdf(...)`. It must assert:

  - all optional text/tables/diagrams are visible even when `details` were closed on screen;
  - `video` and screen controls are hidden;
  - each allowed video has its associated `video_poster_media_key` image, alt text, caption, and `print_playback_reference`;
  - a locked replay prints `locked_replay_explanation` and no locked path;
  - the public sample disclosure text remains while its CTA link is hidden;
  - the PDF begins with `%PDF` and is nonempty.

- [ ] Run `python -m pytest tests/test_guided_report_accessibility.py tests/test_guided_report_browser.py -q`. Expected failures: the initial shell lacks the final responsive, focus, disclosure, and print rules.
- [ ] Implement the accessible interaction and reflow contract in inline CSS. Use fixed safety tokens for text/focus and keep every content grid shrinkable:

  ~~~css
  html { scroll-behavior: smooth; }
  body {
    font-family: Arial, Helvetica, sans-serif;
    line-height: 1.55;
    overflow-wrap: anywhere;
  }
  img, video, svg { max-width: 100%; height: auto; }
  .journey-preview, .phase-list, .practice-steps {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
  }
  .report-control,
  details > summary {
    display: inline-flex;
    min-width: 44px;
    min-height: 44px;
    align-items: center;
  }
  :where(a, button, summary):focus-visible {
    outline: 3px solid var(--focus);
    outline-offset: 2px;
  }
  .table-scroll {
    max-width: 100%;
    overflow-x: auto;
    overscroll-behavior-inline: contain;
  }
  @media (max-width: 40rem) {
    main { width: min(100% - 20px, 72rem); }
    .context-list, .journey-preview, .practice-layout {
      grid-template-columns: minmax(0, 1fr);
    }
    .focused-evidence { margin-inline: 0; }
  }
  @media (prefers-reduced-motion: reduce) {
    html { scroll-behavior: auto; }
    *, *::before, *::after {
      animation-duration: 0.01ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: 0.01ms !important;
    }
  }
  ~~~

- [ ] Add explicit screen/print media blocks. The print branch must never use key-position media as a substitute poster:

  ~~~css
  .print-only { display: none; }
  @media print {
    @page { margin: 12mm 11mm 14mm; }
    body { background: white; color: #17201a; font-size: 10pt; }
    .screen-only, .report-actions, video { display: none !important; }
    .print-only { display: block !important; }
    details > summary { display: none !important; }
    details:not([open]) > :not(summary) { display: block !important; }
    .table-scroll { overflow: visible; }
    table { width: 100%; font-size: 8pt; }
    tr, img, figure, .phase-card, .practice-card, .refilm-card {
      break-inside: avoid;
      page-break-inside: avoid;
    }
    h1, h2, h3 { break-after: avoid; page-break-after: avoid; }
  }
  ~~~

- [ ] For each playable `SwingDetail`, render a `screen-only` video/lock branch and a separate `print-only` figure. Call `media_path(swing.video_poster_media_key)` only when the poster key is non-null and allowed. If playback is locked, render only `locked_replay_explanation` in both media modes.
- [ ] Rerun `python -m pytest tests/test_guided_report_html.py tests/test_guided_report_accessibility.py tests/test_guided_report_browser.py tests/test_premium_report.py -q`. Expected result: all static, browser, print, and legacy regressions pass.
- [ ] Commit: `git add pyproject.toml .github/workflows/ci.yml swinglab/templates/report_guided.html.j2 tests/test_guided_report_accessibility.py tests/test_guided_report_browser.py && git commit -m "test: enforce guided report accessibility and print"`.

## Task 5: Add a gated guided sample generator while preserving the live legacy default

**Files:**

- Modify: `swinglab/sample.py:1-310`
- Modify: `swinglab/config.py:17-289,306-383`
- Modify: `config.yaml:1-15`
- Modify: `tests/test_sample_report.py:17-123`
- Modify: `tests/test_config.py:17-95`
- Regression route unchanged: `swinglab/web/app.py:629-633,1378-1392`

**Interfaces:**

- Consumes `prepare_report_input(video, swings, stats, session_notes, hand, cfg, *, angle, club, level, analysis_fps, replay_locked, visual_evidence, media, reason_codes, safe_media_keys, navigation) -> ReportPresentationInput` and `build_report_document(source, cfg) -> ReportDocument` from `swinglab.report_presenter`.
- Consumes `write_report_document_html(...)` and `GUIDED_REPORT_PRESENTATION_VERSION` for an explicit guided preview or an enabled public sample. The default public path continues to call the legacy writer.
- Produces `build_guided_sample_report(sample_dir: Path, cfg: Config) -> Path` for preview/QA without changing a live flag.
- A guided sample contains one `media/focused-priority.png` cropped evidence
  illustration plus optional collapsed key-position strips. The main image uses
  a solid golfer silhouette with clear callouts, never a skeleton or stick
  figure. It produces no guided `overlay_s*.png` and no green full-body corrected
  ghost.
- The illustration is explicitly labeled as synthetic sample art. Orange marks the observed head position, green marks the starting reference zone, and a dashed line marks the coaching boundary. Neither the image nor alt text uses `ideal`, `perfect pose`, or `corrected body`.
- Preserves the current atomic `.report.html.tmp` replacement and current-format/current-presentation idempotency behavior for both presentations.
- Extends Plan 2's existing `Config.report -> dict[str, Any]` mapping with a bare-code and shipped-config `guided_sample_enabled: false`; it does not redefine the property or replace `guided_presentation_enabled`. Only `cfg.report.get("guided_sample_enabled") is True` selects guided output; string/integer truthy values remain off. Other workstreams may add keys to the same `report` mapping.
- Retains `draw_sample_overlay` and the current legacy sample assembly solely for `premium-coach-v2` rollback. Turning the flag off after a guided preview/live sample regenerates the legacy report and restores its marker.
- Changes no customer-job presentation, cohort, deployment, or existing customer report. Plan 6 owns setting the sample flag and reports public-sample state separately from code/deployment/customer-cohort state.

- [ ] Add failing config tests first. `Config().report["guided_sample_enabled"]` and the shipped `config.yaml` must both be `False`; an explicit YAML boolean `true` passes the strict gate; `"true"`, `1`, `"1"`, and missing values do not. Also assert Plan 2's `guided_presentation_enabled` key remains present and false after defaults and YAML are merged.
- [ ] Add `"guided_sample_enabled": False` inside Plan 2's existing `DEFAULTS["report"]` mapping and add `guided_sample_enabled: false` beside the existing report-presentation keys in shipped `config.yaml`, with an activation/rollback comment. Preserve every pre-existing `report` key and the single `Config.report` property.
- [ ] Change the sample tests next. Require:

  - default `ensure_sample_report` retains `caddie-brief-v1` plus `premium-coach-v2` and the existing legacy sample media;
  - explicit `build_guided_sample_report` emits `caddie-brief-v1` plus `guided-report-v1` in a separate temporary directory;
  - the guided output has exactly one main-path `focused-priority.png` reference and no `overlay_s` file/reference;
  - guided output contains the sample banner, `Your next move`, `Understand`, `Practice`, and `Re-film`;
  - guided output contains one `Head sway (backswing)` priority, one primary drill name, and one pass mark;
  - neither sample fabricates `<video`;
  - optional guided strips remain collapsed and are not the default mobile visual;
  - loading a config whose `cfg.report.get("guided_sample_enabled") is True` makes `ensure_sample_report` select guided;
  - turning the flag back off refreshes a guided `report.html` to `premium-coach-v2` and the legacy report no longer references `focused-priority.png`;
  - same-presentation generation remains byte-for-byte idempotent;
  - `/sample-report/` and media traversal tests remain unchanged.

- [ ] Run `python -m pytest tests/test_config.py tests/test_sample_report.py -q`. Expected failures: the sample key and `build_guided_sample_report` do not exist; Plan 2's report mapping/property tests remain green.
- [ ] Keep `draw_sample_overlay` and `build_sample_swings` unchanged for the
  legacy branch. Add `draw_sample_focused_evidence(out_path: Path, cfg: Config)
  -> Path` with one square canvas and one cropped solid golfer silhouette—not a
  skeleton or stick figure. Draw only the three approved semantic marks and a
  footer reading `Sample illustration — not golfer footage or a predicted ideal
  pose.`.
- [ ] Add `build_guided_sample_swings` for guided-only collapsed `every_swing` depth. Assign stable key-position media keys and alt text and omit the legacy `overlay` key.
- [ ] Build a checksum-backed core `MediaEntry` for the focused image and a typed `RenderedEvidence` that matches the synthetic head-sway priority:

  ~~~python
  focused_path = draw_sample_focused_evidence(
      sample_dir / "media" / "focused-priority.png", cfg
  )
  focused_media = MediaEntry(
      key="sample-focused-priority",
      role="priority_evidence",
      mime_type="image/png",
      entitlement="core",
      relative_path="media/focused-priority.png",
      checksum_sha256=hashlib.sha256(
          focused_path.read_bytes()
      ).hexdigest(),
  )
  focused_evidence = RenderedEvidence(
      kind="head_boundary",
      state="rendered",
      media_key=focused_media.key,
      swing=1,
      phase="going_back",
      phase_method="highest_tracked_hands",
      timestamp_ms=2860,
      events=(
          EventProvenance(
              event="top",
              method="highest_tracked_hands",
              timestamp_ms=2860,
              label="Top estimate",
          ),
      ),
      tracking_state="clear",
      tracking_reasons=(),
      render_reasons=(),
      observed_label="Head position near the top",
      reference_label="Starting head-position zone",
      boundary_label="0.35 shoulder-width coaching boundary",
      readable_swings=3,
      triggered_swings=2,
      supporting_measurement=MeasurementDetail(
          id="head-sway-backswing",
          label="Head sway going back",
          plain_value="0.37 shoulder widths",
          numeric_value=0.37,
          unit="shoulder_widths",
          benchmark_relation="above",
          benchmark_value=0.35,
          benchmark_upper_value=None,
          benchmark_label="Coaching line: 0.35 shoulder widths",
          explanation=(
              "How far the head moved from address to the top estimate."
          ),
          limitation=(
              "A face-on 2D estimate, not a 3D center-of-mass measure."
          ),
      ),
      observation=(
          "The head moved beyond its starting reference zone on two of "
          "three readable swings."
      ),
      alt_text=(
          "Sample illustration of swing 1 near the top. An orange head "
          "marker sits outside the green starting zone and beyond a dashed "
          "coaching boundary."
      ),
  )
  ~~~

- [ ] Construct the sample once through the shared compatibility preparation path, not by repeating `report.py:175-275`:

  ~~~python
  source = prepare_report_input(
      sample_video(),
      build_guided_sample_swings(sample_dir, cfg),
      stats,
      notes,
      "right",
      cfg,
      angle="face_on",
      club="iron",
      visual_evidence=focused_evidence,
      media=tuple(sample_media_entries),
      navigation=ReportNavigation(
          app_url="/",
          storefront_url=None,
          gear_collection_url=None,
      ),
  )
  document = build_report_document(source, cfg)
  write_report_document_html(
      temporary_report,
      document,
      cfg=cfg,
      sample_banner={
          "text": BANNER_TEXT,
          "cta_label": BANNER_CTA,
          "cta_url": "/",
      },
  )
  ~~~

- [ ] Ensure guided `sample_media_entries` includes the focused image and each retained optional strip with real SHA-256 checksums. Do not include a path not referenced by `ReportDocument`.
- [ ] Implement `build_guided_sample_report` with the shared preparation/render path above and the atomic temporary-file replace. Its refresh guard requires `REPORT_FORMAT_VERSION` and `GUIDED_REPORT_PRESENTATION_VERSION`.
- [ ] Keep the existing legacy builder/marker guard and make `ensure_sample_report` choose it unless the strict typed flag is exactly true:

  ~~~python
  def ensure_sample_report(sample_dir: Path, cfg: Config) -> Path:
      if cfg.report.get("guided_sample_enabled") is True:
          return build_guided_sample_report(sample_dir, cfg)
      return build_legacy_sample_report(sample_dir, cfg)
  ~~~

- [ ] Preserve replacement of only `sample-report/report.html` and exact known synthetic media; never inspect or rewrite a customer session. On rollback, the legacy HTML must not reference guided media even if an unreferenced synthetic preview file remains.
- [ ] Rerun `python -m pytest tests/test_config.py tests/test_sample_report.py tests/test_guided_report_html.py tests/test_guided_report_browser.py -q`. Expected result: legacy-default, explicit preview, strict activation, rollback, idempotency, routes, and guided visual tests all pass.
- [ ] Commit: `git add swinglab/config.py config.yaml swinglab/sample.py tests/test_config.py tests/test_sample_report.py && git commit -m "feat: add gated guided sample preview"`.

## Task 6: Produce the Playwright/manual rendered-QA matrix and close the full web gate

**Files:**

- Create: `scripts/render_guided_report_qa.py`
- Create: `docs/quality/guided-report-rendered-review.md`
- Modify: `tests/report_view_fixtures.py`
- Create: `tests/test_guided_report_qa_script.py`

**Interfaces:**

- Produces a required `--output` command argument; the script never defaults to a tracked repository directory.
- Produces one directory per named fixture, each containing `report.html` and only its declared relative media.
- Uses `GUIDED_DOCUMENT_QA_FIXTURE_NAMES` from `tests/report_view_fixtures.py`: `coaching-improve-clear-long-copy`, `coaching-protect-clear`, `coaching-improve-limited`, `coaching-improve-visual-unavailable`, `coaching-dtl-clear`, `capture-only-angle`, `capture-only-tracking`, `free-locked`, and `pro-unlocked`.
- Also produces `guided-sample-preview` by calling `build_guided_sample_report` directly and `legacy-sample-default` by calling `ensure_sample_report` with bare `Config()`. Both use temporary QA directories; the script never flips `guided_sample_enabled` or changes the live `/sample-report/`.
- Produces no final approval. `docs/quality/guided-report-rendered-review.md` is a repeatable developer/product-owner checklist whose signed copy and screenshots move into workstream 6 release evidence.

- [ ] Add a failing script test that invokes `main(["--output", str(tmp_path)])`, asserts all fixture directories/reports exist, asserts no undeclared file exists, and reparses every rendered report with the structural accessibility audit.
- [ ] Add a failing refusal test for an omitted `--output` argument and for an output path that resolves to the repository root. The script may create only the exact explicit child directory.
- [ ] Run `python -m pytest tests/test_guided_report_qa_script.py -q`. Expected failure: the script does not exist.
- [ ] Implement `scripts/render_guided_report_qa.py` with `argparse`, `Path.resolve()`, `report_document_fixture(name)`, deterministic copying of each fixture's declared media, and `write_report_document_html`. Add the explicit guided preview and default legacy sample calls described above. Do not start the web app, touch SQLite, run analysis, mutate config, or use customer files.
- [ ] Write `docs/quality/guided-report-rendered-review.md` with checkbox rows for each fixture and these exact states:

  - desktop at 1440 by 1000;
  - default mobile at 390 by 844 with the sample banner absent;
  - longest-copy mobile at 390 by 844;
  - large text at 200 percent without the fold requirement;
  - 320 CSS-pixel reflow and 200 percent browser zoom;
  - keyboard-only traversal and visible focus;
  - reduced-motion emulation;
  - screen-reader order: priority, observation, cue, drill, pass mark;
  - print preview and generated PDF;
  - DTL timing-only, capture-only, clean/protect, limited, visual-unavailable, free locked, Pro unlocked, guided sample preview, and legacy-default sample.

- [ ] Add evidence fields to each checklist row: fixture name, viewport/media mode, screenshot/PDF filename, pass/fail, reviewer, date, notes, and blocking safety misunderstanding. Require screenshots of the opening fold and full-page/print output.
- [ ] Generate the local deterministic bundle:

  ~~~powershell
  $qaRoot = Join-Path $env:TEMP "caddieinsight-guided-report-qa"
  python scripts/render_guided_report_qa.py --output $qaRoot
  Get-ChildItem -LiteralPath $qaRoot -Recurse
  ~~~

- [ ] Capture baseline desktop/mobile screenshots and print PDFs with installed Edge; keep output under the explicit temporary QA root:

  ~~~powershell
  $edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
  & $edge --headless=new --disable-gpu --hide-scrollbars `
    --window-size=1440,1000 `
    --screenshot="$qaRoot\coaching-improve-clear-long-copy\desktop-1440x1000.png" `
    "file:///$($qaRoot.Replace('\','/'))/coaching-improve-clear-long-copy/report.html"
  & $edge --headless=new --disable-gpu --hide-scrollbars `
    --window-size=390,844 `
    --screenshot="$qaRoot\coaching-improve-clear-long-copy\mobile-390x844.png" `
    "file:///$($qaRoot.Replace('\','/'))/coaching-improve-clear-long-copy/report.html"
  & $edge --headless=new --disable-gpu `
    --print-to-pdf="$qaRoot\coaching-improve-clear-long-copy\report.pdf" `
    --no-pdf-header-footer `
    "file:///$($qaRoot.Replace('\','/'))/coaching-improve-clear-long-copy/report.html"
  ~~~

- [ ] Inspect in a real browser at 320 CSS pixels, 200 percent zoom, keyboard-only, reduced motion, and print preview. Confirm no body-level horizontal scrolling, no clipped text/actions, a single large default evidence frame, no synthetic corrected body, no hidden locked media, and expanded print depth.
- [ ] At 390 by 844 default text size, use the longest-copy owned fixture without the banner and record that the complete priority title, observation, and cue are inside the opening viewport. At large text, record reflow/reachability instead of enforcing the fold.
- [ ] Run the focused web gate:

  ~~~powershell
  python -m pytest `
    tests/test_guided_report_html.py `
    tests/test_guided_report_accessibility.py `
    tests/test_guided_report_browser.py `
    tests/test_guided_report_web_composition.py `
    tests/test_guided_report_qa_script.py `
    tests/test_sample_report.py `
    tests/test_report.py `
    tests/test_premium_report.py `
    tests/test_camera_angle.py `
    tests/test_club_aware_coaching.py `
    tests/test_club_context.py `
    tests/test_drills.py `
    tests/test_explainers.py `
    tests/test_level_context.py `
    tests/test_praise_notes.py `
    tests/test_replay_gate.py -q
  ~~~

- [ ] Run `python -m pytest -q`. Expected result: the complete Python suite passes, including Chromium layout/print tests.
- [ ] Run `git diff --check` and inspect `git status --short`. Expected result: no whitespace errors; only this plan's implementation files and intentional upstream-plan files are changed.
- [ ] Confirm Plan 2's unit tests still inject only `write_test_report_html`, while `create_app` injects only `write_report_document_html`; a real app-created `guided-report-v1` job reaches the guided bundle with that writer, and the existing `write_report_html`/`report.html.j2` regression still passes for `premium-coach-v2`. Do not enable a customer cohort in this plan.
- [ ] Commit: `git add scripts/render_guided_report_qa.py docs/quality/guided-report-rendered-review.md tests/report_view_fixtures.py tests/test_guided_report_qa_script.py && git commit -m "test: add guided report rendered QA gate"`.

## Completion Checklist

- [ ] `report_guided.html.j2` consumes only `ReportDocument` and uses `media_by_key` for every path.
- [ ] The FastAPI composition root injects the real guided writer into `JobManager`; Plan 2 remains renderer-agnostic and fails closed when a guided writer is absent.
- [ ] The format/outcome/priority markers remain compatible and the guided presentation marker is independent.
- [ ] Coaching improve, protect, limited rendered, visual-unavailable, DTL, capture-only, free, and Pro fixtures pass static and browser tests.
- [ ] Priority/strength, primary drill, and pass mark each have exactly one canonical main-path card.
- [ ] No guided report or public sample contains a synthetic corrected full-body overlay.
- [ ] Heading/landmark order, alt text, status text/icons, contrast, focus, 44-pixel targets, reduced motion, no-JavaScript reading, 320-pixel reflow, large text, and 200 percent zoom are verified.
- [ ] Print expands optional text/tables/diagrams, replaces allowed video with its associated poster/caption/reference, and preserves lock explanations without leaking media.
- [ ] The guided sample preview renders in an explicit temporary directory; the live public sample remains legacy by default, activates only for strict `cfg.report.get("guided_sample_enabled") is True`, rolls back when false, and retains atomic/idempotent refresh plus route traversal protection.
- [ ] Focused report tests, legacy regressions, the full Python suite, and `git diff --check` pass.
- [ ] Desktop/mobile/large-text/reduced-motion/print screenshots and PDFs are attached to the rendered-review record; workstream 6 still owns product-owner sign-off, moderated comprehension, cohort enablement, deployment, and rollback reporting.
