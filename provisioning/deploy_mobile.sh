#!/usr/bin/env bash
# Run from an already pulled, clean checkout. Does not execute trading jobs.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXPECTED_ROOT=/home/ubuntu/stockbot
if [[ "$ROOT" != "$EXPECTED_ROOT" ]]; then
  echo "Run this from the dashboard checkout: $EXPECTED_ROOT" >&2; exit 1
fi
if [[ -n "$(git -C "$ROOT" status --porcelain)" ]]; then
  echo 'Refusing to deploy a dirty checkout.' >&2; exit 1
fi
python3 -m venv --system-site-packages /home/ubuntu/.venvs/capital-ledger
/home/ubuntu/.venvs/capital-ledger/bin/pip install -r "$ROOT/dashboard/mobile-requirements.txt"
# No secrets are printed; reject insecure default sign-in settings.
/home/ubuntu/.venvs/capital-ledger/bin/python - <<'PY'
from dotenv import load_dotenv
import os
load_dotenv('/home/ubuntu/.env')
assert os.getenv('DASH_USERS') and 'admin:changeme' not in os.getenv('DASH_USERS',''), 'Configure DASH_USERS before deploying'
assert len(os.getenv('DASH_SESSION_SECRET','')) >= 24 and os.getenv('DASH_SESSION_SECRET') != 'insecure-default-change-me', 'Configure a strong session secret'
import oracledb, pandas, pytz, requests, pyotp, streamlit
PY
# Validate routing before changing the existing HTTPS configuration.
sudo caddy validate --config "$ROOT/provisioning/mobile.Caddyfile" --adapter caddyfile
BACKUP="/home/ubuntu/.local/state/capital-ledger/deploy-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP"
sudo cp /etc/caddy/Caddyfile "$BACKUP/Caddyfile"
if [[ -f /etc/systemd/system/stockbot-dashboard.service.d/mobile.conf ]]; then
  sudo cp /etc/systemd/system/stockbot-dashboard.service.d/mobile.conf "$BACKUP/mobile.conf"
fi
rollback() {
  echo 'Deployment check failed; restoring previous routing.' >&2
  sudo cp "$BACKUP/Caddyfile" /etc/caddy/Caddyfile
  if [[ -f "$BACKUP/mobile.conf" ]]; then
    sudo cp "$BACKUP/mobile.conf" /etc/systemd/system/stockbot-dashboard.service.d/mobile.conf
  else
    sudo rm -f /etc/systemd/system/stockbot-dashboard.service.d/mobile.conf
  fi
  sudo systemctl daemon-reload
  sudo systemctl restart stockbot-dashboard
  sudo systemctl reload caddy
}
trap rollback ERR
sudo install -m 644 "$ROOT/provisioning/systemd/capital-ledger-mobile.service" /etc/systemd/system/capital-ledger-mobile.service
sudo systemctl daemon-reload
sudo systemctl enable --now capital-ledger-mobile
sudo systemctl restart capital-ledger-mobile
for i in {1..20}; do
  if curl --fail --silent http://127.0.0.1:8502/health >/dev/null; then break; fi
  sleep 1
done
curl --fail --silent http://127.0.0.1:8502/health
sudo mkdir -p /etc/systemd/system/stockbot-dashboard.service.d
sudo install -m 644 "$ROOT/provisioning/systemd/stockbot-classic.conf" /etc/systemd/system/stockbot-dashboard.service.d/mobile.conf
sudo systemctl daemon-reload
sudo systemctl restart stockbot-dashboard
for i in {1..20}; do
  if curl --fail --silent http://127.0.0.1:8501/classic/_stcore/health >/dev/null; then break; fi
  sleep 1
done
curl --fail --silent http://127.0.0.1:8501/classic/_stcore/health
sudo install -m 644 "$ROOT/provisioning/mobile.Caddyfile" /etc/caddy/Caddyfile
sudo systemctl reload caddy
curl --fail --silent https://raramdas-stockbot.duckdns.org/health
trap - ERR
printf '\nMobile app deployed. Previous configuration: %s\n' "$BACKUP"
