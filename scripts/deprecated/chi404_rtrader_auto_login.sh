#!/bin/bash
# Launch R|Trader under Wine and complete Paper/Chicago login via xdotool (CHI404).
set -euo pipefail

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

WINE_PREFIX="${RTRADER_WINE_PREFIX:-/root/.wine-rtrader}"
LOG_DIR="/root/hft3/logs/rtrader"
LAUNCH="$LOG_DIR/launch_rtrader.sh"
LOGIN_LOG="$LOG_DIR/auto_login.log"

export WINEPREFIX="$WINE_PREFIX"
export WINEARCH=win64
export DISPLAY=:99

mkdir -p "$LOG_DIR" "$WINE_PREFIX/drive_c/users/root/Documents/Rithmic"

apt-get install -y -qq xdotool >/dev/null 2>&1 || true

pgrep -f "Xvfb :99" >/dev/null || {
  rm -f /tmp/.X99-lock
  Xvfb :99 -screen 0 1024x768x16 &
  sleep 2
}

if ! pgrep -af "Rithmic Trader Pro" >/dev/null; then
  echo "Launching R|Trader..." | tee "$LOGIN_LOG"
  nohup bash "$LAUNCH" >> "$LOG_DIR/launch.out" 2>&1 &
fi

echo "Waiting for R|Trader window..." | tee -a "$LOGIN_LOG"
WIN=""
for _ in $(seq 1 60); do
  WIN=$(xdotool search --name "Rithmic" 2>/dev/null | head -1 || true)
  if [[ -n "$WIN" ]]; then
    break
  fi
  sleep 2
done

if [[ -z "$WIN" ]]; then
  echo "ERROR: R|Trader window not found after 120s" | tee -a "$LOGIN_LOG"
  tail -20 "$LOG_DIR/rtrader.log" 2>/dev/null | tee -a "$LOGIN_LOG" || true
  exit 1
fi

echo "Found window $WIN — sending login keystrokes" | tee -a "$LOGIN_LOG"
xdotool windowactivate "$WIN"
sleep 1

# Common login flow: username, tab, password, tab through system/gateway, enter.
if [[ -n "${RITHMIC_USERNAME:-}" ]]; then
  xdotool type --clearmodifiers --delay 50 "$RITHMIC_USERNAME"
  xdotool key Tab
fi
if [[ -n "${RITHMIC_PASSWORD:-}" ]]; then
  xdotool type --clearmodifiers --delay 30 "$RITHMIC_PASSWORD"
  xdotool key Tab
fi

# Paper environment + Chicago gateway (may need extra Tabs depending on UI).
for _ in 1 2 3 4; do
  xdotool key Tab
  sleep 0.3
done
xdotool type --clearmodifiers "Paper"
xdotool key Tab
sleep 0.3
xdotool type --clearmodifiers "Chicago"
xdotool key Return

sleep 15
if pgrep -af "Rithmic Trader Pro" >/dev/null; then
  echo "LOGIN_OK: R|Trader process running" | tee -a "$LOGIN_LOG"
  exit 0
fi

echo "WARNING: R|Trader exited after login attempt — check $LOG_DIR/rtrader.log" | tee -a "$LOGIN_LOG"
exit 1
