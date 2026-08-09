# Pro launch — owner action checklist

**Written 2026-08-09** against the audit in
`docs/quality/2026-08-09-launch-readiness-audit.md` and the tier design in
`docs/superpowers/specs/2026-08-09-two-tier-membership-and-free-proof-cycle-design.md`.

Everything here needs Kyle's hands — a Shopify admin click, a Railway variable,
a real card. Code-side work is tracked separately and does not appear below.

**Do not sell Pro until §1 is fully checked and §2 passes.** Today a customer
who pays is charged and receives nothing.

---

## 1. Unblock the money path

### 1.1 Deploy the app config and re-authorize — **the blocker**

`shopify.app.toml` has been fixed in the repo: `read_orders` added to scopes,
and `orders/paid` · `orders/cancelled` · `refunds/create` ·
`customers/create|update|delete` subscribed at
`https://app.caddieinsight.com/webhooks/shopify`.

```bash
cd /path/to/SwingLab
shopify app deploy
```

Then — **this is the step people skip** — open Shopify admin → Settings → Apps
and sales channels → **CaddieInsight Customer Bridge** and re-authorize it. A
scope change requires fresh merchant consent; `shopify app deploy` alone does
not grant it, and the webhook subscriptions will not exist until it is granted.

**Verify as the Bridge app, not through any other connector.** Querying
`webhookSubscriptions` through a different app returns `[]` for every app but
itself, so an unrelated tool reporting "no webhooks" proves nothing. Confirm
against client_id `003f5e06c49ffe797a116b22d3f6dafc`.

- [ ] `shopify app deploy` succeeded
- [ ] App re-authorized in admin
- [ ] `webhookSubscriptions` lists all six topics at the correct URI

### 1.2 Check `SHOPIFY_STORE_DOMAIN` on Railway

It must be **`e0hbgh-ip.myshopify.com`**, not `caddieinsight.com`.

`shopify_billing.py` compares the `X-Shopify-Shop-Domain` header against this
value, and Shopify always sends the myshopify host. The custom domain is a
valid-looking value that silently 400s every order — the worst kind of wrong,
because it looks right in the dashboard.

While you are there, confirm `SHOPIFY_WEBHOOK_SECRET` has no stray whitespace
(the code strips it defensively, but a wrong secret and a whitespace-padded
secret fail identically).

- [ ] `SHOPIFY_STORE_DOMAIN` is the myshopify host
- [ ] `SHOPIFY_WEBHOOK_SECRET` matches the value Shopify shows

### 1.3 Set `SWINGLAB_ADMIN_TOKEN`

It is currently unset, and `require_admin` returns **404** when unset — so
`/admin/kpis`, `/admin/shopify-sync` and the retry endpoint are all dark. On
launch day you would have no KPI surface, no sync health, and no way to see
whether an entitlement landed. `deploy/README.md` tells you to watch
`/admin/shopify-sync` during rollout; right now you cannot.

Set a long random string in Railway, then:

```bash
curl -H "Authorization: Bearer <token>" https://app.caddieinsight.com/admin/kpis
```

- [ ] Returns 200, not 404

### 1.4 Prove email actually delivers

`passwordless_login: true` means a code is emailed on every signup and login,
and the app returns **HTTP 503** when mail is unavailable. Production has
`SWINGLAB_SMTP_URL` and `SWINGLAB_MAIL_FROM` but **no `RESEND_API_KEY`** — one
transport, no fallback. `docs/environment.md` notes Resend's HTTPS API is
preferred precisely because hosts like Railway commonly block outbound SMTP.

A buyer who pays and never receives a claim code has paid for nothing.

- [ ] Requested a real login code from production and it arrived
- [ ] It landed in the inbox, not spam (check SPF / DKIM / DMARC on the sending domain)
- [ ] `RESEND_API_KEY` added as a second transport (the mailer prefers it and falls back to SMTP automatically)

