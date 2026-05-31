#!/bin/bash
source /root/hft3/.env
curl -sk -u admin:$HFT3_BMC_PASSWORD https://10.10.91.93/redfish/v1/Systems/Self/Bios | python3 -c "
import json,sys
a=json.load(sys.stdin)['Attributes']
for k,v in sorted(a.items()):
    if any(x in k.lower() for x in ['pbo','ppt','edc','tdc','boost','cpb','curve','precision']):
        print(k,'=',v)
"
