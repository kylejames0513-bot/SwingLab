# Store policies — final text for Shopify admin

Paste-ready policy text for **Shopify admin → Settings → Policies**, plus the
things that pasting alone will not fix.

> ## These are drafts for a lawyer to review. They are not legal advice.
>
> They were written by reading what the store and the app actually do — the
> live `/policies/*` pages, the Storefront and Admin APIs, `config.yaml`, and
> the subscription selling plans attached to the membership variants — and then
> writing that down accurately. That makes them *truthful*. It does not make
> them *sufficient*. Have a lawyer read them before you spend money on ads, and
> especially before the store takes a recurring payment from someone outside
> the United States.
>
> Three clauses carry real regulatory weight and are the ones worth paying a
> professional to look at: **automatic renewal** (§7 of the Terms — US federal
> ROSCA plus a patchwork of state automatic-renewal laws, and separate regimes
> in several of the 21 Asian markets the store ships to), **the Founders Pass
> wind-down** (§8 — it converts a perpetual promise into a bounded one, and the
> pro-rata refund it defines is a real liability), and **the swing-video
> licence** (§6 — the store collects video of people's bodies, which several
> regimes treat as a special category).

Kept in the repo rather than only in an admin text box because the storefront,
the app and the policies make overlapping promises — returns, renewals, data —
and those drift apart the moment they live in three places. This file is the
single source. If a policy exists here and somewhere else, this one wins.

---

## What changed on 2026-08-09

The Terms of Service went from a bracketed draft to final text. Specifically:

- **Every bracket is filled** except one, and that one is the owner block —
  legal entity name and the business mailing address to publish. It is the same
  two lines in three policies, so it is one find-and-replace, not three
  decisions. It is listed under "Before you paste" below.
- **§8 Founders Pass is new.** The old draft's §10 said "we may discontinue any
  part of the Services" while the product page says *"One payment, Pro for
  good"*. Those two sentences cannot both be true, and the contradiction was
  sitting live on a page taking $149. §8 defines what "for good" buys: a
  90-day wind-down notice, an export window, and a pro-rata refund on a 36-month
  schedule. §11 is now explicitly subject to it.
- **§6 Your swing video is new.** Customers upload video of their own bodies
  and there was no licence term anywhere — not in the Terms, which did not
  exist, and not in the privacy policy, which is still Shopify's stock template
  and has never heard of video. §6 grants a narrow, purpose-bound licence,
  states plainly that it is *not* a licence to use anyone's likeness in
  marketing, and publishes the retention behaviour the code actually
  implements.
- **§7 auto-renewal is rewritten** to the click-to-cancel standard: what
  renews, how often, at what price, how to stop it, and when stopping takes
  effect — with cancellation by email counted from the moment we receive it.
- **The refund policy is rewritten for a mixed catalogue.** The live one is
  still Shopify's physical-goods template — "unworn, with tags, original
  packaging", and a prepaid return label promised unconditionally. The store
  sells no apparel, and a prepaid label on a $12.99 item shipping back to an
  overseas supplier costs more than the item.
- **Contact identity is fixed in every block.** Every visible email address
  matches the address its link actually opens, and every policy carries the
  same business address.

### Two decisions made here, both worth vetoing

**Minimum age: 16 to use the app, 18 to buy.** The alternative was 13, which is
the floor US COPPA sets for collecting a child's data. Three things argue past
it. The app ingests video of a person's whole body and derives pose landmarks
from it, which is the kind of data several regimes treat as special-category
rather than ordinary. The store ships to markets whose guardian-consent
thresholds sit above 13 — South Korea and China both draw the line at 14 — and
we have built no age gate and no parental-consent flow, so any age below the
strictest threshold we sell into is a promise we cannot operate. And 16 still
admits the junior golfer, who is a real part of this market; a 14-year-old can
use a parent's account, which is the honest arrangement anyway since the parent
is the one with the card. Purchases require 18 because a minor cannot form a
binding payment contract in Tennessee.

**No arbitration clause, deliberately.** A mandatory-arbitration and
class-waiver clause is the default in US consumer terms and it is *not*
drafted here, because it is a decision with a real downside (mass arbitration
is expensive, and the clause is what makes it possible) and it is not a
decision an engineer should make on an owner's behalf. §14 instead requires
informal resolution first and names a court. If counsel wants arbitration,
that is the clause to add — but add it knowingly.

---

## Before you paste

Two values are still missing and both are owner decisions, not lookups.

**The owner block.** Replace this token everywhere it appears — it is
byte-identical in the Terms, the Refund policy and the Contact information
block, so one find-and-replace does all three:

```
[TODO — OWNER: legal entity name, then the business mailing address to publish]
```

The billing address on file, 918 Carter Ridge Dr, Knoxville TN, is
**residential**. Publishing a home address on four public policy pages that
paid traffic will land on is a decision, not a formatting detail. The usual
answers are a registered-agent address, a virtual business address, or a PO box
— note that Meta's commerce review generally wants a street address, and a PO
box alone sometimes fails it. Whatever you choose, use the same string in all
three places.

