# CADDIEINSIGHT — LAUNCH READINESS SYNTHESIS
**2026-08-09 · repo SwingLab @ cf4432c · deployed a8dcc6f8 (SUCCESS, matches main)**

---

## 1. STATE OF THE UNION

The infrastructure and the writing are the two strongest things you have, and they are genuinely good. Railway is running current `main` (cf4432c), TLS is valid until 2026-10-25, a persistent volume is mounted at `/data` so `swinglab.db` and every Pro entitlement survive redeploys, auth gating is correct on every route, the admin endpoints are properly 404-cloaked with a constant-time compare, the Shopify webhook HMAC verification is textbook (raw bounded body, stripped secret, byte compare, shop-domain match, both trailing-slash routes registered), and CSRF is now defended at all 21 state-changing routes. The storefront copy is specific, honest, threshold-driven writing that reads like someone who actually understands golf, the theme is hand-built with a real token layer and better accessibility than most paid themes, and the hero photography is legitimately premium. Selling plans are correctly attached — `SellingPlan/3547398316` (MONTH/1) on SL-PRO-1MO and `SellingPlan/3547431084` (YEAR/1) on SL-PRO-12MO — so the auto-renewal copy on `/pricing` is truthful. That is a real product.

The money path is not connected. `shopify.app.toml:22-27` declares only the three mandatory GDPR compliance topics; `orders/paid` is not subscribed anywhere, and `shopify.app.toml:10` requests `read_customers,write_customers` — no `read_orders`, so the app could not create that subscription even if the topic were added. The store's entire order history is one draft-order-completed $0.00 grant (`#261001`, from draft `#D1`, created by "Shopify ChatGPT MCP App") with `transactions: []` and `abandonedCheckoutsCount: 0` — no card has ever been charged, no checkout has ever been rendered against a real cart, and Railway's entire retained log window (01:48Z–15:25Z) contains zero POST requests of any kind. There is no backup of the single SQLite file that holds every entitlement, no error monitoring, no Railway healthcheck path, and no operator tool to grant or inspect Pro when something goes wrong.

Pro is roughly three to four hours of focused work away from being safely sellable — the blockers are config, not code. The premium gap is a separate day: one Liquid bug is disfiguring every page of the storefront, the app header renders the brand as plain text where the store renders a logo, the sample report is serving a stale pre-rebrand stylesheet, and the money page shows four stick-figure illustrations instead of a single screenshot of the software.

---

## 2. MONEY PATH VERDICT

**No. A customer who pays you today is charged and receives nothing.**

Walking the chain:

| Link | State | Evidence |
|---|---|---|
| **Store checkout** | ✅ WORKS | `Product/8672414105772` ACTIVE, published, storefront not password-protected. All three variants live and priced (46811170177196 $9.99 / 46811170209964 $69.99 / 46839745282220 $149.00). Shop Pay + Apple Pay + Google Pay present. Anonymous purchase confirmed possible on `caddieinsight.com/products/swinglab-pro`. |
| **Payment capture** | ⚠️ UNPROVEN | `orders(query:"status:any")` returns exactly one node, `transactions: []`, total $0.00, created from a manually-completed draft order — it never touched cart, checkout, tax, shipping, or a gateway. Shopify Payments is inferred live from `supportedDigitalWallets`, never exercised. |
| **Webhook delivery** | ❌ **BROKEN — this is the link** | No `orders/paid` subscription declared in `shopify.app.toml:22-27`. App scopes lack `read_orders` (`shopify.app.toml:10`), so the subscription cannot be created until the scope is added and re-consented. Zero POSTs to `/webhooks/shopify` in the full Railway log window; the only hit is one manual GET returning 405 (route registered, POST-only, as designed). |
| **Entitlement grant** | ⚠️ UNPROVEN + suspected defect | Handler and SKU map are correct and well unit-tested (`config.py:239`: SL-PRO-1MO→31, SL-PRO-12MO→365, SL-PRO-LIFE→36500). But `users.py:7215-7235` refuses to grant to an email-matched account when the payload carries `customer.id` — days park in `pro_grants` instead. `claim_pending_grant` only runs at signup/login/code-signin/reset (`app.py:962-965, 1977, 2051, 2146, 2274, 2345`), never on an ordinary request and never from the `customers/create` handler. `tests/test_account_sync.py:1061-1086` asserts exactly this: a customer-bearing paid order leaves `is_pro False, pending_days 31`. Every `pro_order()` fixture in `tests/test_shopify_billing.py:101-106` omits the customer object, so the green suite never exercises the production payload shape. **This is unverified by adversarial pass — the live test purchase in Blocker 5 settles it.** |
| **Gated feature** | ⚠️ DEGRADED | Pro-only surfaces work, but a *free* user's report renders an empty black "Evidence theater" panel (slow motion is gated behind the coach-replay lock at `report_guided.html.j2:919-921` despite `slow_motion_media_key` being ungated at `report_presenter.py:922`), and the paywall renders `<summary>Coach replay · 0</summary>` plus six sections reading "This section is locked." with no price, no link, no CTA anywhere in the file. |

