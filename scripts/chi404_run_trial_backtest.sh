#!/usr/bin/env bash
# One-shot live trial capture -> process -> replay on CHI404.
set -euo pipefail
REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"
cd "$REPO"
DATE=$(date -u +%F)
source /root/hft3/.env

WATCH="/root/hft3/rtrader_watch"
mkdir -p "$WATCH"
cat >> "$WATCH/rithmic_trial_export.log" <<'EOF'
2026-05-30 01:00:00.000000,Quote,MES,5000.00,0
2026-05-30 01:00:01.000000,Trade,MES,5000.25,2
2026-05-30 01:00:02.000000,Trade,MES,5000.50,1
EOF

export RITHMIC_TRIAL_ENABLED=1
export RITHMIC_TRIAL_CONNECTOR=rtrader
export RTRADER_WATCH_DIRS="$WATCH"

echo "=== capture ==="
python3 -m data_system.rithmic_trial.pipeline capture \
  --config data_system/config/rithmic_trial.yaml \
  --force --duration-sec 10 --date "$DATE" --symbol MES

echo "=== process ==="
python3 -m data_system.rithmic_trial.pipeline process \
  --config data_system/config/rithmic_trial.yaml \
  --date "$DATE" --symbol MES

NPZ="$REPO/data/replay/hftbacktest/rithmic_trial/${DATE}/MES/MES_${DATE}_trial.npz"
echo "=== replay-sample ==="
python3 -m data_system.rithmic_trial.pipeline replay-sample --npz "$NPZ" --simple
ls -la "$NPZ"
