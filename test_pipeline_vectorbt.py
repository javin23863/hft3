"""Test pipeline stage 3 - VectorBT filter."""
import sys
import time
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
print(f"  VectorBT available: {inv.vectorbt_available}")

print("Stage 1: Data readiness...")
data_result = stages.stage_data_readiness(REPO_ROOT, run_ctx, inv)
print(f"  Status: {data_result.get('status')}")

print("Stage 2: Data fingerprint...")
feature_result = stages.stage_data_fingerprint(REPO_ROOT, run_ctx, data_result)
print(f"  Events: {feature_result.get('event_count')}")

print("Stage 3: VectorBT filter...")
t0 = time.time()
vectorbt_manifest = stages.stage_vectorbt_filter(REPO_ROOT, run_ctx, feature_result, inv)
dt = time.time() - t0
print(f"  Time: {dt:.2f}s")
print(f"  Backend: {vectorbt_manifest.backend}")
print(f"  Parameters tested: {vectorbt_manifest.parameters_tested}")
print(f"  Top candidates: {vectorbt_manifest.top_n_forwarded}")
if vectorbt_manifest.top_candidates:
    best = vectorbt_manifest.top_candidates[0]
    print(f"  Best PnL: {best.get('net_pnl')}")
    print(f"  Best Sharpe: {best.get('sharpe')}")
    print(f"  Best trades: {best.get('num_trades')}")

print("\nVectorBT filter stage passed!")
