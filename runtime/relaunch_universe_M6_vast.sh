#!/usr/bin/env bash
set -euo pipefail
SESSION=universe_M6_full
cd /root/hft3/repo || exit 1

tmux kill-session -t "$SESSION" 2>/dev/null || true
pkill -f 'scripts/run_event_universe.py' 2>/dev/null || true
sleep 2
pkill -9 -f 'scripts/run_event_universe.py' 2>/dev/null || true

chmod +x runtime/launch_universe_M6_full.sh
export WORKERS="${WORKERS:-$(($(nproc)-4))}"
echo "launch workers=$WORKERS nproc=$(nproc) ulimit_u=$(ulimit -u)" >&2
tmux new-session -d -s "$SESSION" "export WORKERS=$WORKERS; exec bash /root/hft3/repo/runtime/launch_universe_M6_full.sh"
echo "Started tmux session $SESSION workers=$WORKERS"
tmux ls
