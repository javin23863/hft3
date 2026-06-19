#!/usr/bin/env bash
set -euo pipefail
cd /root/hft3/repo
export HFT3_NPZ_ROOT=/data/npz
export HFT3_MANIFEST_PATH=/data/npz/manifest.json
export HFT3_FEATURE_BACKEND=cpp

python3 scripts/generate_vbt_paid_units_jsonl.py \
  --out runtime/reports/vbt_cpi_medium.jsonl \
  --event-types CPI \
  --research-split discovery_confirmation \
  --max-units 50 \
  --model-id SPREAD_BLOWOUT_RECOMPRESSION \
  --symbols MES.v.0 \
  --start-date 2019-05-01 \
  --validation-cpi-first

RUN_ID="paid_cpi_medium_$(date -u +%Y%m%dT%H%M%SZ)"
OUT="research_cards/pipeline_runs/${RUN_ID}"
EVENTS_HASH="$(python3 -c "import hashlib;print(hashlib.sha256(open('packages/data_system/config/events.csv','rb').read()).hexdigest()[:32])")"
LAKE_HASH="$(python3 -c "import hashlib;print(hashlib.sha256(open('/data/npz/manifest.json','rb').read()).hexdigest()[:32])")"

tmux kill-session -t vbt_cpi_medium 2>/dev/null || true
tmux new -d -s vbt_cpi_medium \
  "cd /root/hft3/repo && export HFT3_NPZ_ROOT=/data/npz HFT3_MANIFEST_PATH=/data/npz/manifest.json HFT3_FEATURE_BACKEND=cpp && \
   python3 -u scripts/run_paid_screen.py \
   --units-jsonl runtime/reports/vbt_cpi_medium.jsonl \
   --out ${OUT} \
   --vectorbt-scope paid-compute \
   --workers 32 \
   --max-wall-clock-seconds 7200 \
   --no-llm \
   --owner-waiver 'vast D1b CPI medium pilot validation' \
   --events-csv-hash ${EVENTS_HASH} \
   --lake-manifest-hash ${LAKE_HASH} \
   2>&1 | tee runtime/reports/${RUN_ID}.log; echo EXIT_CODE=\$? >> runtime/reports/${RUN_ID}.log"

echo "LAUNCHED ${RUN_ID} OUT=${OUT}"
