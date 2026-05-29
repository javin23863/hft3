#!/bin/bash
set -euo pipefail
WATCH="/root/hft3/rtrader_watch"
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
ENV_FILE=/root/hft3/.env
if grep -q '^RTRADER_WATCH_DIRS=' "$ENV_FILE"; then
  sed -i "s|^RTRADER_WATCH_DIRS=.*|RTRADER_WATCH_DIRS=\"${WATCH}\"|" "$ENV_FILE"
else
  echo "RTRADER_WATCH_DIRS=\"${WATCH}\"" >> "$ENV_FILE"
fi
grep RTRADER_WATCH "$ENV_FILE"
