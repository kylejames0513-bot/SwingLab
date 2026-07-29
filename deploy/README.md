# Deploying CaddieInsight

Two paths to a real URL you can open from any device:

- **Docker (recommended once you're growing):** on any machine with Docker,
  `docker compose up -d` in the repo root builds the image and runs the app on
  port 8000 with sessions in a persistent volume. Upgrades are
  `git pull && docker compose up -d --build` — interrupted analyses re-queue
  automatically on start. The same image runs unchanged on any container
  host (Fly.io, Cloud Run, ECS, a VPS), which is the scaling story: one image,
  bigger machines or more of them as demand grows.
- **Bare VM (simplest first deploy):** the script below sets up a fresh
  Ubuntu 24.04 VM end to end. Roughly $6/month at most providers, and every
  step works from a browser (no computer needed).

## VM steps

1. **Create a VM** at any provider (DigitalOcean, Hetzner, AWS Lightsail, ...):
   - Image: **Ubuntu 24.04** (CaddieInsight needs Python 3.11+)
   - Size: the cheapest option works; 2 GB RAM is comfortable
   - Networking: allow inbound **HTTP (port 80)** — most providers' default
     firewall setting already does
2. **Open the VM's web console/terminal** (every provider has one in the
   browser) and paste:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/kylejames0513-bot/SwingLab/main/deploy/server-setup.sh | bash
   ```

3. The script prints the URL when it finishes (`http://<your-vm-ip>/`).
   Open it, upload a swing video, done.

The app runs as a systemd service, so it survives reboots and crashes.
Useful commands (in the same web console):

```bash
journalctl -u swinglab -f        # live logs
systemctl restart swinglab       # restart the app
cd /opt/swinglab && git pull && systemctl restart swinglab   # update
```

## Custom domain (caddieinsight.com) — CONFIGURED

The domain layout is live: `caddieinsight.com` is the Shopify storefront's
primary domain, and `app.caddieinsight.com` is a CNAME to the Railway
service (record managed in Shopify admin → Settings → Domains →
caddieinsight.com → DNS settings). `PUBLIC_BASE_URL` on Railway is
`https://app.caddieinsight.com`, and every "Open the app" link in the
storefront theme, store pages, and generated art points at the new
subdomain. The old Railway URL keeps working indefinitely as a fallback,
and the Shopify webhooks still deliver to it — moving them to the custom
domain is optional and changes nothing functionally.

`config.yaml`'s `shop.store_url` — the report's "Matched training aids"
link — points at the storefront; it can stay on the `.myshopify.com`
address or move to `https://caddieinsight.com` (both resolve to the same
store).

## Tuning for real traffic

The `web` section of `config.yaml` is the knob panel: `workers` (analyses
running at once — match it to CPU cores), `max_upload_mb`,
`max_active_jobs_per_ip`, `trusted_proxies` (which proxy's
`X-Forwarded-For` to believe — shipped `"*"` for PaaS; see the inline doc
for the trust model), the auth throttles (`login_attempts_per_15min`,
`signups_per_hour_per_ip`), `retention_days` (auto-delete old sessions so
the disk never fills — shipped 180 days), and `delete_source_after_done`
(drop the raw upload once the report exists; deliverables stay). `/healthz`
reports queue depth plus `disk_free_mb` and `sessions_count` for load
balancers and uptime monitors — alert on `disk_free_mb` before it reaches
zero, because disk-full is the most likely first outage.

## Error monitoring (optional Sentry)

Inert until configured, like everything else: install the ops extra and set
the DSN —

```bash
pip install "swinglab[ops]"     # adds sentry-sdk (never a base dependency)
export SENTRY_DSN=https://...@o0.ingest.sentry.io/0
```

With both in place, unexpected analysis failures and web errors are
reported to Sentry (the app logs through the standard `logging` module, so
they land in `journalctl`/Railway logs either way). With either missing,
nothing changes — every error path works identically without it.

## Backup and recovery