**Secondary break:** `app.caddieinsight.com/pricing` cannot sell to anonymous visitors at all. All three CTAs render as `<a href="/login">Log in to upgrade</a>` (`web_pricing.html.j2:254-255`) because every store-link branch is gated on `user`. The Shopify storefront is unaffected, so Pro is *sellable* — just not from the app's own pricing page, which is where paid traffic would land.

---

## 3. LAUNCH BLOCKERS

Ordered by dependency. Do not sell Pro until all nine are true.

**1. Subscribe `orders/paid` — and add the scope that makes it possible.**
*Why it blocks:* This is the only mechanism that grants Pro. Without it, 100% of purchases take money and grant nothing, silently — no request ever arrives, so nothing is logged.
*Fix:* `shopify.app.toml:10` → `scopes = "read_customers,write_customers,read_orders"`. Add a subscription block for `orders/paid`, `orders/cancelled`, `refunds/create` at `https://app.caddieinsight.com/webhooks/shopify` (no trailing slash — senders do not follow 3xx). Run `shopify app deploy`, then **re-authorize the "CaddieInsight Customer Bridge" app in Shopify Admin** — a scope change requires fresh merchant consent and the deploy alone does not grant it. Verify by querying `webhookSubscriptions` *as the Bridge app* (client_id `003f5e06c49ffe797a116b22d3f6dafc`), not as the Claude connector, which returns `[]` for any app but itself. Also confirm `SHOPIFY_STORE_DOMAIN` on Railway is `e0hbgh-ip.myshopify.com` and not `caddieinsight.com` — `shopify_billing.py:262-274` compares the `X-Shopify-Shop-Domain` header against it and Shopify always sends the myshopify host, so the custom domain is a valid-looking value that 400s every order.

**2. Verify email delivery end to end.**
*Why it blocks:* `passwordless_login: true` (`config.yaml:237`) and commerce being enabled force an emailed code on every signup and login; `app.py:2191-2206` returns HTTP 503 when mail is unavailable. Production has `SWINGLAB_SMTP_URL` and `SWINGLAB_MAIL_FROM` but no `RESEND_API_KEY` — one transport, no fallback, and `docs/environment.md:129` says Resend HTTPS is preferred precisely because hosts like Railway block SMTP. A buyer who pays and cannot receive a claim code has paid for nothing.
*Fix:* Send yourself a real login code from production right now and confirm it lands in the inbox, not spam. Check SPF/DKIM/DMARC on the sending domain. Add `RESEND_API_KEY` as a second transport regardless — `mailer.py:71-74` prefers it automatically and falls back to SMTP.

**3. Set `SWINGLAB_ADMIN_TOKEN`.**
*Why it blocks:* `require_admin` (`app.py:924-935`) returns 404 when the token is unset. It is unset. So `/admin/kpis`, `/admin/shopify-sync` and `/admin/shopify-sync/ref/{ref}/retry` are all dark — on launch day you have no KPI surface, no sync health, and no way to see whether an entitlement landed. `deploy/README.md:206` instructs you to watch `/admin/shopify-sync` during rollout; you currently cannot.
*Fix:* Set a long random string in Railway. Verify `curl -H "Authorization: Bearer <token>" https://app.caddieinsight.com/admin/kpis` returns 200, not 404.

**4. Back up the entitlement database.**
*Why it blocks:* `users.pro_until`, `pro_grants`, `shopify_orders` and the lifecycle-email ledger all live in one SQLite file at `/data/sessions/swinglab.db`. Railway's variable list (13 user keys) contains no `CADDIE_BACKUP_*` and no `LITESTREAM_*`; `swinglab/backups/cli.py:78` is a manual operator command inert unless `CADDIE_BACKUP_ENABLED=true`, and `docs/architecture/platform-cost-audit.md:40-44` records that Railway's own Backups page reads "No Backups." Lose that volume and every paying customer silently drops to Free with no reconstruction path but replaying Shopify order history by hand.
*Fix:* Set the `CADDIE_BACKUP_*` variables against an S3/R2 bucket, install the `backup` extra in the Docker image, run `caddieinsight-backup` from a Railway cron every few hours — **and restore one snapshot into a scratch directory to prove it works.** An untested backup is not a backup.

**5. Run one real end-to-end purchase, then refund it.**
*Why it blocks:* Nothing above is proven until this passes. It is also the only thing that settles the parked-grant question in section 2.
*Fix:* Logged-out incognito, real card, cheapest SKU (SL-PRO-1MO, $9.99) on `caddieinsight.com`. Confirm in order: (a) the order carries a `transactions` node with kind SALE / status SUCCESS; (b) Railway logs show `POST /webhooks/shopify` returning 200 and the line `Shopify order webhook reconciled.`; (c) **Pro appears on the account without logging out and back in** — if it does not, you have hit the `pro_grants` parking defect and need the `current_user` claim hook before launch; (d) tax was calculated (Pro variants are `taxable: true`, and this is the first time digital-goods tax will ever be computed). Then refund and confirm `refunds/create` fires and revokes.

