#!/bin/bash
set -euo pipefail
export WINEPREFIX=/root/.wine-rtrader
export WINEARCH=win64
export DISPLAY=:99
LOG=/root/hft3/logs/rtrader/ndp472_direct.log

pgrep -f "Xvfb :99" >/dev/null || {
  Xvfb :99 -screen 0 1024x768x16 &
  sleep 2
}

echo "Installing .NET 4.7.2..." | tee "$LOG"
cd /root/.cache/winetricks/dotnet472
if [[ ! -f NDP472-KB4054530-x86-x64-AllOS-ENU.exe ]]; then
  winetricks -q dotnet472 || true
fi
wine NDP472-KB4054530-x86-x64-AllOS-ENU.exe /passive 2>&1 | tee -a "$LOG" || \
  wine NDP472-KB4054530-x86-x64-AllOS-ENU.exe /q 2>&1 | tee -a "$LOG" || true

if [ -d "$WINEPREFIX/drive_c/windows/Microsoft.NET/Framework/v4.0.30319" ]; then
  echo "DOTNET_OK" | tee -a "$LOG"
else
  echo "DOTNET_FAILED" | tee -a "$LOG"
fi
