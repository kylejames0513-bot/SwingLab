# Mockup inventory

What was imported from the Claude Design project **"Caddieinsight UI mockups"**
(`1c9e7678-ee3d-41b3-89db-2a450d41172a`), what each screen contains, and which
assets it uses. Written at the Phase 0 checkpoint — nothing has been built yet.

Source of truth: `design-source/CaddieInsight Mockups.dc.html` (188 KB, 1,859
lines). Everything below is read out of that file, not inferred.

---

## 1. What is now in `design-source/`

All 22 files, verified on disk. Nothing here needs a live connection any more.

| File | Size | Notes |
| --- | --- | --- |
| `CaddieInsight Mockups.dc.html` | 188,487 B | The mockups. 7 turns, 17 screens. |
| `industry.css` | 12,444 B | The design system. Byte-identical to `_ds/…/styles.css`. |
| `_ds/industry-…/styles.css` | 12,444 B | Duplicate of the above. |
| `_ds/industry-…/readme.md` | 8,265 B | The system's written rules. |
| `_ds/industry-…/_ds_manifest.json` | 7,383 B | Token list, component cards, font declarations. |
| `_ds/industry-…/_adherence.oxlintrc.json` | 4,211 B | Lint rules: no raw hex, no raw px, Barlow only. |
| `_ds/industry-…/_ds_bundle.js` | 311 B | **Empty.** Declares a namespace and exits. |
| `ios-frame.jsx` | 16,859 B | Omelette starter iOS device frame. |
| `support.js` | 71,061 B | Claude Design's canvas runtime. |
| `ci-mark-ink-512.png` | 24,046 B | 512×512, transparent |
| `ci-mark-paper-512.png` | 24,154 B | 512×512, transparent |
| `ci-mark-ink-128.png` | 4,471 B | 128×128, transparent, grooves dropped |
| `club-ink-512.png` | 13,805 B | 512×512, transparent |
| `club-paper-512.png` | 13,585 B | 512×512, transparent |
| `favicon-512/64/32/16.png` | 12,333 / 828 / 463 / 225 B | Club on the solid green field |
| `ci-favicon-64/32/16.png` | 2,352 / 1,030 / 429 B | CI monogram, ink, transparent |
| `ci-favicon-paper-32.png` | 994 B | CI monogram, paper, transparent |

Every PNG was checked for a valid signature and its real pixel dimensions.

**Not imported:** `scraps/sketch-2026-08-11T21-47-33-liegv5.napkin` — a scratch
sketch in the project's `scraps/` folder, not in the file set the brief listed,
and not referenced by any screen. Say the word and I will pull it.

### Two files do less than the brief assumes

- **`_ds_bundle.js` is empty.** `"components":[]`, `"startingPoints":[]`. The
  Industry system is pure CSS with no JavaScript layer. There is nothing to port
  from the bundle, and nothing to extract from it for `DESIGN_TOKENS.md` — the
  tokens live in `industry.css` and are mirrored in `_ds_manifest.json`.
- **`support.js` is not product code.** Its first line reads `GENERATED from
  dc-runtime/src/*.ts`. It is the runtime that renders a `.dc.html` canvas
  document in the browser — DOM parsing, React mounting, the `<x-dc>` element.
  None of it describes CaddieInsight behaviour. The brief's "check `support.js`
  for any interaction behavior the mockups rely on" resolves to: **none**.

The mockups carry **no product JavaScript at all**. Every moving thing in them
is CSS — four keyframe animations (`scrub`, `rise`, `dash`, `pulse`) driving the
video scrubber, the bar charts, the trace lines and the pass-mark pulse. That is
good news for both surfaces: it ports as CSS.

---

## 2. The design system, in one screen

Industry: a blueprint grammar on a paper ground. Square corners, hairline
borders, `+` registration marks at the corners of every framed object.
Barlow Condensed over Barlow. One steel accent. Cards and figures are
transparent line drawings; the primary button is the one solid object.

The whole 188 KB document uses **twelve distinct hex values**. That is the
strongest signal in the import — the palette is genuinely closed.