**6. Publish a Terms of Service.**
*Why it blocks:* `/policies/terms-of-service` returns 404 and the Admin API confirms no `TERMS_OF_SERVICE` record exists — only CONTACT_INFORMATION, PRIVACY_POLICY, REFUND_POLICY, SUBSCRIPTION_POLICY. You are selling two auto-renewing plans and a $149 pass whose copy reads "One payment, Pro for good" (`templates/index.json:204`) — an unbounded perpetual obligation with nothing defining it. Users upload video of their own bodies with no licence or retention terms. The live privacy policy literally cites "our Terms of Service," which 404s.
*Fix:* A paste-ready draft already exists at `docs/runbooks/store-policies.md:196-277` — fill five bracketed values (entity, address, min age, governing law = TN, effective date). **Add a Founders Pass clause the draft is missing**: the draft's §10 "we may discontinue any part of the Services" directly contradicts "Pro for good." Define notice period, data export, and pro-rata refund window on wind-down. Publish via Settings → Policies. No theme deploy needed — `footer.liquid:110-115` iterates `shop.policies` and the link appears automatically. While you are there, replace the Refund policy: it is still Shopify's stock physical-goods template ("unworn, with tags, original packaging," unconditional prepaid return label) which contradicts the app's "refundable within 14 days," and its contact link is `<a href="mailto:kylejames0513@icloud.com">inquiry@caddieinsight.com</a>`.

**7. Enforce the Founders Pass cap.**
*Why it blocks:* The storefront promises "first 100 members" three times and frames it as a solvency ethic. Variant 46839745282220 has `inventoryQuantity: 100` and `inventoryPolicy: DENY` — but `inventoryItem.tracked: false`, which makes both inert. Nothing stops sale 101, 500, or 5000, each at $149 against perpetual compute.
*Fix:* One checkbox. Products → CaddieInsight Pro → Founders Pass → Inventory → tick "Track quantity." Quantity is already 100 at `Location/85733736620`, policy is already DENY. **Do not enable tracking on SL-PRO-1MO (qty 0) or SL-PRO-12MO (qty -1)** — both are CONTINUE and would become unbuyable. The theme already renders the disabled option, "Sold out" label, and schema.org `OutOfStock` off `variant.available` (`main-product.liquid:88, 92, 265, 269, 345`), so no theme deploy is required. Then correct `docs/runbooks/store-manual-actions.md:108-113`, which currently says the cap is enforced by hand.

**8. Make `/pricing` sell to anonymous visitors.**
*Why it blocks:* Any ad, email, or social post landing cold traffic on `app.caddieinsight.com/pricing` converts at zero. The `/login` link carries no `next`, so even a visitor who signs in loses the plan they clicked.
*Fix:* In `web_pricing.html.j2:252-256`, add an `{% elif pro_store_url %}` branch above the fallbacks emitting `href="{{ store_href or pro_store_url }}"` — this preserves the per-card `?variant=` deep links already computed at `app.py:3348-3354`. Keep the `/login` branch only for the Stripe-only case. No backend change needed: `claim_pending_pro` (`app.py:962-965`) already exists specifically to attach purchases made before an account existed.

**9. Set a Railway healthcheck path.**
*Why it blocks:* `config.deploy` has no `healthcheckPath`. Railway ignores the Dockerfile's `HEALTHCHECK` entirely. With auto-deploy from `main`, a single replica, and no staging environment, a bad merge cuts live traffic over to a crash-looping container with nothing to stop it — and `app.py:495-502` will hard-fail startup if a variable edit trips the Shopify sync config path.
*Fix:* Set health check path to `/healthz`, timeout ~120s, restart policy ON_FAILURE with a retry cap. Also fix `Dockerfile:35` — the shell-form `CMD` means PID 1 is `/bin/sh` and SIGTERM is never forwarded, so every redeploy SIGKILLs the container mid-analysis with the SQLite WAL uncheckpointed. Use `CMD ["sh","-c","exec swinglab serve --host 0.0.0.0 --port ${PORT:-8000} --sessions-dir /data/sessions"]`.

**Not Pro blockers, but do not sell gear until fixed:** sales tax is off on all 17 CI-* variants (`taxable: false` on tangible goods with TN nexus, while the archived SL-* gear was correctly `true` — a 2026-08-03 import regression), `taxShipping` is false, the International market is enabled for Canada/UK/EU/Australia with no shipping zone covering any of them (only "Domestic" US and "Asia"/21 countries exist), supplier cost is published in `compareAtPrice` on all 17 variants (byte-identical to `inventoryItem.unitCost`, visible in `/products/*.js`), and CI-MAT-OUT is a dead variant on a live product whose description says "Choose Indoor or Outdoor Use."

---

## 4. THE PREMIUM GAP

Ranked by payoff-per-effort. The first five are under two hours combined and account for most of the perceived quality delta.

