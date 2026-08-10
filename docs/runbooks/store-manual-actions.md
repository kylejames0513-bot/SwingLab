# Store manual actions — run once in Shopify admin

These actions could not be completed via the Admin API from the assistant
session (missing `write_legal_policies` scope / permission-layer blocks).
Everything here is paste-ready. Estimated total time: ~20 minutes.

Completed via API already (2026-08-03): branded SKUs on all six gear products
(CI-MAT-* / CI-ROT-* / CI-CB-* / CI-ARM-* / CI-TT-* / CI-ROPE-*), Rotation
Trainer Red price corrected $38.99 → $28.99, Pro repriced to the approved
structure ($9.99 / Season Pass $69.99 / Founders Pass $149) with rewritten
product description, Lifetime variant renamed Founders Pass, all Pro variants
untracked (memberships are subscription-based — no quantity, per owner;
Founders repriced $149 → $249 on 2026-08-10 with the Coach rollout),
`swinglab:tempo` tag added to Tempo Rope, Pro vendor set to CaddieInsight,
Contact page filled + `contact` template assigned, About and FAQ pages
updated to the new pricing.

## 1. Legal policies — the text now lives in one place

**`docs/runbooks/store-policies.md` holds the final, paste-ready text.** The
drafts that used to sit here have been deleted rather than updated, because two
copies of a policy in one repo is how a store ends up publishing the older one.
That is not hypothetical: the copy that was here said *"we currently ship
physical gear within the United States"* while checkout was already selling to
21 Asian markets, and it described a "Lifetime" variant that had been renamed
Founders Pass months earlier.

