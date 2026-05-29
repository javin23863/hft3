#!/bin/bash
# Start xvfb + x11vnc + R|Trader for one-time Paper/Chicago login on CHI404.
set -euo pipefail

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

WINE_PREFIX="${RTRADER_WINE_PREFIX:-/root/.wine-rtrader}"
LOG_DIR="/root/hft3/logs/rtrader"
mkdir -p "$LOG_DIR"

export WINEPREFIX="$WINE_PREFIX"
export WINEARCH=win64
export DISPLAY=:99

pgrep -af "Xvfb :99" >/dev/null || {
  Xvfb :99 -screen 0 1024x768x16 &
  sleep 2
}

pgrep -af "x11vnc.*:99" >/dev/null || {
  nohup x11vnc -display :99 -forever -nopw -listen localhost -rfbport 5900 \
    >> "$LOG_DIR/vnc.log" 2>&1 &
  sleep 1
  echo "VNC on localhost:5900 — from workstation: ssh -L 5900:localhost:5900 chi404"
}

LAUNCH="$LOG_DIR/launch_rtrader.sh"
if [[ -x "$LAUNCH" ]]; then
  pgrep -af "Rithmic Trader Pro" >/dev/null || nohup bash "$LAUNCH" >> "$LOG_DIR/launch.out" 2>&1 &
else
  echo "Missing $LAUNCH — run infrastructure/chi404/08_rtrader_wine_setup.sh first" >&2
  exit 1
fi

echo "R|Trader login: connect VNC to Paper / Chicago with credentials from /root/hft3/.env"
