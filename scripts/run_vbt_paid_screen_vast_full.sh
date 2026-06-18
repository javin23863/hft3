#!/usr/bin/env bash
# Phase D full VectorBT paid screen on Vast 256 vCPU (230 workers).
# Authority: docs/project/VBT_PAID_SCREEN_UNIT_SCOPE.md
# Run ON the Vast instance (NPZ lake already present). Do not use 4-worker smoke topology.
# Units are generated on-host from events.csv + active model registry (not local Stage A survivors).
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

EVENTS_CSV="${VBT_EVENTS_CSV:-packages/data_system/config/events.csv}"
UNITS_JSONL="${VBT_FULL_UNITS_JSONL:-runtime/reports/vbt_full_units.jsonl}"
GATE_FILE="${VBT_READY_GATE_FILE:-runtime/reports/paid_screen_ready_gate.json}"
SYMBOLS="${VBT_SYMBOLS:-MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0}"
MODEL_SCOPE="${VBT_MODEL_SCOPE:-active}"
MODEL_IDS="${VBT_MODEL_IDS:-}"
EVENT_TYPES="${VBT_EVENT_TYPES:-}"
RESEARCH_SPLIT="${VBT_RESEARCH_SPLIT:-discovery_confirmation}"
DECL_FILE="${VBT_FULL_RUN_DECLARATION:-runtime/reports/vbt_full_run_declaration.json}"

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

"${GEN_ARGS[@]}"

UNIT_COUNT="$(grep -c . "$UNITS_JSONL" || true)"
echo "Full unit count: $UNIT_COUNT (must match declaration expected_work_units)"

if [[ ! -f "$DECL_FILE" ]]; then
  echo "ERROR: Full-run declaration missing: $DECL_FILE" >&2
  echo "Generate units on-host, record expected_work_units, then rerun. See docs/project/VBT_PAID_SCREEN_POST_GATE_PLAYBOOK.md (Phase D0)." >&2
  echo "  python3 scripts/generate_vbt_paid_units_jsonl.py ... --out $UNITS_JSONL" >&2
  echo "  wc -l $UNITS_JSONL  # write count to $DECL_FILE as expected_work_units" >&2
  exit 1
fi

DECL_EXPECTED="$(python3 - "$DECL_FILE" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["expected_work_units"])
PY
)"

if [[ "$DECL_EXPECTED" != "$UNIT_COUNT" ]]; then
  echo "ERROR: Declaration expected_work_units=$DECL_EXPECTED != generated unit count=$UNIT_COUNT" >&2
  echo "Regenerate $UNITS_JSONL or update $DECL_FILE before starting workers." >&2
  echo "See docs/project/VBT_PAID_SCREEN_POST_GATE_PLAYBOOK.md (Phase D0)." >&2
  exit 1
fi

echo "Declaration OK: expected_work_units=$DECL_EXPECTED"

export VBT_FULL_RUN_ID="${VBT_FULL_RUN_ID:-paid_full_$(date -u +%Y%m%dT%H%M%SZ)}"
OUT_DIR="${REPO_ROOT}/research_cards/pipeline_runs/${VBT_FULL_RUN_ID}"
LOG_FILE="${OUT_DIR}/orchestrator.log"
mkdir -p "$OUT_DIR"

echo "Starting full run id=$VBT_FULL_RUN_ID workers=$WORKERS out=$OUT_DIR"
python3 scripts/run_vectorbt_paid_screen.py \
  --units-jsonl "$UNITS_JSONL" \
  --out "$OUT_DIR" \
  --vectorbt-scope paid-compute \
  --workers "$WORKERS" \
  --ready-gate-file "$GATE_FILE" \
  --max-wall-clock-seconds "${VBT_MAX_WALL_CLOCK_SECONDS:-86400}" \
  --no-llm \
  2>&1 | tee "$LOG_FILE"

echo "Manifest: ${OUT_DIR}/paid_screen_run_manifest.json"
python3 scripts/aggregate_vbt_promoted_ids.py \
  --manifest "${OUT_DIR}/paid_screen_run_manifest.json" \
  --out runtime/reports/vbt_full_promoted_ids.json
