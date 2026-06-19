import json
from collections import Counter
from pathlib import Path
m = json.loads(Path("/root/hft3/repo/research_cards/pipeline_runs/paid_full_v2_20260619T005057Z/paid_screen_run_manifest.json").read_text())
ur = m.get("unit_results") or []
items = ur[:100]
c = Counter()
for v in items:
    st = v.get("status") if isinstance(v, dict) else str(v)
    c[st] += 1
print("first100_status", dict(c))
print("total_unit_results", len(ur))
allc = Counter(v.get("status") if isinstance(v, dict) else str(v) for v in ur)
print("all_status", dict(allc))