| Hex | Uses | Role |
| --- | --- | --- |
| `#5980a6` | 147 | `--color-accent` — steel |
| `#f2f2f3` | 113 | `--color-bg` — paper |
| `#070f0b` | 53 | **Deep green field — not an Industry token** |
| `#94bce3` | 50 | `--color-accent-400` — accent on dark grounds |
| `#1d1f20` | 48 | `--color-text` — ink |
| `#416180` | 27 | `--color-accent-700` — accent text on paper |
| `#e9e9ea` | 15 | `--color-surface` |
| `#1d2d3d` | 6 | `--color-accent-900` — the announcement-bar field |
| `#0d1a13`, `#0b1712` | 1 each | Gradient stops inside the green field (4a) |
| `#e6e6e8`, `#d8d8db` | 1 each | Canvas chrome — the mockup viewer, **not product** |

Nine of the twelve are Industry tokens used correctly. The two canvas colours
belong to the Claude Design page furniture and must not be carried across.

**`#070f0b` is the one addition the mockups make to the system.** Industry has
no dark ground; CaddieInsight's deep green is the reversed field for video,
evidence and the capture screen. It appears 53 times and needs to become a
first-class token (see Question 3).

The accent behaves differently on each ground, exactly as the system's readme
prescribes: `#5980a6` on paper, `#94bce3` on green. The brand marks encode the
same rule — ink marks use `#5980a6` grooves, paper marks use `#94bce3`.

---

## 3. Screens

The file runs **newest turn first** — turn 7 at the top, turn 1 at the bottom.
That ordering matters: turns 5 → 6 → 7 are an evolution, and the later turn wins.

### Turn 1 — CaddieInsight app: report, progress, drills · `1a`–`1f`

The **app** surface (`app.caddieinsight.com`). App nav throughout:
`Sessions · Progress · Drills · Gear` + plan badge (`PRO` / `COACH`) + avatar.

| ID | Screen | Width | Sections |
| --- | --- | --- | --- |
| `1a` | Swing report — desktop | 1180 | App nav · session header (`SESSION 014 · 11 AUG 2026 · 18:42`) with `Compare` / `Re-film this swing` · swing-clip player on the green field with P1–P9 position scrubber and 0.5× speed · "Measured this session" 5-row metrics table (This clip / Last matched / Δ / Noise floor) · capture-context spec block (club, view, handedness, swings found) · priority panel (`PRIORITY 01 · OF 04 FINDINGS`) · pass-mark plate |
| `1b` | Swing report — mobile | 402 | Back bar · portrait clip on green with centerline + key-positions toggles · "YOUR ONE JOB" priority card · pass-mark meter (`NOW 0.29` / `MARK 0.32`) · "Practice this" drill card with dosage · collapsed evidence rows with disclosure chevrons |
| `1c` | Progress — desktop | 1180 | App nav · club/view/handedness filter strip · club tabs (7-Iron / Driver / Wedge) · four stat tiles · "Head sway · session over session" chart with dashed pass-mark line and a shaded noise floor, S01→S14 |
| `1d` | Progress — mobile | 402 | Verdict card (`HEAD SWAY · IMPROVED`, `0.41 → 0.29`) · sparkline · 4-cell stat grid · priority history list with Active / Held / Unproven states · `Film session 15` CTA |
| `1e` | Drill library — desktop | 1180 | App nav · `24 DRILLS · FREE TO READ BEFORE YOU FILM` · "Matched to your priority" banner · filter chips (All / Lead-hip / Head stability / Tempo / Extension / Rotation / No ball / Range / Indoor) · drill cards with looping demo figures and a "Your priority" flag |
| `1f` | Drill detail — mobile | 402 | Back bar · demonstration loop on green · dosage/time/setup spec row · numbered "How to do it" steps · "WHAT THIS DRILL HAS TO PROVE" pass-mark block · one quiet optional-gear line · `Mark practiced · film next` |

### Turn 2 — Shopify storefront: home & product · `2a`–`2c`

