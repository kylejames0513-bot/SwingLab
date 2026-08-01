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
- [Shopify customer-sync runbook](docs/shopify-customer-sync.md)
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
swinglab batch path/to/clips.jsonl --out results/ --dry-run --json
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

### Repeatable manifest batches

`swinglab analyze folder --batch` remains the quick way to process every video
currently in one folder. For a named, reviewable set of clips with its own
camera and golfer context, use the sequential JSONL batch command instead:

```bash
# Validate every row and print one machine-readable plan. Nothing is analyzed
# and no resume state is written in dry-run mode.
swinglab batch clips.jsonl --out results/ --dry-run --json

# Run serially. A state file named clips.jsonl.state.json is atomically updated
# after each completed report, so an interrupted run can be resumed.
swinglab batch clips.jsonl --out results/
swinglab batch clips.jsonl --out results/ --resume
```

Each non-blank line is one JSON object. `id` and `path` are required; relative
paths resolve from the manifest's directory. `hand`, `angle`, `club`, `level`,
and manual `strikes` are per-clip context and are written through to that
clip's report and `metrics.json`.

```json
{"id":"driver-baseline","path":"clips/driver-baseline.mov","hand":"right","angle":"face-on","club":"driver","level":"improving","strikes":[12.5,31.0]}
{"id":"wedge-dtl","path":"clips/wedge-dtl.mp4","hand":"left","angle":"dtl","club":"wedge","level":"new"}
```

The manifest is fully validated before analysis starts: IDs must be unique,
video paths must exist, context values must be supported, and supplied strike
times must be finite and strictly increasing. `--json` reserves stdout for one
summary object; progress and failures go to stderr. `--resume` skips only a
completed ID whose normalized instruction still matches and whose report still
exists. The saved instruction also includes the source file's size and modified
time, so a re-exported clip is not silently treated as an old report. If an
instruction or source changed, choose a new `--state` path after reviewing the
change; this fails closed instead of silently mixing reports from two plans. The
runner is intentionally serial—there is no hidden parallel queue.

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

Every coaching-eligible report ends with a practice plan built from what the
session flagged. A report that needs a re-film stops at capture guidance and
unannotated slow motion; it does not present measurements, derived coaching
visuals, drills, or commerce from data the app has rejected.
Each coaching flag (`tempo`, `sway`, `hip-slide`, `head-dip`,
`arm-extension`, `shoulder-tilt`, `balance`, `consistency`) maps to
evidence-matched
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
  completely inert. The inactive Stage 0B backup foundation creates WAL-safe
  SQLite snapshots, checksummed report/media bundles, and scratch-only restore
  drills; see the
  [backup and recovery runbook](docs/operations/backup-recovery.md).

The JSON API under `/api` is the surface a future mobile app talks to:

- `POST /upload` — multipart upload (`video`, `hand`, optional `strikes`,
  optional `fast`); redirects to the session page, or returns
  `{"id", "url"}` when called with `Accept: application/json`
- `GET /api/session/{id}` — status, queue position, progress log, and, when
  done, additive `coaching_eligible` + `outcome` fields. Coaching-ready
  results include `report_url` + `metrics_url`; a current capture-only
  re-film result includes only its safe `report_url`. Older rejected reports
  are withheld because they predate this trust boundary.
- `GET /api/sessions` — recent sessions, including the same additive outcome
  fields for completed results
- `GET /session/{id}/files/...` — owned session artifacts. Re-film-required
  results gate raw metrics and derived coaching visuals; current capture-only
  reports and their slow-motion capture reference remain available.

### Accounts and Pro memberships

With `web.require_account: true` (the shipped default), visitors sign up with
email + password (hashed locally with scrypt — no external auth service) —
or, once email delivery is configured, with just their email via a six-digit sign-in
code ("One account: email-code sign-in" below). Accounts get
`billing.free_per_month` analyses per calendar month, and can upgrade to
**Pro** for `billing.pro_per_month` (0 = unlimited). The first rejected clip
each month is forgiven; every later upload uses the normal allowance even if
it also needs re-filming. Each
account sees only its own history, and results are private to their owner
(sessions from before accounts stay reachable by link). Set
`require_account: false` for an open, no-login instance.

Pro can be sold two ways, both **inert until configured** — the pricing page
shows Pro as "coming soon" until one is set up. When both are configured,
buyers are sent to the Shopify store.

