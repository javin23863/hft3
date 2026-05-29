#!/bin/bash
# Fresh win32 Wine prefix + dotnet472 + R|Trader (CHI404).
set -euo pipefail
LOG=/root/hft3/logs/rtrader/wine32_setup.log
PREFIX="${RTRADER_WINE_PREFIX:-/root/.wine-rtrader}"
INSTALLER="${RTRADER_INSTALLER_PATH:-/root/hft3/installers/rithmic_portable.zip}"
export DISPLAY=:99

{
  echo "=== wine32 setup $(date -u) ==="
  systemctl stop hft3-rithmic-trial 2>/dev/null || true
  killall -9 wine wineserver winetricks 2>/dev/null || true
  pkill Xvfb 2>/dev/null || true
  rm -f /tmp/.X99-lock
  sleep 2

  rm -rf "$PREFIX"
  export WINEPREFIX="$PREFIX"
  export WINEARCH=win32
  mkdir -p "$PREFIX"

  Xvfb :99 -screen 0 1024x768x16 &
  sleep 2

  wineboot --init
  sleep 5

  mkdir -p "$PREFIX/drive_c/users/root/AppData/Roaming"
  mkdir -p "$PREFIX/drive_c/users/root/Documents/Rithmic"
  mkdir -p "$PREFIX/drive_c/Program Files/Rithmic Trader Pro"

  if [[ -f "$INSTALLER" ]]; then
    unzip -o "$INSTALLER" -d "$PREFIX/drive_c/Program Files" || true
  fi

  if [[ -f /root/hft3/installers/rithmic_wine_seed.zip ]]; then
    unzip -o /root/hft3/installers/rithmic_wine_seed.zip -d /tmp/rithmic_seed2 || true
    cp -a /tmp/rithmic_seed2/Documents_Rithmic/. "$PREFIX/drive_c/users/root/Documents/Rithmic/" 2>/dev/null || true
  fi

  echo "AppData: $(wine cmd /c 'echo %AppData%' 2>/dev/null || echo FAIL)"
  winetricks --force dotnet472

  EXE="$PREFIX/drive_c/Program Files/Rithmic Trader Pro/Rithmic Trader Pro.exe"
  if [[ ! -f "$EXE" ]]; then
    EXE=$(find "$PREFIX/drive_c" -name 'Rithmic Trader Pro.exe' | head -1)
  fi

  if [[ -d "$PREFIX/drive_c/windows/Microsoft.NET/Framework/v4.0.30319" && -f "$EXE" ]]; then
    echo "WINE32_OK exe=$EXE"
    LAUNCH=/root/hft3/logs/rtrader/launch_rtrader.sh
    cat > "$LAUNCH" <<EOF
#!/bin/bash
export WINEPREFIX="$PREFIX"
export WINEARCH=win32
export DISPLAY=:99
Xvfb :99 -screen 0 1024x768x16 &
sleep 1
wine "C:\\\\Program Files\\\\Rithmic Trader Pro\\\\Rithmic Trader Pro.exe" >> /root/hft3/logs/rtrader/rtrader.log 2>&1
EOF
    chmod +x "$LAUNCH"
  else
    echo "WINE32_FAILED"
    exit 1
  fi
} >> "$LOG" 2>&1
