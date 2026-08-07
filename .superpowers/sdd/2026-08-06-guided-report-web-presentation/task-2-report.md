# Task 2 report — coaching-ready guided journey

## Status and scope

Implemented the coaching-ready action-first report journey from clean Task 1
head `21d4ee0373ea991f311e03ac1b340d66e1d1f8e7`. The change is limited to:

- `swinglab/templates/report_guided.html.j2`
- `tests/report_view_fixtures.py`
- `tests/test_guided_report_html.py`
- this report

No boundary worktree, Task 3+ content, public flag, sample flag, live data,
deployment, merge, or push was changed.

## RED evidence

The five required fixture variants were first constructed from the typed
`coaching-improve-clear` view with `dataclasses.replace` and crossed
`report_view_to_dict` / `report_view_from_dict` successfully:

```text
coaching-improve-clear-long-copy improve 5
coaching-protect-clear protect 5
coaching-improve-limited improve 5
coaching-improve-visual-unavailable improve 5
coaching-dtl-clear improve 1
```

The behavior tests were then added before the production template changed.
Their first exact run was:

```text
python -m pytest tests/test_guided_report_html.py -q
7 failed, 9 passed in 0.69s
```

All seven failures reached the intended missing presentation behavior:

- the four stable journey block hooks were absent;
- the improve/protect authored next-move treatment was absent;
- focused evidence provenance, counts, and measurement disclosure were absent;
- DTL event provenance was absent;
- visual-unavailable explanation markup was absent;
- no phase disclosure cards existed;
- canonical practice/refilm and limitations content was absent.

There were no fixture, serialization, media, collection, or permission errors.

## Implementation

- Replaced the coaching-ready shell with one ordered priority → understand →
  practice → re-film path and exactly one canonical card for each authored
  priority, practice prescription, and re-film protocol.
- Preserved server-owned next-move copy, phase order, phase status labels,
  `expanded_by_default`, evidence counts, event order/timestamps/methods,
  drill steps, alternatives, re-film checklist, target, and action label.
- Rendered focused media only through `media_path`; unavailable evidence keeps
  its observation, provenance, tracking, render-failure reason, trust
  explanation, and measurement link without falling back to depth media.
- Added a whole-swing disclosure list: five supplied face-on phases or one
  supplied DTL timing/rhythm phase. Icons are decorative and status text stays
  visible.
- Kept technical measurement copy inside the matching ID disclosure and kept
  the opening priority card free of copied measurement values.
- Added authored practice setup, feel, dose, equipment, instructional-image
  boundary label, conditional full steps, alternatives, and a target-free link
  to the re-film step.
- Added checklist-order re-film controls, one pass mark, explicit same-context
  confirmations, and an offline note instead of a dead action link when the
  app URL is unavailable.
- Added compact responsive structure, visible keyboard focus, a skip link,
  readable cards, and a single-column small-screen fallback without changing
  any persisted data or renderer composition.

## GREEN evidence

Focused Task 2 gate after implementation and cleanup:

```text
python -m pytest tests/test_guided_report_html.py -q
16 passed in 1.03s
```

Task 2 plus Task 1 composition and legacy regressions:

```text
python -m pytest tests/test_guided_report_html.py tests/test_guided_report_web_composition.py tests/test_premium_report.py -q
23 passed in 2.93s
```

Static verification:

```text
python -m compileall -q swinglab tests
exit 0

git diff --check
exit 0 (Git emitted only working-tree LF-to-CRLF conversion notices)

git status --short
 M swinglab/templates/report_guided.html.j2
 M tests/report_view_fixtures.py
 M tests/test_guided_report_html.py
```

## Self-review and mutation coverage

- Removing or reordering any journey block fails the order and canonical-count
  test; repeating the drill name or pass mark fails the exact-count checks.
- Choosing an open phase from its ID/status instead of
  `expanded_by_default` fails improve/protect phase-card assertions.
- Reordering or deriving DTL event timestamps fails the literal four-event
  provenance assertions.
- Inferring a triggered count when the persisted value is null fails the
  limited-evidence assertion.
- Substituting a depth swing image when focused rendering fails violates the
  unavailable-evidence media assertion.
- Moving the measurement value into the opening card, changing its server ID,
  or dropping the disclosure fails the priority/evidence boundary checks.
- Rendering full drill steps unconditionally fails the limited fixture; using
  a dead action link without `navigation.app_url` fails the offline assertion.
- Changing phase order/status/reason copy or re-film checklist order fails the
  supplied-content tests.

## Concerns

None for Task 2. Capture-only content remains the intentionally minimal Task 1
shell for Task 3, and rendered desktop/mobile browser QA remains owned by the
later presentation verification task.
