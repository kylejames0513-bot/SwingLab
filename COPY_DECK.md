# Site map and copy deck

Final copy for every page on both surfaces. Where the mockups carry real copy
it is kept verbatim and marked **[mockup]**. Where they hold nothing, copy is
written new and marked **[new]**.

> ### Corrections applied 2026-08-12
>
> **The Founders count is 100 of 100, nothing claimed.** The mockups draw a
> sold-through story — "37 OF 100 REMAINING", "63 CLAIMED" — which is fiction.
> Every occurrence now reads **100 remaining / 0 claimed**, and the count is
> built to stay accurate rather than be typed in. See §4.
>
> **One address: `inquiry@caddieinsight.com`.** The mockups invent `support@`,
> `billing@` and `press@`. The codebase has only ever used `inquiry@` — 33
> uses, and it is what `scripts/refresh_store_readiness.py` checks for. All
> returns and help go there.
>
> ### Still to check before publishing
>
> Prices go live exactly as drawn: **Pro $9.99/mo · Coach $19.99/mo · annual
> $69.99 (save 42%) · Founders Pass $249 · `RANGE15` for 15% off gear**, and
> support hours `Mon–Fri, 09:00–17:00 ET`. This is the last place they are
> cheap to change.

---

## 1. Voice

Plain, confident, direct. Short sentences. The product's own vocabulary —
*priority, pass mark, matched re-film, noise floor, club context* — used
consistently, because it is what makes the thing legible.

Three habits the mockups keep, worth keeping:

- **State the limit.** "Scope: face-on supports movement coaching; down-the-line
  currently supports timing only." Honesty about what it does not do is the
  reason to believe what it does.
- **Never oversell the gear.** "Gear is optional — the report is not."
- **One idea per screen.** One priority, one drill, one number to beat.

No corporate filler. No exclamation marks. No "revolutionise", "unlock",
"game-changing", "take your game to the next level".

---

## 2. Site map

### Storefront — caddieinsight.com

```
/                              Home                          [2a, hero 4b]
/pages/method                  The Method                    ← 301 the-swinglab-method
/pages/how-it-works            How CaddieInsight Works       ← 301 how-swinglab-works
/pages/plans                   Plans                         [2a plans]
/pages/founders                Founders Pass                 [3a]  NEW
/collections/gear              Training gear — the rack      ← 301 swinglab-gear
  /products/{six gear handles}                               [2b, 2c]
/products/swinglab-pro         CaddieInsight Pro             handle unchanged
/pages/about                   About
/pages/faq                     FAQ
/pages/contact                 Contact                       [3c]
/pages/shipping-returns        Shipping & returns            [3b shell]
/pages/data-sharing-opt-out    Your Privacy Choices          [3b shell]
/policies/*                    Privacy, refund, terms        Shopify-hosted
/cart  /search  /404  /blogs/news  /account/*
```

**Redirects.** Shopify creates a 301 automatically on a handle change; I will
add explicit ones too. `swinglab-pro` keeps its handle — the `orders/paid`
webhook is the only thing that grants Pro and `config.yaml`'s allowlist
references product handles, so renaming it risks the money path for no visual
gain. The six archived products get 301s to `/collections/gear`.

### App — app.caddieinsight.com

```
/                    Today                     /login /signup /reset
/sessions            Sessions                  /onboarding
/session/{id}        Swing report   [1a, 1b]   /account
/progress            Progress       [1c, 1d]   /pricing
/drills              Drills         [1e, 1f]   /shop
/scorecard           Scorecard      [5a]       /offline
capture / upload                    [4a]       /sample-report
```

### Global navigation **[mockup]**

**Header** — `Method · Sample report · Plans · Gear` · `Sign in` · `Cart · N` ·
**`Analyze free`** (primary).

"Sign in" and "Analyze free" both go to `app.caddieinsight.com`; "Sample
report" goes to `app.caddieinsight.com/sample-report`.

**Announcement bar** — `OFFER · 15% OFF GEAR · CODE RANGE15`, and on the
Founders variant `FOUNDERS PASS · 100 OF 100 REMAINING · NEVER RENEWS`.

**Footer** — three numbered columns:

| 01 SHOP | 02 LEARN | 03 SUPPORT |
| --- | --- | --- |
| Training gear | The Method | Orders & subscriptions |
| CaddieInsight Pro | How it works | Shipping & returns |
| | FAQ | Contact |

