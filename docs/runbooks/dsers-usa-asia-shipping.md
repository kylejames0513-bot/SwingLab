# DSers + Shopify shipping — USA and Asia

Live audit and setup for `e0hbgh-ip.myshopify.com` / [caddieinsight.com](https://caddieinsight.com).

## How it is wired

| Layer | Role |
| --- | --- |
| **Shopify Markets** | Decides which countries can shop. United States market + International market (includes Asia). |
| **Shopify delivery profile** | Customer-facing shipping rates at checkout. |
| **DSers / `dsers-fulfillment-service` location** | Supplier inventory + order push to AliExpress. Inventory lives here — do not hand-edit quantities. |
| **Storefront copy** | `/pages/shipping-returns`, FAQ, product trust lines. |

DSers does **not** set the rates buyers see. Shopify zones do. DSers only chooses which AliExpress shipping method you pay the supplier.

## Live rates (verified at checkout)

| Zone | Methods | Typical transit (stated) |
| --- | --- | --- |
| **United States** | Standard **$8** · Express **$15** | 6–12 business days after 1–2 day processing |
| **Asia** | Standard **$9** · Express **$18** | 5–14 business days after 1–2 day processing |

Asia zone countries: JP, KR, CN, HK, TW, SG, TH, MY, PH, VN, ID, IN, KH, LA, MM, BN, MN, MO, NP, LK, BD.

Pro / digital memberships do not require shipping.

## What was wrong before

- `shipsToCountries` was **US only** even though an International market existed.
- Delivery profile had only a **Domestic** zone → Asia carts returned empty rates / checkout failure.
- Shipping & Returns page talked only about US delivery.

Fixed via Admin API: Asia zone + rates on the General delivery profile; Shipping & Returns page updated.

## DSers checklist (do in the DSers app)

1. **Open each gear product → Shipping info** and search **United States** and key Asia destinations (JP, KR, SG, CN, HK). Note method names, cost, and delivery window.
2. **Settings → Shipping settings** (Basic or Advanced):
   - Prefer methods with **tracking**.
   - Cap delivery window (e.g. ≤ 25–30 days).
   - Prefer lowest cost that still meets tracking + window.
3. **Pricing rules**: include supplier shipping in cost (`Use supplier shipping` or a fixed buffer) so product price still covers landed cost when Express is chosen on Shopify but Standard is used on AliExpress — or always fulfill with a method whose cost is below the Shopify rate you charged.
4. **Order test**: place one US and one Asia test order (or draft → real low-cost SKU), fulfill in DSers, confirm tracking syncs back to Shopify.
5. **Do not** edit inventory at `dsers-fulfillment-service` in Shopify admin.

## Margin rule of thumb

Charge the customer a **flat regional rate ≥ typical AliExpress shipping + buffer**:

- US Standard $8 should clear common ePacket / Cainiao costs on light aids.
- Asia Standard $9 / Express $18 should clear most light-aid supplier quotes into East / Southeast Asia; re-check mats and heavier kits in DSers Shipping info and raise rates if supplier quotes run higher.

If supplier shipping for a SKU exceeds the flat rate, either raise the zone rate, add a weight-based rate, or retire that SKU.

## Formal Settings → Policies shipping policy

API lacks `write_legal_policies` on the CLI connector. Paste this in **Shopify admin → Settings → Policies → Shipping policy** (also still 404 at `/policies/shipping-policy` until published):

```html
<h2>Shipping, stated plainly</h2>
<p>Training aids ship directly from partner warehouses via DSers rather than from a CaddieInsight facility. That keeps prices down; the trade-off is transit time, and we would rather state it than surprise you.</p>
<ul>
  <li><strong>Processing:</strong> 1–2 business days.</li>
  <li><strong>United States:</strong> Standard $8 or Express $15 at checkout; typically 6–12 business days.</li>
  <li><strong>Asia:</strong> Standard $9 or Express $18 at checkout; typically 5–14 business days. Covered destinations include Japan, Korea, China, Hong Kong, Taiwan, Singapore, Thailand, Malaysia, Philippines, Vietnam, Indonesia, India, and nearby markets in the Asia zone.</li>
  <li><strong>Tracking:</strong> emailed at dispatch.</li>
</ul>
<h2>Digital delivery</h2>
<p>CaddieInsight Pro is delivered digitally — nothing ships. When your paid order is confirmed, Pro access is added to the CaddieInsight account matching your checkout email, usually within minutes.</p>
```

## Theme deploy note

Theme source updates (FAQ, announcement, PDP/cart trust) live in `storefront-theme/`. Upload to an unpublished duplicate theme and preview before publishing — never upsert straight to the live theme. See `storefront-theme/README.md`.

## Optional next steps

- Add a **Rest of World** zone if you want Canada / EU / AU checkout for gear (International market already lists many of those countries, but they have no rates today).
- Install Judge.me; founder photo on About; replace AliExpress stock photos — still the strongest legitimacy gaps for gear.
