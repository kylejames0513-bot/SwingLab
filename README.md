# CaddieInsight

CaddieInsight (package name `swinglab`) is golf swing analysis from a single
phone video. Film yourself hitting balls, point CaddieInsight at the clip,
and get back per-swing metrics plus visual deliverables:

- a labeled **key-position strip** (address / top / impact / finish),
- a smooth **quarter-speed slow-motion** clip per swing,
- an **annotated coach replay** per swing — your own footage with the tracked
  body, hand path, and the key numbers burned in as they happen,
- a **centerline overlay** comparing the captured body (orange) against a
  corrected one (green) via an ankle-pinned shear,
- **report.html** with metrics tables, plain-English coaching notes, issue
  cards for everything the session flagged, an illustrated practice plan, and
  every deliverable embedded, plus machine-readable **metrics.json**.

The whole product is white-label: brand name, logo, colors, footer, watermark,
disclaimer, and every detection/coaching threshold live in `config.yaml` — no
code edits needed to rebrand or retune.

## Project foundation

CaddieInsight is the customer-facing product name. The Python distribution,
import namespace, command, database filename, and several Shopify identifiers
remain `swinglab` for compatibility while the codebase is migrated in stages.

- [Architecture and project boundaries](docs/architecture.md)
- [Environment-variable contract](docs/environment.md)
- [Production and Railway contract](docs/deployment.md)
- [Architecture decisions](docs/adr/0001-caddieinsight-naming-and-compatibility.md)

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
- `--angle face-on|dtl` — camera angle (default face-on). Every body-drift
  and angle metric is defined face-on; `dtl` (down the line) keeps tempo,
  durations and consistency and honestly reports the rest as not measured
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
    ├── replay_s1.mp4     # annotated coach replay
    └── ... one set per swing