**The coach replay is one Pro quality line** (`billing.replay_pro_only`,
shipped `true`): with accounts on, the annotated replay — the report's most
shareable artifact — is rendered only for jobs whose owner has Pro *at
analysis time*. For a trustworthy session, a free user's report keeps
everything else (metrics, slow motion, overlays, drills) and shows an honest
lock-and-key teaser with a
`/pricing` link in the replay slot; the render itself is skipped, so the
gate saves the CPU too. Upgrading later never rewrites an old report —
re-film to get the replay. Open instances (`require_account: false`), CLI
runs, and the public sample report are **never** gated, and the bare-code
default is `false` — the same deliberate DEFAULTS-vs-shipped difference as
`retention_days`, pinned by tests.

**The progress dashboard is the other** (`billing.progress_pro_only`,
shipped `true`, bare default `false` — same pattern): free accounts opening
`/progress` see a locked teaser describing what the dashboard tracks, with
a `/pricing` link, instead of their trend charts; no trend data is computed
for a locked view. Both gates are advertised, not hidden: the pricing page
and the storefront comparison list them as explicit Pro rows.

The pricing page shows the yearly plan first (the hero, a third off
monthly), then monthly, then lifetime (the anchor), then free — using the
**display-only** strings `billing.pro_price_monthly_text` /
`pro_price_annual_text` / `pro_price_lifetime_text` from `config.yaml`
(shipped: `$4.99/month`, `$39.99/year — $3.33/month`, `$79.99 once — Pro
for good`). These are labels, not billing: what is actually charged always
lives in Shopify/Stripe. The pricing page's renewal copy is driven by
`billing.store_subscriptions` (shipped `true` for CaddieInsight; the
bare-code default remains `false`): enable it only once the store actually
sells auto-renewing subscriptions via Shopify's Subscriptions app. When on,
the page says monthly/yearly renew automatically (cancel anytime, Pro runs
to period end); when off, it says honestly that passes simply expire.
Lifetime is always a single payment, and its card
only renders when the primary Shopify commerce bridge is configured (the
lifetime SKU has no Stripe equivalent).

**Selling Pro on the Shopify store** (one checkout for gear and
memberships): create a product whose variant SKUs map to days of access in
`billing.shopify_skus` (shipped mapping: `SL-PRO-1MO` → 31 days,
`SL-PRO-12MO` → 365, `SL-PRO-LIFE` → 36500 — a hundred years; the account
page shows anything more than 50 years out as "Lifetime"), point
`orders/paid`, `orders/cancelled`, and `refunds/create` webhooks at
`/webhooks/shopify`, and
set:

| Variable | What it is |
| --- | --- |
| `SWINGLAB_SECRET` | long random string signing login cookies (always set this) |
| `SHOPIFY_STORE_DOMAIN` | `yourstore.myshopify.com` (shared with the gear shop) |
| `SHOPIFY_WEBHOOK_SECRET` | signing secret from Settings → Notifications → Webhooks |
| `SHOPIFY_PRIVACY_WEBHOOK_SECRET` | dedicated bridge app's client secret, used only for its mandatory privacy deliveries |

The store domain and primary `SHOPIFY_WEBHOOK_SECRET` are the commerce gate:
both are required before the app advertises the Pro store link or applies
Shopify-connected signup semantics. A store plus only
`SHOPIFY_PRIVACY_WEBHOOK_SECRET` keeps the shared endpoint available for signed
mandatory compliance deliveries, but does not expose checkout to buyers.

A paid order first extends Pro on the account linked to the stable Shopify
customer ID, then falls back to the account matching the checkout email; a
purchase made before signup is claimed automatically when that email creates
an account or logs in. Replayed webhooks never double-grant; cancelled orders
and refunds that identify a Pro SKU take their whole order grant back. A
gear-only or unattributable refund leaves Pro unchanged.

Shopify's Subscriptions app creates a new paid order for each successful
billing cycle, so the same SKU path re-extends access. Today those grants are
still fixed passes (31 days for monthly, 365 for yearly), not authoritative
Shopify calendar-period ends. Exact calendar-month/year alignment needs
subscription billing-cycle data and must not be inferred from the order
timestamp or selling-plan name.

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

