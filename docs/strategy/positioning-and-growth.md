# CaddieInsight — positioning, pricing, and credibility strategy

Status: proposal for owner decision. Researched 2026-08-03 against 18Birdies,
Sportsbox AI, V1 Golf, OnForm, Swing Profile, Golfboy, SwingU, Skillest,
GolfFix, GOATY, DeepSwing, plus DTC-golf credibility case studies (Sub 70,
Takomo, Shot Pattern, Divot Board, HackMotion, MyGolfSpy). Sources are cited
inline where a specific number is load-bearing.

## 1. Positioning: the program that proves the fix held

Every competitor stops at diagnosis or capture:

- **18Birdies** ($19.99/mo, $99.99/yr, $7.99/wk; 10M+ users) checks three swing
  positions and outputs a vague Swing Rating with no accuracy claims, no pass
  marks, no verification. Reviewers call it "a round app that added practice
  features — not a practice app."
- **Sportsbox AI** ($15.99/mo, $110/yr) sells 3D kinematics measured under
  best-practice capture conditions it discloses only in a help article.
- **V1 Golf / Swing Profile / OnForm** ($9.99–14.99/mo) are capture-and-drawing
  toolkits that explicitly do not diagnose, prescribe, or verify.
- **GolfFix** (~$15/mo) markets "45+ swing issues detected" while its own
  developers concede accuracy is weak beyond major faults.

Nobody in the surveyed field verifies improvement: no matched re-film
protocol, no pass/fail criteria for a prescribed fix, no repeated confirmation
before declaring a change real. That is CaddieInsight's whole method.

**Positioning statement.** CaddieInsight is the only swing-improvement program
that refuses to call your swing fixed until it proves it. It states exactly
what phone video can and cannot measure, prescribes exactly one prioritized
fix with a drill, a dosage, and a numeric pass mark, then requires a matched
re-film to confirm the change held — twice — before anything counts as
improved. **Diagnose. Prescribe. Prove.**

This is durable positioning, not a feature: an incumbent that adopts
verification contradicts its own instant-results marketing, and one that
publishes its measurement limits indicts its own accuracy claims.

Copy patterns to use (never naming competitors):

- "You don't need 45 problems found. You need the one that causes the other 44."
- "A swing score after every ball is gamification. A pass mark you re-film
  against is coaching."
- "We measure only what a phone can actually measure — and we say so."

## 2. Pricing: current tiers read as toy-priced

**DECIDED AND SHIPPED 2026-08-03 (owner approved):** $9.99/mo · Season Pass
$69.99/yr (featured, $5.83/mo, save 42%) · Founders Pass $149 once, capped at
the first 100 members (manual cap — memberships carry no inventory quantity).
Store variants, product description, storefront copy, and app pricing page
all updated. The analysis below is retained as the rationale.

Reference points (sourced in research run 2026-08-03):

| Comparable | Price |
| --- | --- |
| One private lesson | $50–150 |
| GolfTEC swing evaluation (one session) | $95–149 |
| Skillest human video coaching | $50–200/mo |
| Sportsbox AI | $15.99/mo · $110/yr |
| V1 Golf Plus (no coaching, tools only) | $9.99/mo · $69.99/yr |
| Health & fitness app category median | $9.70/mo (RevenueCat) |
| CaddieInsight Pro today | **$4.99/mo · $39.99/yr · $79.99 lifetime** |

$4.99/mo sits below tools that explicitly do not coach while claiming to do
more. RevenueCat category data shows higher-priced apps convert better
(2.7% vs 1.5% download-to-paid), so the low price is not buying conversion —
trust, not price, is the current blocker (537 sessions, 0 sales). The $79.99
lifetime is 2x annual against a healthy 5x multiple and cannibalizes the
customers most likely to renew.

**Recommendation (ship together with the free-tier change and social proof,
not before):**

- Monthly Pro: **$9.99/mo**.
- Featured plan: annual **Season Pass at $59.99–69.99/yr**, presented
  annual-first as "$5–5.83/mo, billed yearly," with an honest save badge.
  Golf is seasonal; the category's annual/monthly 12-month retention gap is
  44.1% vs 17.5%.
- Lifetime: retire the open $79.99 tier. Either drop lifetime entirely or
  replace with a capped **Founders Pass at $149** (first 100, firm end date,
  founder badge, one published sentence on why it is sustainable). Grandfather
  existing lifetime buyers loudly.
- Optional bridge SKU: single **Full Swing Report at $12.99** one-time
  (18Birdies sells single human reviews at $14.99, proving the a-la-carte
  anchor).
- Keep the free tier's report full-fidelity: "one real analysis beats five
  scores."

The pricing page should carry the sourced comparison strip: one lesson
$75–150, one GolfTEC evaluation $95–149, a launch monitor $499–1,300, Skillest
$50–200/mo — CaddieInsight, less per month than a bucket of range balls, and
the only one that proves the fix held.

## 3. Free tier: the loop must be completable (shipped)

The method is Film → Practice → Re-film → Prove. A free tier of one analysis
per month could never complete it. Shipped in this wave (flag
`allowances.free_matched_refilm`): a free account that produced a
coaching-ready baseline this month gets **one matched re-film within 14 days**
(same club, hand, angle) that does not consume allowance — one credit per
calendar month. Free = "1 full analysis + 1 matched re-film each month."

