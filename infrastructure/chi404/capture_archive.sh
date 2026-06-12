#!/usr/bin/env bash
# Archive closed-trade-date Rithmic capture files to B2 and prune local copies.
#
# For every .cap whose trade date < today's CME trade date (i.e. the file is
# closed and will never be appended again):
#   1. zstd-compress next to it (idempotent: skip if .cap.zst exists)
#   2. rclone copy the .cap.zst (and its manifest) to b2 Hft3repo/capture/rithmic/
#   3. verify remote size matches
#   4. delete the local .cap.zst immediately; delete the raw .cap only when the
#      trade date is older than RETAIN_DAYS (default 30)
#
# Safe to re-run; designed for a daily systemd timer (hft3-capture-archive).
set -euo pipefail

CAPTURE_ROOT="${CAPTURE_ROOT:-/root/hft3/data/capture}"
REMOTE="${ARCHIVE_REMOTE:-hft3-b2:Hft3repo/capture/rithmic}"
RETAIN_DAYS="${RETAIN_DAYS:-30}"
LOG="/root/hft3/logs/capture/archive.log"

mkdir -p "$(dirname "$LOG")"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }

command -v zstd >/dev/null || { log "FATAL zstd missing"; exit 1; }
command -v rclone >/dev/null || { log "FATAL rclone missing"; exit 1; }

# Current CME trade date: after 17:00 CT the trade date is tomorrow.
today_trade_date=$(TZ=America/Chicago date +%F)
if [ "$(TZ=America/Chicago date +%H)" -ge 17 ]; then
    today_trade_date=$(TZ=America/Chicago date -d "+1 day" +%F)
fi
log "run start; current trade date $today_trade_date; retain ${RETAIN_DAYS}d"

archived=0 pruned=0 skipped=0 failed=0
shopt -s nullglob
for cap in "$CAPTURE_ROOT"/*/*.cap; do
    base=$(basename "$cap")                       # SYM_YYYY-MM-DD.cap
    sym_dir=$(basename "$(dirname "$cap")")
    fdate=${base##*_}; fdate=${fdate%.cap}
    # only well-formed dates, and only closed trade dates
    [[ "$fdate" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || { skipped=$((skipped+1)); continue; }
    [[ "$fdate" < "$today_trade_date" ]] || { skipped=$((skipped+1)); continue; }

    zst="$cap.zst"
    if [ ! -f "$zst" ]; then
        zstd -q -T0 -10 "$cap" -o "$zst.tmp" && mv "$zst.tmp" "$zst"
    fi

    dest="$REMOTE/$sym_dir"
    if rclone copyto "$zst" "$dest/$(basename "$zst")" --b2-hard-delete=false 2>>"$LOG"; then
        local_sz=$(stat -c%s "$zst")
        remote_sz=$(rclone lsl "$dest/$(basename "$zst")" 2>/dev/null | awk '{print $1}' | head -1)
        if [ "$local_sz" = "$remote_sz" ]; then
            # manifest rides along (tiny)
            man="${cap%.cap}.manifest.json"
            [ -f "$man" ] && rclone copyto "$man" "$dest/$(basename "$man")" 2>>"$LOG" || true
            rm -f "$zst"
            archived=$((archived+1))
            # prune raw .cap past retention
            cutoff=$(date -d "-${RETAIN_DAYS} days" +%F)
            if [[ "$fdate" < "$cutoff" ]]; then
                rm -f "$cap"
                pruned=$((pruned+1))
            fi
        else
            log "VERIFY FAIL $zst local=$local_sz remote=$remote_sz"
            failed=$((failed+1))
        fi
    else
        log "UPLOAD FAIL $zst"
        failed=$((failed+1))
    fi
done

log "run done archived=$archived pruned=$pruned skipped=$skipped failed=$failed"
[ "$failed" -eq 0 ]
