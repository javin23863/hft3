#!/bin/bash
# Capture CHI404 restore point before memory upgrade (PDF gap-fill).
set -euo pipefail

RESTORE_ID="${RESTORE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
RESTORE_ROOT="${HFT3_RESTORE_ROOT:-/root/hft3/restore_points}"
OUT="${RESTORE_ROOT}/${RESTORE_ID}"
ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"

if [[ ! -f /etc/default/grub ]]; then
  echo "ERROR: /etc/default/grub missing — cannot capture restore point" >&2
  exit 1
fi

mkdir -p "$OUT/etc/default" "$OUT/etc/sysctl.d" "$OUT/etc/systemd/system" "$OUT/root/hft3" "$OUT/proc" "$OUT/diagnostics"

cp -a /etc/default/grub "$OUT/etc/default/grub"

if [[ -f /etc/sysctl.d/99-hft3.conf ]]; then
  cp -a /etc/sysctl.d/99-hft3.conf "$OUT/etc/sysctl.d/99-hft3.conf"
fi

if [[ -f "$ENV_FILE" ]]; then
  cp -a "$ENV_FILE" "$OUT/root/hft3/.env"
fi

for unit in /etc/systemd/system/hft3-*.service; do
  [[ -f "$unit" ]] || continue
  cp -a "$unit" "$OUT/etc/systemd/system/"
done

for dropin in /etc/systemd/system/hft3-*.service.d; do
  [[ -d "$dropin" ]] || continue
  base=$(basename "$dropin")
  mkdir -p "$OUT/etc/systemd/system/$base"
  cp -a "$dropin/." "$OUT/etc/systemd/system/$base/"
done

cp /proc/cmdline "$OUT/proc/cmdline"
lscpu > "$OUT/diagnostics/lscpu.txt" 2>&1 || true
if command -v cpupower >/dev/null; then
  cpupower idle-info > "$OUT/diagnostics/cpupower_idle_info.txt" 2>&1 || true
  cpupower frequency-info > "$OUT/diagnostics/cpupower_frequency_info.txt" 2>&1 || true
else
  echo "cpupower not installed" > "$OUT/diagnostics/cpupower_idle_info.txt"
fi

HOT_CPUS=""
if [[ -f "$ENV_FILE" ]]; then
  HOT_CPUS=$(grep -E '^HOT_CPUS=' "$ENV_FILE" | tail -1 | cut -d= -f2- || true)
fi

IDLE_DISABLED_AT_CAPTURE="unknown"
if command -v cpupower >/dev/null; then
  IDLE_DISABLED_AT_CAPTURE=$(OUT="$OUT" python3 << PY
from pathlib import Path
import os

path = Path(os.environ["OUT"]) / "diagnostics" / "cpupower_idle_info.txt"
if not path.exists():
    print("unknown")
    raise SystemExit(0)
text = path.read_text(encoding="utf-8", errors="replace")
in_states = False
saw_non_poll = False
for line in text.splitlines():
    if "Available idle states" in line:
        in_states = True
        continue
    if not in_states:
        continue
    stripped = line.strip()
    if not stripped or stripped.startswith("CPU"):
        continue
    name = stripped.split(":")[0].strip()
    if name.upper() == "POLL":
        continue
    saw_non_poll = True
    if "disabled" not in line.lower():
        print("false")
        raise SystemExit(0)
if not saw_non_poll:
    print("unknown")
else:
    print("true")
PY
)
fi

python3 << PY
import json
import os
from datetime import datetime, timezone
from pathlib import Path

out = Path("${OUT}")
files = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())
manifest = {
    "restore_id": "${RESTORE_ID}",
    "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "hostname": os.uname().nodename,
    "hot_cpus": "${HOT_CPUS}",
    "cmdline": Path("/proc/cmdline").read_text().strip(),
    "idle_disabled_at_capture": "${IDLE_DISABLED_AT_CAPTURE}",
    "files": files,
}
(out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(json.dumps(manifest, indent=2))
PY

echo "RESTORE_ID=${RESTORE_ID}" | tee "$OUT/RESTORE_ID.txt"
echo "Restore point captured: $OUT"
