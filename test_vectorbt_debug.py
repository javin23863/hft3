"""Debug VectorBT filter to see why no candidates pass."""
import sys
import numpy as np
from pathlib import Path

sys.path.insert(0, r"C:\Users\MSI\Documents\opencode\hft3\packages")
sys.path.insert(0, r"C:\Users\MSI\Documents\opencode\hft3\apps")

from features_engine.src.features.npz_feed import load_npz_events, iter_mbo_events
from features_engine.src.hypotheses.registry import get_active_hypotheses
from features_engine.src.model_registry import resolve_model_id, get_hyp_id_for_slug
from features_engine.src.pipeline.market_state_pipeline import MarketStatePipeline

REPO_ROOT = Path(r"C:\Users\MSI\Documents\opencode\hft3")
npz_path = REPO_ROOT / "data" / "npz" / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz"

print("Loading events...")
raw = load_npz_events(npz_path)
if len(raw) > 200:
    indices = np.linspace(0, len(raw) - 1, 200, dtype=int)
    raw = raw[indices]
print(f"  Events: {len(raw)}")

print("Resolving hypothesis...")
try:
    canonical = resolve_model_id("SPREAD_BLOWOUT_RECOMPRESSION")
    hyp_id = get_hyp_id_for_slug(canonical)
    hyps = [h for h in get_active_hypotheses() if h.hyp_id == hyp_id]
    print(f"  Canonical: {canonical}, hyp_id: {hyp_id}")
except Exception as e:
    print(f"  Error: {e}")
    hyps = get_active_hypotheses()[:1]

if not hyps:
    print("No hypotheses found!")
    sys.exit(1)

hyp = hyps[0]
print(f"  Hypothesis: {hyp.name}")

print("Processing events through MarketStatePipeline...")
pipeline = MarketStatePipeline(tick_size=0.25, latency_ms=1.0)
signals, prices = [], []
for i, ev in enumerate(iter_mbo_events(raw)):
    state = pipeline.process_event(ev)
    sig = hyp.evaluate(state)
    signals.append(sig)
    price = state.f("mid_price", 4500.0)
    prices.append(price)
    if i % 50 == 0:
        print(f"  Event {i}: signal={sig:.4f}, price={price:.2f}")

print(f"\nSignal stats:")
signals_arr = np.array(signals)
print(f"  Min: {signals_arr.min():.4f}")
print(f"  Max: {signals_arr.max():.4f}")
print(f"  Mean: {signals_arr.mean():.4f}")
print(f"  Std: {signals_arr.std():.4f}")

print(f"\nPrice stats:")
prices_arr = np.array(prices)
print(f"  Min: {prices_arr.min():.2f}")
print(f"  Max: {prices_arr.max():.2f}")
print(f"  Mean: {prices_arr.mean():.2f}")

print(f"\nThresholds:")
for threshold in [0.10, 0.15, 0.20, 0.25]:
    entries = signals_arr > threshold
    exits = signals_arr < -threshold * 0.5
    print(f"  threshold={threshold}: entries={entries.sum()}, exits={exits.sum()}")
