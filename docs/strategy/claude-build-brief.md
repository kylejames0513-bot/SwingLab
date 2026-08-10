# CaddieInsight — build brief

Paste this into a fresh Claude session before asking for work. It exists so a
new session starts with the product thesis and the real constraints instead of
rediscovering them, and so it argues with the plan when the plan is wrong.

---

## Read this first

You are working on **CaddieInsight**: a golf swing analysis product that runs
on one phone video, no hardware. It is three surfaces that a customer must
experience as one thing:

1. **`caddieinsight.com`** — Shopify storefront. Sells a Pro membership and a
   handful of dropshipped training aids. Real orders, real money.
2. **`app.caddieinsight.com`** — Python/FastAPI app. Where swings are
   analysed. Also an installable PWA, which is the "mobile app" customers
   actually have today.
3. **`mobile/`** — an Expo scaffold for the App Store and Play Store. It
   builds; it has never been interacted with.

Do not treat these as three projects. A customer sees one brand, and the
whole product thesis depends on them being one loop.

---

## The thesis

Most swing apps sell **measurement**. They hand a golfer a dashboard — tempo
ratio, hip rotation, attack angle — and leave them to work out what to do
about it. The good ones are used by coaches, on behalf of golfers.

CaddieInsight sells **one decision, and proof it worked.**

Every report ends with exactly four things:

- **one priority** — the single thing to fix, chosen from what was measured
- **one drill** — how to fix it
- **one pass mark** — a specific, falsifiable target
- **one re-film** — same club, same camera angle, so the comparison is fair

The golfer re-films. The pass mark either clears or it doesn't. That is the
whole product. Everything else is in service of it.

This is a deliberately *narrow* product in a category that competes on
breadth. Do not widen it. A pull request that adds a metrics dashboard is a
pull request that makes this product ordinary.

---

## The flywheel — and where it is currently broken

The thing no competitor closes:

```
  measure the swing
        ↓
  name ONE priority
        ↓
  prescribe ONE drill
        ↓
  the drill maps to a PRODUCT IN THE STORE
        ↓
  golfer re-films against the pass mark
        ↓
  proof it worked  →  trust  →  next priority
```

Analysis → prescription → commerce → proof. Competitors do one or two of
those. Nobody does the round trip, and the round trip is why the store and the
app must not drift apart.

**It is already built, and the gate that was strangling it is now off.**
> **Correction (2026-08-10):** an earlier revision of this paragraph said the
> first-sale gate was on and "the app recommends no gear at all today, for
> any flag, and `/shop` is empty." `shop.first_sale_catalog_only` was set to
> `false` on 2026-08-09 (owner decision) — the catalogue is promotable and
> `/shop` serves it. This file asks to be pasted into fresh sessions as
> ground truth, which makes a stale claim here maximally expensive: verify
> against `config.yaml` and the live store before trusting this section.
The tag gaps below are the part that remains true — they are a sourcing
problem, not a configuration one.

`swinglab/drills.py` gives every drill a `gear_tag`, and `swinglab/web/shop.py`
matches that tag to Shopify products. The live catalogue carries tags for 3 of
8 drill categories (4 since Connection Ball and Arm Link were retagged):

| `gear_tag` | Drills | Product in store? |
| --- | --- | --- |
| `swinglab:tempo` | 3 | yes — Tempo Trainer, Tempo Rope |
| `swinglab:consistency` | 2 | yes |
| `swinglab:general` | 2 | yes |
| `swinglab:arm-extension` | 3 | yes — Connection Ball, Arm Link (retagged) |
| `swinglab:sway` | 2 | **no** |
| `swinglab:hip-slide` | 2 | **no** |
| `swinglab:head-dip` | 2 | **no** |
| `swinglab:balance` | 2 | **no** |

**8 of 18 drills prescribe something the store carries no product for.** The
flywheel breaks exactly where the coaching is most specific — the moment the
app says something genuinely useful, commerce goes silent.

Some of this was a tagging error, not a catalogue gap: **Connection Ball** and
**Arm Link** are arm-extension products, tagged `swinglab:consistency` only.
Both now also carry `swinglab:arm-extension` (added, not swapped — they serve
both drill families), closing 3 drills' worth of the hole.

The rest is a sourcing question — there is no anti-sway or balance product in
the catalogue yet.

**Closing this loop is the highest-leverage work available.** It raises
average order value, it makes the membership more valuable, and it is the
thing that cannot be copied by an app with no store or a store with no app.

---

## Non-negotiables

These are not style preferences. They are in the code, they are tested, and
breaking them damages the product's only real asset — that its output can be
trusted.

