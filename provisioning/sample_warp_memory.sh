#!/usr/bin/env bash
#
# sample_warp_memory.sh — record warp-svc memory, restarts and swap.
#
# warp-svc leaks. It is capped at MemoryMax=300M so a leak restarts one unit
# instead of triggering the global OOM that took the box down on 2026-08-26,
# but the leak RATE has not been characterised: it looked like ~55 MB/day
# across one three-day window, then ~140 MB in three hours on 2026-09-02.
# A point-in-time reading cannot tell those apart, and the interesting events
# — the approach to the cap, the restart, whether the 09:30 scrape survives it
# — all happen while nobody is looking.
#
# Appends one CSV line per run. Deliberately trivial: no dependencies, no
# database, nothing that can itself fail in an interesting way.
#
#   ts_utc,rss_bytes,rss_mb,nrestarts,swap_used_mb,mem_avail_mb,uptime_s
set -uo pipefail

OUT="${1:-/home/ubuntu/warp_memory.csv}"

[ -f "$OUT" ] || echo "ts_utc,rss_bytes,rss_mb,nrestarts,swap_used_mb,mem_avail_mb,uptime_s" > "$OUT"

rss=$(systemctl show warp-svc -p MemoryCurrent --value 2>/dev/null)
[ -z "$rss" ] || [ "$rss" = "[not set]" ] && rss=0
nres=$(systemctl show warp-svc -p NRestarts --value 2>/dev/null || echo 0)

read -r swap_used mem_avail < <(free -m | awk '/^Swap:/{s=$3} /^Mem:/{a=$7} END{print s, a}')

# Seconds since the unit last (re)started — the restart itself is the event we
# most want dated, and this survives a log rotation in a way journald may not.
started=$(systemctl show warp-svc -p ActiveEnterTimestampMonotonic --value 2>/dev/null || echo 0)
now_mono=$(awk '{printf "%d", $1 * 1000000}' /proc/uptime)
uptime_s=$(( (now_mono - ${started:-0}) / 1000000 ))
[ "$uptime_s" -lt 0 ] && uptime_s=0

printf '%s,%s,%s,%s,%s,%s,%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$rss" \
    "$(( ${rss:-0} / 1048576 ))" \
    "${nres:-0}" \
    "${swap_used:-0}" \
    "${mem_avail:-0}" \
    "$uptime_s" >> "$OUT"
