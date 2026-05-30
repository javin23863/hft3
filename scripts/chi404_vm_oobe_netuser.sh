#!/bin/bash
# Set Administrator password from OOBE cmd (Shift+F10) via VNC key events.
set -euo pipefail
VNC="${VNC:-127.0.0.1::5900}"
VM="${1:-hft3-rtrader-win}"
PW="${VM_ADMIN_PASSWORD:?Set VM_ADMIN_PASSWORD on CHI404}"

virsh send-key "$VM" KEY_LEFTSHIFT KEY_F10
sleep 2
vncdo -s "$VNC" move 512 384 click 1
sleep 0.5
vncdo -s "$VNC" type "net user Administrator ${PW}"
sleep 3
vncdo -s "$VNC" move 920 715 click 1
sleep 90
timeout 5 vncdo -s "$VNC" capture /tmp/vnc-netuser-ok.png
echo "OOBE net user complete; screenshot /tmp/vnc-netuser-ok.png"
