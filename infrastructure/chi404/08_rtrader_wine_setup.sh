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
dpkg --add-architecture i386 2>/dev/null || true
apt-get update -qq
apt-get install -y -qq wine64 wine32 winbind xvfb x11vnc unzip 2>&1 | tee -a "$LOG_DIR/setup.log"

export WINEPREFIX="$WINE_PREFIX"
export WINEARCH=win64
mkdir -p "$WINE_PREFIX"

if [[ ! -d "$WINE_PREFIX/drive_c" ]]; then
  echo "Initializing Wine prefix at $WINE_PREFIX" | tee -a "$LOG_DIR/setup.log"
  wineboot --init 2>&1 | tee -a "$LOG_DIR/setup.log" || true
fi

if [[ -n "$INSTALLER" && -f "$INSTALLER" ]]; then
  echo "Installing R|Trader from $INSTALLER" | tee -a "$LOG_DIR/setup.log"
  if [[ "$INSTALLER" == *.zip ]]; then
    DEST="$WINE_PREFIX/drive_c/Program Files (x86)"
    mkdir -p "$DEST"
    unzip -o "$INSTALLER" -d "$DEST" 2>&1 | tee -a "$LOG_DIR/setup.log" || true
  else
    xvfb-run -a wine "$INSTALLER" /S 2>&1 | tee -a "$LOG_DIR/setup.log" || \
      xvfb-run -a wine "$INSTALLER" 2>&1 | tee -a "$LOG_DIR/setup.log" || true
  fi
else
  echo "RTRADER_INSTALLER_PATH not set or missing — skipping installer" | tee -a "$LOG_DIR/setup.log"
  echo "Set RTRADER_INSTALLER_PATH in /root/hft3/.env and re-run" | tee -a "$LOG_DIR/setup.log"
fi

DISCOVERY="$LOG_DIR/rtrader_discovery.json"
python3 - "$WINE_PREFIX" "$DISCOVERY" "$LOG_DIR" <<'PY'
import json
import sys
from pathlib import Path

prefix = Path(sys.argv[1])
out = Path(sys.argv[2])
log_dir = Path(sys.argv[3])
watch = []
for rel in (
    "drive_c/users/root/Documents",
    "drive_c/Program Files/Rithmic",
    "drive_c/Program Files (x86)/Rithmic",
    "drive_c/Program Files (x86)/Rithmic Trader Pro",
):
    p = prefix / rel
    if p.exists():
        watch.append(str(p))
exes = [p for p in prefix.rglob("*.exe")]
rtrader = next(
    (p for p in exes if "rithmic" in p.name.lower() and "trader" in p.name.lower()),
    exes[0] if exes else None,
)
payload = {
    "wine_prefix": str(prefix),
    "watch_dirs": watch,
    "executables": [str(p) for p in exes][:50],
    "rtrader_exe": str(rtrader) if rtrader else "",
    "note": "Update data_system/config/rithmic_trial.yaml watch_dirs from discovery output",
}
out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(f"Wrote {out}")

launch = log_dir / "launch_rtrader.sh"
if rtrader:
    rel = rtrader.relative_to(prefix / "drive_c")
    win_path = str(rel).replace("/", "\\\\")
    body = f"""#!/bin/bash
export WINEPREFIX="{prefix}"
export WINEARCH=win64
export DISPLAY=:99
Xvfb :99 -screen 0 1024x768x16 &
sleep 1
wine "C:\\\\{win_path}" 2>&1 | tee -a "{log_dir}/rtrader.log"
"""
else:
    body = f"""#!/bin/bash
export WINEPREFIX="{prefix}"
export WINEARCH=win64
export DISPLAY=:99
Xvfb :99 -screen 0 1024x768x16 &
sleep 1
wine "C:\\\\Program Files (x86)\\\\Rithmic\\\\Rithmic Trader Pro\\\\Rithmic Trader Pro.exe" 2>&1 | tee -a "{log_dir}/rtrader.log"
"""
launch.write_text(body, encoding="utf-8")
launch.chmod(0o755)
print(f"Wrote launch script: {launch}")
PY

LAUNCH="$LOG_DIR/launch_rtrader.sh"
grep -q '^RTRADER_WINE_PREFIX=' "$ENV_FILE" 2>/dev/null && \
  sed -i "s|^RTRADER_WINE_PREFIX=.*|RTRADER_WINE_PREFIX=${WINE_PREFIX}|" "$ENV_FILE" || \
  echo "RTRADER_WINE_PREFIX=${WINE_PREFIX}" >> "$ENV_FILE"

echo "Setup complete. Launch script: $LAUNCH"
echo "Discovery: $DISCOVERY"
echo "Provider console/VNC may be required for first R|Trader login."
