"""Generate synthetic crypto lane fixtures for walk-forward validation."""
from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np

N_ROWS = 512
WARMUP_ROWS = 56
DT_MS = 1000
BASE_TS = 1_000_000
SPOT0 = 50_000.0
RNG = np.random.default_rng(42)

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures"


def _period(i: int, n: int) -> str:
    if i < int(n * 0.4):
        return "Discovery"
    if i < int(n * 0.7):
        return "Confirmation"
    return "Holdout"


def _shock_cycle(i: int) -> int:
    """Shared 14-row cadence: mempool fee spike at 0, reset at 1-3."""
    return i % 14


def generate_spot_perp_ticks(path: Path) -> None:
    rows: list[dict] = []
    spot = SPOT0
    total = N_ROWS + WARMUP_ROWS
    for i in range(total):
        t = BASE_TS + (i - WARMUP_ROWS) * DT_MS if i >= WARMUP_ROWS else BASE_TS - (WARMUP_ROWS - i) * DT_MS
        cyc = _shock_cycle(i)
        if cyc == 0:
            spot_ret = 0.022 + float(RNG.normal(0, 0.00015))
        elif cyc <= 7:
            spot_ret = 0.016 - 0.0016 * cyc + float(RNG.normal(0, 0.0001))
        else:
            spot_ret = float(RNG.normal(0, 0.000015))
        if i == 0:
            spot = SPOT0
        else:
            spot = spot * (1.0 + spot_ret)
        basis_signal = 3.5 * math.sin(i / 13.0)
        basis = 70.0 + basis_signal + float(RNG.normal(0, 2.5))
        perp = spot + basis
        fr = 0.00007 + 0.000012 * math.sin(i / 17.0) + float(RNG.normal(0, 2e-5))
        phase = i / 11.0
        stress = 0.35 + 0.12 * math.sin(phase)
        if cyc == 0:
            stress = min(0.95, stress + 0.45)
        elif cyc in (1, 2):
            stress = min(0.88, stress + 0.28)
        spread = 2.0 + 0.5 * stress + (4.5 if cyc == 0 else 0.0) + float(RNG.normal(0, 0.08))
        depth = 108.0 - 10.0 * stress - (18.0 if cyc == 0 else 0.0) + float(RNG.normal(0, 1.5))
        rows.append({
            "exchange_timestamp": t,
            "validation_period": _period(i - WARMUP_ROWS, N_ROWS) if i >= WARMUP_ROWS else "Discovery",
            "spot_mid": round(spot, 2),
            "perp_mid": round(perp, 2),
            "perp_mid_binance": round(perp + 2.0, 2),
            "perp_mid_okx": round(perp - 2.0, 2),
            "funding_rate": round(max(1e-6, fr), 6),
            "funding_rate_binance": round(max(1e-6, fr + 0.000007), 6),
            "funding_rate_okx": round(max(1e-6, fr - 0.000007), 6),
            "spot_return": round(spot_ret, 6),
            "perp_return": round(spot_ret * 1.03, 6),
            "bid_ask_spread": round(max(0.6, spread), 3),
            "depth_btc": round(max(45.0, depth), 2),
            "order_imbalance": round(0.035 * math.sin(i / 9.0) + float(RNG.normal(0, 0.012)), 4),
        })
    _write_csv(path, [r for r in rows if r["exchange_timestamp"] >= BASE_TS])


def generate_mempool_snapshots(path: Path) -> None:
    rows: list[dict] = []
    total = N_ROWS + WARMUP_ROWS
    for i in range(total):
        t = BASE_TS + (i - WARMUP_ROWS) * DT_MS if i >= WARMUP_ROWS else BASE_TS - (WARMUP_ROWS - i) * DT_MS
        node_t = t - 100
        cyc = _shock_cycle(i)
        usage = 0.30 + 0.16 * math.sin(i / 19.0) + (0.22 if cyc == 0 else 0.0)
        if cyc == 0:
            fee = 185.0 + float(RNG.normal(0, 3.0))
        elif cyc == 1:
            fee = 11.0 + float(RNG.normal(0, 0.25))
        elif cyc in (2, 3):
            fee = 13.0 + float(RNG.normal(0, 0.3))
        else:
            fee = 12.0 + 4.0 * math.sin(i / 15.0) + float(RNG.normal(0, 0.4))
        rows.append({
            "node_observation_time": node_t,
            "exchange_timestamp": t,
            "mempool_bytes": int(120_000_000 + 80_000_000 * usage),
            "mempool_max_bytes": 300_000_000,
            "mempool_tx_count": int(40_000 + 20_000 * usage),
            "min_fee_sat": round(max(5.0, fee), 2),
            "btc_blockspace_stress_score": round(min(0.95, usage + 0.04 * math.sin(i / 7.0)), 4),
            "node_clock_drift_ms": 1.0,
            "network_latency_ms": 5.0,
            "processing_latency_ms": 2.0,
            "exchange_clock_drift_ms": 0.5,
            "estimated_latency_ms": 7.0,
        })
    _write_csv(path, [r for r in rows if r["exchange_timestamp"] >= BASE_TS])


def generate_deribit_surface(path: Path) -> None:
    rows: list[dict] = []
    spot = SPOT0
    total = N_ROWS + WARMUP_ROWS
    for i in range(total):
        t = BASE_TS + (i - WARMUP_ROWS) * DT_MS if i >= WARMUP_ROWS else BASE_TS - (WARMUP_ROWS - i) * DT_MS
        cyc = _shock_cycle(i)
        if cyc == 0:
            spot_ret = 0.022 + float(RNG.normal(0, 0.00015))
        elif cyc <= 7:
            spot_ret = 0.016 - 0.0016 * cyc + float(RNG.normal(0, 0.0001))
        else:
            spot_ret = float(RNG.normal(0, 0.000015))
        if i == 0:
            spot = SPOT0
        else:
            spot = spot * (1.0 + spot_ret)
        rv_proxy = 0.46 + 0.035 * abs(math.sin(i / 17.0))
        iv = rv_proxy + 0.035 * math.sin(i / 21.0) + float(RNG.normal(0, 0.045))
        if cyc in (0, 1, 2):
            iv += 0.018 * (3 - cyc)
        rows.append({
            "exchange_timestamp": t,
            "atm_iv": round(max(0.35, iv), 4),
            "skew_25d": round(0.018 + 0.007 * math.sin(i / 13.0), 4),
            "term_structure_slope": round(0.009 + 0.002 * math.sin(i / 11.0), 4),
            "call_mid": round(spot * 0.024, 2),
            "put_mid": round(spot * 0.023, 2),
            "spot_mid": round(spot, 2),
            "strike": round(spot / 100) * 100,
            "rate": 0.05,
            "yield_q": 0.0,
            "tau_years": round(0.08 - i * 1e-6, 4),
            "iv_rv_zscore": round(0.35 * math.sin(i / 17.0), 4),
            "vol_surface_quality_flag": 1,
        })
    _write_csv(path, [r for r in rows if r["exchange_timestamp"] >= BASE_TS])


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    generate_spot_perp_ticks(FIX / "spot_perp_ticks.csv")
    generate_mempool_snapshots(FIX / "mempool_snapshots.csv")
    generate_deribit_surface(FIX / "deribit_surface.csv")
    print(f"Wrote {N_ROWS}-row fixtures to {FIX}")


if __name__ == "__main__":
    main()
