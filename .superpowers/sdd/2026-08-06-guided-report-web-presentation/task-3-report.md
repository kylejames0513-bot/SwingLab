# Task 3 report — capture recovery and optional depth

## Scope

Implemented Task 3 from `2633d7cd2480bf1821c68e213def648c097c486b`.
Only the guided template, typed report-document fixtures, and guided HTML tests
changed. No boundary worktree, Task 4+ code, flag, deployment, push, merge, or
live data was touched.

## RED evidence

The capture and optional-depth tests were added before template behavior. The
first focused run reached seven intended presentation failures:

```text
python -m pytest tests/test_guided_report_html.py -q
7 failed, 16 passed in 1.68s
```

The failures were missing ordered capture correction/actions/safe playback,
missing retry actions, absent optional sections, and absent locked/unlocked
replay branches. One misplaced pre-existing assertion introduced while
appending the tests was corrected before production edits; it was a test-file
NameError and is not counted as product RED evidence.

## Implementation

- Added typed capture variants for angle mismatch, unstable tracking, no
  readable swing, and a failed retry. Each crosses the persisted
  serializer/parser boundary and retains only server-authored capture guidance.
- Added `free-locked` and `pro-unlocked` documents with all eight optional
  section IDs, explicit media maps, swing/media associations, and no locked
  replay key in the Free document.
- Replaced the capture shell with an ordered recovery path: reason,
  explanation, correction, checklist, allowlisted safe playback, and primary /
  secondary recovery actions.
- Added typed optional disclosures for every swing, replay, secondary findings,
  alternative drills, strengths, measurements, glossary, and gear. Tables have
  captions, scoped headers, a labeled scroll region, and technical explanation
  and limitation copy.
- Kept gear after the complete primary Practice card. Capture-only never enters
  the coaching/optional branch, even when malicious depth gear is supplied.
- Locked replay renders only its authored explanation and never calls the media
  resolver for a hidden coach-replay key.

## GREEN evidence

```text
python -m pytest tests/test_guided_report_html.py -q
25 passed in 2.14s

python -m pytest tests/test_guided_report_html.py tests/test_guided_report_web_composition.py tests/test_premium_report.py tests/test_report_view_contract.py tests/test_report_presenter_phases.py tests/test_focused_evidence.py tests/test_report_bundle.py -q
163 passed in 6.36s

python -m compileall -q swinglab tests
exit 0

git diff --check
exit 0
```

## Self-review

- Capture tests prove diagnosis, phases, drills, targets, coach replay, pass
  marks, and gear remain absent; only safe-media keys can produce playback.
- Pro resolves the exact replay media-map path and caption. Free contains no
  replay key or filename and renders the lock explanation only.
- Every optional section is gated by the server-owned availability/lock index;
  unavailable sections stay absent.
- Secondary findings cannot emit a canonical Practice block, and gear follows
  the primary practice prescription.
- Tasks 1 and 2 renderer, composition, typed-view, bundle, and legacy contracts
  remain green.

## Concerns

None for Task 3. Screen/print media separation, real-browser reflow, focus,
reduced motion, and poster behavior remain intentionally owned by Task 4.

## Fix round 1 — optional-authority review

Independent review found four Important contract weaknesses and two Minor test
gaps: alternative drills fell back to array length when their optional-section
entry was absent; locked gear still rendered; Free replay/count fixtures did
not match the producer; the filename sentinel was inert; capture poster
association and action/ordering assertions were incomplete.

The fix makes alternative drills strictly server-gated, renders a lock-only
alternative disclosure, suppresses locked gear, models Free replay as
`available=False`, `locked=True`, `item_count=0`, and derives measurement count
from the typed depth payload. A candidate Pro replay entry now carries the
sentinel before the Free filter removes it, so the leak assertion exercises the
fixture construction path. Capture playback resolves an allowlisted poster and
uses its authored alt text. Tests now prove linked recovery actions, poster
association, locked gear suppression, and gear placement after the closing
primary Practice section.

```text
python -m pytest tests/test_guided_report_html.py -q
26 passed in 2.26s

python -m pytest tests/test_guided_report_html.py tests/test_guided_report_web_composition.py tests/test_premium_report.py tests/test_report_view_contract.py tests/test_report_presenter_phases.py tests/test_focused_evidence.py tests/test_report_bundle.py -q
166 passed in 6.46s

python -m compileall -q swinglab tests
exit 0

git diff --check
exit 0
```
