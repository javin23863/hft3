#!/bin/bash
set -euo pipefail
mkdir -p "/root/.wine-rtrader/drive_c/users/root/Documents"
WATCH="/root/.wine-rtrader/drive_c/Program Files (x86)/Rithmic Trader Pro;/root/.wine-rtrader/drive_c/users/root/Documents"
python3 <<'PY'
import json
from pathlib import Path
watch = [
    "/root/.wine-rtrader/drive_c/Program Files (x86)/Rithmic Trader Pro",
    "/root/.wine-rtrader/drive_c/users/root/Documents",
]
Path("/root/hft3/logs/rtrader/rtrader_discovery.json").write_text(
    json.dumps({"watch_dirs": watch}, indent=2) + "\n", encoding="utf-8"
)
print("watch_dirs:", watch)
PY
ENV_FILE=/root/hft3/.env
if grep -q '^RTRADER_WATCH_DIRS=' "$ENV_FILE"; then
  sed -i "s|^RTRADER_WATCH_DIRS=.*|RTRADER_WATCH_DIRS=\"${WATCH}\"|" "$ENV_FILE"
else
  echo "RTRADER_WATCH_DIRS=\"${WATCH}\"" >> "$ENV_FILE"
fi
grep RTRADER_WATCH "$ENV_FILE"
