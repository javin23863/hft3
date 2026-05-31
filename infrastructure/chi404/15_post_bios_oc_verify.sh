#!/bin/bash
# Post-BIOS verify: DDR5 >= 4800 MT/s configured + hot-CPU MHz under stress.
set -euo pipefail

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${HFT3_OC_LOG_DIR:-/root/hft3/logs/oc/${RUN_ID}}"
mkdir -p "$LOG_DIR"

MIN_MHZ="${HFT3_OC_MIN_MHZ:-5400}"
TARGET_MHZ="${HFT3_OC_TARGET_MHZ:-5700}"
MIN_MEM_MTS="${HFT3_OC_MIN_MEM_MTS:-4800}"
HOT_CPUS="${HOT_CPUS:-2-11}"
STRESS_SEC="${HFT3_OC_STRESS_SEC:-90}"

FIRST=$(echo "$HOT_CPUS" | cut -d- -f1)
LAST=$(echo "$HOT_CPUS" | cut -d- -f2)
CPU_LIST=$(seq "$FIRST" "$LAST" | tr '\n' ' ')

OUT_JSON="$LOG_DIR/oc_verify.json"
export OUT_JSON MIN_MHZ TARGET_MHZ MIN_MEM_MTS CPU_LIST STRESS_SEC LOG_DIR RUN_ID

{
  echo "=== post-BIOS OC verify RUN_ID=$RUN_ID ==="
  dmidecode -t memory 2>/dev/null | grep -E 'Configured Memory Speed|Speed:|Size:' || true
  lscpu | grep -E 'Model name|MHz|boost' || true
} | tee "$LOG_DIR/oc_verify.txt"

stress-ng --cpu "$((LAST - FIRST + 1))" --cpu-method matrixprod --taskset "$FIRST-$LAST" \
  --timeout "${STRESS_SEC}s" >/dev/null 2>&1 &
STRESS_PID=$!
sleep 3

FREQ_SAMPLES="$LOG_DIR/freq_samples.txt"
: > "$FREQ_SAMPLES"
for _ in $(seq 1 30); do
  for cpu in $CPU_LIST; do
    f="/sys/devices/system/cpu/cpu${cpu}/cpufreq/scaling_cur_freq"
    [[ -f "$f" ]] && echo "$cpu $(cat "$f")" >> "$FREQ_SAMPLES"
  done
  sleep 2
done
wait "$STRESS_PID" 2>/dev/null || true

python3 << 'PY'
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

log_dir = Path(os.environ["LOG_DIR"])
out_json = os.environ["OUT_JSON"]
min_mhz = int(os.environ["MIN_MHZ"])
target_mhz = int(os.environ["TARGET_MHZ"])
min_mem = int(os.environ["MIN_MEM_MTS"])
cpu_list = [int(x) for x in os.environ["CPU_LIST"].split() if x.strip()]

def sh(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT).strip()
    except subprocess.CalledProcessError as exc:
        return exc.output.strip()

dmidecode = sh("dmidecode -t memory 2>/dev/null")
cfg_speeds = []
for m in re.finditer(r"Configured Memory Speed:\s*(\d+)\s*MT/s", dmidecode):
    cfg_speeds.append(int(m.group(1)))
populated = [s for s in cfg_speeds if s > 0]
min_cfg = min(populated) if populated else 0

samples = []
freq_file = log_dir / "freq_samples.txt"
if freq_file.exists():
    for line in freq_file.read_text().splitlines():
        parts = line.split()
        if len(parts) == 2:
            samples.append((int(parts[0]), int(parts[1])))

max_khz = max((k for _, k in samples), default=0)
max_mhz = max_khz / 1000.0 if max_khz else 0.0
per_cpu_max = {}
for cpu, khz in samples:
    per_cpu_max[cpu] = max(per_cpu_max.get(cpu, 0), khz)
per_cpu_mhz = {str(c): round(v / 1000.0, 1) for c, v in sorted(per_cpu_max.items())}
hot_mhz = [per_cpu_mhz[str(c)] for c in cpu_list if str(c) in per_cpu_mhz]
min_hot_mhz = min(hot_mhz) if hot_mhz else 0.0

failures = []
if min_cfg < min_mem:
    failures.append(f"memory_configured_mts={min_cfg} < {min_mem}")
if min_hot_mhz < min_mhz:
    failures.append(f"min_hot_cpu_mhz={min_hot_mhz:.0f} < {min_mhz}")

mce = sh("dmesg -T 2>/dev/null | tail -300 | grep -iE 'machine check|hardware error' || true")
if mce.strip():
    failures.append("mce_detected_in_recent_dmesg")

status = "PASS" if not failures else "FAIL"
payload = {
    "run_id": os.environ["RUN_ID"],
    "verified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "memory_configured_mts_min": min_cfg,
    "memory_target_mts": min_mem,
    "max_hot_cpu_mhz": round(max_mhz, 1),
    "min_hot_cpu_mhz": round(min_hot_mhz, 1),
    "min_hot_cpu_mhz_gate": min_mhz,
    "target_mhz_advertised": target_mhz,
    "per_cpu_max_mhz": per_cpu_mhz,
    "failures": failures,
    "oc_verify": status,
}
Path(out_json).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2))
print(f"OC_VERIFY={status}")
if failures:
    raise SystemExit(1)
PY

echo "OC verify JSON: $OUT_JSON"
