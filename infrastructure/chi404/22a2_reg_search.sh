#!/bin/bash
source /root/hft3/.env
curl -sk -u admin:$HFT3_BMC_PASSWORD \
  https://10.10.91.93/redfish/v1/Registries/BiosAttributeRegistryA2936.21.8.0/BiosAttributeRegistryA2936.21.8.0.json \
  -o /tmp/bios_reg.json
python3 <<'PY'
import json,re
attrs=json.load(open("/tmp/bios_reg.json")).get("RegistryEntries",{}).get("Attributes",[])
for a in attrs:
    name=a.get("AttributeName") or ""
    disp=a.get("DisplayName") or ""
    blob=(name+" "+disp).lower()
    vals=a.get("Value") or []
    valtxt=" ".join(str(v.get("ValueDisplayName","")) for v in vals).lower()
    if re.search(r"expo|xmp|profile|4800|5200|5600|6000|dram", blob+valtxt):
        if "pt21" in blob or "usb" in blob:
            continue
        print("===", name, "|", disp)
        for v in vals[:15]:
            print(" ", v.get("ValueName"), "->", v.get("ValueDisplayName"))
PY