**The effective date.** The Terms below say *9 August 2026*. If you paste them
on a later day, change the date to the day you publish. An effective date that
predates publication is small, but it is the kind of small thing that gets read
back to you in a dispute.

---

## Verified against the live store — 2026-08-09

Everything below was read from the running store rather than assumed, via the
public `/policies/*` pages, the public Storefront API, and the Admin API.

**Findings**

1. **`/policies/terms-of-service` returns 404, and no record exists.** The
   Admin API lists exactly four policies: `CONTACT_INFORMATION`,
   `PRIVACY_POLICY`, `REFUND_POLICY`, `SUBSCRIPTION_POLICY`. There is no
   `TERMS_OF_SERVICE` record to edit — it has to be created. The live privacy
   policy cites "our Terms of Service" and links to that 404, so publishing the
   Terms also repairs the privacy policy without touching it.

2. **The contact-link problem is worse than a personal address.** The live
   refund policy's mailto reads
   `<a href="mailto:kylejames0513@icloud.com">inquiry@caddieinsight.com</a>` —
   the visible text and the target disagree, so a customer who clicks "email
   us" writes to a private inbox while believing they wrote to the business. A
   text-only audit cannot see this, which is why
   `tests/test_store_ad_readiness.py` now compares link text against link
   target.

3. **The privacy policy leaks the same address independently.** Shopify's stock
   template renders the shop contact email, which is still the personal iCloud
   address. Rewriting the policy will not fix it — the store's contact email in
   Settings has to change. This is the one item on this page that no amount of
   pasting resolves.

4. **The store ships to 21 Asian countries, not just the US.** Live zones:
   Domestic (US) Standard $8 / free over $70 / Express $15; Asia Standard $9 /
   Express $18. Any policy sentence beginning "we ship within the United
   States" is false at checkout.

5. **"Two shipping methods both named Standard" is not a bug.** It is one
   method definition (`832731349164`) with a rate-range condition: $8.00, and
   $0.00 once the order total reaches $70.00. The Admin API lists the tiers
   separately, which is what made it look like a duplicate. A customer sees one
   rate at a time. Nothing to fix — though nothing on the storefront advertises
   free shipping over $70 either, which is a conversion lever sitting unused.

6. **Pro really does auto-renew.** Two recurring selling plans are attached:
   `SellingPlan/3547398316` (`MONTH/1`) on `SL-PRO-1MO` and
   `SellingPlan/3547431084` (`YEAR/1`) on `SL-PRO-12MO`. The Founders Pass is a
   single payment with no selling plan. Terms of Service is therefore a legal
   exposure, not housekeeping.

7. **Swing Path Mat "Outdoor Use" is out of stock with policy `DENY`** while
   the product is live. The product still reports `availableForSale: true`
   because the Indoor variant has stock — which is why nothing surfaced it.

**App behaviour these policies describe, read from `config.yaml`**

| Setting | Value | What the policy says because of it |
| --- | --- | --- |
| `web.retention_days` | `180` | Reports, clips and measurements are deleted 180 days after a session finishes |
| `web.delete_source_after_done` | `true` | The raw upload is deleted as soon as the analysis completes — or fails |
| `web.history_reset_enabled` | `true` | "Delete your swing history yourself, at any time" is a real button, not a promise |

**Still blocked on values only the operator has**

| Needed for | Value |
| --- | --- |
| Terms, Refund, Contact | Legal entity name and the business mailing address (the owner block above) |
| Shipping Policy | Measured transit times per zone. `docs/first-sale-launch.md` forbids promising delivery dates a supplier has not demonstrated, and no supplier SLA has been measured. This is the reason the shipping policy below still has brackets while nothing else does — filling them by guessing would be the exact failure the rule exists to prevent |

---

## 1. Terms of Service — FINAL

Create a new policy: **Settings → Policies → Terms of service**.

This is written as HTML rather than prose for two reasons. Shopify's policy
editor keeps headings and lists when it is given HTML and flattens them when it
is given a wall of text; and HTML puts the link text and the link target on the
same line, in the same place, where a mismatch between them is visible. That
mismatch is the defect that has been live on the refund policy for months.

