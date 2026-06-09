"""Debug momentum signals with mid prices."""
import sys
import numpy as np
from pathlib import Path

sys.path.insert(0, r"C:\Users\MSI\Documents\opencode\hft3\packages")
sys.path.insert(0, r"C:\Users\MSI\Documents\opencode\hft3\apps")

from features_engine.src.features.npz_feed import load_npz_events, iter_mbo_events
from features_engine.src.pipeline.market_state_pipeline import MarketStatePipeline

REPO_ROOT = Path(r"C:\Users\MSI\Documents\opencode\hft3")
npz_path = REPO_ROOT / "data" / "npz" / "MES.v.0_CPI_2024_09_11_TIGHT_mbo.npz"

print("Loading events...")
raw = load_npz_events(npz_path)
if len(raw) > 200:
    indices = np.linspace(0, len(raw) - 1, 200, dtype=int)
    raw = raw[indices]
print(f"  Events: {len(raw)}")

print("Extracting mid prices via MarketStatePipeline...")
pipeline = MarketStatePipeline(tick_size=0.25, latency_ms=1.0)
prices = []
for i, ev in enumerate(iter_mbo_events(raw)):
    state = pipeline.process_event(ev)
    mid = state.f("mid_price", 0.0)
    if mid > 0:
        prices.append(mid)
    if i % 50 == 0:
        print(f"  Event {i}: mid={mid:.2f}, total prices={len(prices)}")

print(f"\n  Total prices: {len(prices)}")
if len(prices) < 50:
    print("  Not enough prices!")
    sys.exit(1)

prices_arr = np.array(prices)
print(f"  Price range: {prices_arr.min():.2f} - {prices_arr.max():.2f}")

print("Computing returns...")
with np.errstate(divide='ignore', invalid='ignore'):
    returns = np.diff(prices_arr) / prices_arr[:-1]
    returns = np.nan_to_num(returns, nan=0.0, posinf=0.0, neginf=0.0)

print(f"  Returns: {len(returns)}")
print(f"  Return range: {returns.min():.6f} - {returns.max():.6f}")

print("Generating momentum signals...")
window = 10
signals_arr = np.zeros(len(returns))
for i in range(window, len(returns)):
    momentum = np.sum(returns[i-window:i])
    signals_arr[i] = np.tanh(momentum * 10)

print(f"  Signal range: {signals_arr.min():.4f} - {signals_arr.max():.4f}")
print(f"  Signal mean: {signals_arr.mean():.4f}, std: {signals_arr.std():.4f}")

print("\nTesting thresholds...")
for threshold in [0.01, 0.02, 0.05, 0.10, 0.15]:
    entries = signals_arr > threshold
    exits = signals_arr < -threshold * 0.5
    print(f"  threshold={threshold}: entries={entries.sum()}, exits={exits.sum()}")
    
    # Simulate one parameter set
    holding = 15
    position, pnl, trade_count, hold_counter, trade_pnls = 0.0, 0.0, 0, 0, []
    for j in range(len(returns)):
        if hold_counter > 0:
            hold_counter -= 1
            pnl += position * returns[j]
            if hold_counter == 0 or (position > 0 and exits[j]) or (position < 0 and entries[j]):
                trade_pnls.append(pnl)
                position, hold_counter = 0.0, 0
        if position == 0 and entries[j]:
            position, hold_counter = 1.0, min(holding, len(returns) - j)
            trade_count += 1
        elif position == 0 and exits[j]:
            position, hold_counter = -1.0, min(holding, len(returns) - j)
            trade_count += 1
    
    net_pnl = float(pnl * 100)
    num_trades = max(trade_count, len(trade_pnls))
    if trade_pnls:
        sharpe = float(np.array(trade_pnls).mean() / max(np.array(trade_pnls).std(), 1e-10) * np.sqrt(252))
    else:
        sharpe = 0.0
    print(f"    -> trades={num_trades}, pnl={net_pnl:.4f}, sharpe={sharpe:.4f}")
