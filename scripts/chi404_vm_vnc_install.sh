#!/bin/bash
# Drive Windows Server install via VNC on CHI404 (colo-only; no workstation).
# See docs/rithmic_trial/CHI404_VM_BUGS.md for footguns and OOBE password notes.
set -euo pipefail

VNC="${VNC:-127.0.0.1::5900}"
LOG="/root/hft3/logs/rtrader/vm_vnc_install.log"
DISK="/var/lib/libvirt/images/hft3-rtrader/hft3-rtrader-win.qcow2"

click() { vncdo -s "$VNC" move "$1" "$2" click 1 2>>"$LOG" || true; sleep "$3"; }
key() { vncdo -s "$VNC" key "$@" 2>>"$LOG" || true; sleep 1; }

echo "=== VNC install drive $(date -u) ===" | tee "$LOG"

# 1) Language screen → Install now
key alt-n
sleep 3
key enter
echo "Waiting for Setup is starting / edition list (up to 120s)..." | tee -a "$LOG"
sleep 15

# 2) Desktop Experience (2nd row) — required for R|Trader GUI
key down
sleep 1
key alt-n
sleep 5

# 3) EULA checkbox + Next
key space
sleep 1
key alt-n
sleep 5

# 4) Custom install — click row; Down+Enter wrongly selects Upgrade
click 512 430 1
click 512 430 2
sleep 5

# 5) Disk (SATA: Drive 0 visible) → Next
key alt-n
sleep 10

echo "Install kicked off; watch disk growth:" | tee -a "$LOG"
echo "  du -h $DISK" | tee -a "$LOG"
echo "Gate: size > 500M within ~5 min; OOBE password screen needs manual/vncdo finish." | tee -a "$LOG"
