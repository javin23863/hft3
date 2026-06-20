#!/usr/bin/env bash
set -euo pipefail

echo "=== Feature store check ==="
for r in "${HFT3_FEATURE_ROOT:-}" "/root/hft3/repo/data/features" "/data/features"; do
  [[ -n "$r" && -d "$r" ]] || continue
  echo "feature_root=$r"
  sample=$(find "$r" -name '*MES.v.0*' 2>/dev/null | head -1 || true)
  if [[ -n "$sample" ]]; then
    echo "sample=$sample"
    python3 - "$sample" <<'PY'
import sys
import pyarrow.parquet as pq
t = pq.read_table(sys.argv[1])
print(f"rows={t.num_rows} cols={t.num_columns}")
PY
  else
    echo "no MES.v.0 sample under $r"
  fi
  break
done

echo "=== Declaration ==="
python3 - <<'PY'
import json
d = json.load(open("/root/hft3/repo/runtime/reports/vbt_full_run_declaration.json"))
print("expected_work_units=", d.get("expected_work_units"))
print("decl_run_id=", d.get("run_id") or d.get("vbt_full_run_id"))
PY

echo "=== Kill old tmux if any ==="
tmux kill-session -t vbt_full_v2 2>/dev/null || true

echo "=== Launch CLEAN v2 run ==="
tmux new -d -s vbt_full_v2 "cd /root/hft3/repo && HFT3_NPZ_ROOT=/data/npz HFT3_MANIFEST_PATH=/data/npz/manifest.json VBT_RESUME=0 bash scripts/run_vbt_paid_screen_vast_full.sh 2>&1 | tee /root/vbt_full_v2.log"
sleep 2
tmux ls
echo "LAUNCHED"