**1. The site-wide Liquid error — 20 min, five defects, every page.**
`layout/theme.liquid` uses the `images[...]` Shopify Files lookup at lines 57, 70, 110 for `og-caddieinsight.png`, `caddieinsight-favicon.png` and `caddieinsight-logo.png`. None of those three filenames exist in Files. `images['missing.png']` returns a *truthy drop*, not nil, so the `{% if %}` guards pass, `image_url` throws, and the carefully-written `asset_url` fallback branches are unreachable dead code. Result, live right now: every page renders `<link rel="icon" href="Liquid error (layout/theme line 72): invalid url input">` — so the browser tab shows a default globe on 100% of pages, which is the single loudest hobby-project signal a site can emit. On every non-product page `og:image` and `twitter:image` are the same error string, so pasting `caddieinsight.com` into iMessage or Slack produces an image-less card. And the Organization JSON-LD emits `"logo": Liquid error (...)` unquoted, making the whole block invalid JSON that Google discards. **Do not touch theme settings — this theme has no favicon setting.** Replace lines 70-77 with unconditional `asset_url` tags, drop the Files fallback at 57, and quote line 117 with `| asset_url | prepend: 'https:' | json`.

**2. The app header renders "CADDIEINSIGHT" as text where the store renders a logo — 10 min.**
`config.py:21` sets `logo_url: None` with no environment override, so `web_layout.html.j2:1027-1031` always falls to `{{ brand.name | upper }}`. A customer clicking from the store into the app watches the logo turn into plain type. The template even carries styling for the image it never emits (`web_layout.html.j2:228, 230`, including a premium invert filter) — dead CSS proving the intent. The asset isn't even present: `caddieinsight-logo.png` is in `storefront-theme/assets/` but not `swinglab/web/static/`, which still ships `swinglab-logo.png`. Copy both logos in, set the default. Same pass: the favicon is `/static/swinglab-favicon.png` while an unreferenced `caddieinsight-favicon.png` sits in the same directory.

**3. The sample report is serving a stale pre-rebrand stylesheet — 5 min.**
Live, `/sample-report/` renders in `system-ui` on a warm cream palette (`--paper: #fffdf8`, `--brand-accent: #e8720c`) while the app shell runs Archivo on cool green-grey (`--sl-bg: #eef2ef`). It reads as a different company's product. **This is already fixed in the repo** — commit 8ee7837 changed the template on 2026-08-08 and is an ancestor of deployed HEAD. Production serves a cached file at `/data/sessions/sample-report/report.html` because `_report_is_current` (`sample.py:382-397`) validates only two version markers, neither of which the revamp changed. Delete the file, restart, done. Then make the gate content-aware (hash the template bytes into a `caddieinsight-sample-render` meta) or this recurs on every future edit. **Do not bump `GUIDED_REPORT_PRESENTATION_VERSION`** — `report_html.py:53` raises on anything but `guided-report-v1`.

**4. A free user's report shows an empty black box where the demo should be — 15 min.**
`report_guided.html.j2:911` opens the Evidence Theater when `slow_motion or coach_replay` is true, and `capabilities.slow_motion` stays true for free users because `slow_motion_media_key` is populated unconditionally (`report_presenter.py:922`). But the inner loop at `:919-921` skips every swing where `replay_locked`. `.media-theater__grid` has no min-height, so it collapses to zero. The premium dark-gradient panel renders its eyebrow, the title "Slow-mo and replay," a lead paragraph — then nothing. This also directly contradicts `/pricing`, which lists slow motion as Included on Free (`web_pricing.html.j2:327, 355-356`). Gate the coach-replay figure only, and put a locked poster-frame card with a `/pricing` link in the empty slot.

**5. The report paywall reads "Coach replay · 0" — 30 min.**
The highest-intent surface in the product contains zero occurrences of "pricing," "upgrade," or "checkout." A locked user's entire Pro-facing experience is a collapsed `<details>` whose summary renders label + item_count = "Coach replay · 0" (count is zero because COACH_REPLAY entries are stripped from `media` at `report_presenter.py:770-775`), then one sentence repeated once per detected swing, then six sections reading "This section is locked." That reads as a bug, not an offer. The working upsell already exists — `report.html.j2:1178-1189` has a lock badge, honest copy and `<a href="/pricing">See Pro plans →</a>` — it just never got ported to the guided template. Port it, suppress the `· {{ item_count }}` suffix when locked, move the explanation out of the swing loop. Note `tests/test_guided_report_html.py:679` asserts the current string.

**6. The public sample report — your entire demo — has no route to /pricing at all — 20 min.**
32,558 bytes, zero occurrences of "replay," "pricing," "upgrade," or "unlock." Because it's served as a bare `FileResponse` (`app.py:1454-1462`) it doesn't inherit `web_layout`, so it has no nav bar either. Nine hrefs total: `/`, a skip link, six in-page anchors, and one gear link. A prospect who reads all 361 lines is never told Pro exists.

**7. No `og:` / `twitter:` / description / canonical tags anywhere in the app — 30 min.**
`web_layout.html.j2:1-27` has charset, viewport, theme-color, PWA metas, title, manifest, icons, fonts. Nothing else. There is no `head` block, so per-page overrides are currently impossible. A shared swing report — the single most viral action in the product — previews as a bare grey URL. The 1200×630 asset already exists at `storefront-theme/assets/og-caddieinsight.png`; copy it to `/static/`, add a `{% block meta %}`, pass `canonical_url` and `og_image_url` from the existing `render()` helper at `app.py:1010`.

