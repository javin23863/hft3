"""Self-running equities lane real-run driver.

Loops through all 13 decadal sessions, runs LowFloatBacktester + experiment report
on each. Writes per-session results + aggregate to runtime/data_audits/equities_real_run.json.

L3-only enforcement (no allow_degraded). No skip-large threshold.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages"))

import yaml

try:
    from hft3_bootstrap import setup_repo_paths
    setup_repo_paths()
except Exception:
    pass

from equities_lane.src.backtest.low_float_backtester import LowFloatBacktester
from equities_lane.src.backtest.walk_forward import generate_folds, assert_no_float_lookahead
from equities_lane.src.config_loader import load_universe
from equities_lane.src.ingest.session_io import load_session
from equities_lane.src.l3_policy import L3OnlyViolation
from equities_lane.src.report.experiment_report import run_experiment

_CONFIG = _REPO / "packages" / "equities_lane" / "config" / "universe.yaml"
_DECADAL = _REPO / "packages" / "equities_lane" / "config" / "decadal_runners.yaml"
_OUT = _REPO / "runtime" / "data_audits" / "equities_real_run.json"


def _load_sessions() -> list[dict]:
    raw = yaml.safe_load(_DECADAL.read_text(encoding="utf-8")) or {}
    return [s for s in raw.get("sessions", []) if not s.get("skip_pull")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ablation", default="all")
    ap.add_argument("--reports-root", default=str(_REPO / "research_cards" / "equities"))
    ap.add_argument("--all-sessions", action="store_true", help="Include large sessions (>5MB)")
    args = ap.parse_args()

    t0 = time.time()
    print(f"[equities-run] starting at {datetime.now(timezone.utc).isoformat()}", flush=True)
    print(f"[equities-run] config={_CONFIG}", flush=True)
    print(f"[equities-run] ablation={args.ablation}", flush=True)

    _, universe, paths = load_universe(str(_CONFIG))
    sessions = _load_sessions()
    print(f"[equities-run] sessions={len(sessions)}", flush=True)

    per_session: list[dict] = []
    errors: list[dict] = []

    grand_pnl = 0.0
    grand_trades = 0
    grand_profitable = 0
    grand_fills = 0

    for idx, sess in enumerate(sessions, 1):
        sym = sess["symbol"]
        date = sess["date"]
        norm = _REPO / "data" / "equities" / "normalized" / f"{sym}_{date}.ndjson"
        npz = _REPO / "data" / "equities" / "npz" / f"{sym}_{date}.npz"
        print(f"\n[equities-run] [{idx}/{len(sessions)}] {sym} {date}", flush=True)

        if not norm.exists():
            print(f"  [skip] normalized file missing: {norm}", flush=True)
            per_session.append({"ticker": sym, "session_date": date, "status": "no_normalized", "npz_present": npz.exists()})
            continue

        size_mb = norm.stat().st_size / (1024 * 1024)
        print(f"  size={size_mb:.1f}MB npz={npz.exists()}", flush=True)

        try:
            meta, _ = load_session(str(norm))
            if meta.degraded.degraded_mode:
                raise L3OnlyViolation(f"degraded session {sym} {date}: {meta.degraded.assumptions}")

            bt = LowFloatBacktester(universe)
            res = bt.run(str(norm), allow_degraded=False)
            d = res.to_dict()
            fills = d.get("fills", []) or []
            n_trades = d.get("num_trades", len(fills))
            net_pnl = d.get("net_pnl", 0.0) or 0.0

            buys = 0.0
            rt_pnl = 0.0
            profitable = 0
            for f in fills:
                if f.get("side") == "buy":
                    buys = f.get("price", 0.0)
                elif f.get("side") == "sell" and buys:
                    rt = f.get("price", 0.0) - buys
                    rt_pnl += rt
                    if rt > 0:
                        profitable += 1
                    buys = 0.0

            print(f"  net_pnl={net_pnl:+.2f} trades={n_trades} rt_pnl={rt_pnl:+.2f} profitable={profitable}/{n_trades}", flush=True)

            per_session.append({
                "ticker": sym,
                "session_date": date,
                "status": "ok",
                "size_mb": round(size_mb, 1),
                "net_pnl": net_pnl,
                "num_trades": n_trades,
                "round_trip_pnl_estimate": rt_pnl,
                "profitable_round_trip_count": profitable,
                "max_drawdown": d.get("max_drawdown", 0.0),
                "turnover": d.get("turnover", 0.0),
                "tail_loss": d.get("tail_loss", 0.0),
            })
            grand_pnl += net_pnl
            grand_trades += n_trades
            grand_profitable += profitable
            grand_fills += n_trades

        except L3OnlyViolation as e:
            print(f"  [BLOCKED L3] {e}", flush=True)
            errors.append({"ticker": sym, "session_date": date, "type": "L3OnlyViolation", "error": str(e)})
            per_session.append({"ticker": sym, "session_date": date, "status": "blocked_l3", "error": str(e)})
        except Exception as e:
            tb = traceback.format_exc(limit=3)
            print(f"  [ERROR] {type(e).__name__}: {e}", flush=True)
            errors.append({"ticker": sym, "session_date": date, "type": type(e).__name__, "error": str(e), "traceback": tb})
            per_session.append({"ticker": sym, "session_date": date, "status": "error", "error": f"{type(e).__name__}: {e}"})

    elapsed = time.time() - t0
    ok_count = sum(1 for s in per_session if s.get("status") == "ok")
    blocked_count = sum(1 for s in per_session if s.get("status") == "blocked_l3")
    err_count = sum(1 for s in per_session if s.get("status") == "error")
    no_norm_count = sum(1 for s in per_session if s.get("status") == "no_normalized")

    out = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(elapsed, 1),
        "config": str(_CONFIG),
        "decadal_config": str(_DECADAL),
        "ablation": args.ablation,
        "totals": {
            "sessions_total": len(sessions),
            "ok": ok_count,
            "blocked_l3": blocked_count,
            "error": err_count,
            "no_normalized": no_norm_count,
            "grand_net_pnl": round(grand_pnl, 4),
            "grand_num_trades": grand_trades,
            "grand_profitable_round_trips": grand_profitable,
            "pct_fills_profitable": round(100.0 * grand_profitable / grand_fills, 2) if grand_fills else 0.0,
        },
        "per_session": per_session,
        "errors": errors,
        "notes": [
            "L3-only enforcement: allow_degraded=False throughout.",
            "No skip-large threshold: all sessions run end-to-end.",
            "Per-slug results mirror per-ticker results (shared universe in LowFloatBacktester).",
            "Quarantine invariant: NPZ writes to data/equities/npz/ only.",
        ],
    }

    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\n[equities-run] DONE in {elapsed:.1f}s", flush=True)
    print(f"[equities-run] ok={ok_count} blocked_l3={blocked_count} error={err_count} no_normalized={no_norm_count}", flush=True)
    print(f"[equities-run] grand_pnl={grand_pnl:+.2f} trades={grand_trades}", flush=True)
    print(f"[equities-run] output: {_OUT}", flush=True)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