Tightening option if conversion needs it later: research supports an even
sharper shape — one complete proof cycle ever (or per season) free, with the
re-film returning the verdict only. Keep this in reserve; measure first.

The upgrade moment to instrument: the verdict that says "change held — one
more confirmation to count." That is the emotional peak and the natural
paywall trigger, aligned with the "first goal tracked" pattern.

## 4. Anti-fake checklist (golf-specific scam pattern-match)

Documented scam markers for golf stores: new domain + extreme discounts +
urgency timers + no contact info + template policies + supplier stock photos +
zero social presence + no named humans. Against CaddieInsight today:

- [ ] **Supplier stock photos on gear** — replace with original photos/video
  of the actual aids in the founder's hands at a real range. This is the
  single strongest fake-signal on the site today (the AliExpress-sourced
  gallery images on gear products).
- [x] SKUs that leak AliExpress option codes (cleaned for 4 of 6 products;
  Tempo Trainer + Tempo Rope in the manual runbook).
- [ ] Refund/Terms/Shipping policies must exist at /policies/* (paste-ready
  text in docs/runbooks/store-manual-actions.md — API scope unavailable).
- [ ] Contact page must contain a named human + response SLA ("emails answered
  by Kyle within 24 hours, usually same day") — theme contact form shipped;
  page body text in the runbook.
- [ ] A founder page: name, face, 300–600 words, phone-shot video, your own
  swing journey through the app. Founder stories convert 18–27% better on
  cold traffic. Being small is not the scam signal; pretending to be big is.
- [ ] At least one active linked social profile.
- [ ] Reviews: install Judge.me free tier; never seed; show negatives
  (4.2–4.7 converts better than 5.0; 53% of buyers look for bad reviews
  first).
- [ ] No countdown timers / "X viewing" theatrics — currently clean; keep it
  that way.
- [ ] Payment logos (Shop Pay, PayPal, Apple Pay) near the payment step, not
  a generic badge wall. (Dynamic payment buttons shipped in the theme pack.)
- [ ] Gear catalog: cull to aids you have physically tested on camera, each
  wired to the report flag its drill serves ("needed for this drill" framing).

## 5. Product moves that compound the differentiator

1. **Accuracy & Limits page** (public, linked from the homepage, not a help
   center): exactly what 2D phone video measures, required capture conditions,
   what is not measured. Publishing limits is the credibility play golf's most
   trusted brands use (MyGolfSpy datacratic testing; HackMotion published
   reliability; even Sportsbox's buried accuracy page).
2. **Side-by-side matched re-film comparison UI** — baseline vs re-film with
   the measured checkpoint overlaid. Simultaneously the table-stakes feature
   users expect from V1/Swing Profile and the native UI of the proof cycle.
   Highest-ROI feature in the backlog.
3. **Verified Changes counter** on the progress dashboard — increments only
   after a two-confirmation proof cycle. An honest metric no competitor can
   copy without adopting the same evidence standard.
4. **Drill demo videos** for every prescribed drill — founder-shot phone video
   is fine and on-brand; a written drill + dosage reads thin next to SwingU's
   600-lesson library.
5. **Proof streak / re-film cadence mechanics** — formalize the matched
   re-film the way 18Birdies formalizes rounds (reminders, streaks, badges
   when a fix survives two checks).
6. **Shareable verdict cards** — every confirmed improvement becomes a
   branded before/after image with on-screen numbers; users generate the proof
   content (the Divot Board / Lag Shot pattern).
7. Later, human-in-the-loop add-on: optional coach review SKU with Pro member
   pricing (18Birdies profitably sells these at $14.99–19.99).

## 6. 90-day credibility plan

- **Days 1–15**: anti-fake purge (section 4); founder page; guarantee near
  every buy button; support SLA; Judge.me; Accuracy & Limits page; pricing
  comparison strip.
- **Days 1–30**: recruit 15–20 real golfers (range acquaintances, local group
  chats, disclosed offers in golf communities) into free Pro through one full
  proof cycle. Ship the free-tier change + new pricing together.
- **Days 15–45**: side-by-side comparison UI; founder-shot drill demos for the
  most-prescribed drills.
- **Days 30–60**: ask each beta golfer for an honest review at their pass-mark
  moment; label free-access reviews; publish 3–5 numeric before/after case
  studies (metric, drill, dosage, verified result) as homepage social proof.
- **Days 60–90**: content engine. X: reply with genuinely useful
  screenshot-backed analysis under golf-instruction accounts (Shot Pattern
  playbook — one good reply out-reaches months of posting). Reddit r/golf:
  90/10 participation, always disclose "I built this," never sockpuppet; a
  transparent "I analyzed 50 swing videos, here's the most common fault"
  post is respected where a promo is flamed. GolfWRX: no vendor posts —
  editorial spotlight is the long-term route. Seed micro golf YouTubers
  (1k–50k subs) with free lifetime Pro + a training aid, no script (the
  Takomo model).

## 7. What we are deliberately NOT doing

- Not competing on GPS/scorecard/social breadth — 18Birdies wins that with
  10M users; a feature race is unwinnable. The vertical they structurally
  cannot serve without contradicting themselves is verified, honestly-scoped
  swing change.
- Not renaming the swinglab handles casually — the migration is coordinated
  in docs/runbooks/rebrand-cutover.md.
- Not seeding reviews, buying followers, or astroturfing — golf communities
  detect it, and the brand is built on the opposite.