Storefront nav throughout: `Method · Sample report · Plans · Gear` +
`Sign in` + `Cart · N` + **`Analyze free`** (the primary CTA).
Announcement bar above it on `--color-accent-900`.

| ID | Screen | Width | Sections |
| --- | --- | --- | --- |
| `2a` | **Home** — desktop | 1180 | Announcement bar (`OFFER · 15% OFF GEAR · CODE RANGE15`) · nav · hero "Bring one clear move to the range." with dual CTA and three trust chips · hero figure on green (range footage, club/priority/target spec strip) · scope disclaimer · **Method** — 4 numbered steps (01–04) with caption chips · **Inside the report** — 3 numbered points + sample-report figure · **Rack** — 3 product cards + "View all training gear" · **Plans** — Free / Pro $9.99 / Coach $19.99 · Founders Pass band · footer in three numbered columns (01 SHOP / 02 LEARN / 03 SUPPORT) |
| `2b` | Product — desktop | 1180 | Breadcrumb · gallery with FRONT / IN USE / SCALE / ▶ CLIP thumbnails · specification table (Trains / Paired drill / Use / Fits / Ships / Returns) · buy box: price, discount note, description, **"Matched to your current priority"** callout, Size + Bundle variant pickers, qty stepper, add-to-cart, three fulfilment chips · "Gear is optional" band · "How to use it" 3 steps · "Also in the rack" 4-up |
| `2c` | Product — mobile | 402 | Condensed announcement · hamburger nav · hero shot · title/price · priority match · Size picker · specification · "Gear is optional" · **sticky buy bar** (qty + add to cart) |

### Turn 3 — Founders Pass, policies, contact, checkout · `3a`–`3d`

| ID | Screen | Width | Sections |
| --- | --- | --- | --- |
| `3a` | **Founders Pass** page + home band | 1180 | Announcement variant (`FOUNDERS PASS · 37 OF 100 REMAINING · NEVER RENEWS`) · hero "Pay once. Coach forever." · 100-cell scarcity grid (each cell = 10 passes) · "What $249 buys, permanently" 6-item checklist · payback calculation (13 months) · three numbered rationale blocks (Why it is capped / What founders get first / What it is not) · **4-column comparison table** Free / Pro / Coach / Founders · the drop-in band for `2a` |
| `3b` | **Policy shell** — desktop | 1180 | Breadcrumb · left sidebar listing all six policies (privacy, refund, cancellation, shipping & returns, terms, contact information) + a support card · `LAST UPDATED` stamp · summary spec row (Memberships 14 days / Training aids 30 days) · numbered clauses 01–04 · contact footer line. **One shell, six documents.** |
| `3c` | **Contact** — desktop | 1180 | `SUPPORT · TYPICAL FIRST REPLY UNDER 24 HOURS` · form routed by subject (An order / My membership / A swing report / Something else), with a conditional order-number field · "Before you write" FAQ deflection card · Direct-contact spec table (orders, memberships, press, hours) · returns-address warning |
| `3d` | **Checkout** — desktop | 1180 | Minimal header, 4-step breadcrumb · Contact / Shipping address / Delivery / Payment · order summary with a mixed cart (physical gear + Founders Pass) · discount applied · "After checkout" activation note. **Reference only — see Question 9.** |

### Turn 4 — Golf feel + the iPhone capture screen · `4a`, `4b`

| ID | Screen | Notes |
| --- | --- | --- |
| `4a` | **iPhone capture** — 402×874 | The only screen using `ios-frame.jsx`, via `<x-import … dark={true}>`. Camera view on `#070f0b` with mown-fairway stripe gradient, live framing guides, context chips (`7-IRON` / `FACE-ON` / `RIGHT`), `FRAMING OK`, `MATCHES SESSION 013`, audio strike detection (`2 SWINGS HEARD`), 0.5× and FLIP controls |
| `4b` | **Golf-feel hero** — alternate | An alternate home hero: "Play the shot you practiced." Introduces fairway stripes in the green field, the flagstick as the pass-mark marker, and a scorecard-style stat band (SESSION / CLUB / VIEW / SWAY / HIP / TEMPO) |

