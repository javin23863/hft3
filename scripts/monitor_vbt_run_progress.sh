#!/usr/bin/env bash
# Poll VBT paid-screen progress with ETA (run on workstation against Vast via SSH).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INTERVAL="${VBT_MONITOR_INTERVAL_SEC:-120}"
MAX_ROUNDS="${VBT_MONITOR_MAX_ROUNDS:-9999}"
STATUS_FILE="${VBT_STATUS_FILE:-$REPO_ROOT/runtime/reports/vbt_full_status.json}"
AUDIT_MODE_ARG="--fast-status"
if [[ "${VBT_MONITOR_FULL_AUDIT:-}" == "1" ]]; then
  AUDIT_MODE_ARG=""
fi

die() {
  echo "FATAL: $*" >&2
  exit 2
}

status_field() {
  local key="$1"
  [[ -f "$STATUS_FILE" ]] || return 1
  python3 - "$STATUS_FILE" "$key" <<'PY'
import json
import sys

path, key = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as handle:
    value = json.load(handle).get(key)
if value in (None, ""):
    raise SystemExit(1)
print(value)
PY
}

require_value() {
  local name="$1"
  local value="$2"
  local hint="$3"
  [[ -n "$value" ]] || die "$name is required; $hint"
  printf '%s\n' "$value"
}

cd "$REPO_ROOT"
SSH_PORT="$(require_value VAST_SSH_PORT "${VAST_SSH_PORT:-}" "set VAST_SSH_PORT (not derivable from $STATUS_FILE)")"
SSH_HOST="$(require_value VAST_SSH_HOST "${VAST_SSH_HOST:-$(status_field ssh_host 2>/dev/null || true)}" "set VAST_SSH_HOST or sync a current $STATUS_FILE with ssh_host")"
RUN_PATTERN="$(require_value VBT_RUN_PATTERN "${VBT_RUN_PATTERN:-$(status_field run_id 2>/dev/null || true)}" "set VBT_RUN_PATTERN or sync a current $STATUS_FILE with run_id")"
TMUX_SESSION="$(require_value VBT_TMUX_SESSION "${VBT_TMUX_SESSION:-$(status_field tmux_session 2>/dev/null || true)}" "set VBT_TMUX_SESSION or sync a current $STATUS_FILE with tmux_session")"
HOST_LABEL="${VBT_HOST_LABEL:-$(status_field host_label 2>/dev/null || printf 'Vast')}"
scp -o ConnectTimeout=15 -P "$SSH_PORT" scripts/audit_vbt_run_progress.py "${SSH_HOST}:/root/hft3/repo/scripts/" >/dev/null

round=0
while [[ "$round" -lt "$MAX_ROUNDS" ]]; do
  round=$((round + 1))
  echo "=== monitor round $round/$(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  ssh -o ConnectTimeout=15 -p "$SSH_PORT" "$SSH_HOST" \
    "cd /root/hft3/repo && export PYTHONPATH=/root/hft3/repo:/root/hft3/repo/packages VBT_HOST_LABEL=\"${HOST_LABEL}\" VAST_SSH_HOST=\"${SSH_HOST}\" VBT_TMUX_SESSION=\"${TMUX_SESSION}\" && python3 scripts/audit_vbt_run_progress.py --pattern \"${RUN_PATTERN}\" ${AUDIT_MODE_ARG}" \
    || true
  scp -o ConnectTimeout=15 -P "$SSH_PORT" \
    "${SSH_HOST}:/root/hft3/repo/runtime/reports/vbt_run_progress_audit.json" \
    "$REPO_ROOT/runtime/reports/vbt_run_progress_audit.json" 2>/dev/null || true
  scp -o ConnectTimeout=15 -P "$SSH_PORT" \
    "${SSH_HOST}:/root/hft3/repo/runtime/reports/vbt_full_status.json" \
    "$REPO_ROOT/runtime/reports/vbt_full_status.json" 2>/dev/null || true
  if [[ "$round" -lt "$MAX_ROUNDS" ]]; then
    sleep "$INTERVAL"
  fi
done