### 1.5 Back up the entitlement database

`users.pro_until`, `pro_grants`, `shopify_orders` and the lifecycle-email ledger
all live in **one SQLite file** at `/data/sessions/swinglab.db`. There is no
`CADDIE_BACKUP_*` or `LITESTREAM_*` variable set, and Railway's own Backups page
reads "No Backups." Lose that volume and every paying customer silently drops to
Free, with no reconstruction path but replaying Shopify order history by hand.

- [ ] `CADDIE_BACKUP_*` variables set against an S3/R2 bucket
- [ ] `backup` extra installed in the Docker image
- [ ] `caddieinsight-backup` running on a Railway cron
- [ ] **One snapshot restored into a scratch directory to prove it works.** An untested backup is not a backup.

### 1.6 Set a Railway healthcheck path

`config.deploy` has no `healthcheckPath`, and Railway ignores the Dockerfile's
`HEALTHCHECK` entirely. With auto-deploy from `main`, a single replica and no
staging environment, a bad merge cuts live traffic to a crash-looping container
with nothing to stop it.

- [ ] Health check path `/healthz`, timeout ~120s
- [ ] Restart policy ON_FAILURE with a retry cap

### 1.7 Enforce the Founders Pass cap

The storefront promises "first 100 members" three times and frames it as a
solvency ethic. Variant `46839745282220` has `inventoryQuantity: 100` and
`inventoryPolicy: DENY`, but `inventoryItem.tracked: false` makes **both inert**.
Nothing stops sale 101, or 5000.

Products → CaddieInsight Pro → Founders Pass → Inventory → tick **Track quantity**.

> ⚠️ **Do NOT enable tracking on `SL-PRO-1MO` (qty 0) or `SL-PRO-12MO` (qty −1).**
> Both are `CONTINUE` and would become **unbuyable the instant you tick the box.**
> This is the single most dangerous adjacent click in the whole launch.

The theme already renders the disabled option, the "Sold out" label and
schema.org `OutOfStock` off `variant.available`, so no theme deploy is needed.

- [ ] Track quantity ticked on the Founders Pass variant **only**
- [ ] Both subscription variants still add to cart

### 1.8 Publish the policies

`/policies/terms-of-service` returns 404 and no `TERMS_OF_SERVICE` record
exists — while you sell two auto-renewing plans and a pass whose copy reads
"One payment, Pro for good." The live privacy policy cites a Terms of Service
that 404s.

Final paste-ready text is in `docs/runbooks/store-policies.md`.

- [ ] Terms of Service published (includes the Founders Pass wind-down clause, the swing-video licence, and auto-renewal terms)
- [ ] Refund policy replaced — the live one is Shopify's stock **physical-goods** template promising unconditional prepaid return labels, which contradicts the app's 14-day digital refund and makes an expensive promise on $12.99 dropshipped items
- [ ] **Shop contact email changed in Shopify Settings.** The live refund policy links `mailto:kylejames0513@icloud.com` behind the visible text `inquiry@caddieinsight.com` — target and text disagree. The privacy policy leaks the same personal address independently because Shopify renders the shop contact email from Settings, so rewriting policy text does **not** fix it.

---

## 2. The test that decides whether Pro is released

Nothing above is proven until this passes. It is also the only thing that
settles the suspected parked-grant defect.

**Logged out, incognito, real card, cheapest SKU (`SL-PRO-1MO`, $9.99) on
caddieinsight.com.**

Confirm in order:

- [ ] **(a)** The order carries a `transactions` node with kind `SALE`, status `SUCCESS`. Every order in the store's history so far is a $0.00 draft-order grant with `transactions: []` — no card has ever been charged, so this is genuinely the first time checkout runs.
- [ ] **(b)** Railway logs show `POST /webhooks/shopify` returning **200** and the line `Shopify order webhook reconciled.`
- [ ] **(c)** **Pro appears on the account without logging out and back in.** If it does not, you have hit the parked-grant defect — the days are sitting in `pro_grants` and the opportunistic claim hook must ship before launch.
- [ ] **(d)** Tax was calculated. Pro variants are `taxable: true` and this is the first time digital-goods tax will ever be computed.

