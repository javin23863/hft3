#!/bin/bash
set -euo pipefail
export RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
export HFT3_TUNING_RESUME_STEP=4
export HOT_CPUS=2-11
export HFT3_REPO_DIR="${HFT3_REPO_DIR:-/root/hft3/repo}"
mkdir -p "${HFT3_REPO_DIR}/logs/tuning/${RUN_ID}"
bash "${HFT3_REPO_DIR}/infrastructure/chi404/run_chi404_tuning.sh" 2>&1 | tee "${HFT3_REPO_DIR}/logs/tuning/${RUN_ID}/orchestrator.log"
echo "EXIT=$? RUN_ID=$RUN_ID"