```html
<h2>Terms of Service</h2>
<p><strong>Effective 9 August 2026.</strong> These terms are written to be read. If any part of them is unclear, email <a href="mailto:inquiry@caddieinsight.com">inquiry@caddieinsight.com</a> and ask — we would rather explain a clause than argue about it later.</p>

<h2>1. Who we are</h2>
<p>These terms govern your use of caddieinsight.com, app.caddieinsight.com, the CaddieInsight installable app, and everything we sell through them (together, the "Services"). The Services are operated by:</p>
<p>[TODO — OWNER: legal entity name, then the business mailing address to publish]<br>
Email: <a href="mailto:inquiry@caddieinsight.com">inquiry@caddieinsight.com</a></p>
<p>"We", "us" and "our" mean that business. "You" means you.</p>

<h2>2. Accepting these terms</h2>
<p>Using the Services or placing an order means you accept these terms. If you do not accept them, do not use the Services. If you are agreeing on behalf of a business, you are confirming you have authority to bind it.</p>

<h2>3. Who can use the Services</h2>
<ul>
  <li>You must be <strong>at least 16</strong> to create an account or upload video. The Services are not directed at children, and we do not knowingly collect data from anyone under 16. If you believe a younger child has an account, email us and we will delete it.</li>
  <li>You must be <strong>at least 18</strong> to buy anything, because a purchase is a binding contract. A parent or guardian can buy on a younger golfer's behalf and hold the account.</li>
  <li>You must be able to form a binding contract where you live, and not be barred from using the Services under applicable law.</li>
</ul>

<h2>4. What CaddieInsight is — and is not</h2>
<p>CaddieInsight produces automated movement and timing estimates from a single phone video, and generates coaching suggestions from those estimates. It is:</p>
<ul>
  <li>not instruction from a qualified golf professional;</li>
  <li>not a medical, physiotherapy, fitness or injury-prevention service, and not a diagnosis of anything;</li>
  <li>not a launch monitor. It does not measure club path, face angle, attack angle, launch, spin, carry, strike location, clubhead speed, ball speed or ball flight, and it will tell you so rather than guess.</li>
</ul>
<p>Estimates from a single 2D camera have real limits. Camera angle, framing, lighting and frame rate all affect them, and when the footage will not support a measurement the report says so instead of returning a worse number quietly.</p>
<p><strong>Golf is a physical activity and the app prescribes drills.</strong> You take part at your own risk. Warm up, use your judgement, and stop anything that causes pain. If you have an injury or a medical condition, talk to a professional before following a drill.</p>

<h2>5. Your account</h2>
<p>You are responsible for what happens under your account and for keeping access to your email secure — sign-in codes sent to it act as your key. One account per person. Tell us promptly at <a href="mailto:inquiry@caddieinsight.com">inquiry@caddieinsight.com</a> if you think your account has been used without your permission. We may suspend an account being used to abuse the Services, other customers, or these terms.</p>

<h2>6. Your swing video</h2>

<h3>6.1 You own it</h3>
<p>The video you upload is yours. Nothing here transfers ownership of it, of your swing, or of your likeness.</p>

<h3>6.2 The licence you give us, and how narrow it is</h3>
<p>To analyse a video we have to hold it, convert it, read it frame by frame, cut clips out of it and draw on top of it. So you grant us a non-exclusive, worldwide, royalty-free licence to store, transcode, analyse, extract frames from, annotate and display footage you upload — <strong>for one purpose only: producing your analysis and showing it back to you, and helping you with your account when you ask us to.</strong></p>
<p>The licence is limited to that purpose. It cannot be sub-licensed except to the hosting provider that runs our servers on our behalf, under contract. It lasts only as long as we hold the footage under §6.4, and it ends when the footage is deleted.</p>

<h3>6.3 What the licence is not</h3>
<ul>
  <li><strong>It is not permission to use you in marketing.</strong> We will not use your video, your image, your likeness, your name or your swing in any advertisement, campaign, social post, case study or promotional material. If we ever want to, we will ask you first, in writing, for that specific use — and no is a complete answer that changes nothing about your membership.</li>
  <li><strong>We do not sell your footage</strong> or licence it to anyone, and we do not use it for advertising or share it with advertisers.</li>
  <li><strong>We do not use your uploads to train machine-learning models.</strong> The pose model the analysis runs on is pre-trained and fixed; it reads your video and learns nothing from it. The coaching rules are deterministic and published — they are not generated by a model and they do not adapt to other people's swings.</li>
  <li>Your reports are private to your account. We look at a specific session only when you ask us to for support, or where we must to investigate abuse or comply with the law.</li>
</ul>

<h3>6.4 How long we keep it</h3>
<ul>
  <li><strong>The original upload is deleted as soon as the analysis finishes</strong> — and also when one fails. What survives is the report itself: the clips, the frames and the measurements taken from the video, not the video you sent.</li>
  <li>Those results are kept for <strong>up to 180 days</strong> after the session completes, and are then deleted automatically. The practical trade-off, stated plainly: re-analysing an old session needs a fresh upload.</li>
  <li>You can <strong>delete your swing history yourself at any time</strong> from your account. That deletion is immediate and permanent, and we cannot undo it for you afterwards.</li>
  <li>To have the account itself removed, email us. We will do it and confirm when it is done. Records we are required to keep — order and tax records, for example — are kept for as long as the law requires and no longer.</li>
</ul>
<p>Our <a href="/policies/privacy-policy">Privacy Policy</a> covers everything else we hold.</p>

<h3>6.5 Filming other people</h3>
<p>Upload only footage you have the right to upload. Do not upload video of another person without their consent, and never of a child who is not in your care.</p>

<h2>7. Memberships, automatic renewal, and cancelling</h2>

<h3>7.1 What is on offer</h3>
<p>Membership plans, what each one includes, and what each one costs are listed on the <a href="/products/swinglab-pro">membership page</a> and at <a href="https://app.caddieinsight.com/pricing">app.caddieinsight.com/pricing</a>. Those pages are authoritative; prices here would go stale. Some plans are billed monthly or yearly and renew automatically. The Founders Pass is a single payment and never renews — §8 covers it. There is also a free plan, which requires no purchase and does not expire.</p>

<h3>7.2 Automatic renewal, stated before you pay</h3>
<p><strong>If you choose a monthly or yearly plan, it renews automatically and your card is charged again each period until you cancel.</strong> Before you pay, checkout shows you the plan, the price and the billing interval; completing that order is your agreement to the charge repeating on that interval.</p>
<ul>
  <li><strong>Monthly plans</strong> renew every month on the date you first subscribed. <strong>Yearly plans</strong> renew every twelve months on that date.</li>
  <li>Each renewal is charged at the price then in effect for your plan, and we email you a receipt for every charge.</li>
  <li>Before a <strong>yearly</strong> plan renews we email you at least <strong>7 days</strong> beforehand with the date and the amount, so a renewal on an annual cycle is never a surprise.</li>
</ul>

<h3>7.3 Cancelling — and it is genuinely one step</h3>
<p>You can cancel at any time, and cancelling is deliberately no harder than subscribing was.</p>
<ul>
  <li>Sign in to your account on caddieinsight.com and cancel the subscription there. There is no phone call, no retention offer to decline, and no form to fill in.</li>
  <li>If that does not work for you, email <a href="mailto:inquiry@caddieinsight.com">inquiry@caddieinsight.com</a> from the address on the order and say you want to cancel. We will do it by hand and confirm by email. <strong>A cancellation counts from the moment we receive your email, not from the moment we get round to processing it</strong> — if a renewal charges in between, we refund it.</li>
</ul>
<p><strong>What cancelling does:</strong> it stops the next charge. Your membership keeps running to the end of the period you have already paid for, and then stops. It is not a refund of the period you are in — the <a href="/policies/refund-policy">Refund Policy</a> covers when a refund is due, and it is more generous than this paragraph.</p>

<h3>7.4 Price changes</h3>
<p>We will give you at least <strong>30 days' notice by email</strong> before any price change affecting your plan, and you can cancel before it takes effect. A price change never applies to a period you have already paid for.</p>

<h3>7.5 Where you live may give you more</h3>
<p>Where the law where you live gives you stronger rights over recurring payments — longer cancellation windows, extra notice, additional authorisation for each charge, or a right to a refund we have not offered here — <strong>those rights apply and nothing in these terms reduces them.</strong></p>

<h2>8. The Founders Pass, and what "for good" means</h2>
<p>The Founders Pass is a single payment for membership that does not expire and never renews. It is limited to the first 100 sold. It is tied to one person and one account, and it cannot be transferred, shared or resold.</p>
<p>"Does not expire" is a real promise, so this section says exactly what it covers and exactly what happens if we cannot keep it. A perpetual promise with nothing behind it is worth less than a bounded one that is honoured.</p>

<h3>8.1 What it grants</h3>
<p>The Founders Pass grants, for as long as we operate the Services, the highest individual membership tier we offered at the time you bought it, and any tier that directly replaces it. If we later introduce a tier <em>above</em> that one, it is not automatically included — but <strong>we will not move a feature your pass already includes into a higher tier.</strong> What you bought stays bought.</p>
<p>It is a membership, not an unmetered resource. The fair-use, rate and file-size limits in §9 apply to it like any other plan.</p>

<h3>8.2 If we wind the Services down</h3>
<p>We will not simply switch the Services off. If we decide to discontinue them, or to discontinue the tier the Founders Pass grants without replacing it:</p>
<ul>
  <li><strong>Notice.</strong> We will email every Founders Pass holder at least <strong>90 days</strong> before the Services stop.</li>
  <li><strong>Your data.</strong> Through that whole notice period, and for at least <strong>30 days after</strong> the Services stop, we will make your reports, clips and measurements available for you to download. If the app itself is already gone, email us and we will send them to you.</li>
  <li><strong>A pro-rata refund.</strong> If the wind-down happens within <strong>36 months</strong> of your purchase, we will refund the unused part of what you paid: <em>purchase price × (36 − whole months elapsed since purchase) ÷ 36</em>. A pass wound down in its eighth month refunds 28/36 of what was paid — about 78%. After 36 months no refund is due: at that point the pass has delivered more than three years of the top membership tier for one payment, and the promise has been kept.</li>
</ul>

<h3>8.3 If the business changes hands</h3>
<p>If the Services are sold or transferred, these Founders Pass obligations transfer with them and bind whoever runs the Services next. If a buyer will not take them on, §8.2 applies instead — notice, export, and the pro-rata refund.</p>

<h2>9. The free plan, and fair use</h2>
<p>The free plan includes a limited number of analyses, described on the pricing page. We apply rate limits, file-size limits, and abuse controls so the Services stay available for everyone, and we may adjust those limits. We will not quietly reduce the fidelity of a report to push you towards a paid plan — a free report is a full report.</p>

<h2>10. Acceptable use</h2>
<p>Do not: upload content you do not have the right to upload; upload footage of another person without their consent; upload anything unlawful; attempt to break, overload, scrape or reverse-engineer the Services; use automated means to create accounts or submit uploads; resell or share access; or use the Services to build a competing product from our output. We may refuse or withdraw service to protect the platform or other customers.</p>

<h2>11. Availability and changes to the Services</h2>
<p>The Services are provided on an "as available" basis and we may change, suspend or discontinue parts of them — <strong>subject to §8, which governs the Founders Pass and takes precedence over this section.</strong> We aim to give paying members reasonable advance notice of any material change that reduces what their plan includes, and where a change materially reduces what you are paying for, you may cancel and we will refund the unused part of the period you have paid for.</p>

<h2>12. Training gear</h2>
<p>Physical products are sold subject to our <a href="/policies/refund-policy">Refund Policy</a> and <a href="/policies/shipping-policy">Shipping Policy</a>. They ship from supplier partners, including from outside the United States, and international orders may attract import duties and taxes that are the recipient's responsibility. Training aids are exercise equipment: read the instructions, and use them sensibly.</p>

<h2>13. Disclaimers and liability</h2>
<p>The Services are provided "as is" and "as available", without warranties of any kind to the fullest extent permitted by law. We do not warrant that the Services will be uninterrupted, error-free, or that any estimate they produce is accurate for your particular swing, camera or conditions.</p>
<p>To the maximum extent permitted by law, our total liability arising out of or relating to the Services is limited to the amount you paid us in the <strong>12 months</strong> before the claim, and we are not liable for indirect, incidental or consequential loss.</p>
<p><strong>Nothing in these terms limits or excludes liability that cannot lawfully be limited or excluded</strong> — including liability for death or personal injury caused by our negligence, for fraud, or under any consumer-protection right you have that cannot be waived.</p>

<h2>14. Disputes, and the law that applies</h2>
<p><strong>Talk to us first.</strong> If something has gone wrong, email <a href="mailto:inquiry@caddieinsight.com">inquiry@caddieinsight.com</a> and give us 30 days to put it right. Most things are fixed in a day, and nearly everything is cheaper to fix than to argue about.</p>
<p>These terms are governed by the laws of the <strong>State of Tennessee</strong>, United States, without regard to its conflict-of-laws rules, and the state and federal courts sitting in Knox County, Tennessee have exclusive jurisdiction — except that if you live somewhere whose law gives you the right to bring a claim locally, or applies its own consumer-protection law to this contract, that right is unaffected.</p>

<h2>15. Changes to these terms</h2>
<p>We may update these terms as the product changes. The current version always lives on this page with its effective date at the top. If a change materially affects a paying member, we will email about it before it takes effect rather than relying on you to re-read the page.</p>

<h2>16. Getting hold of us</h2>
<p>Email <a href="mailto:inquiry@caddieinsight.com">inquiry@caddieinsight.com</a> or use the <a href="/pages/contact">contact page</a>. We answer within 1–2 business days, and every message is read by a person.</p>
<p>[TODO — OWNER: legal entity name, then the business mailing address to publish]</p>
```

