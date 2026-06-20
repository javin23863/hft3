set -euo pipefail
source /root/hft3/.env
cd /root/hft3/repo
CANARY_ID=canary_$(date -u +%Y%m%dT%H%M%SZ)
OUT=/root/hft3/repo/research_cards/pipeline_runs/${CANARY_ID}
mkdir -p "$OUT"
echo '{"event_id": "CPI_2019_06_12_TIGHT", "event_type": "CPI", "hyp_id": 5, "model_id": "SPREAD_BLOWOUT_RECOMPRESSION", "symbol": "MES.v.0", "thesis": "canary", "unit_id": "SPREAD_BLOWOUT_RECOMPRESSION_MES.v.0_CPI_2019_06_12_TIGHT"}' > /tmp/canary_unit.jsonl
EVENTS_HASH=bfa4911d8de2d07821563c16160f9388
LAKE_HASH=$(python3 -c 'import hashlib;print(hashlib.sha256(open("/data/npz/manifest.json","rb").read()).hexdigest()[:32])')
echo "canary_out=$OUT events_hash=$EVENTS_HASH lake_hash=$LAKE_HASH npz_root=$HFT3_NPZ_ROOT"
python3 scripts/run_paid_screen.py --units-jsonl /tmp/canary_unit.jsonl --out "$OUT" --vectorbt-scope paid-compute --workers 1 --ready-gate-file runtime/reports/paid_screen_ready_gate.json --no-llm --events-csv packages/data_system/config/events.csv --events-csv-hash "$EVENTS_HASH" --lake-manifest-hash "$LAKE_HASH" --max-wall-clock-seconds 600
python3 -c "import json;from pathlib import Path;import sys; m=Path(sys.argv[1])/'paid_screen_run_manifest.json'; d=json.loads(m.read_text()); print('manifest_status', d.get('status'), 'completed', d.get('completed_work_units'), 'failed', d.get('failed_work_units')); [print('unit', r.get('unit_id'), r.get('status'), r.get('error')) for r in (d.get('unit_results') or [])[:3]]" "$OUT"
echo CANARY_DIR=$OUT