```

### What the report measures — honestly

Everything comes from one hip-height phone camera and 2D pose landmarks
projected into the image plane. CaddieInsight tracks the golfer's **body** — it
does not track the club, does not reconstruct 3D, and makes no ball-flight
claims. Angle metrics are the angles **as seen from the camera** (face-on),
and lateral metrics are normalized by shoulder width at address (SW) so
numbers are comparable across camera distances.

**Camera-angle truth.** Every lateral and angular metric below is *defined*
face-on. A down-the-line clip (`--angle dtl`, or the upload form's radio)
gets timing only — tempo, backswing/downswing durations, consistency, which
are camera-angle-agnostic — and the face-on-only metrics are written as
NaN/null with a session note saying so, rather than silently mis-measured.
As a cross-check, the projected shoulder-width-to-height ratio at address
(wide face-on, narrow down the line) is compared against the chosen angle;
when the footage strongly disagrees, the report carries a low-confidence
warning. The thresholds are deliberately conservative — uncertain footage
warns nobody. When target-direction inference falls back to its last-resort
guess, the swing's coaching notes now carry an explicit low-confidence line
about the toward/away signs. Every metric also ships a plain-English
explainer (`swinglab/explainers.py`) shown behind tap-to-open expanders in
the report tables and on `/progress` — benchmarks are framed as references
to move toward, never day-one targets.

Per swing:

- **Backswing / downswing duration and tempo ratio** — time from takeaway to
  the top vs top to impact (benchmark 3:1).
- **Head sway and hip slide** (address→top and top→impact, in SW) — signed
  lateral drift; positive = away from the target.
- **Head dip** (address→impact, in SW) — how far the head drops on the way
  to the ball, from the ear/nose centroid with single-frame jitter smoothed
  out. A small squat is normal; a large dip moves the swing's low point.
- **Lead-arm angle at impact** (degrees; 180 = straight) — the
  shoulder–elbow–wrist angle of the lead arm at the strike, as projected in
  the camera's view.
- **Shoulder tilt at impact, and its change from address** (degrees, measured
  face-on) — positive means the trail shoulder is lower. At impact the trail
  shoulder should be clearly lower; level or reversed shoulders are the
  classic hang-back pattern.
- **Finish balance** (in SW) — mean drift of the ankle midpoint during the
  frames after the finish. A held, quiet finish reads near zero; a step or
  stumble reads tenths of a shoulder width.

Session-level mean and standard deviation cover all of the above, and every
threshold that turns a number into a flag lives in `config.yaml`.

### Issue cards — "What to work on"

Each flag the session fires becomes a card in the report: the session value
against its benchmark, a per-swing sparkline (flagged swings marked in the
accent color), two honest sentences on why it matters, a one-line fix, and
links straight to the matching drills in the practice plan. Cards are sorted
by severity — "major" when the session mean breaches the threshold or every
measured swing is flagged.

### Coach replay (`replay_sN.mp4`)

The annotated replay is exactly what it sounds like: the engine annotating
the golfer's **own footage**. The same slow-motion window is re-rendered with
the tracked skeleton, a fading trace of the **hand path** (wrist centroid —
the body point we actually track; CaddieInsight never claims club tracking), a
dashed centerline from the setup position, and metric chips that appear at
each swing event (top, impact, finish) and persist. The replay is never
motion-interpolated — that keeps the burned-in text crisp and the render
fast — so `--fast` does not change it. Set `slowmo.annotated: false` in
`config.yaml` to skip it entirely.

### Practice plans in the report

Every report ends with a practice plan built from what the session flagged.
Each coaching flag (`tempo`, `sway`, `hip-slide`, `head-dip`,
`arm-extension`, `balance`, `consistency` — `shoulder-tilt` shares the
impact-extension drills, since both are flip-at-impact faults) maps to 2–3
curated drills in `swinglab/drills.py` — an aim, a step-by-step protocol, a
dosage, and a measurable re-film target expressed in the same numbers the
report prints, so "fixed" means the next report says so. A session with no
flags gets a maintenance set instead. The threshold numbers inside the drill
text come from the `coaching` section of `config.yaml`, so retuning the
thresholds retunes the targets with no code edits.

Every drill also ships with a **follow-along setup diagram and a looping
animation** of its key positions — hand-built inline SVG with CSS-only
crossfades, drawn in the configured brand colors. No JavaScript, no external
assets: the report stays a single self-contained HTML file that renders
offline. The animation sits behind a "Show the motion" toggle and freezes on
the setup pose for viewers who ask their device for reduced motion.

Set `shop.store_url` in `config.yaml` (the shipped config points at the
CaddieInsight store; empty = no link) and the plan ends with a quiet "Matched
training aids" link to that store's `/collections/swinglab-gear` collection —
the same tag-matched gear the web app recommends on finished analyses.

## Web app

```bash
pip install -e ".[web]"
swinglab serve --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 for a branded upload page: drag a clip in (upload
progress shown), choose handedness and **camera angle** (face-on = full
report; down the line = tempo & rhythm only, stated up front), optionally a
**club** and — under Advanced — **Fast mode** or manual strike times. Then
watch a live status page — queue position while waiting, then per-swing
progress — while the analysis runs in the background (the exact same
`pipeline` module the CLI uses — nothing is duplicated in the web layer).
`/sessions` lists every past analysis. Failed analyses are explained in
plain English on the status page (sound off? body out of frame?) with a
filming-checklist link — the raw pipeline error stays available via the
JSON API and the CLI.

**Public sample report** — `GET /sample-report` serves a complete example
report generated at startup from synthetic session data run through the
real coaching/report machinery (`swinglab/sample.py`), with a banner saying
it's a sample and drawn stand-in imagery (never fake footage; the video
sections are simply absent). No login required — it's linked from the
landing page ("See a sample report first") so visitors can see the product
before the signup wall.

**Club context (display only)** — the upload form's optional club select
(Driver / Fairway wood / Hybrid / Iron / Wedge) is stored on the job and in
metrics.json's `meta` block and shown as a chip on the report header, the
session list, and `/progress` (which gains a club filter once more than one
club is present). There are **no per-club thresholds yet** — the club
changes no numbers and no flags; it exists so sessions compare cleanly.