---

## 2. Refund Policy — FINAL, replaces what is live

Paste into **Settings → Policies → Refund policy**, replacing everything there.

What is live today is Shopify's stock physical-goods template. It talks about
items being "unworn, with tags, in original packaging" for a store that sells no
apparel; it promises a prepaid return label with no conditions attached; it
carries an EU cooling-off clause for a store with no EU shipping zone; and it
says nothing at all about the membership, which is the only thing on the site
that charges a card more than once.

Two judgement calls are embedded and both are worth vetoing if you disagree.

**The customer pays return postage on a change-of-mind return; we pay when the
item is damaged, defective or wrong.** The current policy promises a prepaid
label unconditionally, which the margin on a $12.99 item shipping back to an
overseas supplier cannot carry. Silence would be safer than the current promise
but worse than being explicit.

**A renewal that caught someone out is refunded, used or not.** That is more
generous than §7.3 of the Terms requires, and it is deliberate: a surprise
renewal that gets refused becomes a chargeback, and a chargeback costs the fee
*plus* the refund *plus* a mark against the merchant account. Refunding it is
the cheaper outcome as well as the decent one.

```html
<h2>Returns and refunds, stated plainly</h2>
<p>We sell two very different things — a digital membership and physical training gear — so this policy is in two halves. If anything here leaves a question, reply to your order confirmation email or use the <a href="/pages/contact">contact page</a> and a person will sort it out.</p>

<h2>Memberships</h2>
<p>Memberships are delivered digitally. Nothing ships and there is nothing to send back, so a refund simply removes the access it granted.</p>
<ul>
  <li><strong>14 days, if unused.</strong> Ask within 14 days of a charge and we will refund it in full, as long as the membership is unused. Unused means what it says: since that charge, you have not run an analysis the free plan would not have covered.</li>
  <li><strong>A renewal that caught you out is refunded whether you used it or not.</strong> If a monthly or yearly renewal charged when you thought you had cancelled, or you simply forgot it was coming, tell us within 14 days of the charge and we will refund that charge. You do not have to explain yourself.</li>
  <li><strong>Cancelling is not the same as a refund.</strong> Cancel any time from your account, or email us. Cancelling stops the next charge and your membership keeps running to the end of the period you have already paid for. If you also want the current period refunded, say so and the two rules above apply.</li>
  <li><strong>Founders Pass.</strong> One payment, refundable within 14 days of purchase if unused, on the same terms above. After 14 days it is not refundable in the ordinary way — but it is not unbounded either: §8 of our <a href="/policies/terms-of-service">Terms of Service</a> commits us to 90 days' notice, a window to download your data, and a pro-rata refund if we ever wind the service down inside the first 36 months.</li>
  <li>A refunded membership loses the access it granted, from the moment the refund is issued.</li>
</ul>
<p>The free plan never expires and requires no purchase at all, so there is nothing to refund on it.</p>

<h2>Training gear</h2>
<p>Training aids ship from supplier partners, and some of them ship from outside the United States. That keeps the prices low, and it is also why the returns rules below are specific rather than breezy — we would rather tell you what a return actually involves than promise something we cannot do at these prices.</p>
<ul>
  <li><strong>30 days to ask.</strong> You have 30 days from delivery to request a return. Gear must be unused and in its original packaging. Unused means what it says — a training aid you decided against is returnable; one that has done three weeks of range work is not.</li>
  <li><strong>Ask first.</strong> Start a return by replying to your order confirmation email or through the <a href="/pages/contact">contact page</a>. We will reply within 1–2 business days with the return address. Items sent back without asking first cannot be processed, because we will have no way to match the parcel to your order.</li>
  <li><strong>Changed your mind:</strong> you cover the postage back, and the original shipping charge is not refunded. <strong>Check the postage before you spend it</strong> — the return address may be outside the United States, and on a lower-priced item the postage can cost more than a refund is worth. Ask us first and we will tell you honestly whether it is worth sending.</li>
  <li><strong>Damaged, defective, or not what you ordered:</strong> that one is on us. Send a photo within 30 days of delivery and we will replace it or refund it in full, and we will cover the cost of getting it back to us if we need it back at all. Usually we will not — on lower-priced items we will simply refund you and tell you to keep it or bin it, because postage across an ocean costs more than the item does.</li>
  <li><strong>Never arrived?</strong> If tracking has not shown delivery 30 days after dispatch, tell us and we will open an investigation with the carrier and either replace the order or refund it.</li>
  <li><strong>Changed your mind before it ships?</strong> Tell us before dispatch and we will cancel the order and refund it in full.</li>
  <li><strong>Refused at customs, or an address that could not be delivered to:</strong> we will refund the item, less the original shipping and any carrier charges we are billed for the failed delivery. Please check your address carefully — a parcel cannot be rerouted once it has shipped.</li>
  <li><strong>Not returnable:</strong> gift cards, used gear, and anything asked about outside the 30-day window.</li>
</ul>

<h2>How and when refunds are paid</h2>
<p>Refunds go back to the original payment method. We issue them within 5 business days of approving the request — for gear needing a return, that is 5 business days from the return arriving and being checked. Your bank or card issuer then takes its own time, usually another 5–10 days, and that part is out of our hands.</p>

<h2>Getting hold of us</h2>
<p>Email <a href="mailto:inquiry@caddieinsight.com">inquiry@caddieinsight.com</a> or use the <a href="/pages/contact">contact page</a>. We answer within 1–2 business days.</p>
<p>[TODO — OWNER: legal entity name, then the business mailing address to publish]</p>
```

