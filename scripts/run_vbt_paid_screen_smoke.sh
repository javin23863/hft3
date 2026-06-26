#!/usr/bin/env bash
# Bounded smoke path for VectorBT paid screen (Phase B). See docs/project/VBT_PAID_SCREEN_RUNBOOK.md
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
SMOKE_UNITS="${REPO_ROOT}/runtime/reports/vbt_smoke_units.jsonl"
RUN_ID="paid_smoke_$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${REPO_ROOT}/research_cards/pipeline_runs/${RUN_ID}"

python3 scripts/generate_vbt_paid_units_jsonl.py \
  --out "$SMOKE_UNITS" \
  --smoke-count 12 \
  --symbols MES.v.0,ES.v.0 \
  --event-types CPI,NFP \
  --model-id HYP_5

python3 scripts/run_paid_screen.py \
  --units-jsonl "$SMOKE_UNITS" \
  --out "$OUT_DIR" \
  --vectorbt-scope paid-compute \
  --workers 4 \
  --owner-waiver "phase_b_smoke_before_ready_gate" \
  --max-wall-clock-seconds 3600 \
  --no-llm

echo "Smoke manifest: ${OUT_DIR}/paid_screen_run_manifest.json"