**Never invent a measurement.** Estimates come from 2D single-camera pose.
When tracking quality is poor or the camera angle is wrong, the report says
so and degrades to a capture-only result. It does not guess. Down-the-line
footage yields tempo and rhythm only, because body-drift and angle numbers are
measured face-on — the report says that out loud rather than quietly
returning worse numbers.

**No LLM in the coaching engine.** Coaching comes from measured pose and
deterministic rules. This is deliberate. A generative caddie that hallucinates
a hip angle is worse than no caddie. You may use models for prose and
presentation; do not put one between the measurement and the recommendation.

**No fabricated proof.** No fake testimonials, no invented customer results,
no screenshots implying measurements the product did not take. Campaign
imagery is labelled as campaign imagery.

**Personal data never gets cached on device.** The service worker's cacheable
surface is an allowlist (`/offline`, `/static/`). Reports, sessions and
uploads are excluded by construction, not by remembering to exclude each new
route. Keep it that way.

**The product is not instruction, and not medical.** It says so. Keep it
saying so.

---

## What exists, so you do not rediscover it

**Stack.** Python 3.11, FastAPI, MediaPipe pose, Pillow, Jinja templates.
~2,000 tests. Shopify theme is Liquid in `storefront-theme/`. Native client is
Expo + Expo Router + TanStack Query in `mobile/`.

**Two kinds of measurement.** `metrics.py` is positional — where a joint was
at address, top, or impact. `sequence.py` is temporal: the order body segments
reach peak rotation speed through the downswing, which is how a swing either
transmits speed or leaks it. The distinction matters when choosing what to
measure next, because a swing can pass every positional check and still be
badly out of order — casting, arms-first and a stalled pelvis are all
sequencing faults that head-sway and hip-slide cannot see.

`sequence.py` refuses more than it answers, on purpose: not face-on, too few
downswing samples, any tracking gap in the phase, or peaks closer together
than the frame rate can resolve all return a named failure rather than a
number. **It is not yet wired into the guided report** — see the build order.

**Drill figures are silhouettes, not stick figures.** `diagrams.py` strokes
each body segment at its own weight from the same joint vocabulary, and the
animation draws every pose at once as a fading trail beneath the animated
figure so the shape of a movement is visible rather than remembered. If you
add a scene, you get all of this for free — do not hand-author SVG bodies.

**Deploy paths differ, and this catches people out:**

- The app **auto-deploys from `main`** via Railway. Merging is deploying.
- The Shopify theme deploys **manually**. Merging changes nothing on the
  store. It must be uploaded as an unpublished theme and published by hand.

**Brand — "Tour Caddie v4".** Mark is an instrument dial: a tick fan for
measurement, one amber run for the reading, a flagstick through the middle.
Generated from one geometry in `store-assets/brand_mark.py` as both raster and
SVG — regenerate with `make_brand.py`, never hand-edit the outputs. Type is
Archivo (display and body) plus IBM Plex Mono for measured evidence. Palette
is "Turf Instrument": cool mist `#eef2ef`, deep forest `#0f3d28`, one amber
accent `#e8720c` used once per composition.

**Store today.** 7 active products. Pro membership at $9.99/mo, $69.99/yr,
$249 Founders Pass (capped at 100; repriced from $149 on 2026-08-10, sold as Coach-for-life; Coach $19.99/mo · $139.99/yr). Markets: US primary plus International
covering 21 Asian countries, with shipping zones and rates already configured.
Payments live — Shop Pay, Apple Pay, Google Pay. Policies exist for Privacy,
Refund, Contact and Cancellations.

**Key documents.** `CLAUDE.md` for working agreements.
`docs/quality/local-visual-verification.md` before trusting any screenshot.
`docs/runbooks/gear-coverage.md` for the drill-to-product coverage ledger.
`docs/runbooks/store-policies.md` for policy drafts.
`docs/runbooks/rebrand-cutover.md` for theme deploy sequence.
`store-assets/prompts/` for campaign imagery specs.

---

## Build order

Roughly highest leverage first. Argue with this if you have a better read —
but say why.

### 1. Close the flywheel

- Retag Connection Ball and Arm Link as `swinglab:arm-extension`.
- Source or add products for `swinglab:sway`, `swinglab:hip-slide`,
  `swinglab:head-dip`, `swinglab:balance` — or, if that is not viable,
  collapse those drill categories onto gear that does exist rather than
  leaving the recommendation silent.
- ~~Add a test that **fails when a drill's `gear_tag` has no matching
  product**.~~ Done — `tests/test_gear_coverage.py`, checked at two layers
  (stocked, and recommendable through the shipped first-sale gate).
  This gap was invisible precisely because nothing checked for it.

### 2. Wire kinematic sequence into the guided report

