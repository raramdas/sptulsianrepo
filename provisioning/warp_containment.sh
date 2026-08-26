#!/bin/bash
# Contain the warp-svc leak so it can never take the box down again.
set -euo pipefail

echo "###### 1. SWAP (2GB, currently zero) ######"
if [ -f /swapfile ]; then
  echo "  /swapfile already exists — skipping creation"
else
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  echo "  created and enabled, persisted in /etc/fstab"
fi
# Swap is an emergency cushion, not a working tier — keep the kernel off it
# until it genuinely needs it.
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swappiness.conf >/dev/null
sudo sysctl -q -w vm.swappiness=10
free -m | grep -i swap

echo
echo "###### 2. CONTAIN warp-svc ######"
sudo mkdir -p /etc/systemd/system/warp-svc.service.d
sudo tee /etc/systemd/system/warp-svc.service.d/10-memory.conf >/dev/null <<'CONF'
# warp-svc leaks: OOM-killed 2026-08-18/20/22/24, ~250MB RSS each time, on a
# 956MB box. Unbounded, its growth triggers a GLOBAL OOM where the kernel
# picks victims anywhere — on 2026-08-26 that took out sshd and Caddy and the
# VM needed a force reboot from the OCI console.
#
# MemoryMax turns that into a local failure: systemd kills only this unit and
# restarts it. The scraper needs the proxy for about a minute a day, so a
# restart is invisible to the workload. Steady state is ~70-90MB.
[Service]
MemoryHigh=220M
MemoryMax=300M
Restart=always
RestartSec=5s
CONF

echo "  drop-in written:"
sudo cat /etc/systemd/system/warp-svc.service.d/10-memory.conf | grep -E "^(Memory|Restart)"

echo
echo "###### 3. CAP JOURNALD (632MB, uncapped; warp floods it) ######"
sudo sed -i 's/^#\?SystemMaxUse=.*/SystemMaxUse=200M/' /etc/systemd/journald.conf
grep -q '^SystemMaxUse=' /etc/systemd/journald.conf || echo 'SystemMaxUse=200M' | sudo tee -a /etc/systemd/journald.conf >/dev/null
sudo sed -i 's/^#\?RuntimeMaxUse=.*/RuntimeMaxUse=50M/' /etc/systemd/journald.conf
grep -q '^RuntimeMaxUse=' /etc/systemd/journald.conf || echo 'RuntimeMaxUse=50M' | sudo tee -a /etc/systemd/journald.conf >/dev/null
grep -E '^(SystemMaxUse|RuntimeMaxUse)' /etc/systemd/journald.conf

echo
echo "###### 4. APPLY ######"
sudo systemctl daemon-reload
sudo systemctl restart systemd-journald
sudo journalctl --vacuum-size=200M 2>&1 | tail -2
sudo systemctl restart warp-svc
sleep 8

echo
echo "###### 5. VERIFY ######"
sudo systemctl show warp-svc -p MemoryMax -p MemoryHigh -p Restart -p MemoryCurrent
echo -n "warp-svc active: "; sudo systemctl is-active warp-svc
echo "-- journal now --"; sudo journalctl --disk-usage
echo "-- memory --"; free -m