**8. `pro-plans.png` advertises "3 analyses every month" on the free plan — 2 min.**
Gallery image 4 on the money page, contradicted by every text source on the site and by `config.py:218` (`free_per_month: 1`). **The corrected art already exists** at `store-assets/out/pro-plans.png` and already reads "1 full analysis every month" — the live upload is simply stale. Delete `MediaImage/34119666466988`, re-upload, re-set alt text, drag back to position 4. No regeneration, no theme deploy.

**9. `/shop` is in the primary nav and ships zero products — 10 min.**
It renders "Practice aids are shown here only after sample-tested fulfillment... Check back soon." The store is *not* empty — the `swinglab-gear` collection holds 6 ACTIVE products with real inventory, and the unauthenticated Storefront API call works fine. The page is emptied by `config.yaml:374 first_sale_catalog_only: true` combined with a stale allowlist at `config.yaml:381-384` naming three ARCHIVED seed products. The same stale allowlist is silently zeroing every in-report gear recommendation via `shop.py:160` — killing the attach-rate revenue path with no error surfaced.

**10. Four SwingLab handles leak into every shared link — 45 min, and it only gets more expensive.**
`/products/swinglab-pro` (the money page), `/collections/swinglab-gear`, `/pages/the-swinglab-method`, `/pages/how-swinglab-works`. These propagate into canonical, `og:url`, four JSON-LD `url` fields, the BreadcrumbList, and the sitemap. Two footer links have text that disagrees with their destination. **Sequencing matters:** the theme uses handle-keyed Liquid lookups (`all_products['swinglab-pro']` at `footer.liquid:5`, `header.liquid:12`, `plans-band.liquid:5`, `comparison.liquid:5`; `collections['swinglab-gear']` at `gear-showcase.liquid:4`) — a handle lookup does **not** follow a 301, it returns blank. Handle edits take effect instantly while the theme deploys manually, so renaming first produces a visibly broken storefront. Ship `| default:` fallbacks in the theme first, then rename, then clean up.

**11. Everything else, roughly in order:** raw Python tracebacks rendered into the customer-facing failure panel (`jobs.py:1344-1350` → `humanize.py:97-98` → `web_status.html.j2:401`); no social proof of any kind while the page carries a "NOT A CUSTOMER TESTIMONIAL" disclaimer, and the Pro buy box has no trust rail at all because `main-product.liquid:293-312` sits inside the gear `{%- else -%}` branch (a $19 alignment stick gets three trust rows, the $149 pass gets zero); `font-weight: 900` in five places with only 400-800 loaded, producing synthetic faux-bold on the footer wordmark and the "Compare Free and Pro." heading; 6.1 MB of PNG plan cards on the page where people decide to buy; mobile centre-aligning product card titles and prices (`base.css:944-949`); the hero's live-analysis signal card — the most "AI product" element on the site — `display:none` on phones (`hero.liquid:442, 497`); three consecutive near-black homepage sections with no seam; no dark mode anywhere; `/docs`, `/redoc` and `/openapi.json` publicly serving a Swagger dev console that also publishes the `SWINGLAB_ADMIN_TOKEN` cloaking design in a docstring; and zero security headers on any response.

---

## 5. PHOTOGRAPHY & IMAGERY PLAN

The central problem is inversion: three genuinely excellent photoreal images are bound to the homepage plans band, while the page that takes $149 shows four flat vector plates — a hollow phone outline, four stick figures labeled ADDRESS/TOP/IMPACT/FINISH, a stick-figure overlay, and a plan chart. The homepage "Inside the report" card is also an illustration (its own Files alt text says "Illustrated address, top, impact, and finish position sequence"). Nothing anywhere on either surface shows the actual application.

**Do first — real product evidence (highest revenue impact):**

| Asset | Dimensions | Surface | Art direction |
|---|---|---|---|
| Pro gallery 1 | 2048×2048 (1:1 — theme crops square) | Pro PDP, featured | Re-crop `caddieinsight-pro-card-v2.png` square, subject right-of-centre |
| Pro gallery 2 | 2048×2048 | Pro PDP | Screenshot: rendered report with the centreline overlay on **real footage** — this is the shot that proves the AI works |
| Pro gallery 3 | 2048×2048 | Pro PDP | Screenshot: annotated coach replay, still frame from a real `replay_sN.mp4` |
| Pro gallery 4 | 2048×2048 | Pro PDP | Screenshot: progress dashboard across 3+ real sessions (populate it first) |
| Report proof strip | 1600×480 | Homepage `report-feature` | Replace the illustrated `caddieinsight-report-preview` with a real `strip_sN.png` from `swinglab/strip.py` — keep the "demonstration data" disclosure |
| Before/after pair | 1600×900 | Homepage or Pro PDP | Same golfer, same club, same view, two reports, the metric that moved — this is simultaneously your best demo and your only honest social proof |

**Do not harvest from `/sample-report/`** — its `strip_s*.png` files are procedurally generated stick figures from `swinglab/sample.py:235`, cruder than what is already on the page.

