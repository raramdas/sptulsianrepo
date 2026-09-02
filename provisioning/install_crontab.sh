#!/usr/bin/env bash
#
# install_crontab.sh — install provisioning/crontab as the ubuntu user's
# schedule, after showing exactly what would change.
#
# The schedule lived only on the VM until 2026-09-02. A rebuild would have
# lost it silently, and a hand-edit could drift from the documentation with
# nothing to diff against. This makes the repo the source of truth.
#
#   bash provisioning/install_crontab.sh            # diff, confirm, install
#   bash provisioning/install_crontab.sh --check    # diff only, exit 1 if drifted
#   bash provisioning/install_crontab.sh --force    # install without asking
#
# --check is the useful one in CI or a health check: it answers "is the live
# schedule still what the repo says?" without touching anything.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/crontab"
MODE="${1:-}"

[ -f "$SRC" ] || { echo "Missing $SRC"; exit 1; }

LIVE="$(mktemp)"; NEW="$(mktemp)"
trap 'rm -f "$LIVE" "$NEW"' EXIT
crontab -l 2>/dev/null > "$LIVE" || true
cp "$SRC" "$NEW"

# Compare the SCHEDULE, not the prose: comments and blank lines differ freely
# between the repo and whatever was last installed, and a comment-only change
# should not read as schedule drift.
schedule_only() { grep -vE '^\s*(#|$)' "$1" | sed 's/[[:space:]]\+/ /g' | sort; }

if diff -q <(schedule_only "$LIVE") <(schedule_only "$NEW") >/dev/null 2>&1; then
    echo "Live crontab already matches the repo ($(schedule_only "$NEW" | wc -l | tr -d ' ') entries)."
    [ "$MODE" = "--check" ] && exit 0
    # Comments may still have changed; reinstalling is harmless and keeps the
    # documentation on the VM current.
    echo "Reinstalling anyway to sync comments."
else
    echo "SCHEDULE DIFFERS — live vs repo:"
    echo "-------------------------------------------------------------"
    diff <(schedule_only "$LIVE") <(schedule_only "$NEW") \
        | sed 's/^</  live only: /; s/^>/  repo only: /' || true
    echo "-------------------------------------------------------------"
    if [ "$MODE" = "--check" ]; then
        echo "Drift detected. Run without --check to install the repo version."
        exit 1
    fi
fi

if [ "$MODE" != "--force" ] && [ "$MODE" != "--check" ]; then
    read -r -p "Install the repo crontab? [y/N] " reply
    case "$reply" in [yY]*) ;; *) echo "Aborted; nothing changed."; exit 0 ;; esac
fi

BACKUP="/home/ubuntu/crontab.backup.$(date +%Y%m%d-%H%M%S)"
cp "$LIVE" "$BACKUP"
echo "Backed up the live crontab to $BACKUP"

crontab "$NEW"
echo "Installed. Active schedule:"
crontab -l | grep -vE '^\s*(#|$)' | while read -r line; do
    m=${line%% *}; rest=${line#* }; h=${rest%% *}
    job=$(echo "$line" | grep -oE '[a-z_]+\.py|duck\.sh' | tail -1)
    if [ "$h" = "*" ]; then
        printf "  %-9s %-9s %s\n" "$m $h" "(always)" "$job"
    else
        ist=$(TZ=Asia/Kolkata date -d "$(date -u +%Y-%m-%d) $h:$m UTC" '+%H:%M' 2>/dev/null || echo '?')
        printf "  %02d:%02d UTC  %s IST  %s\n" "$h" "$m" "$ist" "$job"
    fi
done
