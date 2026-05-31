#!/bin/bash
source /root/hft3/.env
BMC=10.10.91.93
HDR=/tmp/bios_sd_headers.txt
curl -sk -u admin:$HFT3_BMC_PASSWORD -D "$HDR" \
  https://$BMC/redfish/v1/Systems/Self/Bios/SD -o /tmp/bios_sd.json
ETAG=$(grep -i '^etag:' "$HDR" | head -1 | awk '{print $2}' | tr -d '\r')
echo "ETAG=$ETAG"
PATCH='{"@Redfish.SettingsApply":{"ApplyTime":"OnReset"},"Attributes":{"CbsCmnMemTargetSpeedDdrRPL":4800,"CbsCmnMemTimingSettingDdrRPL":"CbsCmnMemTimingSettingDdrRPLAuto","CbsCmnMemCtrllerPowerDownEnDdrRPL":"CbsCmnMemCtrllerPowerDownEnDdrRPLDisabled","CbsCmnCpuCpbRPL":"CbsCmnCpuCpbRPLAuto","CbsCmnCpuGlobalCstateCtrlRPL":"CbsCmnCpuGlobalCstateCtrlRPLDisabled"}}'
curl -sk -u admin:$HFT3_BMC_PASSWORD -w "\nHTTP:%{http_code}\n" -X PATCH \
  -H "Content-Type: application/json" \
  -H "If-Match: $ETAG" \
  -d "$PATCH" \
  https://$BMC/redfish/v1/Systems/Self/Bios/SD
