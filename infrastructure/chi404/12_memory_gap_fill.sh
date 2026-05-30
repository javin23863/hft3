#!/bin/bash
# PDF §2–3 gap-fill only: append missing GRUB tokens (no cpupower — use 12_memory_idle_apply.sh).
# Does NOT re-run full kernel tuning or strip existing cmdline args.
set -euo pipefail

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
LOG_DIR="${HFT3_MEMORY_LOG_DIR:-/root/hft3/logs/memory_upgrade/${RUN_ID}}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$LOG_DIR"

GRUB_FILE="/etc/default/grub"
OUT="$LOG_DIR/memory_gap_fill.txt"
log() { echo "$*" | tee -a "$OUT"; }

log "=== memory gap-fill (GRUB) RUN_ID=$RUN_ID ==="
log "HOT_CPUS=${HOT_CPUS:-unset} (logging only — not rewriting isolation)"
log "[cmdline before]"
cat /proc/cmdline | tee -a "$OUT"

cp "$GRUB_FILE" "$LOG_DIR/grub_before.txt"

append_grub_token() {
  local token="$1"
  local cmdline="$2"
  if grep -qF "$token" <<< "$cmdline"; then
    log "SKIP already present: $token"
    return 0
  fi
  export TOKEN="$token"
  python3 << 'PY'
import os
import re
from pathlib import Path

token = os.environ["TOKEN"]
grub = Path("/etc/default/grub")
text = grub.read_text()
m = re.search(r'^GRUB_CMDLINE_LINUX_DEFAULT="([^"]*)"', text, re.M)
if not m:
    raise SystemExit("GRUB_CMDLINE_LINUX_DEFAULT not found")
args = m.group(1).split()
if token in args:
    print(f"SKIP grub already has {token}")
else:
    args.append(token)
    merged = " ".join(args)
    text2 = re.sub(
        r'^GRUB_CMDLINE_LINUX_DEFAULT="[^"]*"',
        f'GRUB_CMDLINE_LINUX_DEFAULT="{merged}"',
        text,
        count=1,
        flags=re.M,
    )
    grub.write_text(text2)
    print(f"APPENDED {token}")
PY
}

CMDLINE=$(cat /proc/cmdline)

append_grub_token "rcu_nocb_poll" "$CMDLINE"

if [[ "${HFT3_SKIP_IDLE_POLL:-0}" != "1" ]]; then
  append_grub_token "idle=poll" "$CMDLINE"
else
  log "SKIP idle=poll (HFT3_SKIP_IDLE_POLL=1)"
fi

append_grub_token "acpi_irq_nobalance" "$CMDLINE"

if lscpu 2>/dev/null | grep -qiE 'vendor id.*intel'; then
  append_grub_token "intel_idle.max_cstate=0" "$CMDLINE"
else
  log "SKIP intel_idle.max_cstate=0 (non-Intel CPU)"
fi

MEMORY_GRUB_CHANGED=0
if ! cmp -s "$LOG_DIR/grub_before.txt" "$GRUB_FILE"; then
  MEMORY_GRUB_CHANGED=1
  cp "$GRUB_FILE" "$LOG_DIR/grub_after.txt"
  update-grub 2>&1 | tee -a "$OUT"
fi

echo "MEMORY_GRUB_CHANGED=${MEMORY_GRUB_CHANGED}" > "$LOG_DIR/memory_grub_changed"
log "MEMORY_GRUB_CHANGED=${MEMORY_GRUB_CHANGED}"

# Pre-reboot idle apply (re-applied after reboot in orchestrator step 4 path)
bash "$SCRIPT_DIR/12_memory_idle_apply.sh" | tee -a "$OUT"

log "Gap-fill complete. logs=$LOG_DIR"