**Fix / replace:**

| Asset | Dimensions | Surface | Note |
|---|---|---|---|
| `caddieinsight-free-card-v2.png` | 1536×1024 | Homepage plans band | Regenerate to its own written prompt (`caddieinsight-tour-caddie-v4-photography.md:157-177`): overcast municipal range, mid-backswing, three-quarters behind, phone on a bucket. Current file is golden-hour, blown-out sky, manicured course, face lit — off-system versus Pro and Founders, and dated 8/3 while they were regenerated 8/9. Also stop rendering it at `sizes="148px"` while the paid cards get 396px. |
| `pro-plans.png` | 1600×1600 | Pro PDP img 4 | Re-upload the existing corrected `store-assets/out/` version. Two minutes. |
| `pro-overlay-detail.png` | 1600×1600 | Pro PDP | TEMPO 2.6:1 renders with the flagged orange bar, but the page states "tempo flagged under 2.4:1." Recolour green or change to 2.2:1. |
| `og-caddieinsight-v2.png` | 1200×630, subject inside centre 1000×500 | Storefront default | Photographic, not the current drawn text card. Your own spec calls this "the single highest-leverage upgrade here." |
| `og-app.png` | 1200×630 | `/static/`, app default | Report UI in a device frame |
| Hero WebP pair | 1672×941 + 1122×1402 | Theme + app | Re-encode from the source PNGs and ship **byte-identical** bytes to both. Currently theme=97,918 B vs app=54,298 B — the app, which people pay for, gets the worse copy, contradicting the spec's explicit requirement. |
| Tempo Trainer + Connection Ball heroes | 1600×1600 | Gear | Currently 1672×941 and 1648×954 against four siblings at 1254×1254; `aspect-ratio: 1; object-fit: cover` eats ~44% of their width |
| Plan card PNGs ×3 | WebP at rendered size | Theme assets | 2.37 + 1.88 + 1.74 MB of PNG on the buy page; the hero already proves ~95% reduction is achievable |

**Expo (only if pursuing the native beta):** dedicated adaptive-icon foreground 1024×1024 with the mark inside the centre 66% (the current file is full-bleed and will be mask-clipped), splash 1284×2778 on `#06110c`, App Store 6.7" 1290×2796 ×5, iPad 12.9" 2048×2732 ×5, Play feature graphic 1024×500, Play phone 1080×1920 ×5.

**Missing entirely, worth shooting when there's time:** a golfer reviewing their report on-phone in the car park after a session (1600×1200), and a hands-on-phone tripod-setup detail (1600×1200) for the "how it works" band and paid social.

**Housekeeping:** `store-assets/out/banner-about.png` labels six products (TEMPO WAND, METRONOME, ALIGN STICKS, HIP BAND, SWING MIRROR, CAP) that no longer exist; roughly 2.5 MB of `product-*`, `drill-*`, `detail-cap` and `banner-*` art belongs to the retired catalog and is referenced nowhere — move it to `out/archive-v3/`. Delete the four retired-brand entries still in Shopify Files (`swinglab-logo.png`, `swinglab-logo-inverse.png`, `swinglab-favicon.png`, `og-swinglab.png`); a Files entry beats a theme asset of the same name, so the header logo picker is one wrong click from silently restoring the v3 mark — and the v3 lockup is 1400×214 against v4's 1400×279, so a mis-pick also breaks header layout.

---

## 6. MOBILE BETA

**The honest answer: the PWA is your beta. Ship that today; treat `mobile/` as a post-launch project.**

The Expo scaffold is not a beta, it is a non-functional demo, and the gap is larger than it looks:

- **Nobody can get in.** `connect.tsx:38` tells the tester to open `/account` and create a device token. That control does not exist — `web_account.html.j2` is 412 lines with zero token UI, and a repo-wide grep for `mobile-tokens` and `ciat_` across templates, static and theme returns nothing. `POST /api/v1/mobile-tokens` also hard-rejects any Authorization header (`app.py:730-731`), so the app cannot mint its own. The only path to a credential today is hand-writing a fetch in devtools. 100% blocked funnel.
- **The home screen is a mock.** `index.tsx` reads `headline`, `preferred_club`, `practice_minutes`, `membership.is_pro` — `GET /api/v1/today` returns `resource_version`, `profile`, `latest_session`, `caddie_brief`, `practice_plan`, `practice_checked_in`. Zero of five overlap, so every user sees identical "Today / Not set / Not set / Free plan." Every `/api/v1` response is envelope-wrapped and never unwrapped; `sessions.tsx` reads `session.state` where the server sends `session.status`.
- **Pro is invisible by design.** No `/api/v1` model exposes membership at all — `is_pro` reaches browsers only through the cookie-authed `/auth/storefront/session`, which a bearer token cannot use. A Founders Pass buyer would open the app and see "Free plan."
- **There is no product in it.** No camera, no picker, no upload; `expo-camera` and `expo-image-picker` are not installed. Three read-only screens over a website — which is also App Store guideline 4.2 territory.
- **The documented distribution path is broken.** SDK 52 / RN 0.76.9 is ~21 months stale; store Expo Go supports only the current SDK, so the README's "scan the QR code" instruction produces an incompatibility error. No `eas.json`, no `extra.eas.projectId`, no `owner`, no `expo-updates`. `.expo/devices.json` is `{"devices": []}` — nobody has ever connected.
- The README's justification ("not one `/api/` operation declares a 200 response schema") became false on 2026-08-09 when `api_models.py` landed; every `/api/v1` op in `docs/api/openapi-v1.json` now carries a real `$ref`.

