import json
from collections import Counter
p="/root/hft3/repo/research_cards/pipeline_runs/paid_full_20260620T001204Z/paid_screen_run_manifest.json"
d=json.load(open(p))
print("status", d.get("status"))
print("completed", d.get("completed_work_units"), "failed", d.get("failed_work_units"), "expected", d.get("expected_work_units"))
c=Counter(u.get("status") for u in (d.get("unit_results") or []))
err=Counter(u.get("error") for u in (d.get("unit_results") or []) if u.get("status")!="OK")
print("status_counts", dict(c))
print("error_counts_top", err.most_common(5))
oks=[u for u in (d.get("unit_results") or []) if u.get("status")=="OK"][:3]
print("ok_sample", [(u.get("unit_id"), u.get("error")) for u in oks])