`sequence.py` is built, tested and unused. Surfacing it means deciding how a
sequencing fault competes with the positional faults for the single priority
slot — that is a coaching-rules change, not a rendering change, and it touches
the `guided-report-v1` contract and the priority-selection order.

Worth doing carefully rather than quickly. A sequencing fault is often the
*cause* of a positional one, so if it simply joins the ranking as a peer, the
report will keep naming symptoms while the cause sits one row down.

### 3. Make the store safe to advertise

Blocking paid traffic today: no Shipping Policy, no Terms of Service (Pro
auto-renews, so this matters legally), a Refund Policy promising prepaid
return labels on $12.99 dropshipped items, a contact link that opens a
personal iCloud address, two shipping methods both named "Standard", and a
live sold-out variant. Drafts are in `docs/runbooks/store-policies.md` and
need real values, not placeholders.

### 4. Ship the v4 brand to the storefront

The app is already on it; the store is not, and a customer meeting both sees
two companies. Note that a Shopify **Files** entry beats a theme asset of the
same name — the store still holds v3-brand filenames.

### 5. Photography

The single largest remaining visual gap. Existing art is from an earlier
brand. Prompts are written and reference the exact filenames the code binds.

### 6. The native app, honestly

`mobile/` builds but has never been run. Before the stores:

- Run it. Fix what breaks.
- Build **in-app camera capture**. This is both the missing product — filming
  a swing is the one thing a native app does better — and the answer to
  Apple's guideline 4.2, which rejects apps that are wrappers around a
  website. Your PWA already does everything else, so capture is what makes a
  native app defensible in review.
- Build the report screen.
- Then: Apple Developer Program, Play Console, EAS builds, store listings.

### 7. Pay down the API contract

None of the twelve `/api/` handlers declares a response schema — they return
bare `JSONResponse`. So `docs/api/openapi-v1.json` has accurate paths and no
types, and `mobile/src/api/types.ts` is hand-written as a stopgap. Adding
Pydantic response models lets generated types replace it and removes a whole
class of silent drift.

---

## Traps that have already cost time

- **Headless Chromium here cannot reach Google Fonts.** It fails silently and
  the page renders in a system fallback, so a before/after screenshot pair
  proves nothing about a font change. Embed faces as data URIs. Check
  `document.fonts` before trusting any typography screenshot.
- **Launch Chromium with `executable_path="/opt/pw-browsers/chromium"`.**
- **Two browser tests fail locally on H.264** the container cannot decode.
  They pass in CI. Confirm with `canPlayType` before calling a failure real.
- **`tsc` is not enough for `mobile/`.** It never loads the Metro config, and
  once hid a missing dependency that broke the bundler outright. Use
  `npx expo export`.
- **A Shopify Files entry beats a theme asset of the same name.** Never point
  `images['…']` at a retired filename expecting the theme copy to win.
- **CSS source order.** A base rule placed after a media query silently
  overrides it at equal specificity. This hid a bottom tab bar entirely.
- **Angles need unwrapping before differencing.** A segment crossing the atan2
  branch cut jumps 2π, which is otherwise the largest apparent rotation in the
  swing and captures every peak. Anything differentiating an angle series has
  this bug until proven otherwise.
- **Frame rate bounds what can honestly be claimed.** A downswing is about a
  quarter of a second; at 30fps that is roughly eight samples. Any measurement
  resolving events *within* the downswing has to state what it cannot separate
  rather than report the argmax as fact.

---

## How to work here

**Always merge to main.** Branch, PR, merge — do not stop at a draft awaiting
review. Merge commits, not squash. A red build is the only exception.

**Verify before claiming.** "Typechecks" is not "runs". "Tests pass" is not
"works". If you say something is verified, say precisely what you ran. If you
could not verify something, say that plainly instead of implying you did.

**Break the thing and watch the test fail.** A green test proves nothing until
you have seen it go red. This has bitten twice in one session: once a literal
tautology (`assert measured or failure is not None`), and once a fixture whose
ramp went flat, so the angle it was meant to sweep through 180° parked exactly
on it and never crossed — the test passed with the behaviour it existed to
protect deleted. Both looked completely reasonable on the page.

For anything non-obvious — a smoothing step, a guard threshold, a rendering
rule — delete or invert it, run the test, confirm the *specific* test fails,
then restore. It takes a minute and it is the difference between coverage and
the appearance of coverage.

**Say when the plan is wrong.** Several documented plans in this repo assume
prerequisites that do not exist. Surfacing that is more valuable than building
on the assumption and producing something that compiles but cannot work.

**Prefer the narrow answer.** When a change could either add a feature or
sharpen the single decision the product already makes, sharpen it.
