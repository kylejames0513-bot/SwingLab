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

## Backups (Litestream) — the database holds paid entitlements

`sessions/swinglab.db` is the source of truth for **accounts, Pro purchase
state, and job history**. Losing it means losing who paid you. Session
media is re-creatable (users can re-upload); the database is not — back it
up continuously with [Litestream](https://litestream.io) (a single static
binary that streams every SQLite WAL change to object storage; restores are
to-the-second).

`litestream.yml`:

```yaml
# /etc/litestream.yml
dbs:
  - path: /opt/swinglab/sessions/swinglab.db
    replicas:
      - type: s3
        bucket: yourbucket
        path: swinglab-db
        endpoint: https://<region>.digitaloceanspaces.com   # any S3-compatible store
        # AWS S3: drop `endpoint`, set region instead
        retention: 720h          # keep 30 days of point-in-time history
```

Credentials go in the environment (`LITESTREAM_ACCESS_KEY_ID`,
`LITESTREAM_SECRET_ACCESS_KEY`). On a VM, run it as a second systemd
service:

```bash
litestream replicate -config /etc/litestream.yml
```

**Railway notes:** Railway containers have no sidecar processes, so wrap
the start command — Litestream supervises the app and replicates while it
runs:

```
litestream replicate -config /app/litestream.yml -exec "swinglab serve --host 0.0.0.0 --port $PORT"
```

Add the `litestream` binary in the Dockerfile
(`COPY --from=litestream/litestream:0.3 /usr/local/bin/litestream /usr/local/bin/`),
put `litestream.yml` in the image, and set the two credential variables in
the Railway service settings. The sessions volume must be the Railway
volume mount so the db path is stable across deploys.

**Backup/restore drill** — run it BEFORE you need it, then quarterly:

1. Confirm replication is current:
   `litestream snapshots -config /etc/litestream.yml /opt/swinglab/sessions/swinglab.db`
   (you should see a recent snapshot; generations advance as the app writes).
2. Restore to a scratch path:
   `litestream restore -config /etc/litestream.yml -o /tmp/restored.db /opt/swinglab/sessions/swinglab.db`
3. Verify the restored file is a working database with your real data:
   `sqlite3 /tmp/restored.db "PRAGMA integrity_check; SELECT COUNT(*) FROM users; SELECT COUNT(*) FROM jobs;"`
   — counts should match production (`sqlite3 sessions/swinglab.db ...`).
4. Actual disaster recovery = stop the app, run the same restore with `-o`
   pointing at `sessions/swinglab.db`, start the app. Interrupted jobs
   re-queue themselves; sessions whose media is gone fail honestly with
   "upload it again".

What Litestream does NOT cover: the uploaded videos and generated media in
the session folders. Those are large and re-creatable — if you want them
too, add a nightly `rclone sync` of the sessions directory (excluding
`swinglab.db*`, which Litestream owns) to the same bucket.

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
   `SL-PRO-1MO` (31 days) and `SL-PRO-12MO` (365 days); prices are set on
   the product. Make sure the product's URL handle matches
   `billing.shopify_pro_handle` (default `swinglab-pro`).
2. In Shopify → Settings → Notifications → Webhooks, add **five** webhooks,
   all pointing at the **same** URL `https://<your-app>/webhooks/shopify`
   (no trailing slash — Shopify does not follow redirects, so a stray `/`
   makes every delivery fail), and copy the signing secret shown at the
   bottom of that page:
   - **Order payment** (`orders/paid`) — grants Pro.
   - **Order cancellation** (`orders/cancelled`) — takes Pro back.
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
For auto-renewing memberships, add Shopify's free Subscriptions app to the
product — each billing cycle's order re-extends access automatically.

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

1. In Shopify admin → **Settings → Apps and sales channels → Develop apps**,
   create an app, give it the **Storefront API** `unauthenticated_read_product_listings`
   scope, install it, and copy the Storefront API access token.
2. Set `SHOPIFY_STORE_DOMAIN` (e.g. `yourstore.myshopify.com`) and
   `SHOPIFY_STOREFRONT_TOKEN` in the host's environment and redeploy.

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
