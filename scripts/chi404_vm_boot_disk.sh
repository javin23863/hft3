#!/bin/bash
# Eject Windows install ISO and boot from disk (post-install).
set -euo pipefail
VM="${1:-hft3-rtrader-win}"
virsh destroy "$VM" 2>/dev/null || true
virsh dumpxml "$VM" > /tmp/vm-boot.xml
python3 <<'PY'
from pathlib import Path
import re
t = Path("/tmp/vm-boot.xml").read_text()
# Boot from disk only
t = re.sub(r"\s*<boot dev='cdrom'/>\n", "\n", t)
if "<boot dev='hd'/>" not in t:
    t = t.replace("<os>", "<os>\n    <boot dev='hd'/>", 1)
# Eject windows install ISO (keep virtio-win for guest drivers)
t = re.sub(
    r"<disk type='file' device='cdrom'>\n"
    r"\s*<driver name='qemu' type='raw'/>\n"
    r"\s*<source file='/root/hft3/installers/windows.iso'[^>]*/>\n"
    r"\s*<backingStore/>\n"
    r"\s*<target dev='sdb' bus='sata'/>\n"
    r"\s*<readonly/>\n"
    r"\s*<alias name='[^']*'/>\n"
    r"\s*<address[^>]*/>\n"
    r"\s*</disk>\n",
    "",
    t,
    count=1,
)
Path("/tmp/vm-boot.xml").write_text(t)
PY
virsh define /tmp/vm-boot.xml
virsh start "$VM"
echo "Booting $VM from disk (windows.iso ejected)."
