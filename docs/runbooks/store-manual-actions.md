# Store manual actions — run once in Shopify admin

These actions could not be completed via the Admin API from the assistant
session (missing `write_legal_policies` scope / permission-layer blocks).
Everything here is paste-ready. Estimated total time: ~20 minutes.

Completed via API already (2026-08-03): branded SKUs on all six gear products
(CI-MAT-* / CI-ROT-* / CI-CB-* / CI-ARM-* / CI-TT-* / CI-ROPE-*), Rotation
Trainer Red price corrected $38.99 → $28.99, Pro repriced to the approved
structure ($9.99 / Season Pass $69.99 / Founders Pass $149) with rewritten
product description, Lifetime variant renamed Founders Pass, all Pro variants
untracked (memberships are subscription-based — no quantity, per owner),
`swinglab:tempo` tag added to Tempo Rope, Pro vendor set to CaddieInsight,
Contact page filled + `contact` template assigned, About and FAQ pages
updated to the new pricing.

## 1. Create the three missing legal policies

Settings → Policies. The storefront currently promises "30-day returns" and
"14-day Pro refunds" while /policies/refund-policy returns 404 — this is the
top trust/compliance gap. Paste the following.

### Refund policy

```html
<h2>Returns and refunds, stated plainly</h2>
<p>Short policies, honestly framed. If anything here leaves a question, reply to your order confirmation email or use the <a href="/pages/contact">contact page</a> and a person will sort it out.</p>
<h2>Training gear</h2>
<p>Unused gear can be returned within <strong>30 days of delivery, no questions asked</strong>. Unused means what it says — a training aid you decided against is returnable; one that has done three weeks of range work is not.</p>
<ul>
  <li>Start a return by replying to your order confirmation email or through the <a href="/pages/contact">contact page</a>. We will send return instructions within 1–2 business days.</li>
  <li>Refunds go back to the original payment method once the return is confirmed.</li>
  <li>If an item arrives damaged, defective, or different from what you ordered, tell us within 30 days and we will replace it or refund it in full — that one is on us, not you.</li>
</ul>
<h2>CaddieInsight Pro and digital services</h2>
<p>Pro is refundable within <strong>14 days of purchase if unused</strong>. A refunded order removes the access it granted.</p>
<ul>
  <li>Bought as a subscription, monthly and yearly Pro renew automatically — cancel anytime from your store account, and Pro keeps running to the end of the period you have paid for. Cancelling stops the next charge; it does not refund the current one.</li>
  <li>Bought as a one-time pass, a term is fixed-length — 31 days for the 1-month option, 365 for the 12-month — and simply expires. Nothing renews on its own.</li>
  <li>Lifetime is a single payment that never renews and never expires.</li>
</ul>
<p>The app's free plan — one full analysis per calendar month — never expires and requires no purchase at all.</p>
```

### Shipping policy

```html
<h2>Shipping, stated plainly</h2>
<p>Training aids ship directly from partner warehouses via DSers rather than from a CaddieInsight facility. That keeps prices down; the trade-off is transit time, and we would rather state it than surprise you.</p>
<ul>
  <li><strong>Processing:</strong> 1–2 business days.</li>
  <li><strong>United States:</strong> Standard $8 or Express $15 at checkout; typically 6–12 business days.</li>
  <li><strong>Asia:</strong> Standard $9 or Express $18 at checkout; typically 5–14 business days (Japan, Korea, China, Hong Kong, Taiwan, Singapore, Thailand, Malaysia, Philippines, Vietnam, Indonesia, India, and nearby).</li>
  <li><strong>Tracking:</strong> emailed at dispatch, so you can follow the package the whole way.</li>
  <li><strong>Shipping regions:</strong> physical gear ships to the United States and the Asia zone configured in Shipping settings.</li>
</ul>
<h2>Digital delivery</h2>
<p>CaddieInsight Pro is delivered digitally — nothing ships. When your paid order is confirmed, Pro access is added to the CaddieInsight account matching your checkout email, usually within minutes. Bought before creating an account? The purchase waits and is claimed automatically the first time that email signs up or logs in at <a href="https://app.caddieinsight.com">app.caddieinsight.com</a>.</p>
<p>If a package is late, lost, or arrives damaged, reply to your order confirmation email or use the <a href="/pages/contact">contact page</a> and we will chase it down or make it right.</p>
```

See also the live runbook [dsers-usa-asia-shipping.md](dsers-usa-asia-shipping.md) for zone rates and DSers checklist.
### Terms of service

Review the bracketed governing-law line before publishing.

```html
<h2>Terms of service</h2>
<p>These terms cover the CaddieInsight store (caddieinsight.com) and the CaddieInsight app (app.caddieinsight.com). Using either means you accept them. They are written to be read, not skimmed past.</p>
<h2>What CaddieInsight is</h2>
<p>CaddieInsight produces automated golf-swing analysis from phone video: timing estimates and supported 2D movement measurements, one prioritized coaching focus, and a practice plan with measurable re-film targets. It produces estimates from a single camera. It is not a substitute for instruction from a teaching professional, and it does not measure club path, face angle, launch, spin, carry, strike, or ball flight. We state what it can and cannot see, and we stand behind exactly that.</p>
<h2>Your account</h2>
<p>Accounts are identified by email. Keep access to your email secure; sign-in codes sent to it act as your key. You must be old enough to form a contract where you live. One account per person. We may suspend accounts used to abuse the service, other users, or these terms.</p>
<h2>Your videos and data</h2>
<p>Your swing videos and reports are yours. You grant us the limited license needed to process, store, and display them back to you — that is the whole purpose of the license. We do not sell your footage. You can delete your swing history from your account at any time, and account deletion removes it permanently, as described in our <a href="/policies/privacy-policy">Privacy Policy</a>. Upload only footage you have the right to use.</p>
<h2>Purchases, subscriptions, and refunds</h2>
<p>Prices are in USD. Pro access activates on the CaddieInsight account matching your checkout email. Subscriptions renew automatically until cancelled; cancellation stops the next charge and access runs to the end of the paid period. One-time passes expire on their own. Refunds are governed by our <a href="/policies/refund-policy">Refund Policy</a> (30-day unused gear returns; 14-day unused-Pro refunds).</p>
<h2>Acceptable use</h2>
<p>Do not attempt to break, overload, scrape, or reverse-engineer the service; do not upload content that is unlawful or infringes others' rights; do not resell access. We may refuse service to protect the platform or other customers.</p>
<h2>Honest limitations of liability</h2>
<p>The service is provided as-is. To the maximum extent the law allows, CaddieInsight is not liable for indirect or consequential damages, and our total liability for any claim is capped at the amount you paid us in the twelve months before the claim. Nothing here limits liability that cannot lawfully be limited. Golf swings involve physical activity: warm up, use common sense, and stop if something hurts — the app prescribes drills, not medical advice.</p>
<h2>Changes and contact</h2>
<p>We may update these terms as the product evolves; material changes will be posted here with an updated date. These terms are governed by the laws of [your state], United States. Questions: use the <a href="/pages/contact">contact page</a> or reply to any CaddieInsight email.</p>
```

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

## 3. Founders Pass cap (manual, by owner decision)

Memberships carry no inventory quantity (owner decision: subscription-based,
no quantity). The "capped at the first 100 members" promise is therefore
enforced by hand: watch Founders Pass sales (SKU `SL-PRO-LIFE`) and retire
the variant once 100 have sold. The copy promises the cap — honor it.

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

Also note: the About page hardcodes Pro prices ($4.99/$39.99/$79.99) — update
that paragraph whenever pricing changes (see strategy doc pricing proposal).

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
