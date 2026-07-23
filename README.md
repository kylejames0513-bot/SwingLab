# SwingLab

Golf swing analysis from a single phone video. Film yourself hitting balls,
point SwingLab at the clip, and get back per-swing metrics plus visual
deliverables:

- a labeled **key-position strip** (address / top / impact / finish),
- a smooth **quarter-speed slow-motion** clip per swing,
- a **centerline overlay** comparing the captured body (orange) against a
  corrected one (green) via an ankle-pinned shear,
- **report.html** with a metrics table, plain-English coaching notes, and every
  deliverable embedded, plus machine-readable **metrics.json**.

The whole product is white-label: brand name, logo, colors, footer, watermark,
disclaimer, and every detection/coaching threshold live in `config.yaml` — no
code edits needed to rebrand or retune.

## Requirements

- Python 3.11+
- `ffmpeg` and `ffprobe` on the PATH (called as external binaries — this also
  keeps licensing simple; revisit codec licensing only if you ever bundle
  ffmpeg into an installer)
- On headless Linux, mediapipe's native library needs OpenGL ES even in CPU
  mode: `apt install libgles2 libegl1 libgl1`
- `DejaVuSans-Bold` for image labels (ships with most Linux distributions;
  `apt install fonts-dejavu-core` if missing — Pillow falls back to a default
  font otherwise)

## Install

```bash
pip install -e .          # plus:  pip install -e ".[dev]"  for tests
```

The pose model (`pose_landmarker_lite.task`, ~5.8 MB) is downloaded once on
first run and cached inside the package under `swinglab/models/`.

## Usage

```bash
swinglab analyze path/to/video.mov --out results/ --hand right
swinglab analyze path/to/folder --batch
```

Useful flags:

- `--strikes "12.5,31.0"` — manual strike times (seconds), skips audio
  detection when it misses (or when the clip has no audio track)
- `--hand right|left` — golfer handedness (default right); also overrides the
  target-direction inference
- `--fast` — skip motion-interpolated slow motion (by far the longest step);
  results in a fraction of the time, slightly less smooth clips
- `--config path/to/config.yaml` — alternate branding/threshold config
- `--keep-work` — keep intermediate frames and audio for debugging

A short summary table plus the path to `report.html` is printed when done.
Each analyzed video gets its own session folder:

```
results/<video-name>/
├── report.html
├── metrics.json
└── media/
    ├── strip_s1.png      # key positions
    ├── overlay_s1.png    # centerline overlay
    ├── slowmo_s1.mp4     # quarter-speed clip
    └── ... one set per swing
```

## Web app

```bash
pip install -e ".[web]"
swinglab serve --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 for a branded upload page: drag a clip in (upload
progress shown), choose handedness and optionally **Fast mode**, and watch a
live status page — queue position while waiting, then per-swing progress —
while the analysis runs in the background (the exact same `pipeline` module
the CLI uses — nothing is duplicated in the web layer). `/sessions` lists
every past analysis.

Built to take real traffic on one machine:

- **Bounded worker pool** — `web.workers` analyses run at once; further
  uploads queue (FIFO) with their position shown, instead of a burst of
  uploads swamping the machine.
- **Durable jobs** — job state lives in SQLite next to the session folders.
  Anything queued or mid-analysis when the process dies is **re-queued
  automatically on restart** (the upload is still on disk), and finished
  results keep serving. Sessions from pre-database versions are imported on
  first start.
- **Guardrails** — upload size cap, per-IP active-job limit, and optional
  auto-deletion of old sessions (`web.retention_days`), all in `config.yaml`.
- **`/healthz`** — queue depth for load balancers and uptime monitors.

The JSON API under `/api` is the surface a future mobile app talks to:

- `POST /upload` — multipart upload (`video`, `hand`, optional `strikes`,
  optional `fast`); redirects to the session page, or returns
  `{"id", "url"}` when called with `Accept: application/json`
- `GET /api/session/{id}` — status, queue position, progress log, and (when
  done) `report_url` + `metrics_url`
- `GET /api/sessions` — recent sessions
- `GET /session/{id}/files/...` — report, media, and metrics.json

### Accounts and Pro memberships

With `web.require_account: true` (the shipped default), visitors sign up with
email + password (hashed locally with scrypt — no external auth service),
get `billing.free_per_month` analyses per calendar month, and can upgrade to
a **Pro subscription** for `billing.pro_per_month` (0 = unlimited). Each
account sees only its own history, and results are private to their owner
(sessions from before accounts stay reachable by link). Set
`require_account: false` for an open, no-login instance.

Payments run on Stripe and are **inert until configured** — the pricing page
shows Pro as "coming soon" until these environment variables are set:

| Variable | What it is |
| --- | --- |
| `SWINGLAB_SECRET` | long random string signing login cookies (always set this) |
| `STRIPE_SECRET_KEY` | from Stripe → Developers → API keys |
| `STRIPE_PRICE_ID` | the `price_...` id of your recurring Pro price |
| `STRIPE_WEBHOOK_SECRET` | from the webhook endpoint you point at `/webhooks/stripe` |
| `PUBLIC_BASE_URL` | e.g. `https://yourapp.up.railway.app` (checkout redirects) |

The price itself lives in Stripe — change it in their dashboard, never in
code. Checkout and subscription management happen on Stripe's hosted pages;
plan state only ever changes via the signed webhook.

### Gear shop (Shopify)

Connect a Shopify store and the app grows a **Gear** page (`/shop`) listing
the store's products, and — the interesting part — a **"Train what the report
flagged"** strip on every finished analysis: a quick tempo recommends the
tempo trainer, head sway recommends the anti-sway drills, and so on. Tag
products in Shopify to wire them up:

