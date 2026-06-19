import json
from pathlib import Path
m = json.loads(Path("/root/hft3/repo/research_cards/pipeline_runs/paid_full_v2_20260619T005057Z/paid_screen_run_manifest.json").read_text())
for k in ("status","stop_reason","aborted","resume","completed_work_units","failed_work_units","expected_work_units"):
    print(f"{k}={m.get(k)}")
