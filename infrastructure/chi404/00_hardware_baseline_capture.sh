#!/bin/bash
# Capture CHI404 hardware + kernel + NIC baseline as JSON (diff against runtime/chi404/baseline/).
set -euo pipefail

RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${HFT3_HW_BASELINE_DIR:-/root/hft3/logs/hardware_baseline/${RUN_ID}}"
ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
mkdir -p "$LOG_DIR"

[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a
NIC="${HFT3_NIC:-$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1); exit}')}"
export NIC

OUT_JSON="$LOG_DIR/baseline.json"
OUT_TXT="$LOG_DIR/baseline.txt"

{
  echo "=== CHI404 hardware baseline RUN_ID=$RUN_ID ==="
  date -u
  hostname
  echo "[virt] $(systemd-detect-virt 2>/dev/null || echo unknown)"
  echo "[cmdline] $(cat /proc/cmdline)"
  echo "[lscpu]"
  lscpu
  echo "[memory]"
  free -h
  echo "[dmidecode memory summary]"
  dmidecode -t memory 2>/dev/null | grep -E 'Size:|Speed:|Type:|Manufacturer:|Part Number:|Configured Memory Speed:|Error Correction' || true
  echo "[cpupower frequency]"
  cpupower frequency-info 2>/dev/null || echo "cpupower missing"
  echo "[cpupower idle]"
  cpupower idle-info 2>/dev/null || echo "cpupower idle missing"
  echo "[nics]"
  ip -br link || true
  NIC="${HFT3_NIC:-$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1); exit}')}"
  echo "primary_nic=$NIC"
  ethtool -i "$NIC" 2>/dev/null || true
  ethtool -g "$NIC" 2>/dev/null || true
  ethtool -k "$NIC" 2>/dev/null | head -25 || true
  echo "[vm]"
  virsh list --all 2>/dev/null || true
} | tee "$OUT_TXT"

export OUT_JSON OUT_TXT HFT3_CAPTURE_METHOD="${HFT3_CAPTURE_METHOD:-00_hardware_baseline_capture.sh}"

python3 << 'PY'
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

out_json = os.environ["OUT_JSON"]
out_txt = os.environ["OUT_TXT"]
log_txt = Path(out_txt)
text = log_txt.read_text(encoding="utf-8", errors="replace")
cmdline = Path("/proc/cmdline").read_text().strip()

def sh(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.STDOUT).strip()
    except subprocess.CalledProcessError as exc:
        return exc.output.strip()

_lscpu_text = sh("lscpu")

def lscpu_field(name):
    m = re.search(rf"^{re.escape(name)}:\s*(.+)$", _lscpu_text, re.MULTILINE)
    return m.group(1).strip() if m else None

nic = os.environ.get("NIC", "")
ring = {"rx_current": None, "tx_current": None, "rx_max": None, "tx_max": None}
offloads = {}

def _ring_blocks(gtext):
    cur = re.search(r"Current hardware settings:(.*?)(?:\n\n|\Z)", gtext, re.DOTALL)
    mx = re.search(r"Pre-set maximums:(.*?)(?:Current hardware settings:|\Z)", gtext, re.DOTALL)
    return (cur.group(1) if cur else ""), (mx.group(1) if mx else "")

if nic:
    g = sh(f"ethtool -g {nic}")
    cur_b, max_b = _ring_blocks(g)
    for label, key in (("RX:", "rx"), ("TX:", "tx")):
        m = re.search(rf"{re.escape(label)}\s*(\d+)", cur_b)
        if m:
            ring[f"{key}_current"] = int(m.group(1))
        m = re.search(rf"{re.escape(label)}\s*(\d+)", max_b)
        if m:
            ring[f"{key}_max"] = int(m.group(1))
    k = sh(f"ethtool -k {nic}")
    for feat in (
        "generic-receive-offload",
        "generic-segmentation-offload",
        "tcp-segmentation-offload",
        "large-receive-offload",
    ):
        m = re.search(rf"{re.escape(feat)}:\s*(\S+)", k)
        if m:
            offloads[feat.replace("-", "_")] = m.group(1)

gov = "unknown"
freq = sh("cpupower frequency-info 2>/dev/null") or ""
m = re.search(r'The governor "(\w+)"', freq, re.I) or re.search(r"governor\s*:\s*(\S+)", freq, re.I)
if m:
    gov = m.group(1).lower()

dimms = []
for block in re.finditer(
    r"Size: (\d+) GB.*?Type: ([^\n]+).*?Speed: ([^\n]+).*?Manufacturer: ([^\n]+).*?"
    r"Part Number: ([^\n]+).*?Configured Memory Speed: ([^\n]+)",
    text,
    re.DOTALL,
):
    rated_m = re.search(r"(\d+)", block.group(3))
    cfg_m = re.search(r"(\d+)", block.group(6))
    if not rated_m or not cfg_m:
        continue
    dimms.append(
        {
            "size_gib": int(block.group(1)),
            "type": block.group(2).strip(),
            "rated_speed_mts": int(rated_m.group(1)),
            "configured_speed_mts": int(cfg_m.group(1)),
            "manufacturer": block.group(4).strip(),
            "part_number": block.group(5).strip(),
        }
    )

mem_avail = sh("free -g | awk '/^Mem:/{print $2}'")
mem_avail_gib = int(mem_avail) if mem_avail.isdigit() else None
installed_gib = sum(d["size_gib"] for d in dimms) if dimms else mem_avail_gib

payload = {
    "schema_version": "1.0",
    "host_id": sh("hostname") or "CHI404",
    "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "capture_method": os.environ.get("HFT3_CAPTURE_METHOD", "00_hardware_baseline_capture.sh"),
    "hardware": {
        "cpu": {
            "model": lscpu_field("Model name"),
            "vendor": lscpu_field("Vendor ID"),
            "online_cpus": lscpu_field("On-line CPU(s) list"),
            "offline_cpus": lscpu_field("Off-line CPU(s) list"),
            "threads_per_core": lscpu_field("Thread(s) per core"),
            "max_mhz": lscpu_field("CPU max MHz"),
            "cpufreq_governor": gov,
        },
        "memory": {
            "total_installed_gib": installed_gib,
            "total_available_gib": mem_avail_gib,
            "dimms": dimms,
            "dimm_count": len(dimms),
        },
    },
    "firmware_bios": {"audit_status": "operator_manual_required"},
    "kernel_runtime": {
        "kernel": sh("uname -r"),
        "cmdline": cmdline,
    },
    "cpu_layout": {
        "hot_cpus": os.environ.get("HOT_CPUS", ""),
        "rithmic_cpu": os.environ.get("HFT3_RITHMIC_CPU", ""),
        "os_cpu": os.environ.get("HFT3_OS_CPU", "0"),
    },
    "network": {
        "primary_nic": nic,
        "driver": next(
            (ln.split(":", 1)[1].strip() for ln in sh(f"ethtool -i {nic}").splitlines() if ln.startswith("driver:")),
            "",
        )
        if nic
        else "",
        "ring_buffers": ring,
        "offloads_observed": offloads,
    },
    "vm_sidecar": {"virsh_list": sh("virsh list --all 2>/dev/null")},
    "software_layers": {
        "market_state_hot_memory": {
            "config": "apps/workbench/config/hot_memory_universe.yaml",
        },
    },
    "validation": {
        "pass_criteria_json": "infrastructure/chi404/PASS_CRITERIA.json",
        "validate_script": "infrastructure/chi404/validate_pass_criteria.py",
    },
    "known_gaps": [],
    "drift_warnings": [],
}

Path(out_json).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps({"baseline_json": out_json, "baseline_txt": out_txt}, indent=2))
PY

echo "Baseline JSON: $OUT_JSON"
echo "Compare to repo: runtime/chi404/baseline/2026-05-31T030000Z_baseline.json"
