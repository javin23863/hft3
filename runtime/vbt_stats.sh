#!/usr/bin/env bash
RUN=/root/hft3/repo/research_cards/pipeline_runs/paid_full_v2_20260619T005057Z
LOG=$RUN/orchestrator.log
echo "time_utc=$(date -u +%H:%M:%S)"
echo "ok_batches=$(grep -c 'ok=[1-9]' "$LOG" 2>/dev/null || echo 0)"
echo "zero_ok_batches=$(grep -c 'ok=0 failed=50' "$LOG" 2>/dev/null || echo 0)"
echo "signal_fail=$(grep -c 'signal computer failed' "$LOG" 2>/dev/null || echo 0)"
echo "dict_action=$(grep -c 'dict.*action' "$LOG" 2>/dev/null || echo 0)"
echo "unit_ok=$(grep -c '\[unit\] -> OK' "$LOG" 2>/dev/null || echo 0)"
echo "unit_err=$(grep -c '\[unit\] -> ERROR' "$LOG" 2>/dev/null || echo 0)"
grep 'collected=' "$LOG" | tail -1
grep 'ok=[1-9]' "$LOG" | head -5
echo "log_bytes=$(wc -c < "$LOG")"
pgrep -c python3 || echo 0
tmux has-session -t vbt_full_v2 2>/dev/null && echo tmux=alive || echo tmux=dead
