"""Stage A RESEARCH_ONLY pre-filter screen over the precomputed feature store.

Transliterates HypothesisReplayStrategy gating (signal_threshold ±0.15,
position gating, book_one_sided suppression), _fill_metrics FIFO PnL, FeeModel
costs, and _derive_p_value / MultipleTestingGate BH@0.10 from run_event_universe.

RESEARCH_ONLY — P_fill=1 (no queue model).  Stage B restores queue-conditional
fills via LogProbQueueModel2.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats

_REPO = Path(__file__).resolve().parents[4]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# ---------------------------------------------------------------------------
# Constants — mirrors run_event_universe.py convention
# ---------------------------------------------------------------------------

RESEARCH_EMBARGO_START = "2026-01-01"  # ALPHA_CME.md §4 / DEPLOYMENT.md §4.2: research sweeps must never read data >= this date; first 2026 touch is the M9 paper-shadow bundle.

SIGNAL_THRESHOLD = 0.15

# Regime label order as written by finalize() via REGIME_INDEX_MAP → feature_index slots 41-49:
#   41=normal, 42=event_shock, 43=liquidity_vacuum, 44=stop_cascade, 45=prop_flatten,
#   46=book_rebuild, 47=chop, 48=trend_continuation(REGIME_TREND), 49=spread_stress
REGIME_LABELS_ORDERED = (
    "normal",          # slot 41
    "event_shock",     # slot 42
    "liquidity_vacuum",# slot 43
    "stop_cascade",    # slot 44
    "prop_flatten",    # slot 45
    "book_rebuild",    # slot 46
    "chop",            # slot 47
    "trend_continuation",  # slot 48 — FeatureIndex.REGIME_TREND
    "spread_stress",   # slot 49
)
_REGIME_SLOT_START = 41  # FeatureIndex.REGIME_NORMAL

# Hypotheses 42, 43, 45 pass through regardless of stats (queue-sensitive)
QUEUE_SENSITIVE_PASS_THROUGH: frozenset[int] = frozenset({42, 43, 45})

# VIX sibling store naming convention: VIX.OPT/<event_id> or VIX.OPT_<event_id>_features_v1.npz
_VIX_SYMBOL = "VIX.OPT"


# ---------------------------------------------------------------------------
# Tick resolution (mirrors build_feature_store.py / FeeModel.TICK_VALUES)
# ---------------------------------------------------------------------------

_TICK_VALUES = {
    "ES": 12.50,
    "NQ": 5.00,
    "MES": 1.25,
    "MNQ": 0.50,
}

_TICK_SIZES = {
    "ES": 0.25,
    "NQ": 0.25,
    "MES": 0.25,
    "MNQ": 0.25,
}

_TICK_VALUE_FALLBACK = 1.25   # MES default
_TICK_SIZE_FALLBACK = 0.25


def _symbol_root(symbol: str) -> str:
    return symbol.split(".")[0]


def _tick_value(symbol: str) -> float:
    base = _symbol_root(symbol)
    return _TICK_VALUES.get(base, _TICK_VALUE_FALLBACK)


def _tick_size_for(symbol: str) -> float:
    base = _symbol_root(symbol)
    return _TICK_SIZES.get(base, _TICK_SIZE_FALLBACK)


# ---------------------------------------------------------------------------
# FIFO round-trip PnL — transliterated from replay_matrix._fill_metrics
# Pnl units: price-points × qty; convert to USD via tick_value/tick_size.
# ---------------------------------------------------------------------------

def _fifo_trade_pnls(
    fill_sides: list[str],
    fill_prices: list[float],
    fill_qtys: list[float],
) -> list[float]:
    """Return FIFO round-trip PnLs in price-point*qty units (mirrors _fill_metrics)."""
    trade_pnls: list[float] = []
    open_side = 0  # +1 long, -1 short, 0 flat
    open_lots: deque[list[float]] = deque()  # [price, qty]

    for side, px, qty in zip(fill_sides, fill_prices, fill_qtys):
        if qty <= 0.0 or side not in ("BUY", "SELL"):
            continue
        sign = 1 if side == "BUY" else -1
        if open_side in (0, sign):
            open_lots.append([px, qty])
            open_side = sign
            continue
        remaining = qty
        while remaining > 1e-12 and open_lots:
            lot = open_lots[0]
            matched = min(remaining, lot[1])
            trade_pnls.append((px - lot[0]) * matched * open_side)
            lot[1] -= matched
            remaining -= matched
            if lot[1] <= 1e-12:
                open_lots.popleft()
        if remaining > 1e-12:
            open_lots.append([px, remaining])
            open_side = sign
        elif not open_lots:
            open_side = 0

    return trade_pnls


# ---------------------------------------------------------------------------
# p-value helper — mirrors run_event_universe._derive_p_value exactly
# ---------------------------------------------------------------------------

def _derive_p_value(per_event_expectancies: list[float]) -> float:
    """One-sample two-sided t-test on per-event expectancies vs null=0.
    Returns p=1.0 when n < 3 (insufficient data; documented).
    """
    if len(per_event_expectancies) < 3:
        return 1.0
    arr = np.array(per_event_expectancies, dtype=float)
    _, p_val = stats.ttest_1samp(arr, 0.0)
    return float(np.clip(p_val, 1e-15, 1.0))


# ---------------------------------------------------------------------------
# Per-unit worker (top-level — picklable for spawn pool)
# ---------------------------------------------------------------------------

def _worker(unit: dict[str, Any]) -> dict[str, Any]:
    import sys
    from pathlib import Path as _P
    from collections import deque as _deque

    _repo = _P(__file__).resolve().parents[4]
    if str(_repo) not in sys.path:
        sys.path.insert(0, str(_repo))
    from hft3_bootstrap import setup_repo_paths as _setup
    _setup()

    import numpy as _np
    from scipy import stats as _stats

    from data_system.src.feature_store import load_store, store_path
    from features_engine.src.hypotheses.registry import (
        get_active_hypotheses,
        VIX_HYP_IDS,
    )
    from features_engine.src.hypotheses.modules import MarketState
    from features_engine.src.features.feature_index import FEATURE_NAME_TO_INDEX, REGIME_INDEX_MAP
    from backtest_pipeline.src.fee_model import FeeModel

    symbol: str = unit["symbol"]
    event_id: str = unit["event_id"]
    feature_root = _P(unit["feature_root"])
    band_ms: float = float(unit["band_ms"])
    event_type: str = unit.get("event_type", "")

    t0 = time.monotonic()
    try:
        # --- load primary store ---
        sp = store_path(feature_root, symbol, event_id)
        if not sp.is_file():
            return {
                "symbol": symbol, "event_id": event_id, "event_type": event_type,
                "band_ms": band_ms, "error": f"store not found: {sp}",
                "hypotheses": [], "elapsed_s": 0.0,
            }
        store = load_store(sp)

        ts: _np.ndarray = store["ts"]  # int64 ns
        X: _np.ndarray = store["X"]   # (N, 64) float64
        event_ctx_id: _np.ndarray = store["event_ctx_id"]
        event_ctx_vocab: list[str] = list(store["event_ctx_vocab"])
        regime_state_id: _np.ndarray = store["regime_state_id"]
        regime_state_vocab: list[str] = list(store["regime_state_vocab"])
        vol_state_id: _np.ndarray = store["vol_state_id"]
        vol_state_vocab: list[str] = list(store["vol_state_vocab"])
        liq_state_id: _np.ndarray = store["liq_state_id"]
        liq_state_vocab: list[str] = list(store["liq_state_vocab"])
        best_bid: _np.ndarray = store["best_bid"]
        best_ask: _np.ndarray = store["best_ask"]
        bbo_valid: _np.ndarray = store["bbo_valid"]
        tick_size_stored: float = float(store.get("tick_size", 0.25))

        N = len(ts)
        if N == 0:
            return {
                "symbol": symbol, "event_id": event_id, "event_type": event_type,
                "band_ms": band_ms, "error": None, "hypotheses": [], "elapsed_s": 0.0,
            }

        # --- VIX sibling store (optional) ---
        vix_ts: _np.ndarray | None = None
        vix_X: _np.ndarray | None = None
        vix_cols: list[str] = []

        vix_sym = "VIX.OPT"
        vix_sp = store_path(feature_root, vix_sym, event_id)
        if not vix_sp.is_file():
            # Try alternate naming: VIX.OPT/VIX.OPT_<event_id>_features_v1.npz
            vix_sp2 = feature_root / vix_sym / f"{vix_sym}_{event_id}_features_v1.npz"
            if vix_sp2.is_file():
                vix_sp = vix_sp2
            else:
                vix_sp = None  # type: ignore[assignment]

        vix_load_error: str | None = None
        if vix_sp is not None:
            try:
                import numpy as _npvix
                with _npvix.load(str(vix_sp), allow_pickle=True) as va:
                    vix_ts = _npvix.array(va["ts"], dtype=_npvix.int64)
                    if "columns" in va.files:
                        vix_cols = list(va["columns"])
                        vix_X = _npvix.array(va["X"])
                    elif "X" in va.files:
                        vix_X = _npvix.array(va["X"])
            except Exception as _vix_exc:
                vix_ts = None
                vix_X = None
                vix_load_error = str(_vix_exc)

        has_vix = vix_ts is not None and vix_X is not None and len(vix_ts) > 0

        # --- hypotheses & per-hypothesis state machine ---
        hypotheses = get_active_hypotheses()
        hyp_names = {h.hyp_id: h.name for h in hypotheses}

        feat_latency_ns = int(band_ms * 1_000_000)
        # Both feature latency and order latency equal band_ms by design, mirroring
        # ReplaySession defaults where feature_latency_ms defaults to latency_ms.
        # If they ever diverge, Stage A must accept both as separate params and
        # pass each to the appropriate pipeline stage independently.
        order_latency_ns = feat_latency_ns

        tick_size = tick_size_stored if tick_size_stored > 0 else 0.25
        sym_base = symbol.split(".")[0]
        tick_val_map = {"ES": 12.50, "NQ": 5.00, "MES": 1.25, "MNQ": 0.50}
        tick_val = tick_val_map.get(sym_base, 1.25)
        pts_per_usd = tick_val / tick_size  # price-point * qty → USD: multiply by (tick_val/tick_size)

        fee_model = FeeModel(product=sym_base if sym_base in FeeModel.TICK_VALUES else "MES")
        fee_per_round_trip = fee_model.get_fee_per_contract() * 2.0  # both legs, 1 contract

        n_hyps = len(hypotheses)

        # Per-hypothesis fill tracking (position state machine)
        pos = _np.zeros(n_hyps, dtype=_np.float64)          # current position (+1, -1, 0)
        fill_sides_all:  list[list[str]]   = [[] for _ in range(n_hyps)]
        fill_prices_all: list[list[float]] = [[] for _ in range(n_hyps)]
        fill_qtys_all:   list[list[float]] = [[] for _ in range(n_hyps)]

        # Reusable MarketState shell — mutated each row
        state = MarketState(
            primary_features={},   # not used when feature_vector is set (state.f vector path)
            cross_asset_features={},
            regime_state="normal",
            event_context="NORMAL",
            volatility_state="NORMAL",
            liquidity_state="NORMAL",
            latency_ms=band_ms,
            current_inventory=0,
            feature_vector=None,
            regime_posterior={},
        )

        # Main decision loop
        for i in range(N):
            # visible row j: latest row whose ts <= ts[i] - feat_latency_ns
            vis_ts = int(ts[i]) - feat_latency_ns
            j = int(_np.searchsorted(ts, vis_ts, side="right")) - 1
            if j < 0:
                continue

            # Populate state from visible row j
            vec_j: _np.ndarray = X[j]
            state.feature_vector = vec_j
            state.primary_features = {}  # rely on vector path in state.f
            state.event_context = str(event_ctx_vocab[int(event_ctx_id[j])]) if event_ctx_vocab else "NORMAL"
            state.regime_state = str(regime_state_vocab[int(regime_state_id[j])]) if regime_state_vocab else "normal"
            state.volatility_state = str(vol_state_vocab[int(vol_state_id[j])]) if vol_state_vocab else "NORMAL"
            state.liquidity_state = str(liq_state_vocab[int(liq_state_id[j])]) if liq_state_vocab else "NORMAL"

            # Regime posterior from slots 41-49
            state.regime_posterior = {
                lbl: float(vec_j[_REGIME_SLOT_START + k])
                for k, lbl in enumerate(REGIME_LABELS_ORDERED)
            }

            # VIX cross-asset features (latency-adjusted)
            if has_vix:
                vix_j = int(_np.searchsorted(vix_ts, vis_ts, side="right")) - 1
                if vix_j >= 0:
                    if vix_cols and vix_X is not None:
                        state.cross_asset_features = {
                            "VIX": {
                                col: float(vix_X[vix_j, ci])
                                for ci, col in enumerate(vix_cols)
                                if ci < vix_X.shape[1]
                            }
                        }
                    else:
                        state.cross_asset_features = {}
                else:
                    state.cross_asset_features = {}
            else:
                state.cross_asset_features = {}

            # Execution row: exec_ts = ts[i] + order_latency_ns (order latency = band_ms)
            t_exec = int(ts[i]) + order_latency_ns
            exec_row = int(_np.searchsorted(ts, t_exec, side="right")) - 1
            exec_row = min(max(exec_row, 0), N - 1)

            # book_one_sided check at current row i (same guard as HypothesisReplayStrategy)
            # one-sided = bbo not valid at execution row
            book_one_sided_exec = not bool(bbo_valid[exec_row])
            # Also gate on i-row bbo for signal row
            book_one_sided_i = not bool(bbo_valid[i])

            for hi, hyp in enumerate(hypotheses):
                sig = float(hyp.evaluate(state))
                cur_pos = pos[hi]

                # Suppress entries when book is one-sided (mirrors on_step guard)
                if book_one_sided_i:
                    continue

                # Entry: BUY when sig > threshold and pos <= 0
                if sig > SIGNAL_THRESHOLD and cur_pos <= 0:
                    if not book_one_sided_exec:
                        entry_px = float(best_ask[exec_row])
                        if _np.isfinite(entry_px):
                            # Flatten any existing short first (force-flat residual)
                            if cur_pos < 0:
                                fill_sides_all[hi].append("BUY")
                                fill_prices_all[hi].append(entry_px)
                                fill_qtys_all[hi].append(abs(cur_pos))
                            fill_sides_all[hi].append("BUY")
                            fill_prices_all[hi].append(entry_px)
                            fill_qtys_all[hi].append(1.0)
                            pos[hi] = 1.0

                # Entry: SELL when sig < -threshold and pos >= 0
                elif sig < -SIGNAL_THRESHOLD and cur_pos >= 0:
                    if not book_one_sided_exec:
                        entry_px = float(best_bid[exec_row])
                        if _np.isfinite(entry_px):
                            if cur_pos > 0:
                                fill_sides_all[hi].append("SELL")
                                fill_prices_all[hi].append(entry_px)
                                fill_qtys_all[hi].append(abs(cur_pos))
                            fill_sides_all[hi].append("SELL")
                            fill_prices_all[hi].append(entry_px)
                            fill_qtys_all[hi].append(1.0)
                            pos[hi] = -1.0

                # Exit: signal crosses back — long exits when sig ≤ 0, short when sig ≥ 0
                else:
                    if cur_pos > 0 and sig <= 0.0:
                        exit_px = float(best_bid[exec_row])
                        if _np.isfinite(exit_px):
                            fill_sides_all[hi].append("SELL")
                            fill_prices_all[hi].append(exit_px)
                            fill_qtys_all[hi].append(abs(cur_pos))
                            pos[hi] = 0.0
                    elif cur_pos < 0 and sig >= 0.0:
                        exit_px = float(best_ask[exec_row])
                        if _np.isfinite(exit_px):
                            fill_sides_all[hi].append("BUY")
                            fill_prices_all[hi].append(exit_px)
                            fill_qtys_all[hi].append(abs(cur_pos))
                            pos[hi] = 0.0

        # Force-flat at last row (mid price)
        last_mid = float(X[N - 1, 40]) if N > 0 else 0.0  # FeatureIndex.MID_PRICE = 40
        if last_mid <= 0.0 and N > 0:
            bb = float(best_bid[N - 1]) if _np.isfinite(best_bid[N - 1]) else 0.0
            ba = float(best_ask[N - 1]) if _np.isfinite(best_ask[N - 1]) else 0.0
            last_mid = (bb + ba) / 2.0 if bb > 0 and ba > 0 else 0.0

        for hi in range(n_hyps):
            cur_pos = pos[hi]
            if cur_pos > 0 and last_mid > 0:
                fill_sides_all[hi].append("SELL")
                fill_prices_all[hi].append(last_mid)
                fill_qtys_all[hi].append(abs(cur_pos))
                pos[hi] = 0.0
            elif cur_pos < 0 and last_mid > 0:
                fill_sides_all[hi].append("BUY")
                fill_prices_all[hi].append(last_mid)
                fill_qtys_all[hi].append(abs(cur_pos))
                pos[hi] = 0.0

        # Compute per-hypothesis metrics
        serialized: list[dict[str, Any]] = []
        for hi, hyp in enumerate(hypotheses):
            raw_pnls = _fifo_trade_pnls_inner(
                fill_sides_all[hi], fill_prices_all[hi], fill_qtys_all[hi]
            )
            n_trades = len(raw_pnls)
            # Convert price-points to USD: 1 price-point = tick_val / tick_size USD
            usd_pnls = [p * pts_per_usd for p in raw_pnls]
            fees_total = fee_per_round_trip * n_trades
            net_usd_pnls = [u - fee_per_round_trip for u in usd_pnls]

            if net_usd_pnls:
                win_rate = float(_np.mean([p > 0 for p in net_usd_pnls]))
                expectancy = float(_np.mean(net_usd_pnls))  # mirrors BacktestResult.expectancy
            else:
                win_rate = 0.0
                expectancy = 0.0

            serialized.append({
                "hypothesis_id": hyp.hyp_id,
                "hypothesis_name": hyp.name,
                "num_trades": n_trades,
                "win_rate": round(win_rate, 6),
                "expectancy_usd": round(expectancy, 6),
                "net_usd_pnls": [round(v, 6) for v in net_usd_pnls],
                "n_rows_vix": int(_np.searchsorted(vix_ts, ts[-1], side="right")) if has_vix and N > 0 else 0,
                "has_vix": has_vix,
            })

        elapsed = time.monotonic() - t0
        result_dict: dict[str, Any] = {
            "symbol": symbol,
            "event_id": event_id,
            "event_type": event_type,
            "band_ms": band_ms,
            "error": None,
            "hypotheses": serialized,
            "has_vix": has_vix,
            "elapsed_s": round(elapsed, 3),
        }
        if vix_load_error is not None:
            result_dict["vix_load_error"] = vix_load_error
        return result_dict

    except Exception as exc:
        return {
            "symbol": symbol,
            "event_id": event_id,
            "event_type": event_type,
            "band_ms": band_ms,
            "error": str(exc),
            "hypotheses": [],
            "elapsed_s": round(time.monotonic() - t0, 3),
        }


def _fifo_trade_pnls_inner(
    fill_sides: list[str],
    fill_prices: list[float],
    fill_qtys: list[float],
) -> list[float]:
    """FIFO round-trip PnLs in price-point*qty — mirrors replay_matrix._fill_metrics."""
    from collections import deque as _dq
    trade_pnls: list[float] = []
    open_side = 0
    open_lots: deque = _dq()
    for side, px, qty in zip(fill_sides, fill_prices, fill_qtys):
        if qty <= 0.0 or side not in ("BUY", "SELL"):
            continue
        sign = 1 if side == "BUY" else -1
        if open_side in (0, sign):
            open_lots.append([px, qty])
            open_side = sign
            continue
        remaining = qty
        while remaining > 1e-12 and open_lots:
            lot = open_lots[0]
            matched = min(remaining, lot[1])
            trade_pnls.append((px - lot[0]) * matched * open_side)
            lot[1] -= matched
            remaining -= matched
            if lot[1] <= 1e-12:
                open_lots.popleft()
        if remaining > 1e-12:
            open_lots.append([px, remaining])
            open_side = sign
        elif not open_lots:
            open_side = 0
    return trade_pnls


# ---------------------------------------------------------------------------
# Aggregation — mirrors run_event_universe._aggregate_results cell structure
# ---------------------------------------------------------------------------

def _aggregate_cells(
    unit_results: list[dict[str, Any]],
    band_ms: float,
) -> dict[tuple[int, str], dict[str, Any]]:
    """Aggregate per-unit results into (hyp_id, event_type) cells."""
    accum: dict[tuple[int, str], dict[str, Any]] = {}

    for ur in unit_results:
        if ur.get("error"):
            continue
        etype = str(ur.get("event_type", ""))
        has_vix = bool(ur.get("has_vix", False))
        for hrow in ur.get("hypotheses", []):
            hyp_id = int(hrow["hypothesis_id"])
            key = (hyp_id, etype)
            if key not in accum:
                accum[key] = {
                    "hypothesis_id": hyp_id,
                    "hypothesis_name": hrow["hypothesis_name"],
                    "event_type": etype,
                    "band_ms": band_ms,
                    "n_events": 0,
                    "n_events_with_vix": 0,
                    "total_trades": 0,
                    "sum_expectancy": 0.0,
                    "sum_win_rate": 0.0,
                    "per_event_expectancies": [],
                    "per_event_win_rates": [],
                }
            cell = accum[key]
            cell["n_events"] += 1
            if has_vix:
                cell["n_events_with_vix"] += 1
            cell["total_trades"] += int(hrow["num_trades"])
            cell["sum_expectancy"] += float(hrow["expectancy_usd"])
            cell["sum_win_rate"] += float(hrow["win_rate"])
            cell["per_event_expectancies"].append(float(hrow["expectancy_usd"]))
            cell["per_event_win_rates"].append(float(hrow["win_rate"]))

    # Finalise
    finalised: dict[tuple[int, str], dict[str, Any]] = {}
    for key, cell in sorted(accum.items()):
        n = cell["n_events"]
        expecs = cell["per_event_expectancies"]
        finalised[key] = {
            "hypothesis_id": cell["hypothesis_id"],
            "hypothesis_name": cell["hypothesis_name"],
            "event_type": cell["event_type"],
            "band_ms": band_ms,
            "n_events": n,
            "n_events_with_vix": cell["n_events_with_vix"],
            "total_trades": cell["total_trades"],
            "mean_expectancy_usd": round(cell["sum_expectancy"] / n, 6) if n else 0.0,
            "mean_win_rate": round(cell["sum_win_rate"] / n, 6) if n else 0.0,
            "p5_expectancy_tail_usd": round(float(np.percentile(expecs, 5)), 6) if expecs else 0.0,
            "per_event_expectancies": [round(v, 6) for v in expecs],
            "aggregation_note": (
                "mean/win_rate are arithmetic means of per-event values; "
                "p5_tail is 5th-percentile of per-event expectancies (worst-event, not worst-trade)"
            ),
        }
    return finalised


# ---------------------------------------------------------------------------
# Multiple-testing correction wrappers
# ---------------------------------------------------------------------------

def _run_corrections(
    cells: dict[tuple[int, str], dict[str, Any]],
) -> dict[str, Any]:
    """Run BH@0.10 (SCREEN gate) and Holm@0.05 (reference) on all cells."""
    from decision_engine.python.src.multiple_testing_correction import (
        MultipleTestingGate,
        HypothesisTestResult,
    )

    results: list[HypothesisTestResult] = []
    for (hyp_id, etype), cell in sorted(cells.items()):
        expecs = cell["per_event_expectancies"]
        p_val = _derive_p_value(expecs)
        slug = f"hyp_{hyp_id}__{etype}" if etype else f"hyp_{hyp_id}"
        results.append(
            HypothesisTestResult(
                slug=slug,
                legacy_id=f"HYP_{hyp_id}",
                metric_name="expectancy_usd",
                metric_value=cell["mean_expectancy_usd"],
                p_value=p_val,
                t_statistic=0.0,
                num_trades=cell["total_trades"],
            )
        )

    bh_report = MultipleTestingGate(alpha=0.10).apply_correction(results, method="benjamini_hochberg")
    holm_report = MultipleTestingGate(alpha=0.05).apply_correction(results, method="holm")

    # Attach p-values and adj_alpha back into cells
    for r in bh_report.sorted_results:
        # parse slug back to (hyp_id, etype)
        parts = r.slug.split("__", 1)
        hyp_id_str = parts[0].replace("hyp_", "")
        etype = parts[1] if len(parts) > 1 else ""
        key = (int(hyp_id_str), etype)
        if key in cells:
            cells[key]["p_value"] = r.p_value
            cells[key]["bh_adj_alpha"] = r.adjusted_alpha
            cells[key]["bh_significant"] = r.is_significant

    for r in holm_report.sorted_results:
        parts = r.slug.split("__", 1)
        hyp_id_str = parts[0].replace("hyp_", "")
        etype = parts[1] if len(parts) > 1 else ""
        key = (int(hyp_id_str), etype)
        if key in cells:
            cells[key]["holm_adj_alpha"] = r.adjusted_alpha
            cells[key]["holm_significant"] = r.is_significant

    return {
        "bh_010": {
            "method": "benjamini_hochberg",
            "alpha": 0.10,
            "passed": bh_report.passed_slugs,
            "failed": bh_report.failed_slugs,
            "total_tested": bh_report.total_tested,
        },
        "holm_005": {
            "method": "holm",
            "alpha": 0.05,
            "passed": holm_report.passed_slugs,
            "failed": holm_report.failed_slugs,
            "total_tested": holm_report.total_tested,
        },
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _build_report(
    cells: dict[tuple[int, str], dict[str, Any]],
    corrections: dict[str, Any],
    survivors: list[dict[str, Any]],
    pass_through: list[int],
    stamp_footer: str,
    band_ms: float,
    units_run: int,
    units_skipped: int,
    units_errored: int,
    vix_overall: dict[str, int],
) -> str:
    lines: list[str] = [
        f"# Stage A Screen — RESEARCH_ONLY",
        f"",
        f"band_ms={band_ms}  units_run={units_run}  units_skipped={units_skipped}  "
        f"units_errored={units_errored}",
        f"",
        f"## BH@0.10 Survivors (SCREEN gate)",
        "",
        "| hyp_id | event_type | p_value | adj_alpha | mean_exp_usd | n_events | total_trades |",
        "|--------|------------|---------|-----------|-------------|----------|-------------|",
    ]
    for sv in sorted(survivors, key=lambda x: (x["hyp_id"], x["event_type"])):
        key = (sv["hyp_id"], sv["event_type"])
        cell = cells.get(key, {})
        lines.append(
            f"| {sv['hyp_id']} | {sv['event_type']} | {sv['p']:.4e} | "
            f"{sv['adj_alpha']:.4e} | {cell.get('mean_expectancy_usd', 0.0):.4f} | "
            f"{cell.get('n_events', 0)} | {cell.get('total_trades', 0)} |"
        )

    lines += [
        "",
        "## Holm@0.05 Reference",
        "",
        "| hyp_id | event_type | p_value | holm_adj_alpha | significant |",
        "|--------|------------|---------|----------------|-------------|",
    ]
    for (hyp_id, etype), cell in sorted(cells.items()):
        lines.append(
            f"| {hyp_id} | {etype} | {cell.get('p_value', 1.0):.4e} | "
            f"{cell.get('holm_adj_alpha', 1.0):.4e} | {cell.get('holm_significant', False)} |"
        )

    lines += [
        "",
        f"## Pass-through (queue-sensitive, always in survivors)",
        "",
        f"hyp_ids: {sorted(pass_through)}",
        "",
        f"## VIX coverage",
        "",
        f"n_events_with_vix={vix_overall.get('n_events_with_vix', 0)} / "
        f"n_events={vix_overall.get('n_events', 0)}",
        "",
        "---",
        f"*{stamp_footer}*",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_stage_a(
    repo_root: "str | Path",
    feature_root: "str | Path | None",
    out_dir: "str | Path",
    *,
    band_ms: float,
    symbols: "list[str] | None" = None,
    event_types: "list[str] | None" = None,
    max_units: "int | None" = None,
    workers: int = 1,
) -> dict[str, Any]:
    """Run Stage A RESEARCH_ONLY pre-filter screen over the feature store.

    Returns the result dict (same content as stage_a_result.json).
    """
    from pathlib import Path as _P
    import sys as _sys

    repo_root = _P(repo_root)
    if str(repo_root) not in _sys.path:
        _sys.path.insert(0, str(repo_root))
    from hft3_bootstrap import setup_repo_paths
    setup_repo_paths()

    from data_system.src.feature_store import (
        load_manifest,
        feature_store_root,
        FEATURE_VERSION,
    )
    from features_engine.src.hypotheses.registry import get_active_hypotheses as _get_hyps
    from hft3.validation.research_stamp import build_certification_stamp, format_stamp_footer

    if feature_root is None:
        feature_root = feature_store_root(repo_root)
    feature_root = _P(feature_root)

    out_dir = _P(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    run_start_utc = datetime.now(timezone.utc).isoformat()

    # --- Load manifest (feature-store manifest, NOT the lake) ---
    manifest = load_manifest(feature_root)

    # --- Enumerate units, apply embargo (defense-in-depth) ---
    work_units: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    units_skipped_embargo = 0

    for (sym, event_id), rec in sorted(manifest.items(), key=lambda t: (t[0][0], t[0][1])):
        if symbols and sym not in symbols:
            continue
        etype = str(rec.get("event_type", ""))
        if event_types and etype not in event_types:
            continue
        release_date = str(rec.get("release_date", ""))
        if release_date >= RESEARCH_EMBARGO_START:
            skipped.append({
                "symbol": sym, "event_id": event_id, "reason": "embargo_2026",
            })
            units_skipped_embargo += 1
            continue

        work_units.append({
            "symbol": sym,
            "event_id": event_id,
            "event_type": etype,
            "release_date": release_date,
            "feature_root": str(feature_root),
            "band_ms": band_ms,
        })

    if max_units is not None:
        work_units = work_units[:max_units]

    total = len(work_units)
    print(
        f"Stage A: {total} units to run, {units_skipped_embargo} embargo-skipped, "
        f"band_ms={band_ms}",
        flush=True,
    )

    # --- Run units (spawn pool or serial) ---
    unit_results: list[dict[str, Any]] = []

    if workers > 1 and total > 0:
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=workers) as pool:
            for i, r in enumerate(pool.imap_unordered(_worker, work_units), 1):
                unit_results.append(r)
                if i % 50 == 0 or i == total:
                    errs = sum(1 for x in unit_results if x.get("error"))
                    print(f"  [{i}/{total}] errors={errs}", flush=True)
    else:
        for i, unit in enumerate(work_units, 1):
            r = _worker(unit)
            unit_results.append(r)
            if i % 100 == 0:
                errs = sum(1 for x in unit_results if x.get("error"))
                print(f"  [{i}/{total}] errors={errs}", flush=True)

    units_run = len(unit_results)
    units_errored = sum(1 for u in unit_results if u.get("error"))
    vix_load_errors = sum(1 for u in unit_results if u.get("vix_load_error"))

    # --- Aggregate cells ---
    cells = _aggregate_cells(unit_results, band_ms)

    # --- Ensure full family: every (hyp_id, event_type) combo for all hypotheses
    # evaluated × all event_types that had ≥1 unit attempted must appear in cells
    # so tested_cells (Stage B family size) is honest.  "Attempted" = the unit was
    # dispatched regardless of whether it errored; a unit that errored was still
    # part of the research sweep and its event_type must be represented.
    # Zero-data combos get p=1.0, n_events=0 stubs; corrections will assign
    # bh_significant=False for them.
    _all_etypes_run = {
        str(ur.get("event_type", ""))
        for ur in unit_results
        if str(ur.get("event_type", ""))  # non-empty event_type
    }
    _all_hypotheses = _get_hyps()
    for _hyp in _all_hypotheses:
        for _et in _all_etypes_run:
            _key = (_hyp.hyp_id, _et)
            if _key not in cells:
                cells[_key] = {
                    "hypothesis_id": _hyp.hyp_id,
                    "hypothesis_name": _hyp.name,
                    "event_type": _et,
                    "band_ms": band_ms,
                    "n_events": 0,
                    "n_events_with_vix": 0,
                    "total_trades": 0,
                    "mean_expectancy_usd": 0.0,
                    "mean_win_rate": 0.0,
                    "p5_expectancy_tail_usd": 0.0,
                    "per_event_expectancies": [],
                    "p_value": 1.0,
                    "bh_adj_alpha": 1.0,
                    "bh_significant": False,
                    "aggregation_note": "zero-data stub: no units produced signal for this combo",
                }

    # --- Corrections ---
    corrections = _run_corrections(cells)

    # --- VIX coverage per cell + overall ---
    total_vix_events = 0
    total_events = 0
    for cell in cells.values():
        total_events += cell["n_events"]
        total_vix_events += cell["n_events_with_vix"]
    vix_overall = {"n_events": total_events, "n_events_with_vix": total_vix_events}

    # --- Survivors: BH@0.10 significant + pass-through ---
    bh_passed_slugs = set(corrections["bh_010"]["passed"])
    tested_cells: list[dict[str, Any]] = []
    survivors: list[dict[str, Any]] = []

    for (hyp_id, etype), cell in sorted(cells.items()):
        slug = f"hyp_{hyp_id}__{etype}" if etype else f"hyp_{hyp_id}"
        tc = {
            "hyp_id": hyp_id,
            "event_type": etype,
            "band_ms": band_ms,
            "p": cell.get("p_value", 1.0),
            "adj_alpha": cell.get("bh_adj_alpha", 1.0),
            "significant_bh": cell.get("bh_significant", False),
            "vix_coverage": {
                "n_events_with_vix": cell["n_events_with_vix"],
                "n_events": cell["n_events"],
            },
        }
        tested_cells.append(tc)
        if slug in bh_passed_slugs:
            survivors.append({
                "hyp_id": hyp_id,
                "event_type": etype,
                "p": cell.get("p_value", 1.0),
                "adj_alpha": cell.get("bh_adj_alpha", 1.0),
                "vix_coverage": tc["vix_coverage"],
            })

    pass_through_list = sorted(QUEUE_SENSITIVE_PASS_THROUGH)

    # --- Certification stamp ---
    stamp = build_certification_stamp(
        execution_mode="STAGE_A_SCREEN",
        queue_model="NONE_P_FILL_ASSUMED_1",
        fee_model="FeeModel",
        fill_model_version="stage_a_approx_v1",
        feature_version=FEATURE_VERSION,
    )
    stamp_footer = format_stamp_footer(stamp)

    run_end_utc = datetime.now(timezone.utc).isoformat()

    # --- Output JSON ---
    # Serialise cells as list for JSON
    cells_list = [
        dict(cell, hyp_id=hyp_id, event_type=etype)
        for (hyp_id, etype), cell in sorted(cells.items())
    ]

    result: dict[str, Any] = {
        "schema": "stage_a_result_v1",
        "stage": "A",
        "research_only": True,
        "promotion_label": "RESEARCH_ONLY",
        "certification_stamp": stamp,
        "certification_footer": stamp_footer,
        "ev_approximation_note": (
            "Stage A computes EV ~= E[PnL|fill] - Costs with P_fill=1 (no queue model); "
            "(1-P_fill)*C_miss and queue-conditional fills are restored in Stage B "
            "(LogProbQueueModel2). See System Blueprint EV objective."
        ),
        "aggregation_note": (
            "mean/win_rate are arithmetic means of per-event values; "
            "p5_tail is 5th-percentile of per-event expectancies (worst-event, not worst-trade)"
        ),
        "run_start_utc": run_start_utc,
        "run_end_utc": run_end_utc,
        "band_ms": band_ms,
        "feature_version": FEATURE_VERSION,
        "embargo": {
            "start": RESEARCH_EMBARGO_START,
            "units_skipped_embargo": units_skipped_embargo,
        },
        "units_run": units_run,
        "units_skipped": len(skipped),
        "units_errored": units_errored,
        "vix_load_errors": vix_load_errors,
        "cells": cells_list,
        "corrections": corrections,
        "vix_coverage": {
            "n_events_with_vix": total_vix_events,
            "n_events": total_events,
        },
    }

    result_path = out_dir / "stage_a_result.json"
    result_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    # --- survivors JSON ---
    survivors_payload: dict[str, Any] = {
        "band_ms": band_ms,
        "tested_cells": tested_cells,
        "survivors": survivors,
        "pass_through": pass_through_list,
        "feature_version": FEATURE_VERSION,
        "generated_from": str(result_path),
    }
    survivors_path = out_dir / "stage_a_survivors.json"
    survivors_path.write_text(
        json.dumps(survivors_payload, indent=2, default=str), encoding="utf-8"
    )

    # --- report MD ---
    report_md = _build_report(
        cells=cells,
        corrections=corrections,
        survivors=survivors,
        pass_through=pass_through_list,
        stamp_footer=stamp_footer,
        band_ms=band_ms,
        units_run=units_run,
        units_skipped=len(skipped),
        units_errored=units_errored,
        vix_overall=vix_overall,
    )
    report_path = out_dir / "stage_a_report.md"
    report_path.write_text(report_md, encoding="utf-8")

    print(
        f"Stage A complete. survivors={len(survivors)} pass_through={pass_through_list} "
        f"out={out_dir}",
        flush=True,
    )
    return result
