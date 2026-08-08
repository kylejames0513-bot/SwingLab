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
store still holds `og-swinglab.png` and `swinglab-favicon.png` from the v3
brand, so never point `images['…']` at a retired filename expecting the theme
copy to win. Ship new `caddieinsight-*` names instead of overwriting — an
overwrite bypasses the preview and leaves no rollback.

## Verifying visuals locally

Read `docs/quality/local-visual-verification.md` before trusting a screenshot
taken in a container. Short version:

- Headless Chromium here **cannot reach Google Fonts** and fails silently, so
  pages render in a system fallback. A before/after pair proves nothing about
  a font change. Embed the faces from `store-assets/*.ttf` as data URIs.
- Launch Chromium with `executable_path="/opt/pw-browsers/chromium"`.
- Two `tests/test_guided_report_browser.py` tests fail locally on H.264 the
  container's Chromium cannot decode. They pass in CI. Confirm with
  `canPlayType('video/mp4; codecs="avc1.42E01E"')` before calling a failure
  real.

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
