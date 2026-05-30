#!/bin/bash
# Switch running VM XML to e1000 for NAT DHCP before VirtIO guest tools install.
set -euo pipefail
VM="${1:-hft3-rtrader-win}"
virsh destroy "$VM" 2>/dev/null || true
virsh dumpxml "$VM" > /tmp/vm-fix.xml
python3 <<'PY'
from pathlib import Path
import re
t = Path("/tmp/vm-fix.xml").read_text()
t = re.sub(
    r"(<interface type='network'>.*?<model type=')virtio(')",
    r"\1e1000\2",
    t,
    count=1,
    flags=re.DOTALL,
)
t = t.replace("<on_reboot>destroy</on_reboot>", "<on_reboot>restart</on_reboot>")
Path("/tmp/vm-fix.xml").write_text(t)
PY
virsh define /tmp/vm-fix.xml
virsh start "$VM"
sleep 30
virsh domifaddr "$VM" 2>/dev/null || true
