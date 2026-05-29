#!/bin/bash
set -euo pipefail
export RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
export HFT3_TUNING_RESUME_STEP=4
export HOT_CPUS=2-11
mkdir -p "/root/hft3/logs/tuning/${RUN_ID}"
bash /root/hft3/infrastructure/chi404/run_chi404_tuning.sh 2>&1 | tee "/root/hft3/logs/tuning/${RUN_ID}/orchestrator.log"
echo "EXIT=$? RUN_ID=$RUN_ID"