| Shopify product tag | Recommended when the analysis shows |
| --- | --- |
| `swinglab:tempo` | tempo ratio under `coaching.tempo_warn_below` |
| `swinglab:sway` | head sway beyond `coaching.sway_warn_sw` |
| `swinglab:hip-slide` | hip slide beyond `coaching.sway_warn_sw` |
| `swinglab:consistency` | tempo varying noticeably across swings |
| `swinglab:general` | anything (pads the list; what a clean swing sees) |

Like payments, the shop is **inert until configured** — no link, no page —
via two environment variables:

| Variable | What it is |
| --- | --- |
| `SHOPIFY_STORE_DOMAIN` | `yourstore.myshopify.com` (or the custom domain) |
| `SHOPIFY_STOREFRONT_TOKEN` | Storefront API access token (Shopify admin → Settings → Apps and sales channels → Develop apps → create an app with the Storefront API scope) |

Products, prices, and images live in Shopify — manage them in the Shopify
admin, never in code. The product list is cached in memory
(`shop.cache_minutes`), and a Shopify outage degrades to the last cached
list instead of an error. "Buy" links go to the Shopify storefront; SwingLab
never touches checkout.

For deployment — a one-command `docker compose up -d`, or a fresh-VM script —
see [deploy/README.md](deploy/README.md).

## How it works

1. **Probe** — `ffprobe` reads duration, resolution, fps, and rotation.
   Phone `.mov` files store rotation as metadata which ffmpeg applies
   automatically during extraction; SwingLab never rotates manually (that
   would double-rotate).
2. **Strike detection** — ball strikes are sharp audio transients. The mono
   16 kHz track is enveloped in 10 ms hops and peaks are found with
   configurable height / prominence / minimum-gap thresholds.
3. **Frame extraction** — for each strike `t`, the window `t−1.8s … t+0.8s`
   at 30 fps, 480 px wide. Input-side trimming (`-ss`/`-t` before `-i`) is
   load-bearing: output-side `-t` silently truncates stretched clips.
4. **Pose tracking** — mediapipe pose landmarker (tasks API; pip wheels
   0.10.30+ no longer ship `mp.solutions`). Frames failing an upright sanity
   check (nose above shoulders above hips above ankles) are dropped.
5. **Swing events** — address baseline, takeaway, top of backswing, impact
   (audio time mapped to the nearest frame), finish. All lateral measurements
   are normalized by shoulder width at address so numbers are comparable
   across camera distances.
6. **Metrics** — backswing/downswing durations, tempo ratio (benchmark 3.0),
   signed head sway and hip slide in shoulder widths (positive = away from
   the target), plus per-session mean and standard deviation.
7. **Deliverables and report** — Pillow-rendered strip and overlay, ffmpeg
   `minterpolate` slow motion (interpolate to a high frame rate first, THEN
   stretch), Jinja2 report.

## Configuration

See `config.yaml` — everything is documented inline. Highlights:

| Section | What it controls |
| --- | --- |
| `brand` | name, logo, colors, footer, watermark on/off, disclaimer |
| `detection` | audio peak height / prominence / minimum gap between swings |
| `coaching` | sway warning, tempo target/warning, consistency praise thresholds |
| `analysis` | window size, working/full resolutions, takeaway threshold |
| `slowmo` | slow-motion factor, clip bounds, output height, crf |
| `overlay` | captured/corrected skeleton colors, arrow threshold |
| `web` | worker pool size, upload size cap, per-IP job limit, session retention, `require_account` |
| `billing` | free/Pro analyses per month (price lives in Stripe, not here) |
| `shop` | Shopify gear shop on/off, product cache, recommendation tag prefix and count |

## Tests

```bash
python -m pytest
```

The suite covers the acceptance checks: strike detection within 50 ms on a
synthetic wav, graceful zero-strike behavior, portrait-rotation handling on a
display-matrix `.mov`, white-label config changes reaching the report and
overlays, and an end-to-end three-swing run (three metric rows, three strips,
three slow-motion clips, three overlays, one report) with a replayed pose
sequence so no human footage is required. Tests needing ffmpeg auto-skip when
it is not installed.

## Roadmap

- **Milestone 1 (done)** — CLI: video in → results folder out.
- **Milestone 2 (done)** — FastAPI web app wrapping the same pipeline module
  (upload, status, results page, JSON API).
- **Milestone 3 (done)** — production-ready web: durable SQLite-backed job
  queue with bounded workers and restart recovery, drag-and-drop upload with
  progress, live status with queue position, session history, fast mode,
  abuse guardrails, health endpoint, Docker deployment.
- **Milestone 4 (done)** — accounts (email + password), monthly free tier,
  Stripe Pro subscriptions with hosted checkout/portal and webhook-driven
  plan state, per-user private history, landing/pricing/account pages.
- **Shopify gear shop (done)** — `/shop` page backed by a Shopify store's
  Storefront API plus flag-matched training-aid recommendations on finished
  analyses; inert until the `SHOPIFY_*` environment variables are set.
- **Milestone 5** — white-label polish: PDF export, richer batch mode,
  password reset via email, API tokens for the mobile app.
- A native mobile app can sit on top of the existing JSON API (`/upload`,
  `/api/session/{id}`) without server changes.

## License notes

mediapipe is Apache 2.0 (commercial use fine). ffmpeg is LGPL/GPL and is
invoked as a system binary, which is standard practice for products.
