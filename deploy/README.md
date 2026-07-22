# Deploying SwingLab to a cloud VM

This gets SwingLab a real URL you can open from any device — phone, iPad,
anything with a browser. Total cost is roughly $6/month for the smallest VM at
most providers, and every step below works from a browser (no computer needed).

## Steps

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

## Cautions

- **No login yet.** Anyone who knows the URL can upload videos and see
  results. Fine for personal testing; don't share the URL widely until
  account/payment gating (Milestone 3) is in.
- Traffic is plain HTTP. For HTTPS put the app behind Caddy or nginx with
  Let's Encrypt — a natural follow-up once there's a domain name.
- Analysis is CPU-heavy (slow-motion interpolation most of all). On a $6 VM
  a clip with a few swings takes a couple of minutes — that's expected.
