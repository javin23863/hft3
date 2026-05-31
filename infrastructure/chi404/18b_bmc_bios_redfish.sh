#!/bin/bash
source /root/hft3/.env
P="$HFT3_BMC_PASSWORD"
B="https://10.10.91.93/redfish/v1"
for p in Systems/Self/Bios Systems/Self/Bios/SD Systems/Self; do
  echo "=== $p ==="
  curl -sk -u "admin:$P" "$B/$p" | python3 -m json.tool 2>/dev/null | head -80
done
curl -sk -u "admin:$P" -X POST "$B/Systems/Self/Actions/ComputerSystem.Reset" \
  -H 'Content-Type: application/json' \
  -d '{"ResetType":"GracefulRestart"}' 2>&1 | head -5 || true