Footer tagline: *One priority. One practice plan. Proof when you re-film.*

---

## 3. Home — `/` **[mockup]**

**Hero** (on the field)

> **PHONE-VIDEO SWING COACHING**
> # Bring one clear move to the range.
> Film a repeatable view. Get one prioritized coaching plan with a drill and a
> pass mark for your next clip.
>
> `Analyze a swing free →` · `Explore the sample report`
> `1 REPORT / MONTH` · `NO CARD` · `CLUB SAVED`

Hero figure caption: `RANGE FOOTAGE — DUSK, PHONE ON TRIPOD`, with the spec
strip `CLUB / Iron · PRIORITY / Lead-hip control · TARGET / One pass mark`.

Under it: *SUPPORTED 2D MOVEMENT AND TIMING ESTIMATES FROM PHONE VIDEO ·
EXAMPLE INTERFACE USING DEMONSTRATION DATA*

**The Method** — `THE CADDIEINSIGHT METHOD · 04 STEPS`

> ## Film. Coach. Practice. Prove it.
> Four deliberate steps turn a phone clip into a range session with one clear job.

| | Step | Body | Chip |
| --- | --- | --- | --- |
| 01 / 04 | Choose the club | Iron, wedge, hybrid, fairway or driver — it stays with the report and the next comparison. | CLUB CONTEXT REQUIRED |
| 02 / 04 | Film a repeatable view | Phone at hip height, full body in frame, face-on or down-the-line chosen before upload. | MATCH THE VIEW NEXT TIME |
| 03 / 04 | Work one plan | Findings are ranked, one priority comes forward with a focused drill and a pass mark. | ONE PRIORITY · ONE PASS MARK |
| 04 / 04 | Re-film to prove it | Same club, handedness and angle, so the next clip can test whether the change held. | REPEAT THE CAPTURE SETUP |

> Scope: face-on supports movement coaching; down-the-line currently supports
> timing only. Not club path, face angle or ball flight.

**Inside the report** — `INSIDE THE REPORT`

> ## See the priority before you practice.
> The sample runs on the same report engine as a customer session: required
> club context, one supported priority, and a drill with a pass mark.
>
> 01 See the selected club and capture context
> 02 Understand why one coaching priority comes first
> 03 Leave with a focused drill and a measurable pass mark
>
> `View the sample report →`

Figure: `SAMPLE REPORT` / `DEMONSTRATION DATA`, spec strip
`Club / Iron · Coach / One priority first · Practice / One pass mark`.

**The rack** — `TRAINING AIDS · IN THE RACK 6 / 6`

> ## Train the priority your report surfaces
> `View all training gear →`
>
> 01 Connection Ball — Arm extension · consistency — $12.99
> 02 Tempo Trainer — Consistency · all swings — $28.99
> 03 Rotation Trainer — Consistency · all swings — $28.99
>
> Gear supports the drill a report prioritizes. None of it is required to use
> CaddieInsight.

**Plans** — `CHOOSE YOUR PACE`

> ## Start free. Move up when the reps matter.
> The analysis engine is the same on every plan. Pro removes the monthly limit.
> Coach adds the replay, the dashboard and the verdict.

| | Plan | Price | Body | CTA |
| --- | --- | --- | --- | --- |
| 00 · NO CARD REQUIRED | CaddieInsight Free | $0 | One full analysis and one matched re-film each month, with saved club context and a measurable plan. | Start free |
| 01 · MEMBERSHIP · PRO | CaddieInsight Pro | $9.99/mo | Unlimited analyses with the full report every time — film every range session and keep the whole record. *Or $69.99/yr, save 42%.* | Go Pro |
| 02 · MEMBERSHIP · COACH | CaddieInsight Coach | $19.99/mo | Everything in Pro, plus the annotated replay, the progress dashboard, and the matched re-film verdict that proves a fix held. | Get Coach |

**Founders band** (drops above the plans grid)

> **100 Founders Passes left**
> One payment of $249, every Coach feature for good, never renews. When the
> hundredth is claimed the band is removed.
> `See the Founders Pass →`

Plans footnote: *Founders Pass — one payment of $249, every Coach feature for
good, first 100 members only. 14-day refund on unused memberships.*

---

## 4. Founders Pass — `/pages/founders` **[mockup]**