**Progress dashboard (`/progress`)** — one card per metric with data: an
inline-SVG trend chart of the session means (dots on sessions, dashed line +
shaded band at the flag threshold), latest / best / change-vs-first stats,
and a strip showing which flags keep firing across sessions. With
`billing.progress_pro_only` on (the shipped config), it's a Pro surface:
free accounts see an honest locked teaser instead, and the weekly digest
links them to their session history rather than the lock screen. Legacy sessions that predate the newer metrics simply contribute
the fields they have; sessions with no readable numbers are skipped. With
fewer than two measured sessions the page says so honestly instead of
charting a single dot. Requires `web.require_account: true` (there is no
per-user history to chart in open mode — the route 404s).

**Weekly practice-plan email** — the "one drill a week" promise, made real,
and strictly opt-in. It only ever sends when ALL of these hold:

- email delivery is configured (`SWINGLAB_MAIL_FROM` plus `RESEND_API_KEY`
  or `SWINGLAB_SMTP_URL`, the same transport as verification/reset email) —
  without it the feature has zero behavior;
- `web.digest_enabled: true` in config.yaml (the shipped default);
- the user asked for it — an **unchecked** "Email me one drill a week" box at
  signup, a toggle on the account page, and a signed one-click unsubscribe
  link in every email (works logged out).

Each email is self-contained HTML (inline styles, brand colors, no images or
external assets): exactly one drill selected by the same Caddie Brief priority
as the results page — name, dosage, and the same pass-mark numbers the report
prints — plus one honest progress line once two sessions exist, and links to
the latest report and `/progress`. An hourly scheduler thread sends at most
one email per user per
~week (6.5 days), only to accounts with at least one finished session, and
stamps the send time *before* attempting delivery so a crash can never
double-send within a week. Set `PUBLIC_BASE_URL` so the email's links are
absolute.

### Account sync with Shopify

