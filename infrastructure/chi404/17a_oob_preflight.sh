#!/bin/bash
# OOB preflight: BMC + IPMI/SOL reachable from CHI404 before any BIOS reboot.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${HFT3_OC_LOG_DIR:-/root/hft3/logs/oc/${RUN_ID}}"
mkdir -p "$LOG_DIR"

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

BMC_IP="${HFT3_BMC_IP:-10.10.91.93}"
LOG="$LOG_DIR/oob_preflight.log"

log() { echo "$*" | tee -a "$LOG"; }
fail() { log "OOB_PREFLIGHT=FAIL $*"; exit 1; }

log "=== OOB preflight RUN_ID=$RUN_ID ==="

command -v ipmitool >/dev/null 2>&1 || fail "ipmitool missing"

if ping -c 1 -W 2 "$BMC_IP" >/dev/null 2>&1; then
  log "BMC ping OK ($BMC_IP)"
else
  fail "BMC not pingable at $BMC_IP"
fi

if timeout 2 bash -c "echo >/dev/tcp/${BMC_IP}/443" 2>/dev/null; then
  log "BMC HTTPS OK"
else
  fail "BMC HTTPS not reachable on $BMC_IP:443"
fi

if [[ -n "${HFT3_BMC_PASSWORD:-}" ]]; then
  code=$(curl -sk -u "admin:${HFT3_BMC_PASSWORD}" -o /dev/null -w "%{http_code}" \
    "https://${BMC_IP}/redfish/v1/Managers/Self" || echo "000")
  [[ "$code" == "200" ]] || fail "Redfish auth failed HTTP=$code"
  log "Redfish auth OK"
else
  fail "HFT3_BMC_PASSWORD not set in $ENV_FILE"
fi

ipmitool chassis power status 2>&1 | tee -a "$LOG" || fail "ipmitool KCS/lan failed"

sol_info=$(ipmitool sol info 1 2>&1) || fail "ipmitool sol info failed"
echo "$sol_info" | tee -a "$LOG"
echo "$sol_info" | grep -qi "Enabled" || fail "SOL not enabled on channel 1"

# Dedicated IPMI NIC (optional OOB when OS down)
for nic in enp5s0 enp6s0; do
  if ip link show "$nic" &>/dev/null; then
    state=$(ip link show "$nic" | awk '/state/ {print $9}')
    log "IPMI NIC $nic state=$state"
  fi
done

ipmitool lan print 1 2>&1 | tee -a "$LOG" || true
if ipmitool lan print 1 2>/dev/null | grep -q "IP Address[[:space:]]*:[[:space:]]*0.0.0.0"; then
  log "WARN: dedicated IPMI channel 1 has no IP — recovery when OS down requires SSH jump or colo hands"
fi

log "OOB_PREFLIGHT=PASS"
echo "RUN_ID=$RUN_ID"