**Shortest honest path to a native beta, if Kyle wants one (3-5 days plus account setup):**

1. **Device-token UI in `web_account.html.j2`** — label input, POST to `/api/v1/mobile-tokens`, one-time reveal with copy button, active-device list, revoke. One template section + ~30 lines of fetch JS. This is the single highest-leverage hour on the surface; without it nothing else matters.
2. **Add a Membership model** to `api_models.py` on both `MeResponse` and `TodayResponse` (~15 lines). Prerequisite for the app showing anything Pro-related.
3. **Delete `src/api/types.ts`** and run `npx openapi-typescript ../docs/api/openapi-v1.json -o src/api/schema.d.ts`. Wire `client.ts` to the generated types and fix the compile errors — that is your bug list. Add a CI regen-and-diff step. Also delete `api.devices()`, which can only ever 401 and then wipes the user's stored token.
4. **Add `expo-image-picker` and an upload screen.** Skip `expo-camera` for v1 — launch the system camera, POST FormData to `/upload` with the bearer plus hand/angle/club. The backend already branches on the Authorization header (`app.py:3458-3478`); this path is built and unused.
5. **Bump the SDK.** At 15 files, `npx create-expo-app` fresh and port the four screens is faster than upgrading. Verify with `npx expo export --platform ios`.
6. **`eas init` + `eas build:configure`**, add `expo-updates` with a runtimeVersion policy so beta fixes don't need a new binary. Then TestFlight — which requires an Apple Developer Program membership ($99/yr) and Play internal testing a Play Console account ($25). If neither exists, that is a hard multi-day dependency on Apple/Google, not on code.

Bundling and typechecking will both pass at every stage and prove nothing — the types are fiction that agrees with itself. The verification that actually matters is one curl with a real token against `/api/v1/today`.

---

## 7. RECOMMENDED SEQUENCE

### (a) Unblock the money — ~3-4 hours of work, plus wall-clock on Shopify re-consent

1. Add `read_orders` to `shopify.app.toml:10`; add the `orders/paid` / `orders/cancelled` / `refunds/create` subscription block; `shopify app deploy`; **re-authorize the Bridge app in Admin**.
2. Verify `SHOPIFY_STORE_DOMAIN` on Railway is the myshopify host, and that `SHOPIFY_WEBHOOK_SECRET` has no stray whitespace.
3. Set `SWINGLAB_ADMIN_TOKEN`; confirm `/admin/kpis` returns 200.
4. Send yourself a production login code; confirm delivery. Add `RESEND_API_KEY`.
5. Set Railway healthcheck path `/healthz`; fix the shell-form `CMD` in `Dockerfile:35`.
6. Configure and **test-restore** the SQLite backup.
7. Tick "Track quantity" on Founders Pass variant 46839745282220.
8. Publish Terms of Service; replace the Refund policy; fix the iCloud mailto.
9. Patch `web_pricing.html.j2:252-256` so anonymous visitors reach the store.
10. **Real $9.99 purchase, incognito, real card.** Verify transaction → webhook 200 → Pro on the account *without re-login* → tax computed. Refund. If Pro parks instead of granting, add the opportunistic `claim_pending_grant` call in `current_user` and in `apply_customer` before you go further.
11. Add `SENTRY_DSN` and change `Dockerfile:19` to `pip install ".[web,ops]"` — Sentry cannot load today even if the DSN were set.

**Only after step 10 passes should Pro be considered released.**

### (b) Make it premium — ~1 day of code/config, imagery production separate

12. Fix `layout/theme.liquid` lines 57 / 70-77 / 110-117 (favicon, OG, Organization JSON-LD). Manual theme deploy.
13. Copy the CaddieInsight logos into `swinglab/web/static/`; set `brand.logo_url`; repoint the favicon.
14. Delete `/data/sessions/sample-report/report.html`, restart, then make `_report_is_current` content-aware.
15. Fix the Evidence Theater gate at `report_guided.html.j2:919-921`; port the locked-replay upsell block from `report.html.j2:1178-1189`; suppress the `· 0` suffix; de-duplicate the explanation.
16. Add the Pro upsell + progress teaser to the sample report; bump `GUIDED_REPORT_PRESENTATION_VERSION` in `report_view.py:15` so the cached copy regenerates.
17. Add `{% block meta %}` to `web_layout.html.j2` with description / og / twitter / canonical.
18. Re-upload `pro-plans.png`.
19. Give the Pro buy box a trust rail (14-day refund, cancel anytime, unlocks on your checkout email) — lift the pattern from `main-product.liquid:293-312`.
20. Fix `/shop`: tag the six live SKUs `caddieinsight:fulfillment-verified` + per-product candidate tags and update `config.yaml:381-384`; or set `first_sale_catalog_only: false`; or set `shop.enabled: false` and let the nav item disappear. Do not ship the empty page.
21. Stop rendering Python tracebacks to customers (`humanize.py:97-98` fallback).
22. Add the four security headers; set `docs_url/redoc_url/openapi_url = None` in production; strip the credential mechanics from the `/admin/kpis` docstring and regenerate `docs/api/openapi-v1.json`.
23. Handle rename (theme fallbacks → rename → verify 301s → repoint hardcoded hrefs → re-pick Shopify reference settings → remove fallbacks).
24. Imagery per section 5, starting with the Pro gallery.
25. Polish tail: `font-weight: 900` → 800, WebP the plan cards, un-centre mobile product cards, delete the frontpage collection, write collection SEO fields, add `robots.txt` to the app origin.