`sessions/swinglab.db` is the source of truth for accounts, paid
entitlements, purchase idempotency, and job history. The production database
uses WAL mode, so copying the live `swinglab.db` file is unsafe: committed
transactions may still be in `swinglab.db-wal`.

The inactive Stage 0B tooling and complete operator runbook are in
[`docs/operations/backup-recovery.md`](../docs/operations/backup-recovery.md).
They provide:

- a WAL-safe snapshot through SQLite's online backup API;
- SHA-256 manifests for completed-job reports and generated media;
- private, vendor-neutral S3-compatible upload/download support;
- a provider-verified conditional claim that prevents concurrent writers from
  sharing one backup ID;
- a conditionally created completion marker written last, plus version- or
  ETag-pinned bounded downloads that reject object mutation and clean partial
  output;
- scratch-only restores with integrity, critical-table, entitlement,
  purchase-ledger, and artifact verification.

Nothing is scheduled or connected by default. Do not wrap the Railway start
command, change the Dockerfile, set credentials, create a bucket, or run a
production backup without separate approval.

Litestream remains a possible future database-only option, but it is not
installed or configured. It would need a version-pinned configuration for
`/data/sessions/swinglab.db`, a deliberate supervisor design, independent
artifact backups, monitoring, and the same scratch restore validation. Never
use `rclone sync` as a backup: retention or operator deletion could propagate
into the recovery copy.

## Accounts and payments

Accounts are on by default (`web.require_account` in config.yaml): visitors
sign up free, get a monthly allowance, and upgrade to Pro. Pro can be sold
through the Shopify store (preferred — one checkout for gear and
memberships) or as a Stripe subscription; until either is configured the app
runs happily with payments off (free tier only). Whichever path you take,
first set `SWINGLAB_SECRET` (any long random string) in the host's
environment so logins survive restarts/redeploys.

**Selling Pro on Shopify:**

1. In the Shopify admin, create a product for Pro access (digital — untick
   "This is a physical product"). Give each variant a SKU from
   `billing.shopify_skus` in config.yaml — the shipped mapping is
   `SL-PRO-1MO` (31 days), `SL-PRO-12MO` (365 days), and `SL-PRO-LIFE`
   (36500 days — the lifetime tier; the app displays it as "Lifetime");
   prices are set on the product. Make sure the product's URL handle
   matches `billing.shopify_pro_handle` (default `swinglab-pro`).
2. In Shopify → Settings → Notifications → Webhooks, add **six** webhooks,
   all pointing at the **same** URL `https://<your-app>/webhooks/shopify`
   (no trailing slash — Shopify does not follow redirects, so a stray `/`
   makes every delivery fail), and copy the signing secret shown at the
   bottom of that page:
   - **Order payment** (`orders/paid`) — grants Pro.
   - **Order cancellation** (`orders/cancelled`) — takes Pro back.
   - **Refund creation** (`refunds/create`) — takes the whole order's Pro
     grant back when a refunded line item identifies a configured Pro SKU;
     gear-only or unattributable refunds leave Pro unchanged.
   - **Customer creation** (`customers/create`), **Customer update**
     (`customers/update`), **Customer deletion** (`customers/delete`) —
     these provision the passwordless "store account" so a buyer who
     clicks **Log in** in the app is told to *create your password to
     finish setup* instead of getting a bare "Wrong email or password".
     Omit these three and the store-account claim UX is dead: the app
     never learns the customer exists until they sign up from scratch.
   Paste the secret with no leading/trailing whitespace — a stray newline
   silently rejects every delivery (the app now logs a `bad signature`
   warning when this happens).
3. Set `SHOPIFY_STORE_DOMAIN` and `SHOPIFY_WEBHOOK_SECRET` in the host's
   environment and redeploy.