The existing inbound bridge from merged
[GitHub PR #28](https://github.com/kylejames0513-bot/SwingLab/pull/28) starts
accounts on the store: a customer created in Shopify automatically
exists in the web app, and everything they bought is waiting when they
finish setup there. In the Shopify admin, under **Settings → Notifications
→ Webhooks**, add three more webhooks — `customers/create`,
`customers/update`, and `customers/delete` — pointing at the **same**
`https://<your-app>/webhooks/shopify` endpoint the order webhooks use.
The existing manual notification topics use `SHOPIFY_WEBHOOK_SECRET`.
Mandatory privacy topics declared by the dedicated app use its separate
`SHOPIFY_PRIVACY_WEBHOOK_SECRET`. Every recognized mutation also has to name
the exact configured store in `X-Shopify-Shop-Domain`; a valid signature from
another installed store cannot touch this database.

What each event does:

- **customers/create, customers/update** — creates a passwordless "store
  account" for the customer's (normalized) email, tagged with the Shopify
  customer id. If a verified account already exists, it can link/refresh that
  id. An unverified pre-existing local account is never trusted merely because
  its typed email matches; the customer identity is parked until inbox proof.
  The customer id is the stable identity: an unclaimed store-only stub can
  follow a Shopify email change on the same row; a claimed account keeps its
  verified app login email instead of being split or silently merged.
  Replayed webhooks land on the same row (no duplicates).
- Signing up in the app with a store account's email **claims the same
  account** only after an emailed code proves inbox ownership. The claim lands
  on that row, so the Shopify link and any Pro purchase already granted by the
  order webhooks carry over. A pre-verification password/session cannot survive
  the ownership transition. Until proof, a password login attempt gets pointed
  at the code flow instead of a misleading "wrong password".
- **customers/delete** — deletes the app user only when it is an
  unclaimed stub (no password, no analyses); any Pro days it still
  carried are parked and reclaimed if that email signs up later. A
  claimed account merely loses its store link — store-side deletion never
  destroys app data. A tombstone prevents a delayed create/update webhook
  from recreating the deleted store identity while retaining the internal
  account mapping needed to recognize that same customer's late paid
  events. Redaction severs that mapping.
- **customers/redact** (GDPR) — same as delete, and additionally erases
  the Shopify-sourced profile fields on claimed accounts and any parked
  purchase for a deleted stub's email.
- **customers/data_request** — captures a replay-idempotent,
  integrity-checked, expiring export snapshot for protected operator
  delivery; credential hashes and one-time secrets are excluded.
- **shop/redact** — for the exact configured store, transactionally erases
  local Shopify ledgers, store bindings, pending links, and store-only
  identities while preserving independently owned CaddieInsight accounts
  and golf analyses.

Paid orders follow the linked Shopify customer id first. Normalized checkout
email is used directly only for guest orders without a customer id; a
customer-bearing order that arrives before its customer webhook is parked
until the stable identity can be established. Email fallback never crosses
a conflicting Shopify customer id. Customer-specific parked value can move
to a later Shopify email without carrying another customer sharing the old
address. Recording the order and changing Pro access are one database
transaction, and an early cancellation is remembered so a delayed paid
event cannot restore cancelled access.

**Limitations, honestly:** Shopify does not expose customer credentials,
so store passwords cannot sync. A deployment with the primary Shopify commerce
bridge configured requires inbox proof before a password account can claim or
later receive a commerce identity; store-first and app-first claims use an
emailed one-time code. A claimed user's
Shopify email change is deliberately not made their app login until the
new inbox is verified; support must currently handle that change. Store
customers created without an email address are skipped (there is nothing
to match on). Webhooks cover changes after subscription; a full historical
customer backfill/reconciliation still requires an Admin API process.
Legacy order histories whose exact grant ownership cannot be proven are
left unchanged for that reconciliation instead of guessing and revoking or
restoring the wrong customer's access. Privacy export snapshots require an
authorized operator to deliver them through an approved support/privacy
channel before their retention deadline; sensitive exports are never emailed
automatically by the webhook.

#### App-first Shopify customer sync

The complementary outbound bridge links verified CaddieInsight registrations
through Shopify Admin GraphQL. It is disabled by default:

```yaml
shopify_customer_sync:
  enabled: false
  auto_sync_new_users: true
```

Local registration always commits first, so Shopify downtime never prevents
access to CaddieInsight. Email is used only for the initial normalized,
verified match; after linking, the stored Shopify customer ID is durable.
Passwords are never synchronized, and Shopify email updates do not silently
replace the app login identity.

Activation requires canonical `SHOPIFY_STORE_DOMAIN` and
`SHOPIFY_ADMIN_STORE_DOMAIN` values that match each other and the persisted
Shop binding, explicit `SHOPIFY_ADMIN_API_VERSION`, and exactly one
backend-only authentication mode: the preferred `SHOPIFY_ADMIN_CLIENT_ID` plus
`SHOPIFY_ADMIN_CLIENT_SECRET`, or the legacy `SHOPIFY_ADMIN_ACCESS_TOKEN`. A
split-store configuration leaves inbound signed webhooks healthy but blocks
outbound enrollment, worker startup, and customer requests. Activation also
requires the minimum customer scopes and protected email access, protected
admin health/retry routes, a bound Shop GID, and a reviewed dry-run backfill.
Do not run a production backfill automatically. See the
[Shopify customer-sync runbook](docs/shopify-customer-sync.md) for setup,
retries, staged rollout, rollback, and the manual verification checklist.

**Optional email verification** — inert until configured, like
every other integration:

| Variable | What it is |
| --- | --- |
| `RESEND_API_KEY` | preferred HTTPS delivery through Resend; recommended on Railway, where Hobby blocks outbound SMTP |
| `SWINGLAB_SMTP_URL` | e.g. `smtp+starttls://user:pass@smtp.example.com:587` — also `smtp://` (plain, local relays) and `smtps://` (implicit TLS, port 465); credentials URL-encoded |
| `SWINGLAB_MAIL_FROM` | the From address, e.g. `CaddieInsight <no-reply@yourdomain.com>` |
| `SWINGLAB_MAIL_TRANSPORT` | optional `auto` (default), `resend`, or `smtp`; `smtp` is an explicit rollback for hosts that allow it |

Set `SWINGLAB_MAIL_FROM` plus one transport. When both transports are present,
the Resend HTTPS API is preferred and SMTP remains a fallback for hosts that
permit it. An existing `smtp.resend.com` URL with Resend's standard `resend`
username is automatically delivered through the HTTPS API, using the same
embedded credential; this lets existing Railway Hobby configuration work
without duplicating the secret. Claiming an email that already has anything
attached (a store account, or a Pro purchase made before signup) then requires
a 6-digit code emailed to that address — 10-minute expiry, single-use, stored
hashed, rate-limited per email — and **password reset** appears on the login
page using the same codes. No third-party runtime dependency is required.

> **Security note:** when the primary Shopify commerce bridge is configured,
> every password signup and every store/purchase claim requires inbox proof. If
> email delivery is unavailable, setup fails closed instead of attaching
> Shopify identity or value to an unverified password/session. A standalone
> installation with no primary commerce bridge can still use the documented local
> no-mail fallback.

### One account: email-code sign-in

With email delivery configured, the signed-out experience presents separate
**Create free account** (`GET /signup`) and **Sign in** (`GET /login`) choices.
Both use the same private email-code backend
(`web.passwordless_login`, shipped and defaulted `true`): the visitor enters
an email, receives a six-digit code, and verifies it before any new account is
created. Keeping one verified backend for both visible intentions is what
makes store and app identity **one account**. Existing users keep signing in
with their current verified CaddieInsight email; a new user who bought first
creates the app account with the email used at Shopify checkout:

- an existing app account simply logs in;
- an unclaimed store account (provisioned by the customer webhooks) logs
  in **and is claimed on the spot** — the code proves control of the
  inbox, which is strictly stronger proof than the old password-claim,
  so the Shopify link and any Pro time carry over with no extra step;
- an email with no account at all gets a Free account after code
  verification. The dedicated signup page explains the free allowance and
  that no purchase or Shopify customer account is required.

Neither the page nor the email reveals which of the three happened: every
address gets the same "check your email" screen and the same message, so
the form cannot be used to test which emails have accounts. The codes are
the existing machinery — hashed at rest, 10-minute expiry, single-use,
burned after 5 wrong guesses — and both requesting and mis-entering codes
draw on the login throttle limits (`web.login_attempts_per_15min`, per
email and per IP). A correct code also marks the email verified, which is
what the store-claim rests on.

Passwords stay a first-class fallback, never a dead end: accounts that have
one can always use it, and password recovery is linked directly from the
primary sign-in screen. Passwordless accounts can add one from the Account
page ("Add a password (optional)"); accounts that already have one get a
visible change/reset link there. Setting a password by signing up with a
passwordless account's email also works, and requires the emailed code first
while email is on.

The whole feature is inert without a complete transport:
`SWINGLAB_MAIL_FROM` plus either `RESEND_API_KEY` or `SWINGLAB_SMTP_URL`.
Without that pair, the login and signup pages keep the classic password
flows exactly — which is why the flag can ship `true` without affecting
white-label installs that have no email infrastructure. Set
`web.passwordless_login: false` to force password flows even with email
configured. Honest caveat: if an operator runs with email for a while and
then turns it off, accounts that never added a password cannot sign in
until email returns (or until they set a password via signup — see the
security note above); the account page says so when it applies.

### Gear shop (Shopify)

Connect a Shopify store and the app grows a **Gear** page (`/shop`) listing
the store's products. After trustworthy coaching, a finished analysis may
show at most one optional aid whose tag matches the session's first measured
priority. Clean sessions, unreadable/re-film sessions, and unmatched issues
show no product pitch. Tag products in Shopify to wire exact matches:

| Shopify product tag | Recommended when the analysis shows |
| --- | --- |
| `swinglab:tempo` | tempo ratio under `coaching.tempo_warn_below` |
| `swinglab:sway` | head sway beyond `coaching.sway_warn_sw` |
| `swinglab:hip-slide` | hip slide beyond `coaching.sway_warn_sw` |
| `swinglab:head-dip` | head dropping beyond `coaching.head_dip_warn_sw` on the way to impact |
| `swinglab:arm-extension` | lead arm bent under `coaching.lead_arm_warn_deg` at impact, or a shoulder-tilt priority using its own evidence-matched freeze drill |
| `swinglab:balance` | feet drifting beyond `coaching.finish_balance_warn_sw` during the finish hold |
| `swinglab:consistency` | tempo varying noticeably across swings |
| `swinglab:general` | broad store categorization only; never auto-recommended |

Like payments, the shop is **inert until configured** — no link, no page —
through the store domain:

| Variable | What it is |
| --- | --- |
| `SHOPIFY_STORE_DOMAIN` | `yourstore.myshopify.com` (or the custom domain) |

Products, prices, and images live in Shopify — manage them in the Shopify
admin, add the Gear products to the public `swinglab-gear` collection, and
never duplicate them in code. The product list is cached in memory
(`shop.cache_minutes`), and a Shopify outage degrades to the last cached
list instead of an error. "Buy" links go to the Shopify storefront;
CaddieInsight never touches checkout.

For deployment — a one-command `docker compose up -d`, or a fresh-VM script —
see [deploy/README.md](deploy/README.md).

**Domain layout:** keep the Shopify storefront origin separate from the Railway
application origin. `PUBLIC_BASE_URL` must be the application origin. Actual
hostnames remain deployment state and are intentionally not recorded here.
This repository does not manage DNS or Railway secrets; see
[deploy/README.md](deploy/README.md) and [docs/deployment.md](docs/deployment.md)
for the preserved production contract.

## Measuring what matters

Five KPIs, computed from the app's own SQLite state (`swinglab/kpis.py`) —
no analytics service, no tracking pixels, nothing leaves the box. These are
the numbers that decide whether the product is working, with the targets
from the strategy analysis:

| KPI | Definition | Target |
| --- | --- | --- |
| `activation_rate` | of accounts created in the window, the share whose **first coaching-ready report** landed within 7 days of signup | **> 50%** |
| `w1_refilm_rate` | of those accounts with ≥ 1 coaching-ready analysis, the share whose **second coaching-ready analysis** landed within 7 days of their first — the re-film habit is the core loop | **> 25%** |
| `free_to_pro_rate` | of the window's *activated* accounts, the share that gained Pro within 30 days of signup (Shopify grants timed by the order ledger's `applied_at`; a live Stripe subscription counts — Stripe state carries no grant timestamp) | **2%+** |
| `weekly_retained_filmers` | a count, not a rate: accounts with ≥ 1 coaching-ready analysis in the trailing 7 days | grow it |
| `gear_attach_per_100_reports` | non-cancelled **gear orders** in the window per 100 coaching-ready reports in the window | — |