### Turn 5 — Scorecard + first logo attempt · `5a`, `5b`

| ID | Screen | Status |
| --- | --- | --- |
| `5a` | 18-hole scorecard | Live. A real scorecard (Fernhill Links, blue tees, date/player/HCP, holes 1–18 with OUT/IN/TOT) carrying the coaching record alongside the score |
| `5b` | Four CI marks | **Superseded.** Four marks built from tiles/discs/rules |

### Turn 6 — Logo, redrawn as golf · `6a`, `6b`

Opens: *"Scrapped the stick-and-box clubs."* This turn explicitly replaces `5b`.

| ID | Content |
| --- | --- |
| `6a` | The drawn iron as a reusable unit (tapered grip, thinning shaft, flared hosel, cambered sole, steel grooves) with its construction specs, then four marks built from it: **A** crossed irons · **B** the I is the iron · **C** address · **D** blade badge |
| `6b` | **Mark B in use** — primary lockup at 46/30/18 px, reversed on the green field, and the small-size rule: grip, shaft and blade survive to favicon size, only the grooves go |

**Mark B is the chosen mark.** Turn 6 supersedes turn 5b; turn 6b develops only
Mark B; turn 7 exports only Mark B. See Question 2.

### Turn 7 — Favicon · `7a`, `7b`

| ID | Content |
| --- | --- |
| `7a` | **The favicon set** — Mark B's iron on the green field at 512/64/32/16. Caption: *"PNGs are in the project as favicon-512/64/32/16 — drop into Shopify's favicon slot and the app manifest."* Shown in a browser tab bar over `app.caddieinsight.com` |
| `7b` | **The CI monogram** — transparent PNGs, ink for light grounds and paper for the green field. Caption: *"The two-glyph monogram holds to 32px. At 16, ship the club-only tile from 7a instead."* |

---

## 4. How the mockups actually use the assets

This is the finding that most changes the Phase 4 plan.

**No screen loads a PNG.** Every `<img>` in the document — eleven of them — sits
inside turn 7, and every one is a proof sheet showing the exported files at size.
`club-paper-512.png` is not referenced at all.

Every real screen **draws the mark inline in CSS**. The lockup is one inline-block
15×24 px containing five absolutely-positioned spans:

```
Caddie[ grip · shaft · blade(clip-path) · groove · groove ]nsight
```

Grip, shaft and blade use `currentColor`; the two grooves are hard-set to the
accent. Because the silhouette inherits colour, the same markup renders ink on
paper and paper on green with no variant — which is exactly why the reversed
lockup in `6b` needs no second asset.

The whole document contains **two `<svg>` elements**. Everything else — every
chart, every drill figure, every device outline — is CSS boxes, gradients,
`clip-path` and `border-radius`.

So the asset roles are:

| Asset | Where it belongs |
| --- | --- |
| `favicon-512/64/32/16.png` | Shopify favicon slot, app `<link rel="icon">`, PWA manifest |
| `ci-mark-ink-512/128.png` | Light-ground contexts outside the page: OG images, print, email |
| `ci-mark-paper-512.png` | The same, reversed on green |
| `ci-favicon-64/32.png`, `ci-favicon-paper-32.png` | Monogram favicon variant — see Question 1 |
| `ci-favicon-16.png` | **Do not ship.** `7b` says use the club-only tile at 16px |
| `club-ink-512.png`, `club-paper-512.png` | The club alone, for marks that stand without the wordmark |
| The lockup itself | **CSS, not an image** — a shared snippet on both surfaces |

---

## 5. Coverage against the brief's site map