After pasting, run `python scripts/refresh_store_readiness.py` and delete the
matching entries from `POLICY_TEXT_GAPS` and `MISLEADING_MAILTO` in
`tests/test_store_ad_readiness.py`. Those ledgers ratchet both ways — the tests
fail while a fixed gap is still listed — so a fix cannot be applied and
forgotten, and a waiver cannot quietly outlive the problem it excused.

One drafting constraint worth knowing before anyone edits this text: the phrases
`return shipping label` and `free returns` are in `UNWORKABLE_PROMISES` in that
test file and will fail the build on sight. The wording above says "we will
cover the cost of getting it back to us" for exactly that reason — the promise
is conditional, and the words are chosen so an unconditional version cannot
creep back in unnoticed.

---

## 3. Contact information — FINAL

Paste into **Settings → Policies → Contact information**. It currently lists a
trade name and an email but no business address, and Meta's commerce review
often looks for one.

```html
<h2>Contact information</h2>
<p>CaddieInsight is run by a small team, and every message is read and answered by a person — within 1–2 business days, usually faster.</p>
<p><strong>Email:</strong> <a href="mailto:inquiry@caddieinsight.com">inquiry@caddieinsight.com</a><br>
<strong>Contact form:</strong> <a href="/pages/contact">caddieinsight.com/pages/contact</a></p>
<p>[TODO — OWNER: legal entity name, then the business mailing address to publish]</p>
<p>For anything about an order — tracking, a return, a damaged item — replying directly to your order confirmation email is the fastest route, because your order details arrive with it.</p>
```

