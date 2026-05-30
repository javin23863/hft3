#!/bin/bash
# Type password at Windows lock / sign-in screen via VNC on CHI404.
set -euo pipefail
VNC="${VNC:-127.0.0.1::5900}"
PW="${VM_ADMIN_PASSWORD:?Set VM_ADMIN_PASSWORD on CHI404}"

# Wake + click center (lock screen or sign-in)
vncdo -s "$VNC" key ctrl
sleep 0.2
vncdo -s "$VNC" move 512 400 click 1
sleep 0.3
vncdo -s "$VNC" type "$PW"
sleep 0.2
vncdo -s "$VNC" key ret
sleep 2
vncdo -s "$VNC" key ret
sleep 3
timeout 8 vncdo -s "$VNC" capture /tmp/vnc-unlocked.png
echo "Sent unlock keystrokes; screenshot /tmp/vnc-unlocked.png"
