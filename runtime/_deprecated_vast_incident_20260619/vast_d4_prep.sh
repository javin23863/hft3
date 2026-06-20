set -euo pipefail
source /root/hft3/.env
cd /root/hft3/repo
echo HEAD=$(git rev-parse HEAD)
python3 scripts/generate_vbt_paid_units_jsonl.py \
  --events-csv packages/data_system/config/events.csv \
  --symbols MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0 \
  --all-active-models \
  --research-split discovery_confirmation \
  --out runtime/reports/vbt_full_units.jsonl
UNIT_COUNT=$(wc -l < runtime/reports/vbt_full_units.jsonl)
DECL=$(python3 -c "import json;print(json.load(open('runtime/reports/vbt_full_run_declaration.json'))['expected_work_units'])")
echo unit_count=$UNIT_COUNT decl_expected=$DECL
python3 -c "import json; p='research_cards/pipeline_runs/paid_full_v2_20260619T235111Z/paid_screen_run_manifest.json'; d=json.load(open(p)); print('old_run', d.get('status'), 'failed', d.get('failed_work_units'), 'completed', d.get('completed_work_units')); u=(d.get('unit_results') or [])[:3]; print('first3', [(x.get('unit_id'), x.get('status'), x.get('error')) for x in u])"