Built to take real traffic on one machine:

- **Bounded worker pool** — `web.workers` analyses run at once; further
  uploads queue (FIFO) with their position shown, instead of a burst of
  uploads swamping the machine.
- **Durable jobs** — job state lives in SQLite next to the session folders.
  Anything queued or mid-analysis when the process dies is **re-queued
  automatically on restart** (the upload is still on disk), and finished
  results keep serving. Sessions from pre-database versions are imported on
  first start.
- **Guardrails** — upload size cap, per-IP active-job limit, per-clip
  length cap (`analysis.max_video_s`, shipped 300 s) and strike cap
  (`detection.max_strikes`, shipped 8 — the first N are analyzed and the
  report says so), login/signup throttling
  (`web.login_attempts_per_15min`, `web.signups_per_hour_per_ip`), and
  auto-deletion of old sessions (`web.retention_days`), all in
  `config.yaml`. A client that disconnects mid-upload leaves nothing
  behind — no queued ghost job, no quota charge, no held per-IP slot.
- **Proxy-aware client IPs** — behind Railway (or any reverse proxy) every
  request arrives from the proxy's address, which would make the per-IP
  limit cap the whole site. `web.trusted_proxies` (shipped `"*"` for PaaS)
  says whose `X-Forwarded-For` to believe; see config.yaml for the honest
  spoofing trade-off and when to list explicit proxy IPs instead.
- **Data retention, stated plainly** — sessions hold identifiable video of
  people. The shipped config deletes finished sessions after 180 days and
  deletes the raw upload as soon as the report exists
  (`web.delete_source_after_done`; report/media/metrics are kept —
  re-analyzing needs a re-upload). The same switch drops the upload when an
  analysis FAILS: failed jobs are terminal, don't count against quota, and
  keeping their sources would let refused clips (e.g. over-length videos)
  fill the disk for free. The bare-code defaults keep everything
  forever for white-label installs that manage retention themselves —
  turning retention off is a choice you should be able to defend
  (GDPR storage minimization).
- **`/healthz`** — queue depth plus `disk_free_mb` and `sessions_count`
  for load balancers and uptime monitors; alert on disk before it's full.
- **Ops extras** — optional Sentry error monitoring: `pip install
  "swinglab[ops]"` and set `SENTRY_DSN`; with either missing it is
  completely inert. Backups of the SQLite database (which holds paid
  entitlements): see the tested Litestream recipe in
  [deploy/README.md](deploy/README.md).

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
email + password (hashed locally with scrypt — no external auth service) —
or, once SMTP is configured, with just their email via a six-digit sign-in
code ("One account: email-code sign-in" below). Accounts get
`billing.free_per_month` analyses per calendar month, and can upgrade to
**Pro** for `billing.pro_per_month` (0 = unlimited). Each
account sees only its own history, and results are private to their owner
(sessions from before accounts stay reachable by link). Set
`require_account: false` for an open, no-login instance.

Pro can be sold two ways, both **inert until configured** — the pricing page
shows Pro as "coming soon" until one is set up. When both are configured,
buyers are sent to the Shopify store.

**The coach replay is the Pro quality line** (`billing.replay_pro_only`,
shipped `true`): with accounts on, the annotated replay — the report's most
shareable artifact — is rendered only for jobs whose owner has Pro *at
analysis time*. A free user's report keeps everything else (metrics, slow
motion, overlays, drills) and shows an honest locked note with a `/pricing`
link in the replay slot; the render itself is skipped, so the gate saves
the CPU too. Upgrading later never rewrites an old report — re-film to get
the replay. Open instances (`require_account: false`), CLI runs, and the
public sample report are **never** gated, and the bare-code default is
`false` — the same deliberate DEFAULTS-vs-shipped difference as
`retention_days`, pinned by tests.

