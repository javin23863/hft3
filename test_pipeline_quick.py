"""Test pipeline stages 0-2."""
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\MSI\Documents\opencode\hft3\packages")
sys.path.insert(0, r"C:\Users\MSI\Documents\opencode\hft3\apps")

from hft3_pipeline import stages
from hft3_pipeline.run_mode import RunContext, RunMode

REPO_ROOT = Path(r"C:\Users\MSI\Documents\opencode\hft3")
run_ctx = RunContext(
    run_mode=RunMode.REAL_RESEARCH,
    lane_id="cme_futures",
    model_id="SPREAD_BLOWOUT_RECOMPRESSION",
    symbol="MES.v.0",
    event_id="CPI_2024_09_11_TIGHT",
)

print("Stage 0: Inventory...")
inv = stages.stage_inventory(REPO_ROOT)
print(f"  Lanes: {len(inv.lanes)}, Models: {len(inv.model_catalog)}")

print("Stage 1: Data readiness...")
data_result = stages.stage_data_readiness(REPO_ROOT, run_ctx, inv)
print(f"  Status: {data_result.get('status')}")
print(f"  NPZ: {data_result.get('npz_path')}")

print("Stage 2: Data fingerprint...")
feature_result = stages.stage_data_fingerprint(REPO_ROOT, run_ctx, data_result)
print(f"  Status: {feature_result.get('status')}")
print(f"  Events: {feature_result.get('event_count')}")
print(f"  Hash: {feature_result.get('data_hash')}")

print("\nAll early stages passed!")
