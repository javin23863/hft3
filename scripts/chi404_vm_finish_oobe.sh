#!/bin/bash
# Finish Windows OOBE: Shift+F10 -> net user -> Finish (VNC on CHI404 localhost).
set -euo pipefail
VNC="${VNC:-127.0.0.1::5900}"
VM="${1:-hft3-rtrader-win}"
PW="${VM_ADMIN_PASSWORD:?Set VM_ADMIN_PASSWORD on CHI404}"

virsh send-key "$VM" KEY_LEFTSHIFT KEY_F10
sleep 3
vncdo -s "$VNC" move 512 384 click 1
sleep 0.5

# vncdo treats each argv after 'type' as keystrokes; spaces must be one argument.
vncdo -s "$VNC" type "net user Administrator ${PW}"
vncdo -s "$VNC" key ret
sleep 5
vncdo -s "$VNC" move 920 715 click 1
sleep 120

timeout 8 vncdo -s "$VNC" capture /tmp/vnc-oobe-done.png
virsh domifaddr "$VM" 2>/dev/null || true
echo "OOBE finish attempted; see /tmp/vnc-oobe-done.png"
