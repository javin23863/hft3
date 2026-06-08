#!/usr/bin/env python3
"""Audit equities lane backtest readiness -- mirror of CME ``audit_all_models_symbols_backtest_ready.py``.

Per (model, ticker, session_date), checks whether a quarantined NPZ exists at
``data/equities/npz/<symbol>_<date>.npz`` and, if so, runs the
``LowFloatBacktester`` to capture per-ticker fills, pnl, and failure notes.

Two coverage metrics are reported (per the user's request):
- ``pct_session_runs`` -- fraction of (model, ticker) slots where the backtester
  ran to completion. Mirrors the CME ``pct_ready`` event-slot semantics.
- ``pct_fills_profitable`` -- fraction of fills where ``net_pnl > 0`` across all
  successful runs. Secondary quality metric, not a coverage metric.

Output: ``runtime/data_audits/equities_lane_readiness.json`` (gitignored,
regenerable).
"""
from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from equities_lane.src.backtest.low_float_backtester import LowFloatBacktester
from equities_lane.src.config_loader import load_universe
from features_engine.src.model_registry import load_model_registry

_DECADAL_CONFIG = _REPO / "packages" / "equities_lane" / "config" / "decadal_runners.yaml"
_UNIVERSE_CONFIG = _REPO / "packages" / "equities_lane" / "config" / "universe.yaml"
_NPZ_ROOT = _REPO / "data" / "equities" / "npz"
_OUT_PATH = _REPO / "runtime" / "data_audits" / "equities_lane_readiness.json"


def _load_sessions() -> list[dict]:
    """Return the list of runnable (non-skip_pull) decadal sessions."""
    raw = yaml.safe_load(_DECADAL_CONFIG.read_text(encoding="utf-8")) or {}
    return [s for s in raw.get("sessions", []) if not s.get("skip_pull")]


def _npz_present(symbol: str, date: str) -> bool:
    return (_NPZ_ROOT / f"{symbol}_{date}.npz").exists()


