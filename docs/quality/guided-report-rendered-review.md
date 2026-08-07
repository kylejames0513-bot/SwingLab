# Guided report rendered review

This is a repeatable developer and product-owner review record for synthetic
guided-report fixtures. It is not final product approval, customer-cohort
activation, deployment evidence, or permission to change
`report.guided_sample_enabled`. A signed copy and its screenshots/PDFs belong
with the later release-evidence workstream.

## Prepare an isolated bundle

Use a new explicit temporary directory. The renderer refuses the repository
root and any nonempty output directory.

```powershell
$qaRoot = Join-Path $env:TEMP "caddieinsight-guided-report-qa"
python scripts/render_guided_report_qa.py --output $qaRoot
Get-ChildItem -LiteralPath $qaRoot -Recurse
```

Record before review:

- Commit: ____________________
- Operating system: ____________________
- Browser and version: ____________________
- Reviewer: ____________________
- Review date: ____________________
- QA root (temporary/untracked): ____________________
- Confirm bare `Config().report["guided_sample_enabled"] is False`: [ ]
- Confirm no customer cohort or live public sample was changed: [ ]

Every checked row must include the fixture, viewport/media mode,
screenshot/PDF filename, pass/fail, reviewer, date, notes, and any blocking
safety misunderstanding. “Blocking safety misunderstanding” means the page
could lead a golfer to confuse a reference with an ideal pose, a synthetic
image with their footage, a limited result with a reliable measurement, or a
locked feature with analyzed evidence.

## Required rendering-state evidence

Capture the opening fold and either a full-page screenshot or print output for
each applicable row. Do not apply the opening-fold requirement at large text.

| Done | Fixture name | Viewport / media mode | Required evidence filename | Pass / fail | Reviewer | Date | Notes | Blocking safety misunderstanding |
|---|---|---|---|---|---|---|---|---|
| [ ] | `coaching-improve-clear-long-copy` | Desktop at 1440 by 1000 | `desktop-1440x1000.png`; `desktop-full-page.png` |  |  |  |  |  |
| [ ] | `coaching-improve-clear-long-copy` | Default mobile at 390 by 844 with the sample banner absent | `mobile-390x844-opening-fold.png`; `mobile-390x844-full-page.png` |  |  |  | Confirm complete priority title, observation, and cue end inside the opening viewport. |  |
| [ ] | `coaching-improve-clear-long-copy` | Longest-copy mobile at 390 by 844 | `long-copy-mobile-390x844.png` |  |  |  | Confirm no clipping or abbreviated coaching copy. |  |
| [ ] | `coaching-improve-clear-long-copy` | Large text at 200 percent without the fold requirement | `large-text-200-percent-full-page.png` |  |  |  | Confirm Practice and Re-film remain reachable; judge reflow, not fold. |  |
| [ ] | `coaching-improve-clear-long-copy` | 320 CSS-pixel reflow and 200 percent browser zoom | `reflow-320px-zoom-200.png` |  |  |  | Confirm no body-level horizontal scrolling or clipped action. |  |
| [ ] | `coaching-improve-clear-long-copy` | Keyboard-only traversal and visible focus | `keyboard-focus.png` |  |  |  | Tab from skip link through disclosures/actions; Space toggles a summary without losing focus. |  |
| [ ] | `coaching-improve-clear-long-copy` | Reduced-motion emulation | `reduced-motion.png` |  |  |  | Confirm scroll behavior is immediate and no decorative transition persists. |  |
| [ ] | `coaching-improve-clear-long-copy` | Screen-reader order: priority, observation, cue, drill, pass mark | `screen-reader-order.txt` |  |  |  | Record the five labels in announced order. |  |
| [ ] | `pro-unlocked` | Print preview and generated PDF | `pro-unlocked-print.png`; `pro-unlocked-report.pdf` |  |  |  | Confirm optional depth expands and each video becomes its associated poster/caption/reference. |  |

## Required fixture and safety-state evidence

