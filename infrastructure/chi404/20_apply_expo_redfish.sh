#!/bin/bash
set -euo pipefail
source /root/hft3/.env
BMC="${HFT3_BMC_IP:-10.10.91.93}"
BASE="https://${BMC}/redfish/v1"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${HFT3_OC_LOG_DIR:-/root/hft3/logs/oc/${RUN_ID}}"
mkdir -p "$LOG_DIR"
rf() { curl -sk -u "admin:${HFT3_BMC_PASSWORD}" "$@"; }
log() { echo "$*" | tee -a "$LOG_DIR/redfish_apply.log"; }

log "=== Redfish BIOS 4800 apply RUN_ID=$RUN_ID ==="
HDR="$LOG_DIR/bios_sd_headers.txt"
rf -D "$HDR" "${BASE}/Systems/Self/Bios/SD" -o "$LOG_DIR/bios_sd_before.json"
ETAG=$(grep -i '^etag:' "$HDR" | head -1 | awk '{print $2}' | tr -d '\r')
log "ETAG=$ETAG"

PATCH='{"Attributes":{"CbsCmnMemTargetSpeedDdrRPL":4800,"CbsCmnMemTimingSettingDdrRPL":"CbsCmnMemTimingSettingDdrRPLAuto","CbsCmnMemCtrllerPowerDownEnDdrRPL":"CbsCmnMemCtrllerPowerDownEnDdrRPLDisabled","CbsCmnCpuCpbRPL":"CbsCmnCpuCpbRPLAuto","CbsCmnCpuGlobalCstateCtrlRPL":"CbsCmnCpuGlobalCstateCtrlRPLDisabled"}}'
HTTP=$(rf -w "\nHTTP:%{http_code}" -X PATCH \
  -H "Content-Type: application/json" \
  -H "If-Match: $ETAG" \
  -H '@Redfish.OperationApplyTime: OnReset' \
  -d "$PATCH" \
  "${BASE}/Systems/Self/Bios/SD" 2>&1 | tee -a "$LOG_DIR/redfish_apply.log" | tail -1)

log "PATCH $HTTP"
[[ "$HTTP" == *"204"* || "$HTTP" == *"200"* || "$HTTP" == *"202"* ]] || { log "FAIL"; exit 1; }

log "ForceRestart..."
rf -X POST -H 'Content-Type: application/json' \
  -d '{"ResetType":"ForceRestart"}' \
  "${BASE}/Systems/Self/Actions/ComputerSystem.Reset" | tee -a "$LOG_DIR/redfish_apply.log"
log "REDFISH_BIOS_APPLY=SENT"
echo "RUN_ID=$RUN_ID"