The pricing page shows the annual plan first (the anchor) with the monthly
plan second, using the **display-only** strings
`billing.pro_price_monthly_text` / `billing.pro_price_annual_text` from
`config.yaml` (shipped: `$9.99/month` and `$79.99/year — $6.67/month`).
These are labels, not billing: what is actually charged always lives in
Shopify/Stripe, and the page says honestly that the store's monthly option
is a fixed-length 31-day pass — nothing auto-renews unless the store's
subscription setup or Stripe billing handles renewal.

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

### Progress and weekly practice plans

Two retention surfaces, both built from numbers the pipeline already wrote —
nothing is ever estimated after the fact:

**Progress dashboard (`/progress`)** — every logged-in account gets one card
per metric with data: an inline-SVG trend chart of the session means (dots on
sessions, dashed line + shaded band at the flag threshold), latest / best /
change-vs-first stats, and a strip showing which flags keep firing across
sessions. Legacy sessions that predate the newer metrics simply contribute
the fields they have; sessions with no readable numbers are skipped. With
fewer than two measured sessions the page says so honestly instead of
charting a single dot. Requires `web.require_account: true` (there is no
per-user history to chart in open mode — the route 404s).

**Weekly practice-plan email** — the "one drill a week" promise, made real,
and strictly opt-in. It only ever sends when ALL of these hold:

- SMTP is configured (`SWINGLAB_SMTP_URL` + `SWINGLAB_MAIL_FROM`, the same
  variables as verification/reset email) — with SMTP unset the feature has
  zero behavior;
- `web.digest_enabled: true` in config.yaml (the shipped default);
- the user asked for it — an **unchecked** "Email me one drill a week" box at
  signup, a toggle on the account page, and a signed one-click unsubscribe
  link in every email (works logged out).

Each email is self-contained HTML (inline styles, brand colors, no images or
external assets): the drills for the latest finished session's flags — name,
dosage, and the same pass-mark numbers the report prints — plus one honest
progress line once two sessions exist, and links to the latest report and
`/progress`. An hourly scheduler thread sends at most one email per user per
~week (6.5 days), only to accounts with at least one finished session, and
stamps the send time *before* attempting delivery so a crash can never
double-send within a week. Set `PUBLIC_BASE_URL` so the email's links are
absolute.

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
  account** — as does signing in with an emailed code once SMTP is
  configured (see "One account" below): either way the claim lands on
  that row, so the Shopify link and any Pro purchase already granted by
  the order webhooks carry over — one user, everything kept. Until then,
  a password login attempt with that email gets pointed at the right
  next step (the code flow, or "create your password to finish setup")
  instead of a misleading "wrong password".
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
user proves the email is theirs once, at claim time (an emailed sign-in
code when SMTP is on, or by setting an app password). Store customers
created without an email address are skipped (there is nothing to match
on).

**Optional email verification (SMTP)** — inert until configured, like
every other integration:

| Variable | What it is |
| --- | --- |
| `SWINGLAB_SMTP_URL` | e.g. `smtp+starttls://user:pass@smtp.example.com:587` — also `smtp://` (plain, local relays) and `smtps://` (implicit TLS, port 465); credentials URL-encoded |
| `SWINGLAB_MAIL_FROM` | the From address, e.g. `CaddieInsight <no-reply@yourdomain.com>` |

With both set, claiming an email that already has anything attached (a
store account, or a Pro purchase made before signup) requires a 6-digit
code emailed to that address — 10-minute expiry, single-use, stored
hashed, rate-limited per email — and **password reset** appears on the
login page using the same codes. Standard library SMTP only; no new
dependencies.

> **Security note:** without SMTP configured, behavior is unchanged from
> previous versions: signing up with an email claims whatever that email
> already has (store account, pre-signup purchase, or a passwordless
> account) with no inbox proof — the same trade-off the buy-before-signup
> claim has always had, kept deliberately so the app works with zero
> email infrastructure. Configuring SMTP closes it by verifying control
> of the inbox before a claim.

### One account: email-code sign-in