---

## 4. Privacy policy — an addendum, not a rewrite

**Do not rewrite the privacy policy from here.** Two reasons. It is Shopify's
stock template and it renders the shop contact email from Settings, so the
personal address on it is fixed in Settings and nowhere else. And it cites "our
Terms of Service" and links to a page that currently 404s — publishing §1 above
repairs that link without touching this policy at all.

What it is genuinely missing is any mention of video, which is the most
sensitive thing the business holds. Insert this block into the existing privacy
policy rather than replacing it:

```html
<h2>Swing video and the data taken from it</h2>
<p>If you use the CaddieInsight app, you upload video of yourself. This is what happens to it.</p>
<ul>
  <li><strong>What we derive.</strong> The analysis reads body-position landmarks from the video frames and computes timing and movement measurements from them. It does not identify you from your face, and it is not used for identification of any kind.</li>
  <li><strong>Where it is processed.</strong> On servers we operate at our hosting provider. Video is not sent to a third-party analysis service.</li>
  <li><strong>The original upload is deleted as soon as the analysis finishes</strong> — and also when one fails. What remains is the report: the clips, frames and measurements taken from the video, not the video you sent.</li>
  <li><strong>Those results are kept for up to 180 days</strong> after the session completes, then deleted automatically.</li>
  <li><strong>You can delete your swing history yourself, at any time,</strong> from your account. That deletion is immediate and permanent.</li>
  <li><strong>We do not use your video in marketing, sell it, share it with advertisers, or train machine-learning models on it.</strong> The licence you give us is limited to producing your analysis, and it is set out in section 6 of our <a href="/policies/terms-of-service">Terms of Service</a>.</li>
</ul>
```

