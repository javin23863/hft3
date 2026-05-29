#!/bin/bash
# Install Wine + xvfb and prepare R|Trader Pro on CHI404 (interim bridge).
set -euo pipefail

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

WINE_PREFIX="${RTRADER_WINE_PREFIX:-/root/.wine-rtrader}"
INSTALLER="${RTRADER_INSTALLER_PATH:-}"
LOG_DIR="${HFT3_TUNING_LOG_DIR:-/root/hft3/logs/rtrader}"
mkdir -p "$LOG_DIR"

echo "=== R|Trader Wine setup ===" | tee "$LOG_DIR/setup.log"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq wine64 winbind xvfb x11vnc 2>&1 | tee -a "$LOG_DIR/setup.log"

export WINEPREFIX="$WINE_PREFIX"
export WINEARCH=win64
mkdir -p "$WINE_PREFIX"

if [[ ! -d "$WINE_PREFIX/drive_c" ]]; then
  echo "Initializing Wine prefix at $WINE_PREFIX" | tee -a "$LOG_DIR/setup.log"
  wineboot --init 2>&1 | tee -a "$LOG_DIR/setup.log" || true
fi

if [[ -n "$INSTALLER" && -f "$INSTALLER" ]]; then
  echo "Installing R|Trader from $INSTALLER" | tee -a "$LOG_DIR/setup.log"
  xvfb-run -a wine "$INSTALLER" /S 2>&1 | tee -a "$LOG_DIR/setup.log" || \
    xvfb-run -a wine "$INSTALLER" 2>&1 | tee -a "$LOG_DIR/setup.log" || true
else
  echo "RTRADER_INSTALLER_PATH not set or missing — skipping installer" | tee -a "$LOG_DIR/setup.log"
  echo "Set RTRADER_INSTALLER_PATH in /root/hft3/.env and re-run" | tee -a "$LOG_DIR/setup.log"
fi

LAUNCH="$LOG_DIR/launch_rtrader.sh"
cat > "$LAUNCH" <<EOF
#!/bin/bash
export WINEPREFIX="$WINE_PREFIX"
export WINEARCH=win64
export DISPLAY=:99
Xvfb :99 -screen 0 1024x768x16 &
sleep 1
# Adjust executable path after install discovery:
wine "C:\\\\Program Files\\\\Rithmic\\\\RTrader.exe" 2>&1 | tee -a "$LOG_DIR/rtrader.log"
EOF
chmod +x "$LAUNCH"

DISCOVERY="$LOG_DIR/rtrader_discovery.json"
python3 - "$WINE_PREFIX" "$DISCOVERY" <<'PY'
import json
import sys
from pathlib import Path

prefix = Path(sys.argv[1])
out = Path(sys.argv[2])
watch = []
for rel in (
    "drive_c/users/root/Documents",
    "drive_c/Program Files/Rithmic",
    "drive_c/Program Files (x86)/Rithmic",
):
    p = prefix / rel
    if p.exists():
        watch.append(str(p))
payload = {
    "wine_prefix": str(prefix),
    "watch_dirs": watch,
    "executables": [str(p) for p in prefix.rglob("*.exe")][:50],
    "note": "Update data_system/config/rithmic_trial.yaml watch_dirs from discovery output",
}
out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(f"Wrote {out}")
PY

grep -q '^RTRADER_WINE_PREFIX=' "$ENV_FILE" 2>/dev/null && \
  sed -i "s|^RTRADER_WINE_PREFIX=.*|RTRADER_WINE_PREFIX=${WINE_PREFIX}|" "$ENV_FILE" || \
  echo "RTRADER_WINE_PREFIX=${WINE_PREFIX}" >> "$ENV_FILE"

echo "Setup complete. Launch script: $LAUNCH"
echo "Discovery: $DISCOVERY"
echo "Provider console/VNC may be required for first R|Trader login."
