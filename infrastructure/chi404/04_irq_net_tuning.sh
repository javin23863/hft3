#!/bin/bash
set -euo pipefail

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

RUN_ID="${RUN_ID:-}"
LOG_DIR="${HFT3_TUNING_LOG_DIR:-/root/hft3/logs/tuning/${RUN_ID}}"
mkdir -p "$LOG_DIR"
OUT="$LOG_DIR/irq_net.txt"

NIC="${HFT3_NIC:-$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1); exit}')}"
RITHMIC_CPU="${HFT3_RITHMIC_CPU:-1}"
HOT_CPUS="${HOT_CPUS:-2-11}"

{
  echo "NIC=$NIC RITHMIC_CPU=$RITHMIC_CPU HOT_CPUS=$HOT_CPUS"
  ethtool -i "$NIC" 2>/dev/null || echo "ethtool -i failed"
  echo "--- offloads ---"
  for off in gro lro gso tso; do
    if ethtool -K "$NIC" "$off" off 2>/dev/null; then
      echo "$off off OK"
    else
      echo "$off off FAILED"
    fi
  done
  echo "--- offloads verify ---"
  ethtool -k "$NIC" 2>/dev/null | grep -E 'generic-receive-offload|generic-segmentation-offload|tcp-segmentation-offload|large-receive-offload' || true
  echo "--- rings (before) ---"
  ethtool -g "$NIC" 2>/dev/null || echo "ethtool -g failed"
  RX_MAX=$(ethtool -g "$NIC" 2>/dev/null | awk '/Pre-set maximums:/{f=1;next} f&&/^RX:/{print $2; exit}')
  TX_MAX=$(ethtool -g "$NIC" 2>/dev/null | awk '/Pre-set maximums:/{f=1;next} f&&/^TX:/{print $2; exit}')
  TARGET_RX=${RX_MAX:-4096}
  TARGET_TX=${TX_MAX:-4096}
  if [[ "$TARGET_RX" -gt 4096 ]]; then TARGET_RX=4096; fi
  if [[ "$TARGET_TX" -gt 4096 ]]; then TARGET_TX=4096; fi
  ethtool -G "$NIC" rx "$TARGET_RX" tx "$TARGET_TX" 2>/dev/null \
    && echo "rings set rx=${TARGET_RX} tx=${TARGET_TX} OK" \
    || echo "rings set rx=${TARGET_RX} tx=${TARGET_TX} SKIPPED"
  echo "--- rings (after) ---"
  ethtool -g "$NIC" 2>/dev/null || true
  echo "--- IRQ affinity ---"
  AFF_MASK=$(python3 -c "print(format(1<<int('$RITHMIC_CPU'), 'x'))")
  echo "affinity_mask=$AFF_MASK"
  grep -E "${NIC}|${NIC//./}" /proc/interrupts | while read -r line; do
    irq=$(echo "$line" | awk '{print $1}' | tr -d ':')
    [[ "$irq" =~ ^[0-9]+$ ]] || continue
    echo "$AFF_MASK" > "/proc/irq/${irq}/smp_affinity" 2>/dev/null && echo "irq $irq -> cpu $RITHMIC_CPU" || echo "irq $irq SKIPPED"
  done
  echo "--- RPS ---"
  for rx in /sys/class/net/${NIC}/queues/rx-*/rps_cpus; do
    [[ -f "$rx" ]] && echo "ff" > "$rx" 2>/dev/null && echo "rps $rx" || true
  done
  echo "--- sysctl ---"
  sysctl -w net.core.netdev_max_backlog=250000 2>/dev/null || true
  sysctl -w net.core.rmem_max=134217728 2>/dev/null || true
  sysctl -w net.ipv4.tcp_low_latency=1 2>/dev/null || true
  GW=$(ip route | awk '/default/ {print $3; exit}')
  echo "--- RTT gateway $GW ---"
  [[ -n "$GW" ]] && ping -c 20 -i 0.2 -q "$GW" || echo "no gateway"
  RH="${HFT3_RITHMIC_HOST:-}"
  if [[ -n "$RH" ]]; then
    echo "--- RTT Rithmic host $RH ---"
    ping -c 20 -i 0.2 -q "$RH" || true
  else
    echo "HFT3_RITHMIC_HOST not set — skip Rithmic RTT"
  fi
} | tee "$OUT"

