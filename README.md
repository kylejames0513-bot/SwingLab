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

### Practice plans in the report

Every report ends with a practice plan built from what the session flagged.
Each coaching flag (`tempo`, `sway`, `hip-slide`, `consistency`) maps to 2–3
curated drills in `swinglab/drills.py` — an aim, a step-by-step protocol, a
dosage, and a measurable re-film target expressed in the same numbers the
report prints, so "fixed" means the next report says so. A session with no
flags gets a maintenance set instead. The threshold numbers inside the drill
text come from the `coaching` section of `config.yaml`, so retuning the
thresholds retunes the targets with no code edits.

Set `shop.store_url` in `config.yaml` (the shipped config points at the
SwingLab store; empty = no link) and the plan ends with a quiet "Matched
training aids" link to that store's `/collections/swinglab-gear` collection —
the same tag-matched gear the web app recommends on finished analyses.

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
**Pro** for `billing.pro_per_month` (0 = unlimited). Each
account sees only its own history, and results are private to their owner
(sessions from before accounts stay reachable by link). Set
`require_account: false` for an open, no-login instance.

Pro can be sold two ways, both **inert until configured** — the pricing page
shows Pro as "coming soon" until one is set up. When both are configured,
buyers are sent to the Shopify store.

**Selling Pro on the Shopify store** (one checkout for gear and
memberships): create a product whose variant SKUs map to days of access in
`billing.shopify_skus` (shipped mapping: `SL-PRO-1MO` → 31 days,
`SL-PRO-12MO` → 365), point `orders/paid` + `orders/cancelled` webhooks at
`/webhooks/shopify`, and set:

| Variable | What it is |
| --- | --- |
| `SWINGLAB_SECRET` | long random string signing login cookies (always set this) |
| `SHOPIFY_STORE_DOMAIN` | `yourstore.myshopify.com` (shared with the gear shop) |
| `SHOPIFY_WEBHOOK_SECRET` | signing secret from Settings → Notifications → Webhooks |

A paid order extends Pro on the account matching the checkout email; a
purchase made before signup is claimed automatically when that email creates
an account or logs in. Replayed webhooks never double-grant, cancelled
orders take their days back, and Shopify's Subscriptions app works
unchanged (each billing cycle's order re-extends access).

**Selling Pro as a Stripe subscription:**

| Variable | What it is |
| --- | --- |
| `STRIPE_SECRET_KEY` | from Stripe → Developers → API keys |
| `STRIPE_PRICE_ID` | the `price_...` id of your recurring Pro price |
| `STRIPE_WEBHOOK_SECRET` | from the webhook endpoint you point at `/webhooks/stripe` |
| `PUBLIC_BASE_URL` | e.g. `https://yourapp.up.railway.app` (checkout redirects) |

Either way, prices live in Shopify/Stripe — change them in their dashboards,
never in code. Checkout happens on their hosted pages, and plan state only
ever changes via the signed webhooks.

### Account sync with Shopify

Accounts start on the store: a customer created in Shopify automatically
exists in the web app, and everything they bought is waiting when they
finish setup there. In the Shopify admin, under **Settings → Notifications
→ Webhooks**, add three more webhooks — `customers/create`,
`customers/update`, and `customers/delete` — pointing at the **same**
`https://<your-app>/webhooks/shopify` endpoint the order webhooks use.
One endpoint, one signing secret (`SHOPIFY_WEBHOOK_SECRET`), nothing else
to configure.

What each event does:

- **customers/create, customers/update** — creates a passwordless "store
  account" for the customer's (normalized) email, tagged with the Shopify
  customer id — or, if an account already exists, just links/refreshes
  that id. An existing password or email is **never** overwritten, and
  replayed webhooks land on the same row (no duplicates).
- Signing up in the app with a store account's email **claims the same
  account**: the password is set on that row, so the Shopify link and any
  Pro purchase already granted by the order webhooks carry over — one
  user, everything kept. Until then, a login attempt with that email gets
  a "create your password to finish setup" pointer instead of a
  misleading "wrong password".
- **customers/delete** — deletes the app user only when it is an
  unclaimed stub (no password, no analyses); any Pro days it still
  carried are parked and reclaimed if that email signs up later. A
  claimed account merely loses its store link — store-side deletion never
  destroys app data.
- **customers/redact** (GDPR) — same as delete, and additionally erases
  the Shopify-sourced profile fields on claimed accounts and any parked
  purchase for a deleted stub's email. `customers/data_request` and
  `shop/redact` are acknowledged (200) and logged.

**Limitations, honestly:** Shopify does not expose customer credentials,
so store passwords cannot sync — the store account carries over and the
user sets their app password once, at claim time. Store customers created
without an email address are skipped (there is nothing to match on).

**Optional email verification (SMTP)** — inert until configured, like
every other integration:

| Variable | What it is |
| --- | --- |
| `SWINGLAB_SMTP_URL` | e.g. `smtp+starttls://user:pass@smtp.example.com:587` — also `smtp://` (plain, local relays) and `smtps://` (implicit TLS, port 465); credentials URL-encoded |
| `SWINGLAB_MAIL_FROM` | the From address, e.g. `SwingLab <no-reply@yourdomain.com>` |

With both set, claiming an email that already has anything attached (a
store account, or a Pro purchase made before signup) requires a 6-digit
code emailed to that address — 10-minute expiry, single-use, stored
hashed, rate-limited per email — and **password reset** appears on the
login page using the same codes. Standard library SMTP only; no new
dependencies.

> **Security note:** without SMTP configured, behavior is unchanged from
> previous versions: signing up with an email claims whatever that email
> already has (store account, pre-signup purchase) with no inbox proof —
> the same trade-off the buy-before-signup claim has always had, kept
> deliberately so the app works with zero email infrastructure.
> Configuring SMTP closes it by verifying control of the inbox before a
> claim.

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
| `shop` | Shopify gear shop on/off, product cache, recommendation tag prefix and count, `store_url` for the report's gear link |

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
- **Shopify account sync (done)** — customer webhooks provision store
  accounts in the app, signup claims them with purchases intact, and
  optional SMTP adds code-verified claims plus password reset via email
  (the Milestone-5 reset item, shipped early).
- **Milestone 5** — white-label polish: PDF export, richer batch mode,
  API tokens for the mobile app. (Password reset via email shipped with
  the Shopify account sync above.)
- A native mobile app can sit on top of the existing JSON API (`/upload`,
  `/api/session/{id}`) without server changes.

## License notes

mediapipe is Apache 2.0 (commercial use fine). ffmpeg is LGPL/GPL and is
invoked as a system binary, which is standard practice for products.
