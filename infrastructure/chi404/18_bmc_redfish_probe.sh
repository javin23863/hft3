#!/bin/bash
# Probe ASRockRack BMC Redfish for BIOS configuration APIs.
set -euo pipefail

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

BMC="${HFT3_BMC_IP:-10.10.91.93}"
PASS="${HFT3_BMC_PASSWORD:-admin}"
BASE="https://${BMC}/redfish/v1"

curl_rf() {
  curl -sk -u "admin:${PASS}" "$@"
}

echo "=== Configurations ==="
curl_rf "${BASE}/Oem/Ami/Configurations" | python3 -m json.tool 2>/dev/null | head -60

echo "=== Systems ==="
curl_rf "${BASE}/Systems" | python3 -m json.tool 2>/dev/null | head -40

echo "=== Managers ==="
curl_rf "${BASE}/Managers" | python3 -m json.tool 2>/dev/null | head -40

for path in \
  "${BASE}/Systems/Self" \
  "${BASE}/Managers/Self" \
  "${BASE}/Managers/Self/Oem/Ami/Configurations" \
  "${BASE}/Oem/Ami/InventoryData/Status"; do
  echo "=== $path ==="
  curl_rf "$path" 2>/dev/null | python3 -m json.tool 2>/dev/null | head -30 || echo "(missing)"
done