---

## 5. Shipping Policy — still blocked, and honestly so

Meta and Google both look for a shipping policy on a physical-goods store, and
its absence is a common cause of a rejected commerce ad account. It also
prevents chargebacks, because dropshipped delivery windows are long and an
unstated window reads as fraud to a customer on day 18.

**This is the only draft on this page that still has brackets, and that is
deliberate.** `docs/first-sale-launch.md` forbids publishing a delivery estimate
no supplier has demonstrated, and none has been measured. Filling these numbers
by guessing would produce exactly the kind of promise this whole document exists
to remove. Place two test orders — one US, one Asian — record what actually
happens, then fill them in from the measurements.

```html
<h2>Shipping, stated plainly</h2>
<p>Training aids ship directly from supplier partners rather than from a CaddieInsight warehouse. That is what keeps the prices where they are; the trade-off is transit time, and we would rather state it than surprise you.</p>

<h2>Where we ship</h2>
<p>The United States, and 21 markets across Asia including Japan, South Korea, Singapore, Hong Kong, Taiwan, the Philippines, Malaysia, Thailand, Vietnam, Indonesia and India. If your country is not offered at checkout, we cannot ship there yet.</p>

<h2>Rates</h2>
<ul>
  <li><strong>United States:</strong> Standard $8, free on orders over $70. Express $15.</li>
  <li><strong>Asia:</strong> Standard $9. Express $18.</li>
</ul>

<h2>Processing and delivery</h2>
<p>Orders are processed within [1–2] business days. Orders placed at a weekend or on a holiday begin processing the next business day. Estimated delivery after processing:</p>
<ul>
  <li>United States — Standard: [MEASURE] business days; Express: [MEASURE] business days</li>
  <li>Asia — Standard: [MEASURE] business days; Express: [MEASURE] business days</li>
</ul>
<p>These are estimates, not guarantees. Customs clearance, carrier backlogs and local holidays extend them. Once an order has shipped we cannot speed it up.</p>

<h2>Tracking</h2>
<p>A tracking number is emailed when your order ships. It can take [MEASURE] business days to show its first scan — that is normal and does not mean the parcel is lost.</p>

<h2>Split shipments</h2>
<p>An order with more than one item may arrive in more than one parcel, on different days, at no extra cost to you.</p>

<h2>Customs, duties and taxes</h2>
<p>International orders may attract import duties, taxes and customs fees charged by the destination country. These are not included in your total and are the recipient's responsibility. We cannot predict them, and we cannot refund an order refused at customs beyond what our <a href="/policies/refund-policy">Refund Policy</a> sets out.</p>

<h2>Addresses</h2>
<p>Please check your shipping address carefully. A parcel cannot be rerouted once it has shipped. An order returned to sender because of an incorrect or incomplete address can be reshipped at your cost.</p>

<h2>Late or lost parcels</h2>
<p>If tracking has not shown delivery 30 days after dispatch, email <a href="mailto:inquiry@caddieinsight.com">inquiry@caddieinsight.com</a> and we will open an investigation with the carrier and either replace the order or refund it.</p>

<h2>Digital delivery</h2>
<p>Memberships are delivered digitally — nothing ships. Access is added to the CaddieInsight account matching your checkout email, usually within minutes. Bought before creating an account? The purchase waits and is claimed automatically the first time that email signs up or logs in at <a href="https://app.caddieinsight.com">app.caddieinsight.com</a>.</p>
```

