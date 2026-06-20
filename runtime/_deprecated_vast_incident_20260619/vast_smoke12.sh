set -euo pipefail
source /root/hft3/.env
cd /root/hft3/repo
OUT=/root/hft3/repo/research_cards/pipeline_runs/smoke_recheck_$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$OUT"
# 12 CPI 2019 lines from smoke
grep CPI_2019 runtime/reports/vbt_smoke_units.jsonl 2>/dev/null | head -12 > /tmp/smoke12.jsonl || true
if [ ! -s /tmp/smoke12.jsonl ]; then
  python3 scripts/generate_vbt_paid_units_jsonl.py --events-csv packages/data_system/config/events.csv --symbols MES.v.0,ES.v.0 --model-id SPREAD_BLOWOUT_RECOMPRESSION --event-types CPI --smoke-count 12 --out /tmp/smoke12.jsonl
fi
wc -l /tmp/smoke12.jsonl
EVENTS_HASH=bfa4911d8de2d07821563c16160f9388
LAKE_HASH=$(python3 -c 'import hashlib;print(hashlib.sha256(open("/data/npz/manifest.json","rb").read()).hexdigest()[:32])')
python3 scripts/run_paid_screen.py --units-jsonl /tmp/smoke12.jsonl --out "$OUT" --vectorbt-scope paid-compute --workers 4 --ready-gate-file runtime/reports/paid_screen_ready_gate.json --no-llm --events-csv packages/data_system/config/events.csv --events-csv-hash "$EVENTS_HASH" --lake-manifest-hash "$LAKE_HASH" --max-wall-clock-seconds 1800
python3 /tmp/vast_manifest_check.py 2>/dev/null || python3 -c "import json;from pathlib import Path;d=json.loads((Path('$OUT')/'paid_screen_run_manifest.json').read_text());print(d.get('completed_work_units'), d.get('failed_work_units'), d.get('status'))"
echo OUT=$OUT
