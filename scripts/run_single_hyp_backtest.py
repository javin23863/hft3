#!/usr/bin/env python3
"""Single-hypothesis event backtest on Databento NPZ; latency from CHI404 probe.

Replaced SignalBacktester with run_hypothesis_replay (ReplaySession-backed).
The old SignalBacktester path was deleted because it had silently wrong math:
100 µs markout window (not 100 ms), raw['px'] used as mid, zero slippage,
and ES tick_value on MES contracts.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
for path in (_REPO, _REPO / "packages", _REPO / "apps"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backtest_pipeline.src.replay_matrix import run_hypothesis_replay
from backtest.adapters.rithmic_replay_loader import resolve_event_npz
from data_system.src.events_parser import load_and_parse_events
from features_engine.src.hypotheses.modules import SpreadBlowoutRecompression

DEFAULT_OUT = _REPO / "research_cards" / "single_run_hyp_backtest"
DEFAULT_CHI404_SUMMARY = _REPO / "runtime" / "latency_reports" / "latency_summary.json"


def _relative_npz(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_REPO.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def load_chi404_speed(summary_path: Path) -> dict[str, Any]:
    if not summary_path.is_file():
        raise FileNotFoundError(
            f"CHI404 latency summary missing: {summary_path}. "
            "Run scripts/latency_probe/run_all.sh on CHI404 first."
        )
    s = json.loads(summary_path.read_text(encoding="utf-8"))
    network = s.get("network") or {}
    rithmic_tcp = network.get("rithmic_tcp_65000") or {}
    gateway = network.get("gateway_ping") or {}
    cyclictest = s.get("cyclictest") or {}
    trial = s.get("trial_order_ack_appendix") or {}

    rithmic_tcp_p99_ms = rithmic_tcp.get("p99_ms")
    if not isinstance(rithmic_tcp_p99_ms, (int, float)):
        raise ValueError("CHI404 summary has no rithmic_tcp_65000 p99_ms — cannot set backtest latency")

    return {
        "probe_run_id": s.get("run_id"),
        "probe_timestamp_utc": s.get("timestamp_utc"),
        "source": s.get("authoritative_source"),
        "cpu_loaded_p99_us": cyclictest.get("max_p99_us"),
        "gateway_ping_p99_ms": gateway.get("p99_ms"),
        "rithmic_tcp_65000_p99_ms": float(rithmic_tcp_p99_ms),
        "network_worst_p99_us": s.get("network_p99_us"),
        "network_worst_source": s.get("network_p99_worst_source"),
        "order_ack_p99_ms": s.get("order_ack_p99_ms"),
        "trial_order_ack_p99_ms": trial.get("order_ack_p99_ms"),
        "trial_order_ack_status": trial.get("status"),
        "backtest_latency_ms": float(rithmic_tcp_p99_ms),
        "backtest_latency_source": "CHI404 rithmic_tcp_65000 p99 (measured on colo bare metal)",
        "order_ack_measured": False,
    }


def _event_metadata(event_id: str) -> dict[str, str]:
    events = load_and_parse_events(str(_REPO / "data_system" / "config" / "events.csv"))
    row = events[events["event_id"] == event_id]
    if row.empty:
        raise ValueError(f"event_id not in events.csv: {event_id}")
    record = row.iloc[0]
    return {
        "event_type": str(record["event_type"]),
        "release_date": str(record["release_date"]),
        "release_time_et": str(record["release_time"]),
        "window_utc": f"{record['start_utc']} to {record['end_utc']}",
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="Single HYP backtest with CHI404-measured latency (ReplaySession)"
    )
    p.add_argument("--event-id", required=True, help="Explicit catalog event id from events.csv")
    p.add_argument("--npz", type=Path, default=None, help="Override NPZ; default from --event-id")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument(
        "--chi404-summary",
        type=Path,
        default=DEFAULT_CHI404_SUMMARY,
        help="latency_summary.json from CHI404 probe (or copy synced locally)",
    )
    args = p.parse_args()

    npz_path = args.npz.resolve() if args.npz else resolve_event_npz(args.event_id, _REPO)
    event_meta = _event_metadata(args.event_id)
    chi404 = load_chi404_speed(args.chi404_summary.resolve())
    latency_ms = chi404["backtest_latency_ms"]

    args.out.mkdir(parents=True, exist_ok=True)
    hyp = SpreadBlowoutRecompression()

    print(f"Data: {npz_path}", flush=True)
    print(f"CHI404 probe run: {chi404['probe_run_id']}", flush=True)
    print(f"Backtest latency: {latency_ms:.4f} ms ({chi404['backtest_latency_source']})", flush=True)
    print(f"Running HYP_{hyp.hyp_id} {hyp.name}", flush=True)

    res = run_hypothesis_replay(hyp, str(npz_path), latency_ms=latency_ms)

    fills_path = args.out / "fills.csv"
    if res.fills:
        try:
            import pandas as pd

            pd.DataFrame([f.__dict__ for f in res.fills]).to_csv(fills_path, index=False)
        except ImportError:
            print("Warning: pandas not installed, skipping fills.csv export", file=sys.stderr)

    payload = {
        "scenario": f"{args.event_id} single hypothesis backtest",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "event_id": args.event_id,
        "event_type": event_meta["event_type"],
        "release_date": event_meta["release_date"],
        "release_time_et": event_meta["release_time_et"],
        "window_utc": event_meta["window_utc"],
        "symbol": "MES.v.0",
        "npz_path": _relative_npz(npz_path),
        "hypothesis_id": hyp.hyp_id,
        "hypothesis_name": hyp.name,
        "live_orders_sent": False,
        "chi404_measured_speed": chi404,
        "backtest_latency_ms": latency_ms,
        "backend": "replay_matrix.run_hypothesis_replay / ReplaySession",
        "net_pnl_usd": round(res.net_pnl, 4),
        "num_trades": res.num_trades,
        "win_rate": round(res.win_rate, 4),
        "expectancy_usd": round(res.expectancy, 4),
        "adverse_selection_ticks": round(res.adverse_selection_ticks, 4),
        "tail_loss_usd": round(res.tail_loss, 4),
        "fills_csv": str(fills_path.relative_to(_REPO)).replace("\\", "/") if fills_path.exists() else None,
    }
    result_path = args.out / "result.json"
    result_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print("--- CHI404 MEASURED SPEED ---")
    print(f"  CPU loaded p99     : {chi404['cpu_loaded_p99_us']} us")
    print(f"  Gateway ping p99   : {chi404['gateway_ping_p99_ms']} ms")
    print(f"  Rithmic TCP p99    : {chi404['rithmic_tcp_65000_p99_ms']:.4f} ms  <-- backtest uses this")
    print(f"  Order ack p99      : not measured (R|API+ not wired)")
    if chi404.get("trial_order_ack_status"):
        print(f"  Trial VM order ack : {chi404['trial_order_ack_status']}")
    print("--- BACKTEST RESULT (NPZ replay @ Rithmic TCP p99, ReplaySession) ---")
    print(f"  Net PnL            : ${res.net_pnl:.2f}")
    print(f"  Trades             : {res.num_trades}")
    print(f"  Win rate           : {res.win_rate:.1%}")
    print(f"  Expectancy         : ${res.expectancy:.2f}/trade")
    print(f"  Adverse selection  : {res.adverse_selection_ticks:.2f} ticks")
    print(f"  Tail loss (5th)    : ${res.tail_loss:.2f}")
    print(f"Wrote {result_path}")
    if fills_path.exists():
        print(f"Wrote {fills_path} ({len(res.fills)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
