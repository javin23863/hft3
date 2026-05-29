#!/bin/bash
# Pre-production latency report (extended for CHI404).
set -euo pipefail

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

REPORT_DIR="${REPORT_DIR:-.}"
mkdir -p "$REPORT_DIR"
REPORT_FILE="${REPORT_DIR}/latency_report_$(date +%F_%H%M%S).txt"

echo "Generating Pre-production Latency Report..." | tee "$REPORT_FILE"
echo "=========================================" | tee -a "$REPORT_FILE"
echo "Timestamp: $(date -u)" | tee -a "$REPORT_FILE"
echo "" | tee -a "$REPORT_FILE"

echo "[CPU / virt]" | tee -a "$REPORT_FILE"
systemd-detect-virt | tee -a "$REPORT_FILE" || true
lscpu 2>/dev/null | head -25 | tee -a "$REPORT_FILE" || true
echo "cmdline: $(cat /proc/cmdline)" | tee -a "$REPORT_FILE"
if [[ -f /sys/devices/system/cpu/smt/active ]]; then
  echo "SMT active: $(cat /sys/devices/system/cpu/smt/active)" | tee -a "$REPORT_FILE"
fi
echo "" | tee -a "$REPORT_FILE"

echo "[CPU Steal & Load Average]" | tee -a "$REPORT_FILE"
uptime | tee -a "$REPORT_FILE"
if command -v mpstat &>/dev/null; then
  mpstat 1 5 | tee -a "$REPORT_FILE"
  mpstat 1 5 | awk '/Average:/ {print "Average CPU Steal: " $NF "%"}' | tee -a "$REPORT_FILE"
else
  echo "mpstat not installed" | tee -a "$REPORT_FILE"
fi
echo "" | tee -a "$REPORT_FILE"

echo "[Governor]" | tee -a "$REPORT_FILE"
if command -v cpupower &>/dev/null; then
  cpupower frequency-info 2>&1 | head -20 | tee -a "$REPORT_FILE" || true
else
  cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null | tee -a "$REPORT_FILE" || true
fi
echo "" | tee -a "$REPORT_FILE"

echo "[Clock Sync Status]" | tee -a "$REPORT_FILE"
if command -v chronyc &>/dev/null; then
  chronyc tracking | tee -a "$REPORT_FILE"
  echo "" | tee -a "$REPORT_FILE"
  chronyc sources -v 2>&1 | head -30 | tee -a "$REPORT_FILE" || true
else
  echo "chrony not installed" | tee -a "$REPORT_FILE"
fi
echo "" | tee -a "$REPORT_FILE"

echo "[Disk Latency (iostat)]" | tee -a "$REPORT_FILE"
if command -v iostat &>/dev/null; then
  iostat -dx 1 3 | tail -n 8 | tee -a "$REPORT_FILE"
else
  echo "iostat missing" | tee -a "$REPORT_FILE"
fi
echo "" | tee -a "$REPORT_FILE"

echo "[IRQ / NIC summary]" | tee -a "$REPORT_FILE"
NIC="${HFT3_NIC:-$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1); exit}')}"
echo "NIC=$NIC" | tee -a "$REPORT_FILE"
ethtool -k "$NIC" 2>/dev/null | grep -E 'gro|lro|gso|tso' | tee -a "$REPORT_FILE" || true
grep "$NIC" /proc/interrupts | head -5 | tee -a "$REPORT_FILE" || true
echo "" | tee -a "$REPORT_FILE"

echo "[Cyclictest summary]" | tee -a "$REPORT_FILE"
RUN_LOG="${HFT3_TUNING_LOG_DIR:-}"
if [[ -n "$RUN_LOG" ]]; then
  cat "$RUN_LOG"/jitter_gate.txt 2>/dev/null | tee -a "$REPORT_FILE" || echo "no jitter gate yet" | tee -a "$REPORT_FILE"
fi
echo "" | tee -a "$REPORT_FILE"

GW="${HFT3_GATEWAY_IP:-$(ip route | awk '/default/ {print $3; exit}')}"
if [[ -n "$GW" ]]; then
  echo "[Network Latency to gateway $GW]" | tee -a "$REPORT_FILE"
  ping -c 20 -i 0.2 -q "$GW" | tee -a "$REPORT_FILE"
else
  echo "[Network] no gateway" | tee -a "$REPORT_FILE"
fi
RH="${HFT3_RITHMIC_HOST:-}"
if [[ -n "$RH" ]]; then
  echo "[Network Latency to Rithmic host $RH]" | tee -a "$REPORT_FILE"
  ping -c 20 -i 0.2 -q "$RH" | tee -a "$REPORT_FILE"
else
  echo "[Network] HFT3_RITHMIC_HOST not set — skipped" | tee -a "$REPORT_FILE"
fi
echo "" | tee -a "$REPORT_FILE"

SUMMARY_JSON="${REPORT_DIR}/latency_summary.json"
python3 - "$SUMMARY_JSON" "$REPORT_DIR" "${HFT3_TUNING_LOG_DIR:-}" "${GW:-}" "${RH:-}" <<'PY'
import json
import re
import subprocess
import sys
from pathlib import Path

out_path, report_dir, tuning_log, gw, rh = sys.argv[1:6]
tuning = Path(tuning_log) if tuning_log else None

cyclictest_p99 = {}
if tuning and tuning.is_dir():
    for p in sorted(tuning.glob("cyclictest_cpu*_p99_us")):
        cpu = p.name.replace("cyclictest_cpu", "").replace("_p99_us", "")
        try:
            cyclictest_p99[cpu] = int(p.read_text(encoding="utf-8").strip())
        except ValueError:
            pass

def ping_rtt_ms(host: str) -> dict | None:
    if not host:
        return None
    try:
        out = subprocess.check_output(
            ["ping", "-c", "10", "-i", "0.2", "-q", host],
            text=True,
            stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as exc:
        return {"host": host, "error": exc.output.strip()}
    m = re.search(
        r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms",
        out,
    )
    if not m:
        return {"host": host, "raw": out.strip()}
    return {
        "host": host,
        "min_ms": float(m.group(1)),
        "avg_ms": float(m.group(2)),
        "max_ms": float(m.group(3)),
        "mdev_ms": float(m.group(4)),
    }

payload = {
    "cyclictest_p99_us": cyclictest_p99,
    "gateway_ping": ping_rtt_ms(gw),
    "rithmic_ping": ping_rtt_ms(rh) if rh else None,
    "order_rtt_ms": None,
    "note": "order_rtt_ms filled by reports/rithmic_trial/.../latency_profile.json after trial capture",
    "report_dir": str(report_dir),
}
Path(out_path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(f"Wrote {out_path}")
PY

echo "Report generated at $REPORT_FILE"
echo "Summary JSON at $SUMMARY_JSON"
