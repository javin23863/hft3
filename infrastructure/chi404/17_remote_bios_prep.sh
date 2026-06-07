#!/bin/bash
# Remote BIOS prep from Cambodia: no colo visit — use BMC iKVM via SSH tunnel.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${HFT3_OC_LOG_DIR:-/root/hft3/logs/oc/${RUN_ID}}"
mkdir -p "$LOG_DIR"

BMC_IP="${HFT3_BMC_IP:-10.10.91.93}"

log() { echo "$*" | tee -a "$LOG_DIR/remote_bios.log"; }

log "=== CHI404 remote BIOS prep RUN_ID=$RUN_ID ==="

# Capture restore point before any firmware change
export RESTORE_ID="${RESTORE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
bash "$SCRIPT_DIR/00_restore_point_capture.sh" 2>&1 | tee -a "$LOG_DIR/remote_bios.log"
echo "$RESTORE_ID" > "$LOG_DIR/RESTORE_ID.txt"

bash "$SCRIPT_DIR/17a_oob_preflight.sh" 2>&1 | tee -a "$LOG_DIR/remote_bios.log"

if ping -c 1 -W 2 "$BMC_IP" >/dev/null 2>&1; then
  log "BMC reachable at $BMC_IP (in-band management VLAN)"
else
  log "WARN: BMC $BMC_IP not pingable from host — check colo VLAN"
fi

if timeout 2 bash -c "echo >/dev/tcp/${BMC_IP}/443" 2>/dev/null; then
  log "BMC HTTPS port 443 open"
else
  log "WARN: BMC HTTPS not reachable"
fi

cat > "$LOG_DIR/CAMBODIA_REMOTE_STEPS.txt" << EOF
Remote EXPO/PBO from Cambodia (no Chicago flight)
=================================================

1. On your PC (Cambodia):
   powershell -File scripts/run_chi404_bmc_ikvm_tunnel.ps1

2. Browser: https://localhost:8443
   Login: BMC admin (default admin/admin if never changed)

3. iKVM -> open remote console -> reboot -> press Del/F2 for BIOS

4. Enable EXPO 4800 MT/s (Micron MB32G48U64M2R8.RsM):
   Advanced -> DRAM Profile / EXPO -> profile 4800 -> Save F10

5. Optional PBO: Advanced -> AMD Overclocking -> PBO Advanced -> Motherboard limits

6. After Linux boots, SSH chi404:
   bash infrastructure/chi404/15_post_bios_oc_verify.sh

7. During RTH market load:
   HFT3_OC_MARKET_LOAD=1 HFT3_OC_RUN_BROKER_SWEEP=1 \\
     bash infrastructure/chi404/16_oc_stability_under_load.sh

RESTORE_ID=$RESTORE_ID (rollback if POST fails)
EOF

log "Wrote $LOG_DIR/CAMBODIA_REMOTE_STEPS.txt"

if [[ "${HFT3_BIOS_BOOT_NEXT:-0}" == "1" ]]; then
  if [[ "${HFT3_OOB_CONFIRMED:-0}" != "1" ]]; then
    log "ABORT: set HFT3_OOB_CONFIRMED=1 after workstation iKVM (8443) and IPMI (1623) tunnels verified"
    exit 1
  fi
  log "Setting next boot to BIOS (one-time)"
  ipmitool chassis bootdev bios 2>&1 | tee -a "$LOG_DIR/remote_bios.log"
  if [[ "${HFT3_BIOS_REBOOT:-0}" == "1" ]]; then
    log "Rebooting in 10s — attach iKVM tunnel BEFORE reboot completes"
    sleep 10
    ipmitool chassis power reset 2>&1 | tee -a "$LOG_DIR/remote_bios.log"
  else
    log "Set HFT3_BIOS_REBOOT=1 to actually reboot (after tunnel is open)"
  fi
else
  log "Set HFT3_BIOS_BOOT_NEXT=1 to arm one-time BIOS boot via IPMI"
fi

cat "$LOG_DIR/CAMBODIA_REMOTE_STEPS.txt"
echo "RUN_ID=$RUN_ID"
echo "RESTORE_ID=$RESTORE_ID"
