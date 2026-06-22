#!/usr/bin/env bash
# Phase D full VectorBT paid screen on Vast (v2 long-lived workers in tmux).
# Authority: docs/project/VBT_PAID_SCREEN_UNIT_SCOPE.md, PAID_SCREEN_OPS_COMMANDS.md
# Run ON the Vast instance (NPZ lake already present). Do not use 4-worker smoke topology.
# Units are generated on-host from events.csv + active model registry (not local Stage A survivors).
# v2 env knobs: VBT_CACHE_MEMORY_LIMIT_MB, VBT_CACHE_MAX_ENTRIES, VBT_MAX_BATCHES_BEFORE_RECYCLE, VBT_RESUME=1
# v2 provenance: passes --events-csv + derived --events-csv-hash; lake hash from HFT3_MANIFEST_PATH
# (sha256 file content) or declaration lake_manifest_hash — fail-closed before v2 launch if unavailable.
# tmux wrapper: survives SSH disconnect — the run keeps going after you log out.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Load owner env if present (HFT3_NPZ_ROOT, HFT3_MANIFEST_PATH, …)
if [[ -f "${HFT3_ENV_FILE:-/root/hft3/.env}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${HFT3_ENV_FILE:-/root/hft3/.env}"
  set +a
fi

# Gate-aligned lake manifest (parquet hash); override only when owner sets explicitly.
export HFT3_MANIFEST_PATH="${HFT3_MANIFEST_PATH:-/data/npz/manifest.parquet}"

EVENTS_CSV="${VBT_EVENTS_CSV:-packages/data_system/config/events.csv}"
UNITS_JSONL="${VBT_FULL_UNITS_JSONL:-runtime/reports/vbt_full_units.jsonl}"
GATE_FILE="${VBT_READY_GATE_FILE:-runtime/reports/paid_screen_ready_gate.json}"
SYMBOLS="${VBT_SYMBOLS:-MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0}"
MODEL_SCOPE="${VBT_MODEL_SCOPE:-active}"
MODEL_IDS="${VBT_MODEL_IDS:-}"
EVENT_TYPES="${VBT_EVENT_TYPES:-}"
RESEARCH_SPLIT="${VBT_RESEARCH_SPLIT:-discovery_confirmation}"
DECL_FILE="${VBT_FULL_RUN_DECLARATION:-runtime/reports/vbt_full_run_declaration.json}"
TMUX_SESSION="${VBT_TMUX_SESSION:-vbt_full}"

NPROC="$(nproc)"
if [[ -n "${VBT_WORKERS:-}" ]]; then
  WORKERS="$VBT_WORKERS"
elif [[ "$NPROC" -ge 256 ]]; then
  WORKERS=230
else
  WORKERS=$((NPROC - 26))
  if [[ "$WORKERS" -lt 1 ]]; then WORKERS=1; fi
fi

if [[ ! -f "$GATE_FILE" ]]; then
  echo "ERROR: Ready gate missing: $GATE_FILE (run validate_paid_screen_ready_gate.py first)" >&2
  exit 1
fi

if [[ -z "${HFT3_NPZ_ROOT:-}" ]]; then
  echo "ERROR: HFT3_NPZ_ROOT unset — NPZ lake must be on this host (Vast data from prior sync)." >&2
  exit 1
fi

echo "=== Vast VectorBT paid screen ==="
echo "repo=$REPO_ROOT nproc=$NPROC workers=$WORKERS npz_root=$HFT3_NPZ_ROOT"
echo "events_csv=$EVENTS_CSV symbols=$SYMBOLS model_scope=$MODEL_SCOPE units_out=$UNITS_JSONL"

bash scripts/install_vbt_hbt_handoff_verify_deps.sh
pip3 install 'vectorbt[rust]==1.0.0' -q

# --- Unit generation (all-active-models + research_split + require-runnable-npz) ---
GEN_ARGS=(
  python3 scripts/generate_vbt_paid_units_jsonl.py
  --events-csv "$EVENTS_CSV"
  --symbols "$SYMBOLS"
  --out "$UNITS_JSONL"
)

if [[ -n "$MODEL_IDS" ]]; then
  GEN_ARGS+=(--model-ids "$MODEL_IDS")
elif [[ "$MODEL_SCOPE" == "active" ]]; then
  GEN_ARGS+=(--all-active-models)
else
  GEN_ARGS+=(--model-id "${VBT_MODEL_ID:-SPREAD_BLOWOUT_RECOMPRESSION}")
fi

if [[ -n "$EVENT_TYPES" ]]; then
  GEN_ARGS+=(--event-types "$EVENT_TYPES")
fi

GEN_ARGS+=(--research-split "$RESEARCH_SPLIT")

if [[ "${VBT_REQUIRE_RUNNABLE_NPZ:-1}" == "1" || "${VBT_REQUIRE_RUNNABLE_NPZ:-1}" == "true" ]]; then
  GEN_ARGS+=(--require-runnable-npz)
fi

"${GEN_ARGS[@]}"

UNIT_COUNT="$(grep -c . "$UNITS_JSONL" || true)"
echo "Full unit count: $UNIT_COUNT (must match declaration expected_work_units)"

# --- Declaration check ---
if [[ ! -f "$DECL_FILE" ]]; then
  echo "ERROR: Full-run declaration missing: $DECL_FILE" >&2
  echo "Generate units on-host, record expected_work_units, then rerun." >&2
  exit 1
fi

DECL_EXPECTED="$(python3 - "$DECL_FILE" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["expected_work_units"])
PY
)"

if [[ "$DECL_EXPECTED" != "$UNIT_COUNT" ]]; then
  echo "WARNING: Declaration expected_work_units=$DECL_EXPECTED != generated unit count=$UNIT_COUNT" >&2
  echo "Updating declaration to match generated count." >&2
fi

# --- Write/update declaration ---
EVENTS_HASH="$(python3 -c "import hashlib; print(hashlib.sha256(open('$EVENTS_CSV','rb').read()).hexdigest()[:32])")"
LAKE_HASH="$(python3 -c "
import hashlib, os
p=os.environ.get('HFT3_MANIFEST_PATH','/data/npz/manifest.parquet')
if os.path.isfile(p):
    print(hashlib.sha256(open(p,'rb').read()).hexdigest()[:32])
else:
    print('unknown')
")"

cat > "$DECL_FILE" << EOF
{
  "host_vcpu": $NPROC,
  "reserved_vcpu": 26,
  "workers_requested": $WORKERS,
  "expected_work_units": $UNIT_COUNT,
  "units_source": "Vast on-host: events TIGHT x CME M6 x active models, --require-runnable-npz (NPZ pre-filter)",
  "stall_minutes": 30,
  "abort_on_failed_units": false,
  "git_head": "$(git rev-parse HEAD 2>/dev/null || echo unknown)",
  "events_csv_hash": "$EVENTS_HASH",
  "lake_manifest_hash": "$LAKE_HASH",
  "smoke_units_per_hour": 89.7823
}
EOF
echo "Declaration written: $DECL_FILE (expected_work_units=$UNIT_COUNT)"

# --- Launch v2 runner inside tmux (survives SSH disconnect) ---
export VBT_FULL_RUN_ID="${VBT_FULL_RUN_ID:-paid_full_$(date -u +%Y%m%dT%H%M%SZ)}"
OUT_DIR="${REPO_ROOT}/research_cards/pipeline_runs/${VBT_FULL_RUN_ID}"
LOG_FILE="${OUT_DIR}/orchestrator.log"
mkdir -p "$OUT_DIR"

# Kill any existing tmux session with the same name
tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true

echo "Starting v2 runner in tmux session=$TMUX_SESSION run_id=$VBT_FULL_RUN_ID workers=$WORKERS"
echo "Run: tmux attach -t $TMUX_SESSION  (Ctrl-B D to detach)"

tmux new-session -d -s "$TMUX_SESSION" bash -lc "
cd '$REPO_ROOT'
export HFT3_NPZ_ROOT='$HFT3_NPZ_ROOT'
export HFT3_MANIFEST_PATH='$HFT3_MANIFEST_PATH'
export VBT_FULL_RUN_ID='$VBT_FULL_RUN_ID'
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
python3 scripts/run_vectorbt_paid_screen_v2.py \\
  --units-jsonl '$UNITS_JSONL' \\
  --out '$OUT_DIR' \\
  --vectorbt-scope paid-compute \\
  --workers $WORKERS \\
  --ready-gate-file '$GATE_FILE' \\
  --max-wall-clock-seconds \${VBT_MAX_WALL_CLOCK_SECONDS:-86400} \\
  --no-llm \\
  --resume \\
  --events-csv '$EVENTS_CSV' \\
  --events-csv-hash '$EVENTS_HASH' \\
  --lake-manifest-hash '$LAKE_HASH' \\
  2>&1 | tee -a '$LOG_FILE'
echo 'RUN_FINISHED exit='\$? >> '$LOG_FILE'
"

echo "tmux session '$TMUX_SESSION' started. Attach: tmux attach -t $TMUX_SESSION"
echo "Manifest: ${OUT_DIR}/paid_screen_run_manifest.json"