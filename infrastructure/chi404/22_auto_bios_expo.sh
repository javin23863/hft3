#!/bin/bash
# Fully remote: boot to BIOS setup + search/apply EXPO via Redfish (no human iKVM).
set -euo pipefail
source /root/hft3/.env
BMC="${HFT3_BMC_IP:-10.10.91.93}"
BASE="https://${BMC}/redfish/v1"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${HFT3_OC_LOG_DIR:-/root/hft3/logs/oc/${RUN_ID}}"
mkdir -p "$LOG_DIR"
rf() { curl -sk -u "admin:${HFT3_BMC_PASSWORD}" "$@"; }
log() { echo "$*" | tee -a "$LOG_DIR/auto_bios.log"; }

log "=== auto BIOS/EXPO RUN_ID=$RUN_ID ==="

# 1) Dump registry for EXPO/profile keys
rf "${BASE}/Registries" > "$LOG_DIR/registries.json"
REG=$(python3 <<PY
import json
reg=json.load(open("$LOG_DIR/registries.json"))
for m in reg.get("Members",[]):
    oid=m["@odata.id"]
    if "BiosAttribute" in oid or "bios" in oid.lower():
        print(oid)
PY
)
log "Registry members: $REG"
for r in $REG; do
  rf "https://${BMC}${r}" > "$LOG_DIR/registry.json" 2>/dev/null || rf "${BASE}${r}" > "$LOG_DIR/registry.json" 2>/dev/null || true
  [[ -s "$LOG_DIR/registry.json" ]] && break
done

python3 <<'PY' "$LOG_DIR/registry.json" "$LOG_DIR/expo_attrs.txt" 2>/dev/null || true
import json, re, sys
paths=sys.argv[1:3]
reg={}
for p in paths:
    try:
        reg=json.load(open(p))
        break
    except Exception:
        pass
attrs=reg.get("RegistryEntries",{}).get("Attributes",[])
hits=[]
for a in attrs:
    blob=((a.get("AttributeName") or "")+" "+(a.get("DisplayName") or "")).lower()
    if re.search(r"expo|xmp|dram profile|memory profile|loadline|a-xmp", blob):
        hits.append(a)
with open(sys.argv[-1],"w") as f:
    for a in hits:
        f.write(json.dumps(a, indent=2)+"\n---\n")
        vals=a.get("Value",[])
        print(a.get("AttributeName"), "|", a.get("DisplayName"))
        for v in vals[:8]:
            print(" ", v.get("ValueName"), v.get("ValueDisplayName"))
PY | tee -a "$LOG_DIR/auto_bios.log"

# 2) PATCH BIOS: 4800 + timing manual attempt
HDR="$LOG_DIR/bios_sd_headers.txt"
rf -D "$HDR" "${BASE}/Systems/Self/Bios/SD" -o "$LOG_DIR/bios_sd.json"
ETAG=$(grep -i '^etag:' "$HDR" | head -1 | awk '{print $2}' | tr -d '\r')
PATCH='{"Attributes":{"CbsCmnMemTargetSpeedDdrRPL":4800,"CbsCmnMemTimingSettingDdrRPL":"CbsCmnMemTimingSettingDdrRPLManual","CbsCmnMemCtrllerPowerDownEnDdrRPL":"CbsCmnMemCtrllerPowerDownEnDdrRPLDisabled","CbsCmnCpuCpbRPL":"CbsCmnCpuCpbRPLAuto","CbsCmnCpuGlobalCstateCtrlRPL":"CbsCmnCpuGlobalCstateCtrlRPLDisabled"}}'
HTTP=$(rf -w "\nHTTP:%{http_code}" -X PATCH -H "Content-Type: application/json" -H "If-Match: $ETAG" \
  -H '@Redfish.OperationApplyTime: OnReset' -d "$PATCH" "${BASE}/Systems/Self/Bios/SD" 2>&1 | tail -1)
log "BIOS PATCH $HTTP"

# 3) One-time boot to BIOS setup via Redfish + IPMI
BOOT_PATCH='{"Boot":{"BootSourceOverrideEnabled":"Once","BootSourceOverrideTarget":"BiosSetup","BootSourceOverrideMode":"UEFI"}}'
HTTP2=$(rf -w "\nHTTP:%{http_code}" -X PATCH -H "Content-Type: application/json" \
  -d "$BOOT_PATCH" "${BASE}/Systems/Self" 2>&1 | tail -1)
log "Boot override PATCH $HTTP2"
ipmitool chassis bootdev bios 2>&1 | tee -a "$LOG_DIR/auto_bios.log"

# 4) ForceRestart
rf -X POST -H 'Content-Type: application/json' -d '{"ResetType":"ForceRestart"}' \
  "${BASE}/Systems/Self/Actions/ComputerSystem.Reset" | tee -a "$LOG_DIR/auto_bios.log"
log "Rebooting to BIOS setup — SOL capture next if POST stalls"

echo "RUN_ID=$RUN_ID"
