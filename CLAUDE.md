# Working agreements

## Shipping

**Always merge to main.** Do not leave finished work sitting as a draft PR
waiting for review. The flow is: branch → PR → merge, so CI still runs and
the history stays reviewable, but do not stop at the PR.

The one exception is a red build. "Always merge" does not mean merging broken
code — if a check fails, fix it or say why it is blocked, then merge.

Merge with a **merge commit**, not squash. Every prior PR on `main` is a
`Merge pull request #NN from …`, and the individual commit messages carry the
reasoning.

## Two surfaces, two deploy paths

They look like one product to a customer, and should be kept consistent, but
they ship differently:

- **The app** (`app.caddieinsight.com`) deploys automatically from `main` via
  Railway. Merging is deploying.
- **The Shopify theme** (`storefront-theme/`, live at `caddieinsight.com`)
  deploys **manually**. Merging changes nothing on the store. Upload to a
  duplicate unpublished theme and preview before publishing —
  `docs/runbooks/rebrand-cutover.md`.

A Shopify **Files** entry always beats a theme asset of the same name. The
store still holds `og-swinglab.png`, `swinglab-favicon.png`,
`swinglab-logo.png` and `swinglab-logo-inverse.png` from the v3 brand, so
never point `images['…']` at a retired filename expecting the theme copy to
win. It holds **no** `caddieinsight-*` mark, so those theme assets resolve
from the theme and can be regenerated in place — check the store before
assuming either way, and let `tests/test_theme_brand_filenames.py` hold the
retired list.

## The design system

`.claude/skills/frontend-design` is the reference for anything visual on
either surface. It is INDUSTRY: a blueprint grammar on a paper ground, with
the deep green kept as the one reversed field. Read it before restyling
anything — it carries the colour rules, the token traps, and the Shopify
pitfalls this repo has already paid for.

## Verifying visuals locally

Read `docs/quality/local-visual-verification.md` before trusting a screenshot
taken in a container. Short version:

- Fonts are **self-hosted** now, so the Google Fonts trap no longer bites —
  but check `document.fonts` anyway, because the failure is silent either way
  and five faces should load.
- Launch Chromium with `executable_path="/opt/pw-browsers/chromium"`.
- Two `tests/test_guided_report_browser.py` tests fail locally on H.264 the
  container's Chromium cannot decode. They pass in CI. Confirm with
  `canPlayType('video/mp4; codecs="avc1.42E01E"')` before calling a failure
  real.

**The storefront cannot be rendered locally at all** — there is no local
Liquid render, so theme changes are verifiable only through the pinned tests
and `make theme-zip`. The app CAN be run, and should be:

```bash
python -m uvicorn --factory swinglab.web.app:create_app --port 8799
```

A green test suite is not a rendered page. The 2026-08-11 overhaul is the
reference case — all 70 design-gate tests passed while the app's hero
rendered ink-on-near-black and the nav was invisible, because the tokens were
right and the *grounds* were not.

## The mobile story

"The mobile app" is ambiguous here — say which one:

- The **PWA** is the installable app users actually get today, and it is
  finished and live.
- `mobile/` is an Expo scaffold that builds but has never been interacted
  with. Verify with `npx expo export`, not just `tsc` — typechecking never
  loads the Metro config and once hid a missing dependency that broke the
  bundler outright.

## Generated assets

Brand marks come from `store-assets/brand_mark.py` via `make_brand.py` — one
geometry emitted as both Pillow draws and SVG. Do not hand-edit the outputs;
regenerate. Fonts are not committed (`store-assets/README.md` has the fetch
commands).

**Drawn imagery carries the brand too, and it is the thing most often left
behind.** `report.html.j2` was still rendering a warm cream document from two
brands ago because nothing pointed at it. When the palette moves, so do:
`store-assets/make_assets.py` (the palette `campaign_assets.py` and
`pro_home_assets.py` both import), `config.yaml`'s `primary_color` /
`accent_color` (they tint runtime overlays drawn onto *video*), and any alt
text that names a colour — `sample.py`'s illustration alt text says which mark
is which colour, and leaving it stale describes a picture that is not there.
