#!/bin/bash
# deploy.sh — pulls the latest code from GitHub and restarts the dashboard
# if dashboard files changed. Bots (main.py, main_gtt_oracle.py) don't need
# a restart — cron invokes them fresh from disk on every scheduled run, so
# a `git pull` alone is enough for bot code changes to take effect.
#
# Usage: run this ON THE VM, from anywhere:
#   /home/ubuntu/stockbot/deploy.sh

set -e
cd /home/ubuntu/stockbot

echo "=== Pulling latest from GitHub ==="
BEFORE=$(git rev-parse HEAD)
git pull origin main
AFTER=$(git rev-parse HEAD)

if [ "$BEFORE" == "$AFTER" ]; then
    echo "Already up to date. Nothing to deploy."
    exit 0
fi

echo ""
echo "=== Changed files ==="
CHANGED=$(git diff --name-only "$BEFORE" "$AFTER")
echo "$CHANGED"

if echo "$CHANGED" | grep -q "^dashboard/"; then
    echo ""
    echo "Dashboard files changed — restarting service..."
    sudo systemctl restart stockbot-dashboard
    sleep 2
    sudo systemctl status stockbot-dashboard --no-pager
else
    echo ""
    echo "No dashboard files changed — no restart needed."
    echo "(Bot files, if changed, will take effect on their next cron run automatically.)"
fi

echo ""
echo "=== Deploy complete: $BEFORE -> $AFTER ==="
