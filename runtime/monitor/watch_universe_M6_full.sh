#!/usr/bin/env bash
set -uo pipefail
LOG="${LOG:-/root/hft3/repo/runtime/universe_M6_full_20260618T043132Z.log}"
OUT="${OUT:-/root/hft3/repo/research_cards/universe_M6_full}"
SESSION="${SESSION:-universe_M6_full}"
echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) universe_M6_full watch ==="
echo "--- process ---"
pgrep -af 'scripts/run_event_universe' || echo "NO run_event_universe process"
echo "python_count=$(pgrep -c python 2>/dev/null || echo 0)"
echo "--- tmux ---"
tmux has-session -t "$SESSION" 2>&1 || true
echo "--- resources ---"
uptime
free -h | head -2
df -h / /data 2>/dev/null | tail -n +2
echo "--- log ---"
if [[ -f "$LOG" ]]; then
  wc -l "$LOG"
  grep 'Work units:' "$LOG" | tail -1 || true
  tail -n 8 "$LOG"
  echo "tracebacks=$(grep -c Traceback "$LOG" 2>/dev/null || echo 0)"
  echo "blocking_io=$(grep -c BlockingIOError "$LOG" 2>/dev/null || echo 0)"
  grep -E 'ERROR|FAIL|Traceback|OOM|killed|Killed|MemoryError|BlockingIOError|pthread_create failed' "$LOG" | tail -n 10 || true
else
  echo "MISSING LOG: $LOG"
fi
echo "--- artifacts ---"
if [[ -d "$OUT" ]]; then
  echo "files=$(find "$OUT" -type f 2>/dev/null | wc -l)"
  du -sh "$OUT" 2>/dev/null || true
  wc -c "$OUT/unit_results.jsonl" 2>/dev/null || true
  ls -lt "$OUT" 2>/dev/null | head -5
else
  echo "MISSING OUT: $OUT"
fi
echo "--- lake ---"
[[ -d /data/npz ]] && echo "npz_files=$(find /data/npz -maxdepth 1 -type f 2>/dev/null | wc -l)" || echo "NO /data/npz"
cd /root/hft3/repo 2>/dev/null && git rev-parse --short HEAD 2>/dev/null || true
echo "=== end ==="