With SMTP configured, the login page stops asking for a password
(`web.passwordless_login`, shipped and defaulted `true`): it asks for the
email first, mails a six-digit sign-in code, and a correct code signs the
visitor in. The same step handles every account state, which is what makes
store and app identity **one account** — the email used at Shopify
checkout *is* the app login:

- an existing app account simply logs in;
- an unclaimed store account (provisioned by the customer webhooks) logs
  in **and is claimed on the spot** — the code proves control of the
  inbox, which is strictly stronger proof than the old password-claim,
  so the Shopify link and any Pro time carry over with no extra step;
- an email with no account at all gets one created — signup and login are
  the same "Continue with email" flow, and there is no separate signup to
  find.

Neither the page nor the email reveals which of the three happened: every
address gets the same "check your email" screen and the same message, so
the form cannot be used to test which emails have accounts. The codes are
the existing machinery — hashed at rest, 10-minute expiry, single-use,
burned after 5 wrong guesses — and both requesting and mis-entering codes
draw on the login throttle limits (`web.login_attempts_per_15min`, per
email and per IP). A correct code also marks the email verified, which is
what the store-claim rests on.

Passwords stay a first-class fallback, never a dead end: accounts that
have one can always use it ("Use your password instead" on the login
page, where password reset also lives), and passwordless accounts can add
one from the account page ("Add a password (optional)") — being logged in,
which took a code, is the proof of ownership. Setting a password by
signing up with a passwordless account's email also works, and requires
the emailed code first while SMTP is on.

The whole feature is inert without SMTP: with `SWINGLAB_SMTP_URL` or
`SWINGLAB_MAIL_FROM` unset, the login and signup pages keep the classic
password flows exactly — which is why the flag can ship `true` without
affecting white-label installs that have no email infrastructure. Set
`web.passwordless_login: false` to force password flows even with SMTP
configured. Honest caveat: if an operator runs with SMTP for a while and
then turns it off, accounts that never added a password cannot sign in
until email returns (or until they set a password via signup — see the
security note above); the account page says so when it applies.

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
| `swinglab:head-dip` | head dropping beyond `coaching.head_dip_warn_sw` on the way to impact |
| `swinglab:arm-extension` | lead arm bent under `coaching.lead_arm_warn_deg` at impact (the shoulder-tilt flag matches this tag too) |
| `swinglab:balance` | feet drifting beyond `coaching.finish_balance_warn_sw` during the finish hold |
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
list instead of an error. "Buy" links go to the Shopify storefront;
CaddieInsight never touches checkout.

For deployment — a one-command `docker compose up -d`, or a fresh-VM script —
see [deploy/README.md](deploy/README.md).

**Current domain layout:** `caddieinsight.com` is the Shopify storefront and
`app.caddieinsight.com` is the Railway application. `PUBLIC_BASE_URL` must be
the application origin. This repository does not manage DNS or Railway
secrets; see [deploy/README.md](deploy/README.md) and
[docs/deployment.md](docs/deployment.md) for the preserved production
contract.

## Measuring what matters

Five KPIs, computed from the app's own SQLite state (`swinglab/kpis.py`) —
no analytics service, no tracking pixels, nothing leaves the box. These are
the numbers that decide whether the product is working, with the targets
from the strategy analysis:

| KPI | Definition | Target |
| --- | --- | --- |
| `activation_rate` | of accounts created in the window, the share whose **first DONE report** landed within 7 days of signup | **> 50%** |
| `w1_refilm_rate` | of those accounts with ≥ 1 DONE analysis, the share whose **second** DONE analysis landed within 7 days of their first — the re-film habit is the core loop | **> 25%** |
| `free_to_pro_rate` | of the window's *activated* accounts, the share that gained Pro within 30 days of signup (Shopify grants timed by the order ledger's `applied_at`; a live Stripe subscription counts — Stripe state carries no grant timestamp) | **2%+** |
| `weekly_retained_filmers` | a count, not a rate: accounts with ≥ 1 DONE analysis in the trailing 7 days | grow it |
| `gear_attach_per_100_reports` | non-cancelled **gear orders** in the window per 100 DONE reports in the window | — |

