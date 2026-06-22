#!/usr/bin/env bash
# Resume crashed VectorBT paid-full run (same --out skips OK_CACHED units).
set -euo pipefail
cd /root/hft3/repo
export HFT3_NPZ_ROOT="${HFT3_NPZ_ROOT:-/data/npz}"
GATE="${VBT_READY_GATE_FILE:-runtime/reports/paid_screen_ready_gate.json}"
UNITS="${VBT_FULL_UNITS_JSONL:-runtime/reports/vbt_full_units.jsonl}"
WORKERS="${VBT_WORKERS:-230}"
STATUS_FILE="${VBT_STATUS_FILE:-runtime/reports/vbt_full_status.json}"
DECL_FILE="${VBT_FULL_RUN_DECLARATION:-runtime/reports/vbt_full_run_declaration.json}"

die() {
  echo "FATAL: $*" >&2
  exit 2
}

metadata_field() {
  local key="$1"
  local file
  for file in "$STATUS_FILE" "$DECL_FILE"; do
    [[ -f "$file" ]] || continue
    python3 - "$file" "$key" <<'PY' 2>/dev/null || true
import json
import sys

path, key = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as handle:
    value = json.load(handle).get(key)
if value in (None, ""):
    raise SystemExit(1)
print(value)
PY
  done | head -n 1
}

metadata_first() {
  local key
  local value
  for key in "$@"; do
    value="$(metadata_field "$key")"
    if [[ -n "$value" ]]; then
      printf '%s\n' "$value"
      return 0
    fi
  done
  return 1
}

require_value() {
  local name="$1"
  local value="$2"
  local hint="$3"
  [[ -n "$value" ]] || die "$name is required; $hint"
  printf '%s\n' "$value"
}

RUN_ID="$(require_value VBT_FULL_RUN_ID "${VBT_FULL_RUN_ID:-$(metadata_first configured_run_id run_id 2>/dev/null || true)}" "set VBT_FULL_RUN_ID or provide current $STATUS_FILE/$DECL_FILE metadata")"
SESSION="$(require_value VBT_TMUX_SESSION "${VBT_TMUX_SESSION:-$(metadata_first tmux_session 2>/dev/null || true)}" "set VBT_TMUX_SESSION or provide current $STATUS_FILE/$DECL_FILE metadata")"

if [[ ! -f "$GATE" ]]; then
  echo "FATAL: missing ready gate $GATE" >&2
  exit 2
fi
if [[ ! -f "$UNITS" ]]; then
  echo "FATAL: missing units jsonl $UNITS" >&2
  exit 2
fi

export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
export PYTHONPATH="/root/hft3/repo:/root/hft3/repo/packages"

tmux kill-session -t "$SESSION" 2>/dev/null || true
LOG="research_cards/pipeline_runs/${RUN_ID}/orchestrator.log"
mkdir -p "research_cards/pipeline_runs/${RUN_ID}"
CMD="export HFT3_NPZ_ROOT=\"${HFT3_NPZ_ROOT}\" VBT_FULL_RUN_ID=\"${RUN_ID}\"; cd /root/hft3/repo; python3 scripts/run_vectorbt_paid_screen.py --units-jsonl \"${UNITS}\" --out \"research_cards/pipeline_runs/${RUN_ID}\" --vectorbt-scope paid-compute --workers \"${WORKERS}\" --ready-gate-file \"${GATE}\" --max-wall-clock-seconds 86400 --no-llm 2>&1 | tee -a \"${LOG}\""
tmux new-session -d -s "$SESSION" "bash -lc $(printf '%q' "$CMD")"
echo "Started tmux $SESSION run_id=$RUN_ID workers=$WORKERS"
tmux ls