> `MEMBERSHIP 03 · FOUNDERS`
> # Pay once. Coach forever.
> One payment of $249 for every Coach feature, for good. Capped at the first
> hundred members — a lifetime promise we can only keep if we stop selling it.
>
> `Claim a Founders Pass · $249`
> `ONE PAYMENT · NEVER RENEWS`

Scarcity grid: `0 CLAIMED` / `100 TOTAL` / `EACH CELL = 10 PASSES` — ten empty
rows of ten.

> **The count must stay true on its own.** A number typed into a theme setting
> is wrong the moment the first pass sells, and a scarcity claim that is wrong
> is worse than no claim. The section reads the remaining count from the
> Founders Pass product's tracked inventory, so Shopify is the single source of
> truth and also enforces the cap at checkout — it cannot oversell past 100.
>
> That needs a Founders Pass product with inventory tracking on and quantity
> **100**. It does not exist yet, and creating it changes the live store, so I
> have not. Say the word and I will add it; until then the section falls back
> to a theme setting defaulting to 100 and the page renders correctly either
> way.
>
> When the count reaches zero the band and the announcement variant remove
> themselves rather than reading "0 remaining".

**WHAT $249 BUYS, PERMANENTLY**

✓ Unlimited analyses and matched re-films
✓ Annotated coach replay of every swing
✓ Swing Pattern — how your swing is built, named
✓ Progress dashboard, session over session
✓ Matched re-film verdicts — proof a fix held
✓ Every Coach feature we ship after this one

*Coach at $19.99/mo pays this back in* **13 months**

**01 Why it is capped** — A hundred lifetime members is a cost we can carry
indefinitely. A thousand is not. The cap is the reason the promise is keepable.

**02 What founders get first** — New coaching features land on founder accounts
before general release, and the founders channel is where the roadmap gets
argued about.

**03 What it is not** — Not a hardware bundle and not a coaching subscription
with a human. It is the software, in full, without a renewal date.

**Founders against the rest**

| Feature | Free | Pro $9.99/mo | Coach $19.99/mo | Founders $249 once |
| --- | --- | --- | --- | --- |
| Swing analyses | 1 / month | Unlimited | Unlimited | Unlimited |
| Matched re-films | 1 / month | Unlimited | Unlimited | Unlimited |
| Annotated coach replay | — | — | Included | Included |
| Progress dashboard | — | — | Included | Included |
| Matched re-film verdict | — | — | Included | Included |
| Future Coach features | — | — | While subscribed | For good |
| Renews | — | Monthly | Monthly | **Never** |

---

## 5. Product — `/products/{gear}` **[mockup]**

Shown for the Rotation Trainer; the pattern applies to all six.

> `HOME / TRAINING GEAR / ROTATION TRAINER`
>
> `TRAINING AID · 05 OF 06`
> # Rotation Trainer
> **$28.99** · `RANGE15 · 15% off at checkout`
>
> A body-worn strap that makes rotation the only way to move. If your report
> puts lead-hip control first, this is the drill's training wheel.
>
> **MATCHED TO YOUR CURRENT PRIORITY**
> Lead-hip control · pass mark 0.32 sw — `See the drill`
>
> Size: Standard / Junior · Bundle: Trainer only / + Tempo Rope · $56.99
> `Add to cart · $28.99`
> `IN STOCK` · `SHIPS IN 1–2 DAYS` · `30-DAY RETURNS`

**Specification**

| Trains | Rotation · consistency · all swings |
| --- | --- |
| Paired drill | Lead-Hip Wall Touch · Step-Through Finish |
| Use | Indoor or range · no ball required |
| Fits | Right- and left-handed · adult sizing |
| Ships | Processed 1–2 business days · US 6–12 days |
| Returns | Unused gear within 30 days |

**GEAR IS OPTIONAL — THE REPORT IS NOT**
Every drill works without an aid. CaddieInsight recommends gear only when a
report has already put the matching priority first. `Analyze a swing free →`

**How to use it**
01 Set the strap across the chest with the loop over the lead shoulder.
02 Make slow half swings until the resistance only releases with rotation.
03 Remove it and film the same club and view to check the pass mark.

**Also in the rack** — `View all training gear →`

*Note: the breadcrumb reads `TRAINING GEAR`, not the mockup's `SWINGLAB GEAR`,
because the collection handle is being renamed to `/collections/gear`.*

---

## 6. Contact — `/pages/contact` **[mockup]**

