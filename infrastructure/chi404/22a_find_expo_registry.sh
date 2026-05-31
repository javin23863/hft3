#!/bin/bash
set -euo pipefail
source /root/hft3/.env
BMC=10.10.91.93
BASE="https://$BMC/redfish/v1"
rf() { curl -sk -u "admin:$HFT3_BMC_PASSWORD" "$@"; }

# Find EXPO-related registry entries
rf "$BASE/Registries/BiosAttributeRegistryA2936.21.8.0/BiosAttributeRegistryA2936.21.8.0.json" \
  -o /tmp/bios_reg.json
python3 <<'PY'
import json,re
attrs=json.load(open("/tmp/bios_reg.json")).get("RegistryEntries",{}).get("Attributes",[])
for a in attrs:
    blob=((a.get("AttributeName") or "")+" "+(a.get("DisplayName") or "")).lower()
    if re.search(r"expo|xmp|dram profile|memory profile|load.?line|a-xmp|spd", blob):
        print("===", a.get("AttributeName"), "|", a.get("DisplayName"))
        for v in (a.get("Value") or [])[:12]:
            print(" ", v.get("ValueName"), "->", v.get("ValueDisplayName"))
PY