Then **refund it** and confirm `refunds/create` fires and access is revoked.

---

## 3. Two-tier rollout (after §2 passes)

Per the approved design. Create in Shopify:

| SKU | Price | Selling plan |
|---|---|---|
| `SL-COACH-1MO` | $19.99 | MONTH/1 |
| `SL-COACH-12MO` | $139.99 | YEAR/1 |

And change `SL-PRO-LIFE` (Founders Pass) from **$149 → $249**, now sold as
Coach-for-life. At $149 against a $19.99/mo tier it is 7.5 months of revenue
against perpetual compute; $249 is 12.5 months. Nothing has sold, so no
commitment is broken — but this is the one number you displayed publicly, so
it is the most worth vetoing.

- [ ] Coach variants created with SKUs matching `config.yaml billing.shopify_skus`
- [ ] Selling plans attached to both Coach variants (the existing Pro plans are `SellingPlan/3547398316` MONTH/1 and `SellingPlan/3547431084` YEAR/1 — mirror that shape)
- [ ] New variant IDs recorded in `config.yaml billing.shopify_variant_ids`
- [ ] Founders Pass repriced

---

## 4. Ship the storefront

The Shopify theme **deploys manually** — merging to `main` changes nothing on
the store. Follow `docs/runbooks/rebrand-cutover.md`: upload to a duplicate
unpublished theme, preview, then publish.

Waiting on this deploy: the site-wide Liquid error fix (currently every page
renders `<link rel="icon" href="Liquid error ...">`, so the browser tab shows a
default globe on 100% of pages and shared links have no preview image), the
WebP plan cards, and the plans-band sizing fix.

- [ ] Uploaded to a duplicate unpublished theme
- [ ] Previewed — favicon appears, sharing a link produces an image card
- [ ] Published

### Also in Shopify admin, unrelated to the theme

- [ ] Re-upload `pro-plans.png` — gallery image 4 on the money page advertises **"3 analyses every month"** on the free plan, contradicting every text source on the site. The corrected art already exists in `store-assets/out/`; delete `MediaImage/34119666466988`, re-upload, re-set alt text, drag back to position 4.
- [ ] Delete the four retired-brand entries in Shopify **Files** (`swinglab-logo.png`, `swinglab-logo-inverse.png`, `swinglab-favicon.png`, `og-swinglab.png`). A Files entry beats a theme asset of the same name, so the header logo picker is one wrong click from silently restoring the v3 mark — and the v3 lockup is 1400×214 against v4's 1400×279, so a mis-pick also breaks header layout.
- [ ] Confirm the `RANGE15` code in the sitewide announcement bar is actually active. A dead code in the top-of-page banner is visible on every page.

---

## 5. Not blocking Pro, but do not sell gear until fixed

- Sales tax is **off** on all 17 `CI-*` variants (`taxable: false` on tangible goods with TN nexus). The archived `SL-*` gear was correctly `true` — this is a 2026-08-03 import regression. `taxShipping` is also false.
- The International market is enabled for Canada / UK / EU / Australia with **no shipping zone covering any of them** — only "Domestic" (US) and "Asia" (21 countries) exist.
- **Supplier cost is published in `compareAtPrice`** on all 17 variants, byte-identical to `inventoryItem.unitCost` and publicly visible at `/products/*.js`.
- `CI-MAT-OUT` is a dead variant on a live product whose description says "Choose Indoor or Outdoor Use."
- `/shop` is in the primary nav and ships zero products: `first_sale_catalog_only: true` plus a stale allowlist naming three **archived** seed products. The same stale allowlist is silently zeroing every in-report gear recommendation, killing the attach-rate revenue path with no error surfaced.
