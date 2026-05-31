#!/bin/bash
source /root/hft3/.env
BMC=10.10.91.93
A="admin:$HFT3_BMC_PASSWORD"
for path in / /api/ /api/chassis_status /api/getIKVMStatus /api/configuration/ikvm /ikvm/ /viewer.html /Java/jviewer.jnlp /redfish/v1/Managers/Self/GraphicalConsole /redfish/v1/Managers/Self/SerialConsole; do
  code=$(curl -sk -u "$A" -o /tmp/bmc_probe_body -w "%{http_code}" "https://$BMC$path")
  echo "$code $path"
  head -c 120 /tmp/bmc_probe_body 2>/dev/null | tr '\n' ' '
  echo
done
