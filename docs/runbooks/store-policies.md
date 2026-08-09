# Store policies — ad-readiness drafts

Drafts for the policies `caddieinsight.com` is missing or that do not match how
the store actually fulfils. Paste into **Shopify admin → Settings → Policies**.

Kept in the repo because the storefront and the app make overlapping promises
— returns, subscriptions, data — and those should be reviewed together rather
than living only in an admin text box.

> **These are drafts, not legal advice.** They describe how the store appears
> to operate today (US-based, USD, dropshipped hardware, digital subscription,
> shipping to the US and 21 Asian markets). Have a lawyer review before
> spending on ads, especially the subscription auto-renew terms — those are
> the ones with real regulatory teeth in the US (FTC click-to-cancel) and in
> several Asian markets.

Fill in every `[BRACKET]`. An unfilled bracket is worse than a missing policy.

---

## Verified against the live store — 2026-08-09

Everything below was read from the running store rather than assumed, via the
public `/policies/*` pages, the public Storefront API, and the Admin API. Four
things in the earlier write-up turned out to be wrong or incomplete.

**Corrections**

1. **"Two shipping methods both named Standard" is not a bug.** It is one
   method definition (`832731349164`) with a rate-range condition: $8.00, and
   $0.00 once the order total reaches $70.00. The Admin API lists the tiers
   separately, which is what made it look like a duplicate. A customer sees
   one rate at a time. Nothing to fix — though nothing on the storefront
   advertises free shipping over $70 either, which is a conversion lever
   sitting unused.

2. **The contact-link problem is worse than a personal address.** The live
   refund policy's mailto reads
   `<a href="mailto:kylejames0513@icloud.com">inquiry@caddieinsight.com</a>` —
   the visible text and the target disagree, so a customer who clicks "email
   us" writes to a private inbox while believing they wrote to the business.
   A text-only audit cannot see this, which is why
   `tests/test_store_ad_readiness.py` now compares link text against link
   target.

3. **The privacy policy leaks the same address independently.** Shopify's
   stock template renders the shop contact email, which is still the personal
   iCloud address. Rewriting the policy will not fix it — the store's contact
   email in Settings has to change.

4. **The store ships to 21 Asian countries, not just the US.** Live zones:
   Domestic (US) Standard $8 / free over $70 / Express $15; Asia Standard $9 /
   Express $18. The shipping draft in `docs/runbooks/store-manual-actions.md`
   says "we currently ship physical gear within the United States" — pasting
   that would contradict what checkout actually sells.

**Confirmed**

- Pro really does auto-renew: two recurring selling plans, `MONTH/1` and
  `YEAR/1`. The Founders Pass is a single payment. Terms of Service is
  therefore a legal exposure, not housekeeping.
- `/policies/shipping-policy` and `/policies/terms-of-service` both 404.
- Swing Path Mat "Outdoor Use" is out of stock with policy `DENY` while the
  product is live. The product still reports `availableForSale: true` because
  the Indoor variant has stock — which is why nothing surfaced it.

**Still blocked on values only the operator has**

| Needed for | Value |
| --- | --- |
| Terms of Service | Legal entity name; the business address to publish (the address on file, 918 Carter Ridge Dr, Knoxville TN, is residential — publishing a home address is a decision, not a detail); governing state; minimum age |
| Shipping Policy | Measured transit times per zone. `docs/first-sale-launch.md` forbids promising delivery dates a supplier has not demonstrated, and no supplier SLA has been measured |
| Both | Effective date |

---

## Refund Policy — paste-ready, no brackets

This one needs no operator values, and it is the highest-risk page live today.
It removes the prepaid-return-label promise, fixes the misdirected mailto,
drops the apparel and perishables boilerplate that never applied to golf
training aids, drops the EU cooling-off clause the store has no EU zone for,
and states the auto-renewal that Pro actually performs.

One judgement call is embedded and worth vetoing if you disagree: it says the
customer covers return postage on a change-of-mind return, and that
CaddieInsight covers it when the item is damaged, defective or wrong. The
current policy promises a prepaid label unconditionally, which the margin on a
$11.99 item cannot carry; silence would be safer than that but worse than
being explicit.

Paste into **Settings → Policies → Refund policy**:

