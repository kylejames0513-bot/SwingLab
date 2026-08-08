# Verifying the look locally

Notes for anyone screenshotting this app or the theme from a sandboxed or CI
container. Both traps below produce screenshots that look plausible and are
wrong, which is worse than no screenshot at all.

## Webfonts do not load in headless Chromium here

The app shell and the storefront theme both pull Archivo and IBM Plex Mono
from `fonts.googleapis.com`. In an agent container behind the HTTPS proxy,
`curl` reaches Google Fonts but **headless Chromium does not** — it fails the
request with `net::ERR_CONNECTION_RESET`, because it is not configured to use
the proxy.

The failure is silent. The stylesheet link 404s, the font stack falls through
to `"Avenir Next", "Helvetica Neue", Helvetica, sans-serif`, and the page
renders in DejaVu Sans. Nothing in the screenshot says so.

That makes it easy to "verify" a typography change that never rendered — and
easy to conclude a before/after pair shows a font change when in fact both
sides fell back to the same substitute.

**Check before trusting a screenshot:**

```js
// zero means no webfont rendered
await page.evaluate(() => [...document.fonts].filter(f => f.status === "loaded").length)
```

**Fix — embed the real faces as data URIs.** `store-assets/Archivo-var.ttf`
and `DMMono-Regular.ttf` are already fetched by the asset-generator setup (see
`store-assets/README.md`); inject them after the page loads:

```python
faces = f"""<style>
@font-face {{ font-family:'Archivo';
  src:url(data:font/ttf;base64,{b64_archivo}) format('truetype');
  font-weight:100 900; font-display:block; }}
</style>"""
page.evaluate("html => document.head.insertAdjacentHTML('beforeend', html)", faces)
```

This affects local verification only. Real visitors load the fonts normally,
and no test asserts on rendered glyphs.

## Chromium build mismatch and H.264

Two symptoms, one cause: the pinned Playwright version expects a Chromium
build the image does not ship, and the build it does ship is the open-source
one, without proprietary codecs.

- `BrowserType.launch: Executable doesn't exist at .../chromium_headless_shell-<n>`
  — pass `executable_path="/opt/pw-browsers/chromium"` explicitly.
- `tests/test_guided_report_browser.py` tests that wait on
  `video.readyState >= 1` time out. The fixture MP4 is H.264 (`avc1`), and
  `canPlayType('video/mp4; codecs="avc1.42E01E"')` returns `""` in this
  build. CI runs `playwright install --with-deps chromium`, which ships the
  codec, so these pass there.

Confirm rather than assume:

```js
document.createElement("video").canPlayType('video/mp4; codecs="avc1.42E01E"')
```

An empty string means the container cannot decode it, and the failure is
environmental.

## Liquid is not rendered here

`storefront-theme/` is Shopify Liquid; there is no local renderer. Theme
changes can be checked three ways short of a real preview:

1. `theme-check` in CI (`.github/workflows/theme-check.yml`, fails on warning).
2. The app/storefront parity tests, which pin shared design tokens in both
   `web_layout.html.j2` and `base.css` so the two cannot drift.
3. A type specimen: load the theme's real `base.css` against static markup
   using the theme's own class names (`.sl-page-hero__title`, `.sl-section-head`,
   `.sl-h2`, `.sl-chip`, `.sl-mono-label`) with the fonts embedded as above.
   This is enough to judge type scale, tracking, and colour, though not layout
   logic driven by section settings.

Anything beyond that needs an unpublished theme preview in Shopify.
