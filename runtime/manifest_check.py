import json
import os
from pathlib import Path

run_id = os.environ.get("VBT_FULL_RUN_ID", "paid_full_v2_20260619T042614Z")
default_local = Path(__file__).resolve().parent.parent / "research_cards" / "pipeline_runs" / run_id / "paid_screen_run_manifest.json"
manifest_path = Path(os.environ.get("HFT3_PAID_SCREEN_MANIFEST", default_local))
m = json.loads(manifest_path.read_text())
for k in ("status", "stop_reason", "aborted", "resume", "completed_work_units", "failed_work_units", "expected_work_units"):
    print(f"{k}={m.get(k)}")
