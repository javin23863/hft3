#!/bin/bash
# QUARANTINED — reboot-to-BIOS without OOB caused CHI404 outage (2026-05-31).
# Use 25_expo_sol_preflight.sh after 17a_oob_preflight + HFT3_OOB_CONFIRMED=1.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "QUARANTINED: 22b_apply_and_reboot_bios.sh disabled after incident." >&2
echo "Use: bash infrastructure/chi404/25_expo_sol_preflight.sh" >&2
echo "Recovery: bash infrastructure/chi404/24_recover_boot_to_disk.sh" >&2

bash "$SCRIPT_DIR/17a_oob_preflight.sh" || exit 1
[[ "${HFT3_OOB_CONFIRMED:-0}" == "1" ]] || {
  echo "Set HFT3_OOB_CONFIRMED=1 only after workstation OOB probe (iKVM + IPMI tunnels)." >&2
  exit 1
}

source /root/hft3/.env
BMC=10.10.91.93
BASE="https://$BMC/redfish/v1"
rf() { curl -sk -u "admin:$HFT3_BMC_PASSWORD" "$@"; }
RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
LOG="/root/hft3/logs/oc/$RUN_ID"
mkdir -p "$LOG"

HDR="$LOG/bios_sd.hdr"
rf -D "$HDR" "$BASE/Systems/Self/Bios/SD" -o "$LOG/bios_sd.json"
ETAG=$(grep -i '^etag:' "$HDR" | awk '{print $2}' | tr -d '\r')

PATCH='{"Attributes":{"CbsCmnMemTargetSpeedDdrRPL":4800,"CbsCmnMemTimingSettingDdrRPL":"CbsCmnMemTimingSettingDdrRPLAuto","CbsCmnMemCtrllerPowerDownEnDdrRPL":"CbsCmnMemCtrllerPowerDownEnDdrRPLDisabled","CbsCmnCpuCpbRPL":"CbsCmnCpuCpbRPLAuto","CbsCmnCpuGlobalCstateCtrlRPL":"CbsCmnCpuGlobalCstateCtrlRPLDisabled"}}'
echo "PATCH Bios SD etag=$ETAG"
rf -w "\nHTTP:%{http_code}\n" -X PATCH -H "Content-Type: application/json" -H "If-Match: $ETAG" \
  -H '@Redfish.OperationApplyTime: OnReset' -d "$PATCH" "$BASE/Systems/Self/Bios/SD" | tee "$LOG/patch.log"

HDR_SYS="$LOG/system_self.hdr"
rf -D "$HDR_SYS" "$BASE/Systems/Self" -o "$LOG/system_self.json"
ETAG_SYS=$(grep -i '^etag:' "$HDR_SYS" | awk '{print $2}' | tr -d '\r')
echo "Boot BiosSetup once (If-Match=$ETAG_SYS)"
rf -w "\nHTTP:%{http_code}\n" -X PATCH -H "Content-Type: application/json" -H "If-Match: $ETAG_SYS" \
  -d '{"Boot":{"BootSourceOverrideEnabled":"Once","BootSourceOverrideTarget":"BiosSetup","BootSourceOverrideMode":"UEFI"}}' \
  "$BASE/Systems/Self" | tee -a "$LOG/patch.log"
ipmitool chassis bootdev bios | tee -a "$LOG/patch.log"

echo "ForceRestart"
rf -X POST -H 'Content-Type: application/json' -d '{"ResetType":"ForceRestart"}' \
  "$BASE/Systems/Self/Actions/ComputerSystem.Reset" | tee -a "$LOG/patch.log"
echo "RUN_ID=$RUN_ID"
