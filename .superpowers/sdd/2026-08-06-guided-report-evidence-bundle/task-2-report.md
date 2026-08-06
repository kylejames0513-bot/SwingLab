# Task 2 implementation report

## Interface decision

`EvidenceSnapshot` now retains a validated in-memory `hand` value. This is not persisted or inferred from landmarks. It reconciles the fixed renderer signature with exact handed lead-arm evidence.

## Commits

- `248d268 fix: retain evidence snapshot handedness`
- `633f309 feat: render focused swing evidence`

## Test evidence

- RED: `python -m pytest tests/test_focused_evidence.py -q` produced 11 expected `ModuleNotFoundError: No module named 'swinglab.focused_evidence'` failures before renderer implementation.
- Handedness prerequisite RED: `python -m pytest tests/test_evidence_snapshot.py -q` produced 3 expected failures for absent validation/retention.
- GREEN: `python -m pytest tests/test_evidence_snapshot.py -q` passed `13 passed`.
- GREEN: `python -m pytest tests/test_focused_evidence.py tests/test_drawing.py tests/test_deliverable_images.py -q` passed `17 passed`.
- `git diff --check` passed.
- Full suite: `python -m pytest -q` passed `1422 passed, 28 skipped, 1 warning in 305.68s` (timeout allowance 480 seconds).

## Self-review

- Selection preserves the same snapshot for both metric and source image; it never reranks while rendering.
- Body renderers use orange observed marks and green address/start references; they do not synthesize an ideal skeleton or pose.
- Configured dashed boundaries are gated on confident target direction. Head-height has no directional boundary.
- Lead-arm joints use the retained snapshot handedness. Cropping uses every landmark shown by an annotation and does not mirror source pixels.
- DTL rejects body evidence and timing rendering is a four-event timeline without skeleton, centerline, boundary, or directional language.
- Media is core, has a canonical relative path, and hashes the saved bytes. Pillow/file errors become `FocusedEvidenceRenderError`; unavailable evidence is renderer-only after prior trust.
- Legacy `sheared`, skeleton, and overlay behavior remains unchanged.

## Concerns

The snapshot builder API remains the only hand source. Callers constructing `EvidenceSnapshot` directly must now supply a validated `right` or `left` value, intentionally making the evidence provenance explicit.

## Fix round 1

- RED: canonical-statistics and tempo-provenance regressions failed as expected before implementation (local snapshot mean was selected and timing alt text omitted methods/durations/consistency).
- GREEN: `python -m pytest tests/test_evidence_snapshot.py tests/test_focused_evidence.py tests/test_drawing.py tests/test_deliverable_images.py -q` passed `34 passed`.
- `git diff --check` passed.
- Full suite: `python -m pytest -q` passed `1426 passed, 28 skipped, 1 warning in 309.01s` with a 480-second timeout allowance.
- Self-review: selector uses finite canonical mean and threshold crossing count; timing carries four methods, durations, ratio, and session consistency; boundary labels are visible only with confident direction; snapshot direct construction validates hand; unavailable fallback checks gates/events; save/hash failures are renderer errors. Drawing and deliverable-image regressions cover primitives and core hashed priority media.

## Fix round 2 replacement completion

- Watched RED: `python -m pytest tests/test_focused_evidence.py -q` failed exactly `1 failed, 22 passed`; `test_named_renderers_do_not_depend_on_a_central_geometry_router` proved every named body renderer still called `_render_body`.
- Focused GREEN: `python -m pytest tests/test_focused_evidence.py tests/test_drawing.py tests/test_deliverable_images.py -q` passed `31 passed in 1.30s`.
- `git diff --check` passed before the full regression run.
- Full suite: `python -m pytest -q` passed `1436 passed, 28 skipped, 1 warning in 310.56s` with a 600-second timeout allowance. The warning is the existing Starlette `httpx` deprecation warning.
- Self-review: removed the central EvidenceKind geometry router; dispatch now maps every EvidenceKind exactly to its independently semantic renderer, with only source/landmark validation, crop, colors, and font setup shared. Timing visibly writes each event-method label and the backswing/downswing durations, ratio, and consistency; it uses no corrected-color/body semantics in DTL. Finish stability crops around the complete ordered ankle-midpoint path, draws the connected path, then distinguishes its start and observed endpoint. Deep tests record confidence-gated dashed boundaries, exact handed elbow/arc location, paired address/impact shoulders, ordered finish endpoints, pre-output landmark failure, unmirrored asymmetric pixels, DTL colors/wording, and visible timing text. The deliverable test uses the real renderer and independently hashes saved bytes.
- Concern: the focused module remains intentionally compact and uses Pillow drawing-object recorders in tests only where rendered pixels cannot prove ordered primitive arguments; those recorders delegate every operation to real Pillow.
