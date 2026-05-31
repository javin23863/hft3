#!/bin/bash
source /root/hft3/.env
curl -sk -u admin:$HFT3_BMC_PASSWORD https://10.10.91.93/redfish/v1/Systems/Self/Bios | python3 -c "
import json,sys
a=json.load(sys.stdin)['Attributes']
for k in sorted(a):
    if 'TargetSpeed' in k or 'TimingSetting' in k or 'PowerDown' in k or 'Expo' in k or 'Xmp' in k or 'Profile' in k.lower():
        print(k,'=',a[k])
"
