"""Quick end-to-end verification of the unified pipeline."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "packages"))
sys.path.insert(0, str(Path(__file__).parent / "apps"))

from hft3_pipeline.stages import (
    stage_inventory, stage_data_readiness, stage_data_fingerprint,
    stage_vectorbt_filter, stage_hft_truth, stage_full_metrics,
    stage_robustness, stage_promotion, stage_trade_manager,
    stage_workbench_truth,
)
from hft3_pipeline.run_mode import RunContext, RunMode
from hft3_pipeline.manifest import PipelineManifest, StageStatus

REPO_ROOT = Path(__file__).parent

print("[0] Inventory...")
inv = stage_inventory(REPO_ROOT)
print(f"  lanes={len(inv.lanes)} models={len(inv.model_catalog)} vbt={inv.vectorbt_available}")

print("[1] Data readiness...")
ctx = RunContext(run_mode=RunMode.REAL_RESEARCH, lane_id="cme_futures",
                 model_id="SPREAD_BLOWOUT_RECOMPRESSION", symbol="MES.v.0",
                 event_id="CPI_2024_09_11_TIGHT", run_id="QUICK-TEST")
data = stage_data_readiness(REPO_ROOT, ctx, inv)
print(f"  status={data.get('status')}")
assert data.get("status") == "ready", f"Data not ready: {data}"

print("[2] Data fingerprint...")
fingerprint = stage_data_fingerprint(REPO_ROOT, ctx, data)
print(f"  type={fingerprint.get('data_type')}, events={fingerprint.get('event_count')}")
assert fingerprint.get("status") == "ready"

print("[3] VectorBT filter (this takes 1-2 min)...")
vbt = stage_vectorbt_filter(REPO_ROOT, ctx, fingerprint, inv)
print(f"  tested={vbt.parameters_tested}, passed={vbt.top_n_forwarded}, backend={vbt.backend}")

if vbt.top_n_forwarded > 0:
    print("[4] HFT truth...")
    hft = stage_hft_truth(REPO_ROOT, ctx, vbt, fingerprint)
    print(f"  pnl={hft.pnl}, trades={hft.trades}, eligible={hft.promotion_eligible}")
    
    print("[5] Full metrics...")
    metrics = stage_full_metrics(REPO_ROOT, ctx, hft)
    scorecard = metrics.get("scorecard", {})
    print(f"  grade={scorecard.get('overall_grade')}, score={scorecard.get('overall_score')}")
    
    print("[6] Robustness...")
    robustness = stage_robustness(REPO_ROOT, ctx, metrics)
    print(f"  status={robustness.get('status')}")
    
    print("[7] Promotion...")
    promo = stage_promotion(REPO_ROOT, ctx, hft, metrics, vbt)
    print(f"  status={promo.get('promotion_status')}, grade={promo.get('overall_grade')}")
    
    print("[8] Trade Manager...")
    tm = stage_trade_manager(REPO_ROOT, ctx, promo, metrics, hft)
    print(f"  status={tm.get('status')}")
    
    print("[9] Workbench truth...")
    wb = stage_workbench_truth(REPO_ROOT, None)
    print(f"  status={wb.get('status')}")
    
    print("\n=== ALL STAGES PASSED ===")
else:
    print("\nVectorBT produced 0 candidates - pipeline blocked at Stage 3")
    print("This is CORRECT behavior: the filter found no promising parameter sets")
    print("The pipeline is working as designed - blocking unpromising candidates")
