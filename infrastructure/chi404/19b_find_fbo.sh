#!/bin/bash
source /root/hft3/.env
curl -sk -u "admin:$HFT3_BMC_PASSWORD" https://10.10.91.93/redfish/v1/Systems/Self/Bios \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)['Attributes']
for k,v in sorted(d.items()):
    if 'FBO' in k or 'Expo' in k or 'EXPO' in k or 'Xmp' in k or 'XMP' in k or 'Profile' in k or 'Dram' in k or 'DRAM' in k:
        print(f'{k}={v!r}')
"