> `SUPPORT · TYPICAL FIRST REPLY UNDER 24 HOURS`
> # Contact us
> One inbox. Tell us what it is about and the message gets to the right place
> faster.

*The mockup reads "One inbox, three routes … lands with the person who can
answer it." With a single address that overpromises, so the line is trimmed.
The subject router stays — it tags the message, which is the part that
actually helps.*

**Send a message** — What is it about? *An order · My membership · A swing
report · Something else*. Fields: Name, Email, Order number (*— on your
confirmation email*, shown for order enquiries), Message (*Tell us what
happened, and which club and camera angle you filmed if it concerns a
report.*). `Send message`

*We never share your email. See the privacy policy.*

**BEFORE YOU WRITE** — Most first messages are about filming setup, why a swing
was not detected, or how Pro unlocks after checkout. `Read the FAQ`

| Everything — orders, gear, memberships, returns, press | **inquiry@caddieinsight.com** |
| --- | --- |
| Hours | Mon–Fri, 09:00–17:00 ET |

**Returns address** — Do not ship gear back without a return authorisation —
partner warehouses reject unlabelled parcels. Start a return from your order
email.

---

## 7. Policy shell — `/pages/shipping-returns`, `/policies/*` **[mockup]**

One shell, six documents, with a `LAST UPDATED` stamp, a sidebar listing all
six, a summary spec row, and numbered clauses. The refund policy as drawn:

> # Refund policy
> Digital memberships and physical training aids are refunded on different
> terms. Both are stated in full below.
>
> **Memberships — 14 days**, if unused · **Training aids — 30 days**, unused,
> original packaging

**01 · Memberships** — CaddieInsight Pro and Coach are refundable within 14
days of purchase if no analysis has been run on the paid plan. The Founders
Pass is a one-time purchase and follows the same 14-day window. After that
window it is non-refundable.

**02 · Training aids** — Unused gear may be returned within 30 days of delivery
in its original packaging. Return shipping is the customer's responsibility.

**03 · Damaged or incorrect items** — Photograph the item and packaging and
contact support within 14 days of delivery. Replacements ship at no cost.

**04 · What is not refundable** — Used or damaged training aids, gift cards,
and membership periods on which analyses have already been run.

*Refund requests: inquiry@caddieinsight.com — include the order number from
your confirmation email. Related: cancellation policy, shipping & returns.*

> **Legal copy, not placeholder.** This is the one section where "keep the
> mockup copy verbatim" deserves a second read before publishing.

---

## 8. Pages the mockups do not cover **[new]**

Written from the same voice and built from the same section library.

### The Method — `/pages/method`

> `THE METHOD`
> # One clear move at a time.
> Most swing advice gives you five things to fix. You cannot think about five
> things at the top of the backswing. CaddieInsight ranks what it finds and
> hands you one.

Expands home's four steps with a section each, then:

> **Why one priority**
> A swing fault rarely travels alone. Fix the one that is causing the others
> and the rest often follow; fix the third-most-important one and you have
> spent a range session proving nothing. The report ranks findings by what the
> evidence supports, and carries the top one forward.
>
> **Why a pass mark**
> "Feels better" is not a result. Every priority comes with a number and a
> threshold, measured the same way on the next clip. Either it cleared or it
> did not.
>
> **Why the club matters**
> A 7-iron and a driver are different swings. The club is saved with the
> report so the next comparison is like for like.
>
> **What it does not do**
> No club path. No face angle. No ball flight. Face-on supports movement
> coaching; down-the-line currently supports timing only. Anything a phone
> camera cannot see honestly, we do not report.

`Analyze a swing free →`

### How CaddieInsight Works — `/pages/how-it-works`

The practical companion: filming setup, what happens after upload, what you
get back.

> `HOW IT WORKS`
> # From a phone clip to a range session.
>
> **01 Set up the shot** — Phone at hip height, roughly ten feet away, full
> body in frame including the club at the top. Landscape or portrait both work.
> Pick face-on or down-the-line before you film and stay with it.
>
> **02 Film the whole session** — You do not need to trim to one swing. Film
> the session and the audio strike detection splits the swings out.
>
> **03 Tell it the club** — Required, not optional. Without it there is nothing
> to compare the next clip against.
>
> **04 Read the report** — Findings ranked, one priority carried forward, the
> evidence behind it, and a drill with a pass mark.
>
> **05 Practice, then re-film** — Same club, same view, same handedness. The
> verdict tells you whether it held.
>
> **What you need** — A phone. Somewhere to prop it. That is the whole list.