---

## For the lawyer — the specific questions worth their time

Rather than "please review", these are the four places where a professional
adds something this document cannot.

1. **Automatic renewal outside the US.** §7 is written to the durable core of
   US law — clear disclosure before payment details are taken, express consent
   to the recurring charge, cancellation at least as easy as signing up, and an
   advance reminder before an annual renewal. That core is what ROSCA and the
   state automatic-renewal laws have converged on, and it is stable regardless
   of where any one federal rulemaking lands. What is **not** covered is the 21
   Asian markets the store already ships to, several of which regulate recurring
   card payments directly rather than through consumer-contract law — India's
   e-mandate framework for recurring card debits and Japan's distance-selling
   rules are the two most likely to bite first. Ask specifically: does §7 need a
   market-specific variant, or should recurring plans be restricted to the US
   until it does?

2. **The Founders Pass pro-rata refund (§8.2).** It is a contingent liability of
   up to 100 × the pass price, largest on day one and decaying to zero over 36
   months. Confirm the 36-month schedule is defensible as the measure of a
   perpetual licence, and that the clause reads as a bounded commitment rather
   than a guarantee of solvency.

3. **The swing-video licence (§6).** Body-landmark data derived from video sits
   near the edge of what several regimes call special-category or biometric
   data. Ask whether the derived landmarks are in scope where the store sells,
   and whether the 16-plus age line plus a parent-held account is enough without
   an age gate.

4. **Arbitration.** Deliberately not drafted — see the note at the top. This is
   the clause to add if it is wanted, and it should be added knowingly.

---

## Everything pasting does not fix

**The shop contact email.** Settings → General → Store contact email is still
the personal iCloud address, and Shopify's stock privacy template renders it.
No policy edit touches it. Change it to `inquiry@caddieinsight.com` — this is
the single highest-value click on the list, because it fixes the privacy policy
without editing the privacy policy.

**The yearly renewal reminder.** §7.2 promises an email at least 7 days before
an annual renewal. Confirm the upcoming-subscription-billing notification is
enabled in Shopify, or the Terms promise something the store does not send.
A promise in published terms that no system performs is worse than no promise.

**Account deletion by email.** §6.4 says account removal happens on request.
The app ships self-serve history deletion, but not self-serve account deletion,
so that clause is an operational commitment until one is built.

**Data export for a wind-down.** §8.2 promises reports and media are
downloadable through the notice period and for 30 days after. There is no
bulk-export route in the app today. It is a promise that only becomes due if
the business winds down, so it does not block launch — but it should not be
forgotten either.

**One link in the Terms points at a page that does not exist yet.** §12 links
to `/policies/shipping-policy`, which 404s until the shipping policy is
published — and shipping is blocked on measured transit times. That is the same
defect as finding #1 above, in the opposite direction: the privacy policy has
been citing a 404 Terms page for months. Publishing the Terms first is still
right, because an auto-renewing charge with no terms is the larger exposure —
but this is a known dead link with a known closing date, not an oversight, and
it closes the day the shipping policy goes up. The reverse ordering matters
too: **paste the Terms before the Refund policy and the privacy addendum**,
because both link to it.

**Dead variant.** *Swing Path Mat → Outdoor Use* is 0 inventory with `DENY` on
an ACTIVE product. Paid traffic landing there hits a sold-out button. Restock
it or hide the variant.

**The Founders cap.** §8 publishes a 100-member limit. See
`docs/runbooks/store-manual-actions.md` §3 — the limit is not enforced today,
and a published cap that is not enforced is a false statement in the Terms the
moment sale 101 clears.

## Sequence before spending on ads

1. Fill the owner block and paste: Terms of Service (new), Refund policy
   (replace), Contact information (replace), privacy addendum (insert),
   Shipping once transit times are measured.
2. Change the shop contact email in Settings.
3. Enable the yearly renewal reminder, and enforce the Founders cap.
4. Fix the dead variant.
5. Link Shipping and Terms in the footer — a policy that exists but is not
   reachable does not count.
6. Run `python scripts/refresh_store_readiness.py`, then delete the closed
   entries from `MISSING_POLICIES`, `POLICY_TEXT_GAPS` and `MISLEADING_MAILTO`
   in `tests/test_store_ad_readiness.py` and watch the suite go green.
7. Place one real test order to a US address and one to an Asian address;
   confirm the rates, the confirmation email, and the tracking link. Record the
   transit times — those are the numbers the shipping policy is waiting on.
8. Buy a monthly plan on a test account and cancel it, to confirm the cancel
   path §7.3 promises actually works. Then buy it again and ask for a refund,
   to confirm the 14-day path works too.
