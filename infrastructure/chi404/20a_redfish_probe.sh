#!/bin/bash
set -euo pipefail
source /root/hft3/.env
BMC="${HFT3_BMC_IP:-10.10.91.93}"
BASE="https://${BMC}/redfish/v1"
rf() { curl -sk -u "admin:${HFT3_BMC_PASSWORD}" "$@"; }
echo "=== ResetActionInfo ==="
rf "${BASE}/Systems/Self/ResetActionInfo" | python3 -m json.tool | head -30
echo "=== memory attrs now ==="
rf "${BASE}/Systems/Self/Bios" | python3 -c "import json,sys; a=json.load(sys.stdin)['Attributes']; print('TargetSpeed',a.get('CbsCmnMemTargetSpeedDdrRPL')); print('Timing',a.get('CbsCmnMemTimingSettingDdrRPL')); print('PwrDn',a.get('CbsCmnMemCtrllerPowerDownEnDdrRPL'))"
echo "=== search expo in all keys ==="
rf "${BASE}/Systems/Self/Bios" | python3 -c "import json,sys; a=json.load(sys.stdin)['Attributes'];
[print(k,'=',v) for k,v in a.items() if 'xpo' in k.lower() or 'Xmp' in k or 'Profile' in k or 'EXPO' in k]"
