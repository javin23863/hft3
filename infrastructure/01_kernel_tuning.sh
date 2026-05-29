#!/bin/bash
# Bare-metal kernel tuning — idempotent GRUB, AMD-focused.
set -euo pipefail

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${HFT3_TUNING_LOG_DIR:-/root/hft3/logs/tuning/${RUN_ID}}"
mkdir -p "$LOG_DIR"

NPROC=$(nproc)
LAST=$((NPROC - 1))
HOT_CPUS="${HOT_CPUS:-${HFT3_ISOL_CPUS:-2-${LAST}}}"

echo "HOT_CPUS=$HOT_CPUS nproc=$NPROC" | tee "$LOG_DIR/kernel_tuning.txt"

GRUB_FILE="/etc/default/grub"
cp "$GRUB_FILE" "$LOG_DIR/grub_before.txt"

export HOT_CPUS
python3 << 'PY'
import os
import re
from pathlib import Path

grub = Path("/etc/default/grub")
hot = os.environ.get("HOT_CPUS", "2-23")
text = grub.read_text()
m = re.search(r'^GRUB_CMDLINE_LINUX_DEFAULT="([^"]*)"', text, re.M)
if not m:
    raise SystemExit("GRUB_CMDLINE_LINUX_DEFAULT not found")
args = m.group(1).split()
strip_prefixes = (
    "isolcpus=", "nohz_full=", "rcu_nocbs=", "isolcpus_managed_irq",
    "processor.max_cstate=", "amd_idle.max_cstate=", "cpuidle.off=",
    "mce=", "audit=", "nmi_watchdog=", "nosoftlockup", "intel_idle.max_cstate=",
)
new_args = [a for a in args if not any(a.startswith(p) for p in strip_prefixes)]
add = [
    f"isolcpus={hot}",
    f"nohz_full={hot}",
    f"rcu_nocbs={hot}",
    "isolcpus_managed_irq,domain",
    "processor.max_cstate=0",
    "amd_idle.max_cstate=0",
    "cpuidle.off=1",
    "mce=ignore_ce",
    "audit=0",
    "nmi_watchdog=0",
    "nosoftlockup",
    "nosmt",
]
new_args.extend(add)
merged = " ".join(new_args)
text2 = re.sub(
    r'^GRUB_CMDLINE_LINUX_DEFAULT="[^"]*"',
    f'GRUB_CMDLINE_LINUX_DEFAULT="{merged}"',
    text,
    count=1,
    flags=re.M,
)
grub.write_text(text2)
print("GRUB updated:", merged[:120], "...")
PY

cp "$GRUB_FILE" "$LOG_DIR/grub_after.txt"
update-grub 2>&1 | tee -a "$LOG_DIR/kernel_tuning.txt"

systemctl stop irqbalance 2>/dev/null || true
systemctl disable irqbalance 2>/dev/null || true

if command -v cpupower >/dev/null; then
  cpupower frequency-set -g performance 2>&1 | tee -a "$LOG_DIR/kernel_tuning.txt"
fi

# sysctl hot-path friendly
cat > /etc/sysctl.d/99-hft3.conf << 'EOF'
vm.swappiness=1
kernel.numa_balancing=0
EOF
sysctl -p /etc/sysctl.d/99-hft3.conf | tee -a "$LOG_DIR/kernel_tuning.txt"

grep -q '^HOT_CPUS=' "$ENV_FILE" 2>/dev/null && sed -i "s/^HOT_CPUS=.*/HOT_CPUS=${HOT_CPUS}/" "$ENV_FILE" || echo "HOT_CPUS=${HOT_CPUS}" >> "$ENV_FILE"
grep -q '^HFT3_ISOL_CPUS=' "$ENV_FILE" 2>/dev/null && sed -i "s/^HFT3_ISOL_CPUS=.*/HFT3_ISOL_CPUS=${HOT_CPUS}/" "$ENV_FILE" || echo "HFT3_ISOL_CPUS=${HOT_CPUS}" >> "$ENV_FILE"

echo "KERNEL_REBOOT_REQUIRED=1" > "$LOG_DIR/kernel_reboot_required"
echo "Kernel tuning done. Reboot required for GRUB."
