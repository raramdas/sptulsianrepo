# Dashboard Deployment Guide — Secure Public Access

This sets up the Streamlit dashboard for secure access from anywhere (including
mobile) using: a free DuckDNS domain + Caddy reverse proxy with automatic HTTPS
+ app-level login with brute-force lockout.

Architecture:
```
Your browser/mobile
      │  HTTPS (encrypted)
      ▼
Caddy (port 443)  ──auto HTTPS cert from Let's Encrypt──
      │  proxies to localhost
      ▼
Streamlit (port 8501, localhost only)
      │
      ▼
Oracle Autonomous DB
```

Streamlit itself binds only to localhost — the internet never talks to it
directly, only Caddy does, and only over HTTPS.

---

## Step 1 — Get a free domain (DuckDNS)

1. Go to https://www.duckdns.org and sign in (Google/GitHub)
2. Create a subdomain, e.g. `raramdas-stockbot` → gives you
   `raramdas-stockbot.duckdns.org`
3. Set its IP to your VM's public IP: `140.245.226.35`
4. Note your DuckDNS **token** (shown at the top of the page)

Keep the IP updated automatically (in case it ever changes) with a cron job:
```bash
mkdir -p /home/ubuntu/duckdns
cat > /home/ubuntu/duckdns/duck.sh << 'EOF'
echo url="https://www.duckdns.org/update?domains=raramdas-stockbot&token=YOUR_DUCKDNS_TOKEN&ip=" | curl -k -o /home/ubuntu/duckdns/duck.log -K -
EOF
chmod +x /home/ubuntu/duckdns/duck.sh
# Run every 5 minutes
(crontab -l 2>/dev/null; echo "*/5 * * * * /home/ubuntu/duckdns/duck.sh >/dev/null 2>&1") | crontab -
/home/ubuntu/duckdns/duck.sh   # run once now; should write "OK" to duck.log
cat /home/ubuntu/duckdns/duck.log
```

Replace `raramdas-stockbot` and `YOUR_DUCKDNS_TOKEN` with your actual values.

---

## Step 2 — Open ports 80 and 443 (needed for HTTPS)

Caddy needs port 80 (for the certificate challenge) and 443 (for HTTPS).
Do NOT open 8501 publicly — Streamlit stays private.

**OCI Console:** VCN → Security List → add two Ingress rules:
- Source `0.0.0.0/0`, TCP, destination port **80**
- Source `0.0.0.0/0`, TCP, destination port **443**

**On the VM (ufw):**
```bash
sudo ufw allow 80
sudo ufw allow 443
sudo ufw reload
```

---

## Step 3 — Install dependencies + Caddy

```bash
cd /home/ubuntu/stock_bot_v4/dashboard
pip3 install -r requirements.txt

# Install Caddy (official repo)
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

---

## Step 4 — Configure Caddy

```bash
sudo tee /etc/caddy/Caddyfile << 'EOF'
raramdas-stockbot.duckdns.org {
    reverse_proxy localhost:8501
    encode gzip
    # Basic rate limiting via request headers is limited in core Caddy;
    # the app itself enforces login lockout after 5 failed attempts.
}
EOF

sudo systemctl reload caddy
```

Replace `raramdas-stockbot.duckdns.org` with your actual domain.

Caddy automatically obtains and renews a Let's Encrypt HTTPS certificate the
first time someone visits — no manual cert steps needed.

---

## Step 5 — Run Streamlit as a persistent service (localhost only)

Create a systemd service so the dashboard survives reboots and SSH logout:

```bash
sudo tee /etc/systemd/system/stockbot-dashboard.service << 'EOF'
[Unit]
Description=Stock Bot Streamlit Dashboard
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/stock_bot_v4/dashboard
ExecStart=/home/ubuntu/.local/bin/streamlit run app.py --server.port 8501 --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable stockbot-dashboard
sudo systemctl start stockbot-dashboard
sudo systemctl status stockbot-dashboard
```

> **Current live setup (as of Aug 2026):** on `140.245.226.35` the
> `stockbot-dashboard` unit's actual `WorkingDirectory` is
> `/home/ubuntu/stockbot/dashboard`, not `/home/ubuntu/stock_bot_v4/dashboard`
> as shown above — an earlier deploy predates the `stock_bot_v4` rename/clone
> and the unit was never repointed. `/home/ubuntu/stock_bot_v4` is a separate
> checkout of this same repo and is what the buy/GTT cron jobs (`main.py`,
> `main_gtt.py`) actually run from. Both checkouts are kept up to date with
> `origin/main`, but when deploying dashboard changes, confirm which directory
> the live unit points at first — run:
> `sudo systemctl show -p WorkingDirectory stockbot-dashboard`
> — rather than assuming it matches this guide.

Note `--server.address 127.0.0.1` — Streamlit is bound to localhost, reachable
only by Caddy, never directly from the internet.

---

## Step 6 — Access

Open `https://raramdas-stockbot.duckdns.org` on any device including mobile.
You'll get the login screen; sign in with your DASH_USERS credentials.

---

## Security summary

| Layer | Protection |
|---|---|
| Transport | HTTPS (Let's Encrypt), auto-renewed by Caddy |
| Streamlit exposure | Bound to localhost only, never public |
| App login | Username/password from .env, 5-attempt lockout for 5 min |
| Ports open | Only 80 + 443 (not 8501) |
| DB credentials | In .env, never exposed to the browser |

### Recommended extras (optional, higher security)
- Use **distinct, strong passwords** per user (not shared)
- Restrict OCI ingress on 443 to known IP ranges if you only use a few networks
- Rotate the DASH_USERS passwords periodically
- Consider adding Caddy Basic Auth as a second gate before the app login:
  add `basicauth { username HASHED_PASSWORD }` inside the Caddyfile block
  (generate hash with `caddy hash-password`)

---

## Managing the service

```bash
# View logs
sudo journalctl -u stockbot-dashboard -f
sudo journalctl -u caddy -f

# Restart after code changes
sudo systemctl restart stockbot-dashboard

# Stop / start
sudo systemctl stop stockbot-dashboard
sudo systemctl start stockbot-dashboard
```
