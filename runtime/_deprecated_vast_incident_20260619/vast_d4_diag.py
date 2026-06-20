import json
p="/root/hft3/repo/research_cards/pipeline_runs/paid_full_20260620T001204Z/paid_screen_run_manifest.json"
d=json.load(open(p))
target="SPREAD_BLOWOUT_RECOMPRESSION_MES.v.0_CPI_2019_06_12_TIGHT"
for u in d.get("unit_results") or []:
    if u.get("unit_id")==target:
        print("CPI_canary_unit_in_d4", u)
        break
else:
    print("CPI unit not in results yet")
# count non-zero elapsed
nz=[u for u in (d.get("unit_results") or []) if (u.get("elapsed_seconds") or 0)>0]
print("units_with_elapsed_gt0", len(nz))
if nz:
    print("sample", nz[0].get("unit_id"), nz[0].get("status"), nz[0].get("error"), nz[0].get("elapsed_seconds"))
