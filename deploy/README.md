# Deploying SwingLab

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
   - Image: **Ubuntu 24.04** (SwingLab needs Python 3.11+)
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

## Tuning for real traffic

The `web` section of `config.yaml` is the knob panel: `workers` (analyses
running at once — match it to CPU cores), `max_upload_mb`,
`max_active_jobs_per_ip`, and `retention_days` (auto-delete old sessions so
the disk never fills). `/healthz` reports queue depth for load balancers and
uptime monitors.

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
2. In Shopify → Settings → Notifications → Webhooks, add two webhooks —
   **Order payment** (`orders/paid`) and **Order cancellation**
   (`orders/cancelled`) — both pointing at
   `https://<your-app>/webhooks/shopify`, and copy the signing secret shown
   at the bottom of that page.
3. Set `SHOPIFY_STORE_DOMAIN` and `SHOPIFY_WEBHOOK_SECRET` in the host's
   environment and redeploy.

Buyers check out on the Shopify storefront; a paid order unlocks Pro on the
SwingLab account with the same email (or waits for that email to sign up).
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
- Analysis is CPU-heavy (slow-motion interpolation most of all). On a $6 VM
  a clip with a few swings takes a couple of minutes — that's expected.
  Uploaders can tick **Fast mode** to skip interpolation and get results in a
  fraction of the time.