4. **Verify it works before trusting it.** Place a real (or test) order,
   then confirm the app actually recorded it — the app logs
   `Shopify order <id>: granted N Pro day(s) to <email>` on success, and
   `sqlite3 /data/sessions/swinglab.db "SELECT * FROM shopify_orders"`
   should return a row. An empty table with a green 200 in Shopify's
   delivery log means the wrong topic was subscribed (watch the app log
   for `Ignoring unrecognized Shopify webhook topic`) or the SKU didn't
   match `billing.shopify_skus` (the purchase was recorded as *gear*
   instead — check the `gear_orders` table).

Buyers check out on the Shopify storefront; a paid order unlocks Pro on the
CaddieInsight account with the same email (or waits for that email to sign
up).
For auto-renewing memberships, install Shopify's free **Subscriptions** app
(requires Shopify Payments) and create its selling plans in the app's UI:
a monthly plan attached to the 1-month variant only and a yearly plan
attached to the 12-month variant only — never to the lifetime variant.
Each billing cycle's order carries the same SKU, so access re-extends
through the existing webhook path. The entitlement ledger currently grants
fixed terms (31 days for monthly and 365 days for yearly); it does not have
the authoritative next billing-cycle date required to align access exactly
to Shopify calendar months/years. Do not infer that date from an order
timestamp or selling-plan name. Enable customer accounts so
subscribers can manage or cancel their own subscription. (Selling plans
must be created inside the Subscriptions app — plans created by other API
clients are not billed by it.) Once the plans are live, set
`billing.store_subscriptions: true` in config.yaml and redeploy so the
app's pricing page starts describing auto-renewal — it stays in honest
passes-only wording until then.

**Selling Pro as a Stripe subscription:**

1. Create a [Stripe](https://stripe.com) account, add a **Product** with a
   **recurring price** (this is where the monthly price is set), and copy the
   `price_...` id.
2. In Stripe → Developers → Webhooks, add an endpoint for
   `https://<your-app>/webhooks/stripe` subscribed to `checkout.session.completed`,
   `customer.subscription.updated`, and `customer.subscription.deleted`; copy
   its `whsec_...` secret.
3. Set `STRIPE_SECRET_KEY`, `STRIPE_PRICE_ID`, `STRIPE_WEBHOOK_SECRET`, and
   `PUBLIC_BASE_URL` in the host's environment and redeploy.

## Gear shop (Shopify)

Optional: connect a Shopify store and the app grows a **Gear** page plus
per-analysis training-aid recommendations (see the main README for how
products are matched to swing flags). To enable it:

1. In Shopify, create or confirm the public collection with handle
   `swinglab-gear`, and publish the Gear products to it.
2. Set `SHOPIFY_STORE_DOMAIN` (e.g. `yourstore.myshopify.com`) in the host's
   environment and redeploy. The catalog uses Shopify's public Storefront
   query and does not need an access token.

Until then the shop is invisible — no link, no page. A Shopify outage
degrades to the last cached product list, never an error page.

## Cautions

- On a VM behind plain HTTP, add HTTPS before taking signups or payments —
  put the app behind Caddy or nginx with Let's Encrypt. (Railway/Render/Fly
  domains come with HTTPS already.)
- When you do put nginx/Caddy in front on a VM, change
  `web.trusted_proxies` from the shipped `"*"` to the proxy's actual
  address (e.g. `["127.0.0.1"]`). `"*"` is right for a PaaS where only the
  platform proxy can reach the app; on a VM whose app port is reachable
  directly, `"*"` would let clients spoof their IP via `X-Forwarded-For`
  and dodge the per-IP limits.
- GDPR/data-minimization: sessions contain identifiable video of people.
  The shipped config auto-deletes finished sessions after 180 days
  (`web.retention_days`) and drops the raw upload as soon as the report
  exists (`web.delete_source_after_done`) — turning either off means YOU
  are choosing to hold footage longer; have an answer for why.
- Analysis is CPU-heavy (slow-motion interpolation most of all). On a $6 VM
  a clip with a few swings takes a couple of minutes — that's expected.
  Uploaders can tick **Fast mode** to skip interpolation and get results in a
  fraction of the time.
