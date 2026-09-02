#!/usr/bin/env bash
#
# sample_warp_memory.sh — record warp-svc memory, restarts and swap.
#
# warp-svc leaks. It is capped at MemoryMax=300M so a leak restarts one unit
# instead of triggering the global OOM that took the box down on 2026-08-26,
# but the leak RATE is only loosely known: about 30 MB/day (73 MB just after
# the 2026-08-29 restart, ~190 MB four days later). RSS also swings tens of MB
# between consecutive reads, which is enough for a single sample to mislead —
# a "140 MB in three hours" reading was one such misreading, taken by
# comparing against a restart four days earlier rather than that morning.
# The interesting events — the approach to the cap, the restart, whether the
# 09:30 scrape survives one — all happen while nobody is looking.
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