Pre-metrics reports are treated as coaching-ready legacy results because
re-film outcomes did not exist when they were created. New results use the
same centralized eligibility rule as Caddie Brief, trends, quota, and the
results page; rejected clips therefore do not inflate product KPIs.

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
   configurable height / prominence / minimum-gap thresholds. An optional,
   off-by-default relative-loudness gate can then reject quieter candidates;
   it must be calibrated on labeled clips and cannot recover a strike already
   suppressed by the minimum-gap rule.
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
| `brand` | name, logo, colors, footer, watermark on/off, disclaimer, `support_text` (shown where users need the operator, e.g. password reset while email is unconfigured) |
| `detection` | audio peak height / prominence / minimum gap between swings, optional calibrated relative-loudness noise gate (`relative_height`, shipped off), per-clip strike cap (`max_strikes`, shipped 8 — first N analyzed, honestly noted) |
| `coaching` | flag thresholds: sway warning, tempo target/warning, consistency praise, head dip (`head_dip_warn_sw`), lead-arm angle (`lead_arm_warn_deg`), shoulder tilt (`shoulder_tilt_impact_min_deg`), finish balance (`finish_balance_warn_sw`) |
| `analysis` | window size, working/full resolutions, takeaway threshold, finish-hold frames for the balance metric (`finish_hold_frames`), per-clip length cap (`max_video_s`, shipped 300 s, 0 = off), high-fps analysis (`auto_fps`: sources ≥ 50 fps analyzed at min(source, 60)) |
| `slowmo` | slow-motion factor, clip bounds, output height, crf; annotated replay on/off (`annotated`) and hand-trail fade (`trail_fade_s`) |
| `overlay` | captured/corrected skeleton colors, arrow threshold |
| `web` | worker pool size, upload size cap, per-IP job limit, proxy trust for real client IPs (`trusted_proxies`), login/signup throttles, session retention (shipped 180 days; raw upload deleted after analysis via `delete_source_after_done` — both off in bare-code defaults, see the GDPR note in config.yaml), `require_account`, email-code sign-in (`passwordless_login`, shipped on — self-disables without email delivery), weekly digest on/off (`digest_enabled`) |
| `billing` | free/Pro analyses per month, the coach-replay and progress-dashboard Pro gates (`replay_pro_only` / `progress_pro_only`, shipped on — off in bare-code defaults), plus `pro_price_*_text` display strings for the pricing page (what's charged lives in Shopify/Stripe, not here) |
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
- **Shopify inbound account sync (done)** — customer webhooks provision store
  accounts in the app, signup claims them with purchases intact, and
  optional email delivery adds code-verified claims plus password reset
  (the Milestone-5 reset item, shipped early).
- **Shopify app-first customer sync (staged)** — the backend Admin GraphQL
  bridge is disabled by default and activates only after protected customer
  access, development verification, dry-run reconciliation, and explicit
  rollout approval.
- **One account (done)** — passwordless email-code sign-in: with email delivery
  configured, the store email is the app identity; one "Continue with
  email" flow logs in, claims store accounts, or creates accounts, and a
  password is optional. Self-disables without email delivery.
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
