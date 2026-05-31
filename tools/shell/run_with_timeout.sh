#!/usr/bin/env bash
# Run a command with a hard wall-clock timeout. Exit 124 on timeout.
set -euo pipefail

usage() {
  echo "Usage: run_with_timeout.sh SECONDS LABEL -- command [args...]" >&2
  exit 2
}

[[ $# -ge 3 ]] || usage
TIMEOUT_SEC="$1"
LABEL="$2"
shift 2
[[ "${1:-}" == "--" ]] || usage
shift

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

echo "[$LABEL] start (budget ${TIMEOUT_SEC}s): $*"
START=$(date +%s)

if command -v timeout >/dev/null 2>&1; then
  timeout --kill-after=10 "$TIMEOUT_SEC" "$@" 
  EC=$?
  if [[ $EC -eq 124 ]]; then
    echo "[$LABEL] TIMEOUT after ${TIMEOUT_SEC}s" >&2
  fi
  exit $EC
fi

# macOS fallback without GNU timeout
"$@" &
PID=$!
while kill -0 "$PID" 2>/dev/null; do
  NOW=$(date +%s)
  ELAPSED=$((NOW - START))
  if [[ $ELAPSED -ge $TIMEOUT_SEC ]]; then
    echo "[$LABEL] TIMEOUT after ${TIMEOUT_SEC}s — killing $PID" >&2
    kill -TERM "$PID" 2>/dev/null || true
    sleep 2
    kill -KILL "$PID" 2>/dev/null || true
    exit 124
  fi
  sleep 1
done
wait "$PID"
EC=$?
ELAPSED=$(($(date +%s) - START))
echo "[$LABEL] done in ${ELAPSED}s (exit $EC)"
exit $EC
