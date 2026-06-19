OUT=/root/hft3/repo/research_cards/pipeline_runs/paid_full_v2_20260618T235532Z
LOG=/root/vbt_full_v2.log
MANIFEST="$OUT/paid_screen_run_manifest.json"
ORCH="$OUT/orchestrator.log"
poll_once() {
  n="$1"
  echo "========== POLL $n $(date -u +%Y-%m-%dT%H:%M:%SZ) =========="
  tmux has-session -t vbt_full_v2 2>/dev/null && echo tmux:ALIVE || echo tmux:DEAD
  pgrep -af run_paid_screen.py || echo none:run_paid_screen
  pgrep -af run_vectorbt_paid_screen_v2.py || echo none:run_vectorbt_v2
  echo PaidScreenWorker_count:$(pgrep -cf PaidScreenWorker || echo 0)
  echo paid_screen_procs:$(pgrep -af paid_screen | wc -l)
  cat /proc/loadavg
  if test -f "$MANIFEST"; then
    echo manifest:EXISTS
    python3 -m json.tool "$MANIFEST" 2>/dev/null | head -35
  else
    echo manifest:MISSING
    ls -la "$OUT" 2>/dev/null | head -12
  fi
  if test -f "$ORCH"; then
    echo orchestrator_bytes:$(wc -c < "$ORCH")
    tail -n 6 "$ORCH"
  else
    echo orchestrator:MISSING
  fi
  tail -n 6 "$LOG" 2>/dev/null
  find "$OUT" -maxdepth 3 -name '*heartbeat*' 2>/dev/null | head -5
  grep -r heartbeat "$OUT" 2>/dev/null | wc -l | xargs echo heartbeat_lines:
  grep -iE 'fatal|traceback|CRITICAL' "$LOG" 2>/dev/null | tail -3
  grep -riE 'fatal|traceback|CRITICAL' "$OUT" 2>/dev/null | tail -3
  echo ""
}
i=1
while test $i -le 10; do
  poll_once $i
  if test $i -lt 10; then sleep 60; fi
  i=$((i+1))
done