RX_MAX=$(ethtool -g "$NIC" 2>/dev/null | awk '/Pre-set maximums:/{f=1;next} f&&/^RX:/{print $2; exit}')
TX_MAX=$(ethtool -g "$NIC" 2>/dev/null | awk '/Pre-set maximums:/{f=1;next} f&&/^TX:/{print $2; exit}')
TARGET_RX=${RX_MAX:-4096}
TARGET_TX=${TX_MAX:-4096}
if [[ "$TARGET_RX" -gt 4096 ]]; then TARGET_RX=4096; fi
if [[ "$TARGET_TX" -gt 4096 ]]; then TARGET_TX=4096; fi

python3 - "$NIC" "$LOG_DIR/ring_buffer_limitation.json" "$TARGET_RX" "$TARGET_TX" <<'PY'
import json
import re
import subprocess
import sys

nic, out_path = sys.argv[1], sys.argv[2]
req_rx = int(sys.argv[3]) if len(sys.argv) > 3 else 4096
req_tx = int(sys.argv[4]) if len(sys.argv) > 4 else 4096
try:
    text = subprocess.check_output(["ethtool", "-g", nic], text=True, stderr=subprocess.STDOUT)
except subprocess.CalledProcessError as exc:
    payload = {
        "nic": nic,
        "status": "unknown",
        "error": exc.output.strip(),
        "rx_current": None,
        "tx_current": None,
        "rx_max": None,
        "tx_max": None,
        "requested_rx": req_rx,
        "requested_tx": req_tx,
        "limitation": "Could not read ring buffer sizes",
    }
    open(out_path, "w", encoding="utf-8").write(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote {out_path} (ethtool -g failed)", file=sys.stderr)
    sys.exit(0)

def _field(label: str) -> int | None:
    m = re.search(rf"^{label}:\s*(\d+)", text, re.MULTILINE)
    return int(m.group(1)) if m else None

rx_cur = _field("RX:")
tx_cur = _field("TX:")
rx_max = _field("RX Mini:")  # fallback handled below
# ethtool -g format: Pre-set maximums then Current hardware settings
max_block = re.search(r"Pre-set maximums:(.*?)(?:Current hardware settings:|$)", text, re.DOTALL)
cur_block = re.search(r"Current hardware settings:(.*)", text, re.DOTALL)
if max_block:
    mb = max_block.group(1)
    rx_max = int(re.search(r"RX:\s*(\d+)", mb).group(1)) if re.search(r"RX:\s*(\d+)", mb) else rx_max
    tx_max = int(re.search(r"TX:\s*(\d+)", mb).group(1)) if re.search(r"TX:\s*(\d+)", mb) else None
else:
    tx_max = None
if cur_block:
    cb = cur_block.group(1)
    rx_cur = int(re.search(r"RX:\s*(\d+)", cb).group(1)) if re.search(r"RX:\s*(\d+)", cb) else rx_cur
    tx_cur = int(re.search(r"TX:\s*(\d+)", cb).group(1)) if re.search(r"TX:\s*(\d+)", cb) else tx_cur

limitation = None
if rx_max is not None and rx_max < req_rx:
    limitation = f"Hardware RX ring max is {rx_max}; requested {req_rx}"
elif tx_max is not None and tx_max < req_tx:
    limitation = f"Hardware TX ring max is {tx_max}; requested {req_tx}"

payload = {
    "nic": nic,
    "status": "documented",
    "rx_current": rx_cur,
    "tx_current": tx_cur,
    "rx_max": rx_max,
    "tx_max": tx_max,
    "requested_rx": req_rx,
    "requested_tx": req_tx,
    "limitation": limitation,
}
open(out_path, "w", encoding="utf-8").write(json.dumps(payload, indent=2) + "\n")
print(f"Wrote {out_path}")
PY

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
grep -q '^HFT3_NIC=' "$ENV_FILE" && sed -i "s/^HFT3_NIC=.*/HFT3_NIC=${NIC}/" "$ENV_FILE" || echo "HFT3_NIC=${NIC}" >> "$ENV_FILE"
GW=$(ip route | awk '/default/ {print $3; exit}')
[[ -n "$GW" ]] && { grep -q '^HFT3_GATEWAY_IP=' "$ENV_FILE" && sed -i "s/^HFT3_GATEWAY_IP=.*/HFT3_GATEWAY_IP=${GW}/" "$ENV_FILE" || echo "HFT3_GATEWAY_IP=${GW}" >> "$ENV_FILE"; }

echo "IRQ/net tuning written to $OUT"