The gear side is measurable because the `orders/paid` webhook now records
every **non-Pro** line item into a `gear_orders` ledger (order id, SKU,
title, quantity, normalized email) with the same replay idempotence as the
Pro ledger — a re-delivered webhook never double-counts, and
`orders/cancelled` marks the rows out of the KPI without losing the audit
trail. Pro grant processing is unchanged.

Honesty rule: any metric the data cannot support returns **None with a
stated reason** (accounts disabled, no database yet, empty cohort, no gear
ledger…) — a number is never fabricated. Cohorts count claimed accounts
only; unclaimed store stubs can't log in, so they can't deflate the rates.

Two surfaces, same numbers:

```bash
swinglab kpis                 # clean table, honest "—  (reason)" rows
swinglab kpis --since 30      # trailing 30-day window (default 90)
swinglab kpis --json          # machine-readable, same payload as the endpoint
```

`GET /admin/kpis` (optionally `?since=30`) returns the JSON payload for
dashboards and cron. It is gated by an environment variable:

```bash
SWINGLAB_ADMIN_TOKEN="$(openssl rand -hex 32)"   # set on the server
curl -H "Authorization: Bearer $SWINGLAB_ADMIN_TOKEN" https://your-app/admin/kpis
```

The token is compared in constant time, and the route answers **404** —
not 401/403 — when the variable is unset *or* the token is wrong, so the
endpoint's existence is invisible without the credential. With the
variable unset the endpoint simply doesn't exist, the same
inert-until-configured rule as every other integration.

## How it works

1. **Probe** — `ffprobe` reads duration, resolution, fps, and rotation.
   Phone `.mov` files store rotation as metadata which ffmpeg applies
   automatically during extraction; CaddieInsight never rotates manually
   (that would double-rotate).
2. **Strike detection** — ball strikes are sharp audio transients. The mono
   16 kHz track is enveloped in 10 ms hops and peaks are found with
   configurable height / prominence / minimum-gap thresholds.
3. **Frame extraction** — for each strike `t`, the window `t−1.8s … t+0.8s`
   at 30 fps, 480 px wide. Sources filmed at 50 fps or better are analyzed
   at `min(source_fps, 60)` instead (`analysis.auto_fps`, on by default):
   the downswing is only 7–8 frames at 30 fps, so tempo carries a ~13%
   quantization error that 60 fps halves. The rate actually used is
   recorded in `metrics.json` (`meta.analysis_fps`) and shown in the
   report's session table. Input-side trimming (`-ss`/`-t` before `-i`) is
   load-bearing: output-side `-t` silently truncates stretched clips.
4. **Pose tracking** — mediapipe pose landmarker (tasks API; pip wheels
   0.10.30+ no longer ship `mp.solutions`). Frames failing an upright sanity
   check (nose above shoulders above hips above ankles) are dropped, and so
   are frames whose core landmarks (shoulders/hips/ankles) score below a
   visibility floor — an occluded body produces hallucinated coordinates.
   Each swing also gets a tracking-quality check (fraction of dropped
   frames + largest single-frame core-landmark jump vs shoulder width);
   when it's poor — the signature of the detector locking onto another
   person mid-swing — the swing's coaching notes carry an honest
   low-confidence line instead of silently wrong numbers.
5. **Swing events** — address baseline, takeaway, top of backswing, impact
   (audio time mapped to the nearest frame), finish. All lateral measurements
   are normalized by shoulder width at address so numbers are comparable
   across camera distances.
6. **Metrics** — backswing/downswing durations, tempo ratio (benchmark 3.0),
   signed head sway and hip slide in shoulder widths (positive = away from
   the target), head dip into impact, lead-arm angle and shoulder tilt at
   impact (image-plane angles, as seen from the camera), finish balance,
   plus per-session mean and standard deviation.
