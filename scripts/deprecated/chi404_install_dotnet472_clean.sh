#!/bin/bash
# Fix Wine AppData profile then install .NET 4.7.2 via winetricks (CHI404).
set -euo pipefail
export WINEPREFIX="${RTRADER_WINE_PREFIX:-/root/.wine-rtrader}"
export WINEARCH=win64
export DISPLAY=:99
LOG=/root/hft3/logs/rtrader/dotnet472_clean.log

killall -9 wine wineserver 2>/dev/null || true
pkill -9 Xvfb 2>/dev/null || true
rm -f /tmp/.X99-lock
sleep 2

Xvfb :99 -screen 0 1024x768x16 &
sleep 2

mkdir -p "$WINEPREFIX/drive_c/users/root/AppData/Roaming"
mkdir -p "$WINEPREFIX/drive_c/users/root/AppData/Local"
mkdir -p "$WINEPREFIX/drive_c/users/root/AppData/LocalLow"
mkdir -p "$WINEPREFIX/drive_c/users/root/Documents/Rithmic"

{
  echo "=== dotnet472 clean install $(date -u) ==="
  timeout 120 wineboot -u || true
  sleep 3
  wine reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Shell Folders" \
    /v AppData /t REG_SZ /d "C:\\users\\root\\AppData\\Roaming" /f || true
  wine reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\User Shell Folders" \
    /v AppData /t REG_EXPAND_SZ /d "C:\\users\\root\\AppData\\Roaming" /f || true
  echo "AppData check: $(wine cmd /c 'echo %AppData%' 2>/dev/null || echo FAIL)"
  rm -rf "$WINEPREFIX/drive_c/windows/Microsoft.NET"
  winetricks --force dotnet472
  if [ -d "$WINEPREFIX/drive_c/windows/Microsoft.NET/Framework/v4.0.30319" ]; then
    echo "DOTNET_OK"
  else
    echo "DOTNET_FAILED"
    exit 1
  fi
} >> "$LOG" 2>&1