| Page | Mockup | Gap |
| --- | --- | --- |
| Home | `2a` desktop, `4b` alt hero | No mobile |
| Product | `2b` desktop, `2c` mobile | — |
| Founders Pass | `3a` desktop | No mobile |
| Policies (×6) | `3b` shell, desktop | No mobile |
| Contact | `3c` desktop | No mobile |
| Checkout | `3d` desktop | Reference only |
| Collection / "the rack" | — | Linked 4× as "View all training gear", never designed |
| Cart / cart drawer | — | Cart count in nav; no cart page |
| FAQ | — | Linked from `2a` footer and `3c` |
| About | — | Not in the mockups at all |
| Blog index | — | Not in the mockups at all |
| Terms of service | Listed in `3b`'s sidebar | Uses the policy shell — covered |
| 404 / search results | — | Not designed |
| App: report / progress / drills | `1a`–`1f` | — |
| App: capture | `4a` | — |
| App: sign in / sign up | — | Phase 5 asks for these; no mockup |

**No screen is drawn at 768 px.** The mockups are 1180 (desktop) and 402
(mobile). The brief asks for verification at 375 / 768 / 1440. The tablet
behaviour and both desktop end-points are extrapolations either way.

---

## 6. Content that is stale or provisional

Flagging these now so they do not get built in by accident.

1. ~~**`2b` breadcrumb reads `HOME / SWINGLAB GEAR /`.** The old brand, left in
   the mockup.~~ **Wrong — corrected in Phase 1.** The live collection handle
   really is `swinglab-gear` (titled "CaddieInsight Gear"), so the mockup is
   rendering production accurately. The rebrand changed titles but not handles.
   This is a URL decision, not a copy fix — see `SITE_AUDIT.md` §6 risk 3.
2. **`6a` closes with:** *"Still drafts for judging the idea — a designer should
   redraw the winner as true vector artwork."* The mark we would ship is a
   CSS-drawn draft.
3. **All data is demonstration data**, and the mockups say so repeatedly. But
   prices, the `RANGE15` code, `37 OF 100 REMAINING`, and the three
   `@caddieinsight.com` addresses would go live as written.
4. **`3b` is stamped `LAST UPDATED 04 AUG 2026`** and contains complete,
   specific policy text (14-day membership refunds, 30-day gear returns).
   That is legal copy, not placeholder copy.

---

## 7. Decisions (answered 2026-08-12)

Kyle's ruling: **build the whole thing, not just styling and shell**; the
favicon is **turn 7's club**, not `6b`'s small-size lockup; the rest on my
defaults. Resolved as follows — the original questions are kept below for the
reasoning behind each.