```html
<h2>Returns and refunds, stated plainly</h2>
<p>Short policies, honestly framed. If anything here leaves a question, reply to your order confirmation email or use the <a href="/pages/contact">contact page</a> and a person will sort it out.</p>

<h2>Training gear</h2>
<p>Unused gear can be returned within <strong>30 days of delivery, no questions asked</strong>. Unused means what it says — a training aid you decided against is returnable; one that has done three weeks of range work is not.</p>
<ul>
  <li>Start a return by replying to your order confirmation email or through the <a href="/pages/contact">contact page</a>. We will send return instructions within 1–2 business days.</li>
  <li>For a change-of-mind return, you cover the return postage. If an item arrives damaged, defective, or different from what you ordered, that one is on us — tell us within 30 days and we will replace it or refund it in full, return postage included.</li>
  <li>Refunds go back to the original payment method once the return is confirmed.</li>
  <li>Items sent back without requesting a return first cannot be processed, because we will have no way to match the parcel to your order.</li>
</ul>

<h2>CaddieInsight Pro</h2>
<p>Pro is refundable within <strong>14 days of purchase if unused</strong>. A refunded order removes the access it granted.</p>
<ul>
  <li><strong>Monthly and yearly Pro renew automatically.</strong> Cancel anytime from your account and Pro keeps running to the end of the period you have already paid for. Cancelling stops the next charge; it does not refund the current one.</li>
  <li>The Founders Pass is a single payment. It never renews and never expires.</li>
  <li>Pro is delivered digitally, so nothing ships and there is nothing to return — a refund simply removes the access.</li>
</ul>
<p>The app's free plan — one full analysis per calendar month — never expires and requires no purchase at all.</p>

<h2>Getting hold of us</h2>
<p>Email <a href="mailto:inquiry@caddieinsight.com">inquiry@caddieinsight.com</a> or use the <a href="/pages/contact">contact page</a>. We answer within 1–2 business days.</p>
```

After pasting, run `python scripts/refresh_store_readiness.py` and delete the
matching entries from `POLICY_TEXT_GAPS` and `MISLEADING_MAILTO` in
`tests/test_store_ad_readiness.py`. The tests fail until those waivers go, so
a fix cannot be applied and forgotten.

---

## 1. Shipping Policy — MISSING, and it blocks ads

Meta and Google both look for a shipping policy on a physical-goods store, and
it is the single most common cause of a rejected commerce ad account. It also
does the most to prevent chargebacks, because dropshipped delivery windows are
long and unstated windows read as fraud to a customer at day 18.

```
SHIPPING POLICY

Where we ship
We ship to the United States and to 21 markets across Asia, including Japan,
South Korea, Singapore, Hong Kong, Taiwan, the Philippines, Malaysia,
Thailand, Vietnam, Indonesia and India. If your country is not listed at
checkout, we cannot ship there yet.

Processing time
Orders are processed within [1-2] business days. Orders placed after
[TIME, TIMEZONE] or on a weekend or holiday begin processing the next
business day.

Delivery estimates
Training aids ship from our supplier partners. Estimated delivery after
processing:

  United States — Standard: [7-15] business days
  United States — Express:  [3-7] business days
  Asia — Standard:          [7-20] business days
  Asia — Express:           [5-12] business days

These are estimates, not guarantees. Customs clearance, carrier backlogs and
local holidays can extend them. We are not able to expedite an order once it
has shipped.

Tracking
You will receive a tracking number by email once your order ships. Tracking
can take [3-5] business days to show its first scan — this is normal and does
not mean the parcel is lost.

Split shipments
Orders with more than one item may arrive in more than one parcel, on
different days, at no extra cost to you.

Customs, duties and taxes
International orders may be subject to import duties, taxes and customs fees
charged by the destination country. These are not included in your total and
are the responsibility of the recipient. We cannot predict these charges or
refund an order refused at customs.

Incorrect addresses
Please check your shipping address carefully. We cannot reroute a parcel once
it has shipped. Orders returned to sender because of an incorrect or
incomplete address can be reshipped at your cost.

Lost or delayed parcels
If your order has not arrived [30] days after shipping, contact
inquiry@caddieinsight.com and we will open an investigation with the carrier.

CaddieInsight Pro is a digital membership. Nothing ships, and it is available
immediately after purchase.
```

## 2. Terms of Service — MISSING

You sell an **auto-renewing subscription**, which is the part that matters
legally. A cancellation policy exists but there are no terms governing the
service itself, the licence, or liability.

