#!/bin/bash
source /root/hft3/.env
curl -sk -u admin:$HFT3_BMC_PASSWORD https://10.10.91.93/redfish/v1/Systems/Self/Bios -o /tmp/bios_full.json
python3 <<'PY'
import json
a=json.load(open("/tmp/bios_full.json"))["Attributes"]
for k,v in sorted(a.items()):
    kl=k.lower()
    if any(x in kl for x in ("dram","ddr","expo","xmp","umc","memory","spd","profile")) and "ppt" not in kl and "pt21" not in kl:
        if len(str(v))<80:
            print(f"{k}={v}")
PY