| # | Decision |
| --- | --- |
| 1 | **`favicon-512/64/32/16`** (club on green) → Shopify favicon slot + PWA manifest. `ci-mark-*` monogram → OG images and light grounds. `ci-favicon-16` not shipped, per `7b`. |
| 2 | **Mark B** is the logo. The CSS-drawn lockup ships as-is; no designer gate. |
| 3 | `#070f0b` becomes a named token. *(Phase 1 found it is already the app's `theme-color` — this is bookkeeping, not a change.)* |
| 4 | **Full build.** App screens `1a`–`1f` are built properly, not restyled. |
| 5 | Missing pages designed by extending the Industry grammar. *(Phase 1 found templates already exist for all of them — see `SITE_AUDIT.md` §4.)* |
| 6 | Mobile extended from `2c`'s idiom for the five desktop-only storefront pages. |
| 7 | Prices ship as drawn. Surfaced again in `COPY_DECK.md` for a last look before anything is published. |
| 8 | The three `@caddieinsight.com` addresses ship as drawn; verification moves to `LAUNCH_CHECKLIST.md`. |
| 9 | `3d` is reference only. *(Confirmed: the store is on **Basic**, so checkout is not themeable regardless.)* |
| 10 | Device frame recreated for the capture screen only. |
| 11 | `2a`'s hero is primary; `4b`'s golf-feel treatment becomes section settings. |
| 12 | Mockup labels kept — `Sign in` and `Analyze free`, both to `app.caddieinsight.com`. |

## 8. Questions (as originally asked)

Ordered by how much they block. The first four change what gets built.

**1. Which set is the favicon?**
The brief says *"the `ci-favicon-*` set replaces all current favicons"*. The
mockups say the opposite: `7a` captions the `favicon-*` set with *"drop into
Shopify's favicon slot and the app manifest"*, and `7b` calls `ci-*` the
monogram, adding that at 16 px you should *"ship the club-only tile from 7a
instead"*. Rule 6 says the mockups override — so my default is **`favicon-*` in
the favicon slot** (club on green, which also reads better at 16 px), with the
`ci-mark-*` monogram reserved for OG images and light-ground contexts. Confirm,
or tell me you meant `ci-favicon-*` and I will follow the brief.

**2. Mark B confirmed as the logo?**
Turn 6 scrapped turn 5's four marks outright, turn 6b develops only Mark B, and
turn 7 exports only Mark B. I will treat `5b` as dead. Also: do we ship the
CSS-drawn lockup as-is, or is `6a`'s "a designer should redraw the winner"
a gate before launch?

**3. Deep green — what is it called and where is it allowed?**
`#070f0b` is the second-most-used colour after steel and paper, and Industry has
no token for it. I plan to add it as a first-class CaddieInsight token and
confine it to the roles the mockups give it: video, evidence, the capture
screen, and the reversed lockup. Anything else you want on that ground?

**4. How far does the app work go?**
Phase 5 says *"styling and shell only — do not change app functionality,
routes, business logic."* But `1a`–`1f` are complete app screen designs: a
metrics table with a noise-floor column, a pass-mark meter, a priority-history
list with three verdict states. Building those is not a restyle. Do you want
(a) shell only now and the app screens as a later phase, or (b) the full `1a`–`1f`
build in Phase 5?

**5. What do I do where there is no mockup?**
About, FAQ, Blog index, the collection/rack page, cart, 404 and search have no
design. Rule 6 says extend using the system. Confirm you want me to design those
from the Industry grammar, or say which should be dropped or redirected instead.

**6. Mobile for the five desktop-only storefront pages?**
Home, Founders, policy, contact and checkout exist at desktop only. `2c` shows
the mobile idiom clearly enough (condensed announcement, hamburger, sticky
action bar) that I can extend it. Fine to proceed on that basis?

**7. Are the numbers real?**
Pro $9.99/mo, Coach $19.99/mo, annual $69.99, Founders $249 capped at 100,
`RANGE15` for 15% off gear, and `37 OF 100 REMAINING`. I have a note that
pricing was pending your go-ahead. Which of these ship as written, and should
the Founders counter be wired to real inventory or hard-coded?

**8. The three email addresses.**
`support@`, `billing@` and `press@caddieinsight.com`, plus `Mon–Fri, 09:00–17:00
ET`. Do these exist?

**9. Checkout — reference only, correct?**
`3d` designs a checkout, but hard rule 2 says never touch checkout, and Shopify
checkout is not themeable outside Plus. I will treat `3d` as a visual reference
for cart and post-purchase pages and leave checkout alone. Confirm.

**10. The device frame.**
`ios-frame.jsx` is used for exactly one screen — `4a`, the capture screen — and
it is a generic starter marked *"raw elements/hex/px by design"*, not
CaddieInsight design. The mobile screens `1b`, `1d`, `1f` and `2c` are drawn
with no frame at all. So the frame is not the mockups' general device for
presenting app screenshots. Do you want it recreated just for the capture
screen, or dropped?

**11. `4b` — alternate hero or replacement?**
Turn 4 is newer than turn 2, and `4b` is a different home hero ("Play the shot
you practiced") with fairway stripes and a flagstick pass-mark. Does it replace
`2a`'s hero, or is it a variant to hold?

**12. The storefront's app CTA.**
Phase 3 asks for a prominent "Open the App" / "Log In" action. The mockups use
`Sign in` plus a primary `Analyze free`. I will keep the mockups' labels and
point both at `app.caddieinsight.com` unless you want the brief's wording.

---

## Where this leaves Phase 1

Shopify CLI 4.6.1 is installed and the app repo is in hand, so discovery can
start as soon as you have answered — or as soon as you tell me to proceed on my
defaults and answer as we go.
