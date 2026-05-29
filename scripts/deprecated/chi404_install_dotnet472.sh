#!/bin/bash
# Fix Wine user profile then install .NET 4.7.2 for R|Trader on CHI404.
set -euo pipefail
export WINEPREFIX="${RTRADER_WINE_PREFIX:-/root/.wine-rtrader}"
export WINEARCH=win64
export DISPLAY=:99
LOG=/root/hft3/logs/rtrader/dotnet472_fix.log

pgrep -f "Xvfb :99" >/dev/null || {
  Xvfb :99 -screen 0 1024x768x16 &
  sleep 2
}

mkdir -p "$WINEPREFIX/drive_c/users/root/AppData/Roaming"
mkdir -p "$WINEPREFIX/drive_c/users/root/AppData/Local"
mkdir -p "$WINEPREFIX/drive_c/users/root/AppData/LocalLow"
mkdir -p "$WINEPREFIX/drive_c/users/root/Documents/Rithmic"

wineboot -u 2>&1 | tee "$LOG" || true
sleep 3
wine reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Shell Folders" \
  /v AppData /t REG_SZ /d "C:\\users\\root\\AppData\\Roaming" /f >> "$LOG" 2>&1 || true
wine reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\User Shell Folders" \
  /v AppData /t REG_EXPAND_SZ /d "C:\\users\\root\\AppData\\Roaming" /f >> "$LOG" 2>&1 || true

echo "Running winetricks dotnet472..." | tee -a "$LOG"
winetricks -q dotnet472 >> "$LOG" 2>&1 || {
  echo "winetricks failed; trying direct installer /passive" | tee -a "$LOG"
  cd /root/.cache/winetricks/dotnet472
  wine NDP472-KB4054530-x86-x64-AllOS-ENU.exe /passive >> "$LOG" 2>&1 || true
}

if [ -d "$WINEPREFIX/drive_c/windows/Microsoft.NET/Framework/v4.0.30319" ]; then
  echo "DOTNET_OK" | tee -a "$LOG"
  bash /root/hft3/logs/rtrader/launch_rtrader.sh >> "$LOG" 2>&1 &
else
  echo "DOTNET_FAILED — use VNC: ssh -L 5900:localhost:5900 chi404 then connect viewer to localhost:5900" | tee -a "$LOG"
  exit 1
fi