### (c) Beta the app — half a day (PWA) or 3-5 days + accounts (Expo)

26. **PWA path:** fix the manifest `start_url` (currently `/today`, which 303s to `/login` for a logged-out install) or suppress the install prompt for anonymous visitors; write install instructions; hand testers the URL.
27. **Expo path:** steps 1-6 of section 6, in that order. Do not start before (a) and (b) are done.

**Relative effort:** (a) is small but sequenced and gated on Shopify re-consent — a focused morning. (b) is the biggest block of work but almost entirely parallelizable, and items 12-18 alone are under two hours for most of the visible gain. (c) is either 2 hours or a week, depending on question 5 below.

---

## 8. OPEN QUESTIONS FOR KYLE

**1. Price.** $9.99/mo + $69.99/yr is byte-identical to V1 Golf Plus's App Store IAPs — a 15-year-old video-annotation tool rated 4.1 — and sits at the floor of the AI-report category (SwingSmith $9.99/$14.99/$19.99, Sportsbox consumer $15.99/mo, Runna $19.99/mo with 76k ratings at 4.9). Options: **(a)** hold and compete on price; **(b)** move Pro to $14.99-19.99 and the annual to $119-149; **(c)** restructure — $9.99 "Analysis" (report only) and $19.99 "Coach" (proof cycle + annotated replay), which mirrors the category ladder and lets you own the middle. Changing this is cheapest before the first sale.

**2. Founders Pass.** At $149 it is ~15 months of Pro, or under 10 months if you raise the price. Options: **(a)** enforce 100 and keep $149; **(b)** enforce 100 and raise to $199-249; **(c)** drop the cap language entirely and sell it uncapped. (a) or (b) require the checkbox in Blocker 7; (c) requires editing three places in the theme and the FAQ. What you cannot do is keep promising a cap you do not enforce.

**3. Free tier.** One analysis per month means a free user literally cannot complete the Film → Practice → Re-film cycle that is your actual differentiator — they can never experience the thing you are selling. Options: **(a)** keep 1/month; **(b)** 2-3 analyses in the first 14 days, then 1/month, so the free tier demonstrates the proof cycle and the upgrade prompt lands at the moment they see a change verified. (b) costs one extra inference per free signup and is probably the highest-leverage product change available.

**4. Positioning claim.** "A pass mark for your next clip" is not differentiated — Sportsbox's Goals feature already sets metric goal ranges and scores progress, and overlay/side-by-side comparison is table stakes at V1, Onform and DeepSwing. What I could not find anyone else doing is **enforcing a matched re-film and algorithmically grading whether the prescribed change occurred** — Onform's own page says its comparison "relies on visual human analysis rather than algorithmic detection of mechanical changes." Options: **(a)** keep the current hero; **(b)** rewrite around enforced verification ("Most swing apps tell you what's wrong. CaddieInsight proves whether you fixed it") and date-stamp the claim. If (b), re-verify the competitive absence before publishing — absence of evidence isn't proof.

**5. Mobile beta.** Options: **(a)** ship the PWA as the beta today (2 hours: fix `start_url`, write instructions) and shelve `mobile/`; **(b)** invest 3-5 days plus Apple ($99/yr) and Play ($25) accounts to make the Expo app real. Note that (b) also inherits an App Store 3.1.1 question, since `connect.tsx` opens `/account` in an in-app browser and that page links to external Shopify checkout for a digital product.

**6. Subscription default on the Pro page.** The buy box currently defaults to "One-time purchase" checked, with auto-renew as the unselected alternative. That is consistent with the site's honest posture — nobody is enrolled in a subscription they did not choose — but it means the default $9.99 buyer gets 31 days and then silence, with no recurring revenue and no retention hook. Options: **(a)** keep the default and add a day-25 lifecycle email; **(b)** default to auto-renew, keeping the one-time option visible and its current framing intact. Related and worth deciding at the same time: `users.py:7774-7787` excludes only Stripe-managed subscriptions from the expiry reminder, so Shopify auto-renew subscribers will receive "Your Pro ends in N days — extend it on the store" every single cycle, contradicting the fineprint you are shipping.
