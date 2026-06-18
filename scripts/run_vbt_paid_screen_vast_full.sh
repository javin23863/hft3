#!/usr/bin/env bash
# Phase D full VectorBT paid screen on Vast 256 vCPU (230 workers).
# Authority: docs/project/VBT_PAID_SCREEN_UNIT_SCOPE.md
# Run ON the Vast instance (NPZ lake already present). Do not use 4-worker smoke topology.
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

SURVIVORS="${VBT_STAGE_A_SURVIVORS:-research_cards/stage_a_full/stage_a_survivors.json}"
EVENTS_CSV="${VBT_EVENTS_CSV:-packages/data_system/config/events.csv}"
UNITS_JSONL="${VBT_FULL_UNITS_JSONL:-runtime/reports/vbt_full_units.jsonl}"
GATE_FILE="${VBT_READY_GATE_FILE:-runtime/reports/paid_screen_ready_gate.json}"
SYMBOLS="${VBT_SYMBOLS:-MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0}"

NPROC="$(nproc)"
if [[ -n "${VBT_WORKERS:-}" ]]; then
  WORKERS="$VBT_WORKERS"
elif [[ "$NPROC" -ge 256 ]]; then
  WORKERS=230
else
  WORKERS=$((NPROC - 26))
  if [[ "$WORKERS" -lt 1 ]]; then WORKERS=1; fi
fi

if [[ ! -f "$SURVIVORS" ]]; then
  echo "ERROR: Stage A survivors missing: $SURVIVORS" >&2
  echo "Full scope requires research_cards/stage_a_full/stage_a_survivors.json (not CPI+NFP smoke)." >&2
  exit 1
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
echo "survivors=$SURVIVORS symbols=$SYMBOLS"

bash scripts/install_vbt_hbt_handoff_verify_deps.sh
pip3 install 'vectorbt[rust]==1.0.0' -q

python3 scripts/generate_vbt_paid_units_jsonl.py \
  --from-stage-a-survivors "$SURVIVORS" \
  --events-csv "$EVENTS_CSV" \
  --symbols "$SYMBOLS" \
  --out "$UNITS_JSONL"

UNIT_COUNT="$(grep -c . "$UNITS_JSONL" || true)"
echo "Full unit count: $UNIT_COUNT (must match declaration expected_work_units)"

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
