#!/usr/bin/env python3
import json
import sys
import urllib.request
import ssl
import os

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

bmc = os.environ.get("HFT3_BMC_IP", "10.10.91.93")
password = os.environ["HFT3_BMC_PASSWORD"]
auth = f"admin:{password}".encode()

def get(url):
    req = urllib.request.Request(url)
    import base64
    req.add_header("Authorization", "Basic " + base64.b64encode(auth).decode())
    with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
        return json.loads(r.read().decode())

reg = get(f"https://{bmc}/redfish/v1/Registries/BiosAttributeRegistryA2936.21.8.0/BiosAttributeRegistryA2936.21.8.0.json")
attrs = reg.get("RegistryEntries", {}).get("Attributes", [])
keys = ("expo", "xmp", "profile", "target speed", "pbo", "boost", "dram", "memory speed", "a-xmp")
for a in attrs:
    name = a.get("AttributeName", "")
    disp = (a.get("DisplayName") or "").lower()
    blob = (name + " " + disp).lower()
    if any(k in blob for k in keys):
        vals = a.get("Value", [])
        print("NAME:", name)
        print("  DISPLAY:", a.get("DisplayName"))
        print("  DEFAULT:", a.get("DefaultValue"))
        if vals:
            print("  VALUES:", [v.get("ValueName") for v in vals[:15]])
        print()
