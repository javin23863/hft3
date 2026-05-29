#!/bin/bash
# Unpack Windows-seeded .NET + Rithmic profile into Wine prefix on CHI404.
set -euo pipefail

ZIP="${1:-/root/hft3/installers/rithmic_wine_seed.zip}"
WINE_PREFIX="${RTRADER_WINE_PREFIX:-/root/.wine-rtrader}"
STAGE="/tmp/rithmic_wine_seed"
LOG=/root/hft3/logs/rtrader/windows_seed.log

if [[ ! -f "$ZIP" ]]; then
  echo "Missing seed zip: $ZIP" | tee "$LOG"
  exit 1
fi

mkdir -p "$STAGE" "$WINE_PREFIX/drive_c/windows"
rm -rf "$STAGE"/*
unzip -o "$ZIP" -d "$STAGE" >> "$LOG" 2>&1 || true

if [[ -d "$STAGE/Microsoft.NET" ]]; then
  mkdir -p "$WINE_PREFIX/drive_c/windows/Microsoft.NET"
  cp -a "$STAGE/Microsoft.NET/." "$WINE_PREFIX/drive_c/windows/Microsoft.NET/"
  echo "Seeded Microsoft.NET" | tee -a "$LOG"
fi

mkdir -p "$WINE_PREFIX/drive_c/users/root/Documents/Rithmic"
mkdir -p "$WINE_PREFIX/drive_c/users/root/AppData/Local"
mkdir -p "$WINE_PREFIX/drive_c/users/root/AppData/Roaming"

if [[ -d "$STAGE/Documents_Rithmic" ]]; then
  cp -a "$STAGE/Documents_Rithmic/." "$WINE_PREFIX/drive_c/users/root/Documents/Rithmic/"
  echo "Seeded Documents/Rithmic" | tee -a "$LOG"
fi
if [[ -d "$STAGE/AppData_Local_Rithmic" ]]; then
  cp -a "$STAGE/AppData_Local_Rithmic/." "$WINE_PREFIX/drive_c/users/root/AppData/Local/" 2>/dev/null || \
    cp -a "$STAGE/AppData_Local_Rithmic" "$WINE_PREFIX/drive_c/users/root/AppData/Local/Rithmic"
  echo "Seeded AppData/Local/Rithmic" | tee -a "$LOG"
fi
if [[ -d "$STAGE/AppData_Roaming_Rithmic" ]]; then
  cp -a "$STAGE/AppData_Roaming_Rithmic/." "$WINE_PREFIX/drive_c/users/root/AppData/Roaming/" 2>/dev/null || \
    cp -a "$STAGE/AppData_Roaming_Rithmic" "$WINE_PREFIX/drive_c/users/root/AppData/Roaming/Rithmic"
  echo "Seeded AppData/Roaming/Rithmic" | tee -a "$LOG"
fi

if [[ -d "$WINE_PREFIX/drive_c/windows/Microsoft.NET/Framework/v4.0.30319" ]]; then
  echo "SEED_OK" | tee -a "$LOG"
else
  echo "SEED_PARTIAL — Framework/v4.0.30319 not found" | tee -a "$LOG"
  exit 1
fi