### About — `/pages/about`

> `ABOUT`
> # Built for the range, not the lab.
> CaddieInsight started with a simple frustration: phone-video swing analysis
> either tells you nothing useful, or it tells you fifteen things at once and
> leaves you to guess which one matters.
>
> We built the opposite. One priority, ranked from what the evidence actually
> supports. One drill. One number to beat next time. If the number does not
> move, we say so.
>
> **What we will not do**
> We will not report what a phone camera cannot see. No club path, no face
> angle, no ball flight — those need hardware we are not pretending to be.
> Where a measurement sits inside its own noise floor, the report shows the
> change and declines to call it an improvement.
>
> **Where it runs** — Film on your phone. Read the report anywhere. The app
> installs to your home screen and works offline for everything except the
> analysis itself.
>
> `Analyze a swing free →`

### FAQ — `/pages/faq`

Accordion, grouped. Full list in the build; the shape:

**Filming** — What angle? · How far away? · Does lighting matter? · Do I need to
trim to one swing? · Can I film indoors? · Why was no swing detected?

**The report** — Why only one priority? · What is a pass mark? · What is a noise
floor? · What is a matched re-film? · Why does it need the club? · What is not
measured?

**Plans and billing** — What does Free include? · What does Pro add? · What does
Coach add? · What is the Founders Pass? · How do I cancel? · Refunds?

**Gear** — Do I need it? · Which aid matches my priority? · Shipping? · Returns?

Sample answers:

> **Why only one priority?**
> Because you can only think about one thing at the top of the backswing. The
> report finds everything it can support and ranks it; carrying one forward is
> what makes the next range session worth filming.

> **What is a noise floor?**
> The amount a measurement moves between two identical swings. If a change is
> smaller than that, it is not a change. The report shows it and declines to
> call it an improvement.

> **Why was no swing detected?**
> Usually framing — the club leaves the frame at the top, or the body is cut
> off. Phone at hip height, about ten feet back, full body in frame. Failing
> that, the audio strike detection may not have heard contact; a mat indoors
> is quieter than turf.

### Collection — `/collections/gear`

> `TRAINING AIDS · THE RACK`
> # Gear that matches a priority.
> Every aid here is paired to a drill, and every drill works without it. We
> recommend gear only when a report has already put the matching priority
> first.

Filter chips by what each trains: *Lead-hip · Rotation · Tempo · Extension ·
No ball · Indoor*. Cards carry the paired drill and its pass mark.

### Cart — `/cart`

> # Your cart
> Empty state: **Nothing in the cart yet.** Gear is optional — the report is
> not. `Analyze a swing free →` · `Browse the rack`

Memberships in the cart show `One payment · never renews` or `Renews monthly`,
and the activation note: *Your membership activates on the CaddieInsight
account matching your checkout email — open the app afterwards to claim it.*

### Search — `/search`

> # Search
> Empty: **Nothing matched "{terms}".** Try a drill name, a training aid, or a
> part of the swing. · `Browse the rack` · `Read the FAQ`

### 404

> `ERROR 404`
> # That page is not in the rack.
> The link may be old, or the page may have moved. Nothing here is lost —
> start from the home page or the gear rack.
> `Home` · `Training gear` · `Contact support`

### Blog — `/blogs/news`

Zero articles today. The index gets styled and left empty rather than
scaffolded with filler.

> # Notes
> Range notes, product changes and what we are working on.
> Empty: **Nothing published yet.**

### App sign-in and sign-up

> # Sign in
> Email · `Send a sign-in code` — *No password. We email you a code.*
>
> # Start free
> One full analysis and one matched re-film each month. No card.
> Email · `Create my account`
> *By continuing you agree to the terms and privacy policy.*

Post-checkout: *Signed in with the email you checked out with? Your membership
is already on this account.*

---

## 9. App screens — copy is in the mockups

`1a`–`1f`, `4a` and `5a` carry their copy in `MOCKUP_INVENTORY.md` §3 and in
the mockup file itself. Two rules for the build:

1. **Demonstration data stays demonstration data.** Session 014, 0.29 sw, the
   14-session chart — these are fixtures, not strings to hard-code into
   templates.
2. **The vocabulary is shared with the storefront.** *Priority · pass mark ·
   matched re-film · noise floor · club context* mean the same thing on both
   surfaces, which is most of what makes them one product.