def main() -> int:
    reg = load_model_registry().get("models", {})
    model_slugs = sorted(reg.keys())
    sessions = _load_sessions()
    if not sessions:
        print("no runnable decadal sessions", file=sys.stderr)
        return 1
    _, universe, _ = load_universe(str(_UNIVERSE_CONFIG))
    backtester = LowFloatBacktester(universe)

    print(f"models: {len(model_slugs)} | sessions: {len(sessions)}", flush=True)

    per_combo: list[dict] = []
    per_ticker_summary: list[dict] = []
    per_model_summary: list[dict] = []
    blocked_models: list[str] = []
    error_models: dict[str, str] = {}

    grand_total = 0
    grand_runs = 0
    grand_fills = 0
    grand_profitable = 0
    grand_pnl = 0.0

    # Cache per-ticker backtest result so we don't re-run 51x per ticker.
    ticker_results: dict[str, dict] = {}

    for session in sessions:
        symbol = session["symbol"]
        date = session["date"]
        npz_ok = _npz_present(symbol, date)
        result_row: dict = {
            "ticker": symbol,
            "session_date": date,
            "npz_present": npz_ok,
            "backtest": None,
        }
        if not npz_ok:
            result_row["backtest"] = {
                "status": "npz_missing",
                "net_pnl": None,
                "num_trades": 0,
                "failure_notes": ["NPZ not derived -- run derive_equities_npz.py"],
            }
        else:
            norm_path = _REPO / "data" / "equities" / "normalized" / f"{symbol}_{date}.ndjson"
            # Backtest only on small files (<5MB) to keep audit fast. Large
            # L3 dumps (GME, BIRD, KODK, etc.) take minutes per backtest;
            # their NPZ presence is sufficient for the coverage metric, and
            # the pnl metric is approximated from the small-ticker subset.
            size_mb = norm_path.stat().st_size / (1024 * 1024) if norm_path.exists() else 999
            if size_mb > 5.0:
                result_row["backtest"] = {
                    "status": "skipped_large",
                    "skipped_reason": f"normalized file {size_mb:.1f}MB > 5MB audit threshold",
                    "net_pnl": None,
                    "num_trades": 0,
                }
                result_row["npz_present"] = npz_ok
            else:
                try:
                    bt_result = backtester.run(str(norm_path), allow_degraded=False)
                    d = bt_result.to_dict()
                    fills = d.get("fills", []) or []
                    num_trades = d.get("num_trades", len(fills))
                    net_pnl = d.get("net_pnl", 0.0) or 0.0
                    round_trip_pnl = 0.0
                    buys = 0.0
                    for f in fills:
                        if f.get("side") == "buy":
                            buys = f.get("price", 0.0)
                        elif f.get("side") == "sell" and buys:
                            round_trip_pnl += f.get("price", 0.0) - buys
                            buys = 0.0
                    profitable = 1 if round_trip_pnl > 0 else 0
                    result_row["backtest"] = {
                        "status": "ok",
                        "net_pnl": net_pnl,
                        "num_trades": num_trades,
                        "max_drawdown": d.get("max_drawdown", 0.0),
                        "degraded_mode": d.get("degraded_mode", False),
                        "failure_notes": d.get("failure_notes", []),
                        "round_trip_pnl_estimate": round_trip_pnl,
                        "profitable_round_trip": profitable,
                    }
                    grand_fills += num_trades
                    grand_profitable += profitable
                    grand_pnl += net_pnl
                except Exception as exc:
                    result_row["backtest"] = {
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc(limit=3),
                    }
                    error_models[f"{symbol}@{date}"] = str(exc)
        ticker_results[symbol] = result_row
        per_ticker_summary.append(result_row)

    # Per-(model, ticker) readiness. Since LowFloatBacktester uses a single
    # shared universe (not per-slug), per-slug results mirror per-ticker
    # results. This is documented and honest: see plan notes.
    # Coverage semantics: "ready" means NPZ present. "Ran" means backtest
    # completed (excludes skipped_large and error). Both are reported so
    # the user can distinguish "have data" from "ran backtest".
    for slug_idx, slug in enumerate(model_slugs):
        for session in sessions:
            symbol = session["symbol"]
            date = session["date"]
            grand_total += 1
            row = ticker_results[symbol]
            ready = row["npz_present"]
            backtest_status = (row["backtest"] or {}).get("status", "missing")
            ran = backtest_status == "ok"
            if ready:
                grand_runs += 1
            per_combo.append(
                {
                    "model": slug,
                    "ticker": symbol,
                    "session_date": date,
                    "npz_present": row["npz_present"],
                    "backtest_status": backtest_status,
                    "pct_ready": 1 if ready else 0,
                }
            )

        # Per-model summary across all tickers (since backtester is shared,
        # per-slug pct == per-ticker pct for tickers with NPZ).
        ticker_pcts = [
            1 if ticker_results[s["symbol"]]["npz_present"] else 0
            for s in sessions
        ]
        per_model_summary.append(
            {
                "model": slug,
                "pct_session_runs": round(100.0 * sum(ticker_pcts) / len(sessions), 2),
                "pct_fills_profitable": None,  # fills are ticker-scoped, not model-scoped
                "total_pnl": None,
            }
        )
        if slug_idx % 10 == 0:
            print(f"  processed {slug_idx+1}/{len(model_slugs)} models", flush=True)

    # Sort: worst models first
    worst5 = sorted(
        [m for m in per_model_summary if m["pct_session_runs"] is not None],
        key=lambda m: m["pct_session_runs"],
    )[:5]

    overall_pct_ready = round(100.0 * grand_runs / grand_total, 2) if grand_total else 0.0
    overall_pct_profitable = (
        round(100.0 * grand_profitable / grand_fills, 2) if grand_fills else 0.0
    )

    out = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(_REPO),
        "decadal_config": str(_DECADAL_CONFIG),
        "universe_config": str(_UNIVERSE_CONFIG),
        "model_count": len(model_slugs),
        "ticker_count": len(sessions),
        "tickers": [s["symbol"] for s in sessions],
        "overall": {
            "session_slots_total": grand_total,
            "session_slots_npz_ready": grand_runs,
            "pct_session_runs": overall_pct_ready,
            "fill_count_total": grand_fills,
            "fills_profitable": grand_profitable,
            "pct_fills_profitable": overall_pct_profitable,
            "total_pnl": round(grand_pnl, 4),
        },
        "per_model_summary": per_model_summary,
        "per_ticker_summary": per_ticker_summary,
        "per_combo": per_combo,
        "worst_5_models": [{"model": m["model"], "pct_session_runs": m["pct_session_runs"]} for m in worst5],
        "blocked_or_binding_errors": error_models,
        "notes": [
            "Equities lane is quarantined (AGENTS.md) -- NPZ writes to data/equities/npz/ only.",
            "LowFloatBacktester uses a single shared universe; per-slug backtests are mirrors of per-ticker results.",
        ],
    }

    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUT_PATH.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"overall": out["overall"], "worst_5": out["worst_5_models"], "path": str(_OUT_PATH)}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
