#!/bin/bash
# Recover from BIOS boot override: force next boot to disk (no BIOS entry).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

BMC="${HFT3_BMC_IP:-10.10.91.93}"
BASE="https://${BMC}/redfish/v1"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${HFT3_OC_LOG_DIR:-/root/hft3/logs/oc/${RUN_ID}}"
mkdir -p "$LOG_DIR"

rf() { curl -sk -u "admin:${HFT3_BMC_PASSWORD}" "$@"; }
log() { echo "$*" | tee -a "$LOG_DIR/recover_boot.log"; }

log "=== Recover boot to disk RUN_ID=$RUN_ID ==="

log "ipmitool bootdev disk"
ipmitool chassis bootdev disk options=efiboot 2>&1 | tee -a "$LOG_DIR/recover_boot.log"

HDR="$LOG_DIR/system_self.hdr"
rf -D "$HDR" "${BASE}/Systems/Self" -o "$LOG_DIR/system_self.json"
ETAG=$(grep -i '^etag:' "$HDR" | head -1 | awk '{print $2}' | tr -d '\r')
log "Systems/Self ETAG=$ETAG"

PATCH='{"Boot":{"BootSourceOverrideEnabled":"Disabled","BootSourceOverrideTarget":"None","BootSourceOverrideMode":"UEFI"}}'
rf -w "\nHTTP:%{http_code}\n" -X PATCH \
  -H "Content-Type: application/json" \
  -H "If-Match: ${ETAG}" \
  -d "$PATCH" \
  "${BASE}/Systems/Self" | tee -a "$LOG_DIR/recover_boot.log"

if [[ "${HFT3_RECOVER_REBOOT:-1}" == "1" ]]; then
  log "ForceRestart"
  rf -X POST -H 'Content-Type: application/json' \
    -d '{"ResetType":"ForceRestart"}' \
    "${BASE}/Systems/Self/Actions/ComputerSystem.Reset" | tee -a "$LOG_DIR/recover_boot.log"
fi

log "RECOVER_BOOT=APPLIED"
echo "RUN_ID=$RUN_ID"
