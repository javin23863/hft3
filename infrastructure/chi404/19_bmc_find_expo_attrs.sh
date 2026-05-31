#!/bin/bash
# Dump BIOS Redfish attributes and find EXPO/DDR/PBO keys.
set -euo pipefail
source /root/hft3/.env
curl -sk -u "admin:$HFT3_BMC_PASSWORD" \
  https://10.10.91.93/redfish/v1/Systems/Self/Bios \
  | python3 -c "
import json,sys,re
d=json.load(sys.stdin)
attrs=d.get('Attributes',{})
for k,v in sorted(attrs.items()):
    blob=(k+str(v)).lower()
    if re.search(r'expo|xmp|dram|ddr|memory|pbo|boost|profile|mts|speed|umc', blob):
        print(f'{k}={v!r}')
"