```
TERMS OF SERVICE

1. Who we are
These terms govern your use of caddieinsight.com, app.caddieinsight.com, and
the CaddieInsight applications (together, the "Services"), operated by
[LEGAL ENTITY NAME], [ADDRESS]. Contact: inquiry@caddieinsight.com.

2. Accepting these terms
By using the Services or placing an order you accept these terms. If you do
not accept them, do not use the Services.

3. Eligibility
You must be at least [16/18] and able to form a binding contract. The Services
are not directed at children.

4. What CaddieInsight is — and is not
CaddieInsight produces automated movement and timing estimates from a single
phone video, and generates coaching suggestions from those estimates. It is:

  - not instruction from a qualified golf professional;
  - not a medical, physiotherapy, fitness or injury-prevention service;
  - not a launch monitor, and it does not measure ball flight, club speed,
    spin or carry distance.

Estimates from a single 2D camera have real limits, and camera angle, framing
and lighting all affect them. Use your judgement, and stop any drill that
causes pain. You are responsible for your own safety when practising.

5. Your account
You are responsible for activity under your account and for keeping your
credentials secure. Tell us promptly at inquiry@caddieinsight.com if you
believe your account has been accessed without your permission.

6. Your content
You keep ownership of the videos you upload. You grant us a limited licence to
store and process them solely to provide the Services to you. We do not sell
your videos, and we do not use them for advertising. See the Privacy Policy.

7. CaddieInsight Pro subscriptions
Pro is offered monthly and annually and RENEWS AUTOMATICALLY at the then-current
price until you cancel. Founders Pass is a one-time purchase, not a subscription.

  - You may cancel at any time from the link in your order confirmation email
    or by emailing inquiry@caddieinsight.com.
  - Cancelling stops future renewals. Access continues to the end of the
    period you have already paid for.
  - Except where required by law, we do not refund a partial period.
  - We will give at least [30] days' notice by email before any price change,
    and you may cancel before it takes effect.

8. Free allowance and fair use
Free accounts include a limited number of analyses per calendar month. We may
apply rate limits, file-size limits and abuse controls to keep the Services
available.

9. Acceptable use
Do not upload content you do not have the right to upload, upload footage of
another person without their consent, attempt to break or overload the
Services, resell access, or scrape the Services.

10. Availability and changes
We may change, suspend or discontinue any part of the Services. We aim to give
notice of material changes affecting paid members.

11. Disclaimers and liability
The Services are provided "as is" and "as available", without warranties of
any kind to the fullest extent permitted by law. To the maximum extent
permitted by law, our total liability arising out of the Services is limited
to the amount you paid us in the [12] months before the claim. Nothing here
limits liability that cannot be limited by law.

12. Governing law
These terms are governed by the laws of [STATE], United States, without regard
to conflict-of-laws rules. Mandatory consumer-protection rights in your country
of residence still apply.

13. Changes to these terms
We may update these terms and will post the revised version with a new
effective date.

Effective date: [DATE]
```

## 3. Refund Policy — REWRITE, the current one is unworkable

The live policy promises *"we'll send you a return shipping label"* and 30-day
returns. With hardware shipping from Asian suppliers, a prepaid return label
frequently costs more than the item — the Connection Ball is $12.99. It also
says nothing about the subscription, and it links
`mailto:kylejames0513@icloud.com` while displaying `inquiry@caddieinsight.com`.

Keep the 30-day window — it is a real conversion driver and honours the EU
cooling-off clause already there. Change who pays return postage, and add the
digital-goods case.

```
RETURNS AND REFUNDS

30-day returns on training aids
You have 30 days from delivery to request a return. Items must be unused, in
original packaging, with proof of purchase.

Start a return by emailing inquiry@caddieinsight.com with your order number.
Please do not send anything back before we reply — returns received without a
prior request cannot be processed.

Who pays return shipping
  - Faulty, damaged or incorrect item: we pay. Send a photo with your request
    and we will replace it or refund you in full, usually without asking for
    the item back.
  - Changed your mind: return postage is yours, and the original shipping
    charge is not refunded.

Refunds are issued to the original payment method within 10 business days of
us receiving and inspecting the return. Your bank may take longer to post it.

Damaged on arrival
Inspect your order on delivery. Report damage within [7] days with photos and
we will make it right without a return.

CaddieInsight Pro
Pro is a digital membership delivered immediately, so it is not returnable in
the usual sense. Instead:
  - Cancel at any time; access runs to the end of the paid period.
  - Charged unexpectedly, or never used the membership in the period charged?
    Email inquiry@caddieinsight.com within 14 days and we will refund it.
  - Founders Pass is refundable within 14 days of purchase if unused.

European Union — 14-day cooling off
If your order ships into the EU you may cancel within 14 days of delivery
without giving a reason, subject to the condition requirements above.

Non-returnable
Gift cards, and items returned outside the 30-day window.

Questions: inquiry@caddieinsight.com
```

---

## Other fixes found in the same pass

**Email mismatch.** The live refund policy renders the words
`inquiry@caddieinsight.com` over a `mailto:kylejames0513@icloud.com` link.
Anyone clicking it mails a personal iCloud address. Fix the href.

**Duplicate shipping rate.** The US domestic zone has two active methods both
named "Standard" plus one "Express". Two identical names at checkout look
broken. Rename or delete one.

**Dead variant.** *Swing Path Mat → Outdoor Use* is 0 inventory with
`DENY`, on an ACTIVE product. Paid traffic landing there hits a sold-out
button. Restock it, or hide the variant until it is back.

**Contact page.** Contact Information lists a trade name and email but no
business address. Meta ad review often looks for one on a commerce site.

## Sequence before spending on ads

1. Publish Shipping Policy and Terms of Service, replace Refund Policy.
2. Fix the mailto, the duplicate rate, and the dead variant.
3. Link Shipping and Terms in the footer — a policy that exists but is not
   reachable does not count.
4. Place one real test order to a US address and one to an Asian address;
   confirm the rates, the confirmation email, and the tracking link.
5. Buy Pro monthly on a test account and cancel it, to confirm the cancel path
   the Terms promise actually works.