| Done | Fixture name | Viewport / media mode | Required evidence filename | Pass / fail | Reviewer | Date | Notes | Blocking safety misunderstanding |
|---|---|---|---|---|---|---|---|---|
| [ ] | `coaching-improve-clear-long-copy` | Clean/improve, 390 by 844 and full page | `clean-improve-opening.png`; `clean-improve-full-page.png` |  |  |  | One priority, one primary drill, one pass mark. |  |
| [ ] | `coaching-protect-clear` | Clean/protect, 390 by 844 and full page | `clean-protect-opening.png`; `clean-protect-full-page.png` |  |  |  | Strength is explicitly something to protect, not a fault. |  |
| [ ] | `coaching-improve-limited` | Limited coaching, 390 by 844 and print | `limited-mobile.png`; `limited-report.pdf` |  |  |  | Limitation remains beside the usable coaching action. |  |
| [ ] | `coaching-improve-visual-unavailable` | Visual unavailable, 390 by 844 and full page | `visual-unavailable.png`; `visual-unavailable-full-page.png` |  |  |  | No missing image shell; reason and next safe step are clear. |  |
| [ ] | `coaching-dtl-clear` | DTL timing-only, 390 by 844 and print | `dtl-timing-only.png`; `dtl-timing-only.pdf` |  |  |  | No face-on lateral technical value or pose claim appears. |  |
| [ ] | `capture-only-angle` | Capture-only angle recovery, 390 by 844 and print | `capture-angle.png`; `capture-angle.pdf` |  |  |  | Poster is the declared poster, not key positions; correction and both retry actions are clear. |  |
| [ ] | `capture-only-tracking` | Capture-only tracking recovery, 390 by 844 | `capture-tracking.png`; `capture-tracking-full-page.png` |  |  |  | No coaching verdict appears; filming fix is understandable. |  |
| [ ] | `free-locked` | Free locked, 390 by 844 and print | `free-locked.png`; `free-locked.pdf` |  |  |  | Lock explanation prints; no locked path, video, poster, or filename is exposed. |  |
| [ ] | `pro-unlocked` | Pro unlocked, 1440 by 1000 and print | `pro-unlocked.png`; `pro-unlocked-report.pdf` |  |  |  | Allowed media load; print uses the associated poster rather than key-position art. |  |
| [ ] | `guided-sample-preview` | Guided sample preview, 390 by 844 and print | `guided-sample-opening.png`; `guided-sample-full-page.png`; `guided-sample.pdf` |  |  |  | Synthetic label is explicit; solid cropped golfer, orange observed head, green starting zone, dashed boundary; no corrected body. |  |
| [ ] | `legacy-sample-default` | Legacy-default sample, 1440 by 1000 | `legacy-sample-default.png` |  |  |  | Confirm this is still `premium-coach-v2`; comparison only, not approval to activate guided sample. |  |

## Manual acceptance checks

- [ ] No page has body-level horizontal scrolling at 320 CSS pixels.
- [ ] No text, summary, table label, button, or link is clipped at 200 percent
  text or browser zoom.
- [ ] Every visible interactive target is at least 44 by 44 CSS pixels and has
  a clear focus outline.
- [ ] The default mobile report leads with one large focused evidence frame;
  optional per-swing strips stay collapsed.
- [ ] No guided report or guided sample shows a synthetic corrected full-body
  overlay, green body ghost, “perfect pose,” or personalized ideal.
- [ ] Orange means observed, green means starting reference, and the dashed
  line means coaching boundary; labels and alt text agree.
- [ ] Print expands optional text, tables, and diagrams; hides screen controls
  and videos; and retains allowed poster alt text, caption, and playback
  reference.
- [ ] Locked print output contains the authored lock explanation and no private
  path or filename.
- [ ] The guided sample preview came from `build_guided_sample_report` in this
  temporary bundle; the default public sample path was not activated.

## Review disposition

- Developer rendered-QA result: ____________________
- Product-owner result (later workstream): ____________________
- Blocking defects and evidence filenames: ____________________
- Follow-up owner: ____________________
- Re-review date: ____________________

Leaving product-owner fields blank is expected for an engineering-only run.
Passing automated checks or capturing baseline screenshots is not final
approval to deploy, publish, enable a customer cohort, or switch the public
sample.