Go there for: **Terms of Service** (new — the live URL 404s and no record
exists, while two recurring selling plans charge cards), **Refund policy**
(replaces Shopify's stock physical-goods template), **Contact information**
(adds the business address Meta's commerce review looks for), a **privacy
addendum** covering swing video, and the **Shipping policy** — which is the one
draft still carrying brackets, because `docs/first-sale-launch.md` forbids
publishing transit times no supplier has demonstrated.

Two things there need you rather than a paste:

- **The owner block** — legal entity name and the business mailing address to
  publish. The address on file is residential, so this is a decision. The same
  token appears in three policies; one find-and-replace does all three.
- **Settings → General → Store contact email** is still the personal iCloud
  address, and Shopify's stock privacy template renders it into the live
  privacy policy. No policy edit reaches it. Changing it to
  `inquiry@caddieinsight.com` is the highest-value single click on this page,
  because it fixes a policy you never have to open.

The Terms also publish two promises that need a switch thrown, not a paragraph
written: a renewal reminder at least 7 days before an annual charge (confirm
Shopify's upcoming-subscription-billing notification is enabled), and the
100-member Founders cap in §3 below.

## 2. Fill the Contact page (currently empty) and assign the form template

Online Store → Pages → Contact.

1. Set the theme template to **contact** (the theme PR adds
   `page.contact.json` with a working contact form — after the theme release).
2. Paste this into the page content:

```html
<div class="sl-page-hero">
  <p class="sl-mono-label">SUPPORT</p>
  <h2>Talk to a person</h2>
  <p>Every message is read and answered by the person who runs CaddieInsight — usually within one business day, always within two.</p>
</div>
<h2>Order questions</h2>
<p>The fastest route for anything about an order — tracking, returns, a damaged item — is to <strong>reply directly to your order confirmation email</strong>. Your order details arrive with it, so nothing needs to be looked up.</p>
<h2>Pro access questions</h2>
<p>Pro connects to the CaddieInsight account matching your checkout email. If access has not appeared: open <a href="https://app.caddieinsight.com">app.caddieinsight.com</a>, sign in (or sign up) with the exact email you used at checkout, and it will be claimed automatically. Still stuck? Use the form below and it will be fixed manually.</p>
<h2>App and coaching questions</h2>
<p>Questions about filming, reports, drills, or your account — including anything the <a href="/pages/faq">FAQ</a> does not cover — are welcome through the form below.</p>
<h2>Refunds and returns</h2>
<p>Unused gear returns within 30 days; unused Pro refunds within 14. The full details live on the <a href="/pages/shipping-returns">Shipping &amp; Returns page</a> and in our <a href="/policies/refund-policy">Refund Policy</a>.</p>
```

## 3. Founders Pass cap — one checkbox, and one adjacent click that breaks the store

The storefront promises "the first 100 members" three times, and §8 of the
Terms of Service now publishes that cap as a term of the contract. **It is not
enforced today.** Variant `46839745282220` (`SL-PRO-LIFE`) carries
`inventoryQuantity: 100` and `inventoryPolicy: DENY`, but
`inventoryItem.tracked: false` makes both inert — nothing stops sale 101, or
sale 5,000, each one a perpetual membership against perpetual compute. A cap
published in the Terms and not enforced is not a marketing exaggeration; it is
a false statement in a contract, from the moment sale 101 clears.

Enforcement is one checkbox: the Founders Pass variant → Inventory → **Track
quantity**. No theme deploy is needed — the theme already renders the disabled
option, the "Sold out" label and schema.org `OutOfStock` off `variant.available`.

**Do not enable tracking on `SL-PRO-1MO` (qty 0) or `SL-PRO-12MO` (qty -1).**
Both are `CONTINUE`, and tracking them would make the two subscription plans
immediately unbuyable. This is the single most dangerous adjacent click in the
whole launch — the checkbox that fixes one variant silently kills the two next
to it. See `docs/superpowers/specs/2026-08-09-two-tier-membership-and-free-proof-cycle-design.md`.

## 4. Gear → report tag coverage

No active product carries `swinglab:sway` or `swinglab:hip-slide` — either
source aids for those flags (the archived Alignment Stick / Hip Band /
Mirror products covered them) or soften the collection copy that promises
tempo / sway / hip-slide / consistency mapping.

## 5. Gear inventory (leave it to DSers)

Gear quantities live at the `dsers-fulfillment-service` location and are
managed by the DSers supplier sync — do not hand-edit them. Swing Path Mat
"Outdoor Use" shows 0 available (hidden as sold out on the storefront):
check the supplier in DSers or hide that variant.

## 6. Put a person on the About page (highest-leverage single edit)

The About page is well-written but faceless ("a small golf-tech studio…
we"). For a one-person brand, the faceless "we" is the scam signal; the named
founder is the trust move (founder stories convert 18–27% better on cold
traffic). Add a section like this to Online Store → Pages → About — fill in
the personal details and add a real phone-shot photo of you at the range:

```html
<h2>Who runs this</h2>
<p>CaddieInsight is built and run by me, Kyle. I'm a golfer, not a golf
company — I built the engine because my own range sessions produced feelings
instead of information, and I wanted the phone in my pocket to tell me the
truth about my swing. Every report the app produces is one I use on my own
game, every email to support is answered by me personally (within 24 hours,
usually same day), and every training aid in the shop is one I've tested with
the drill it's matched to.</p>
```

Also note: the About page hardcodes Pro prices ($4.99/$39.99/$79.99), which are
already two price changes stale. Prices are about to move again — a second paid
tier and a Founders Pass repricing are approved in
`docs/superpowers/specs/2026-08-09-two-tier-membership-and-free-proof-cycle-design.md`.
Rewrite that paragraph to name the plans and link the membership page instead of
quoting figures; the policies in `store-policies.md` were written price-free for
the same reason, and every hardcoded number is a page that goes wrong silently.

## 7. Create an "Accuracy & Limits" page (differentiator-compounding)

No competitor states what phone video cannot measure; publishing limits is the
credibility play golf's most trusted brands use. Online Store → Pages → Add
page, title "Accuracy & Limits", then link it from the footer Learn column and
the homepage. Paste:

```html
<div class="sl-page-hero">
  <p class="sl-mono-label">ACCURACY &amp; LIMITS</p>
  <h2>What a phone video can measure — and what it can't</h2>
  <p>Most swing apps tell you what they detect. We think you deserve to know what we can't. This page states CaddieInsight's measurement scope plainly, because a number you can't trust is worse than no number at all.</p>
</div>
<h2>What we measure</h2>
<ul>
  <li><strong>Timing</strong> — backswing and downswing durations and the tempo ratio, benchmarked against the 3.0:1 tour average. Strikes are found from the sound of impact, so clips need sound on.</li>
  <li><strong>Supported 2D body movement</strong> (face-on clips) — head sway and hip slide between address and the top, measured in shoulder widths. Normalizing by your own shoulder width at address makes sessions comparable no matter where the camera stood.</li>
  <li><strong>Key positions</strong> — address, top, impact, and finish frames, plus quarter-speed slow motion and a centerline overlay.</li>
</ul>
<h2>What we do not measure</h2>
<p>Club path, face angle, attack angle, dynamic loft, launch, spin, carry, strike location, clubhead speed, ball speed, and ball flight. A single phone camera cannot measure these honestly, so we don't pretend to. Down-the-line clips currently support timing only; every movement metric is defined face-on, and the report says so rather than guessing.</p>
<h2>What the numbers depend on</h2>
<ul>
  <li>Phone at hip height, full body in frame, face-on or down-the-line, a few seconds between swings.</li>
  <li>Re-films are only comparable when the club, handedness, camera angle, and framing match the baseline — the app checks the first three and tells you when a comparison isn't fair.</li>
  <li>Thresholds are fixed and published: head sway flagged beyond 0.35 shoulder widths, tempo flagged under 2.4:1. The selected club orders same-severity issues; it never changes a measurement.</li>
</ul>
<h2>Why we hold this line</h2>
<p>Because the method depends on it. CaddieInsight prescribes one fix with a numeric pass mark and then verifies it against a matched re-film — twice — before calling it improved. Verification is only honest if the measurements are. Automated estimates from a single camera are not a substitute for instruction from a teaching professional, and every report carries that line too.</p>
```

## 8. Housekeeping (5 minutes, optional but tidy)

- Navigation: delete or repoint the default "Main menu" (contains a
  /collections/all Catalog link the theme never renders) and default
  "Footer menu" — the theme uses the CaddieInsight Main/Footer menus.
- Collections: the default "Home page" collection contains one product and is
  unused by the theme; empty or ignore it.
- Install **Judge.me (free tier)** for product reviews — the theme product
  page will pick it up; never seed reviews.
