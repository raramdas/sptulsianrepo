#!/usr/bin/env bash
#
# warp_keeper.sh — keep the WARP tunnel actually usable, not merely running.
#
# WHY A RESTART AND NOT A RECONNECT
#
# warp-svc degrades as it leaks. On 2026-09-18 it sat at 230 MB with 1,683
# tunnel-disconnect lines in one day, and `warp-cli status` reported
# "Connected / Network healthy" while the SOCKS proxy still returned HTTP 000.
# `warp-cli connect` did not fix it; `systemctl restart warp-svc` did, and
# memory fell 230 MB -> 75 MB with the proxy healthy three seconds later.
#
# So connectivity is checked by USE, not by status. The daemon's own opinion
# of its health has been wrong every time it mattered:
#
#   systemctl is-active   says the process exists
#   warp-cli status       says the tunnel thinks it is up
#   a real fetch          says whether the scraper can actually work
#
# Only the third is trusted here.
#
# Every incident in this system's history correlates with high warp memory —
# 2026-08-26 (OOM took the box down), 09-04 (tunnel died, four dark days),
# 09-17, 09-18. Recycling before it degrades is cheaper than detecting each
# failure afterwards: the scraper needs the proxy for about a minute a day, so
# a 3-second restart costs nothing.
#
#   warp_keeper.sh            restart if unhealthy or over the memory ceiling
#   warp_keeper.sh --check    report only, exit 1 if it would act
#   warp_keeper.sh --force    restart regardless
set -uo pipefail

LOG=/home/ubuntu/warp_keeper.log
MEM_CEILING_MB=180          # well under MemoryMax=300M, where degradation starts
PROBE_URL=https://www.sptulsian.com/
PROXY=127.0.0.1:40000
MODE="${1:-}"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" >> "$LOG"; }

mem_mb() {
    local b
    b=$(systemctl show warp-svc -p MemoryCurrent --value 2>/dev/null)
    [[ "$b" =~ ^[0-9]+$ ]] || { echo 0; return; }
    echo $(( b / 1048576 ))
}

# The only health signal that has ever been reliable: can we actually fetch?
proxy_works() {
    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 \
           --socks5-hostname "$PROXY" "$PROBE_URL" 2>/dev/null)
    [ "$code" = "200" ]
}

MEM=$(mem_mb)
REASON=""

if [ "$MODE" = "--force" ]; then
    REASON="forced"
elif ! proxy_works; then
    REASON="proxy cannot reach the portal"
elif [ "$MEM" -ge "$MEM_CEILING_MB" ]; then
    # Pre-emptive. The proxy still works at this point; recycling now avoids
    # the degraded state rather than waiting to detect it.
    REASON="memory ${MEM}MB >= ${MEM_CEILING_MB}MB ceiling"
fi

if [ -z "$REASON" ]; then
    [ "$MODE" = "--check" ] && echo "healthy: proxy OK, memory ${MEM}MB"
    exit 0
fi

if [ "$MODE" = "--check" ]; then
    echo "would restart warp-svc: $REASON (memory ${MEM}MB)"
    exit 1
fi

log "restarting warp-svc: $REASON (memory ${MEM}MB)"
sudo systemctl restart warp-svc

for i in $(seq 1 25); do
    sleep 3
    if proxy_works; then
        log "  healthy after ~$((i * 3))s, memory now $(mem_mb)MB"
        exit 0
    fi
done

# Deliberately quiet on failure: spt_watchdog.py owns alerting, and a second
# channel shouting about the same fault trains people to ignore both.
log "  STILL UNHEALTHY after restart — spt_watchdog.py will alert"
exit 1