7. **Deliverables and report** — Pillow-rendered strip and overlay, ffmpeg
   `minterpolate` slow motion (interpolate to a high frame rate first, THEN
   stretch), the annotated replay (discrete frames with Pillow-burned
   skeleton/hand-path/chips, encoded without interpolation so the text stays
   crisp), Jinja2 report with inline-SVG issue-card sparklines and drill
   diagrams/animations.

## Configuration

See `config.yaml` — everything is documented inline. Highlights:

| Section | What it controls |
| --- | --- |
| `brand` | name, logo, colors, footer, watermark on/off, disclaimer, `support_text` (shown where users need the operator, e.g. password reset while SMTP is unconfigured) |
| `detection` | audio peak height / prominence / minimum gap between swings, per-clip strike cap (`max_strikes`, shipped 8 — first N analyzed, honestly noted) |
| `coaching` | flag thresholds: sway warning, tempo target/warning, consistency praise, head dip (`head_dip_warn_sw`), lead-arm angle (`lead_arm_warn_deg`), shoulder tilt (`shoulder_tilt_impact_min_deg`), finish balance (`finish_balance_warn_sw`) |
| `analysis` | window size, working/full resolutions, takeaway threshold, finish-hold frames for the balance metric (`finish_hold_frames`), per-clip length cap (`max_video_s`, shipped 300 s, 0 = off), high-fps analysis (`auto_fps`: sources ≥ 50 fps analyzed at min(source, 60)) |
| `slowmo` | slow-motion factor, clip bounds, output height, crf; annotated replay on/off (`annotated`) and hand-trail fade (`trail_fade_s`) |
| `overlay` | captured/corrected skeleton colors, arrow threshold |
| `web` | worker pool size, upload size cap, per-IP job limit, proxy trust for real client IPs (`trusted_proxies`), login/signup throttles, session retention (shipped 180 days; raw upload deleted after analysis via `delete_source_after_done` — both off in bare-code defaults, see the GDPR note in config.yaml), `require_account`, email-code sign-in (`passwordless_login`, shipped on — self-disables without SMTP), weekly digest on/off (`digest_enabled`) |
| `billing` | free/Pro analyses per month, the coach-replay Pro gate (`replay_pro_only`, shipped on — off in bare-code defaults), plus `pro_price_*_text` display strings for the pricing page (what's charged lives in Shopify/Stripe, not here) |
| `shop` | Shopify gear shop on/off, product cache, recommendation tag prefix and count, `store_url` for the report's gear link |

## Tests

```bash
python -m pytest
```

The suite covers the acceptance checks: strike detection within 50 ms on a
synthetic wav, graceful zero-strike behavior, portrait-rotation handling on a
display-matrix `.mov`, white-label config changes reaching the report and
overlays, and an end-to-end three-swing run (three metric rows, three strips,
three slow-motion clips, three annotated replays, three overlays, one report)
with a replayed pose sequence so no human footage is required. Tests needing
ffmpeg auto-skip when it is not installed.

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
- **One account (done)** — passwordless email-code sign-in: with SMTP
  configured, the store email is the app identity; one "Continue with
  email" flow logs in, claims store accounts, or creates accounts, and a
  password is optional. Self-disables without SMTP.
- **Program depth (done)** — four new 2D-honest metrics (head dip, lead-arm
  extension, shoulder tilt, finish balance), issue cards with per-swing
  sparklines, illustrated drills (inline-SVG diagrams + CSS-only
  animations), and the annotated coach replay (`replay_sN.mp4`).
- **Milestone 5** — white-label polish: PDF export, richer batch mode,
  API tokens for the mobile app. (Password reset via email shipped with
  the Shopify account sync above.)
- A native mobile app can sit on top of the existing JSON API (`/upload`,
  `/api/session/{id}`) without server changes.

## License notes

mediapipe is Apache 2.0 (commercial use fine). ffmpeg is LGPL/GPL and is
invoked as a system binary, which is standard practice for products.
