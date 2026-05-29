#!/bin/bash
# Wire Linux capture to VM SMB share (no Wine).
set -euo pipefail

WATCH="/root/hft3/rtrader_watch"
ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"

mkdir -p "$WATCH"

python3 <<PY
import json
from pathlib import Path
watch = ["$WATCH"]
Path("/root/hft3/logs/rtrader/rtrader_discovery.json").write_text(
    json.dumps({"watch_dirs": watch, "bridge": "vm_smb"}, indent=2) + "\n", encoding="utf-8"
)
print("watch_dirs:", watch)
PY

set_kv() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    echo "${key}=${val}" >> "$ENV_FILE"
  fi
}

set_kv RTRADER_WATCH_DIRS "\"${WATCH}\""
set_kv RTRADER_START_WINE "0"
set_kv RITHMIC_TRIAL_ENABLED "1"
set_kv RITHMIC_TRIAL_CONNECTOR "rtrader"

grep RTRADER_WATCH "$ENV_FILE"

systemctl enable hft3-rithmic-trial 2>/dev/null || bash "$REPO/infrastructure/chi404/09_rithmic_trial_systemd.sh"
systemctl restart hft3-rithmic-trial
echo "Capture watching $WATCH (RTRADER_START_WINE=0)"
