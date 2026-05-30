#!/usr/bin/env python3
"""Event-driven replay: events.csv event_id + Databento NPZ + CHI404 latency.

Runs two labeled engines:
  1. hftbacktest_loop — ReplayRunner + MBO-synced CombinedHypothesisStrategy (queue fills)
  2. event_accurate_mbo — SignalBacktester per-hypothesis MBO pipeline (research path)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from backtest.adapters.rithmic_replay_loader import resolve_event_npz
from backtest_pipeline.src.runner import ReplayRunner
from backtest_pipeline.src.signal_backtester import BacktestResult, SignalBacktester
from data_system.src.events_parser import load_and_parse_events
from features_engine.src.features.npz_feed import load_npz_events
from features_engine.src.hypotheses.registry import get_active_hypotheses

DEFAULT_CHI404_SUMMARY = _REPO / "runtime" / "latency_reports" / "latency_summary.json"
DEFAULT_EVENTS_CSV = _REPO / "data_system" / "config" / "events.csv"


def _relative_repo_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_REPO.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def load_chi404_speed(summary_path: Path) -> dict[str, Any]:
    if not summary_path.is_file():
        raise FileNotFoundError(
            f"CHI404 latency summary missing: {summary_path}. "
            "Run scripts/latency_probe/run_all.sh on CHI404 or chi404_sync_trial_data.sh."
        )
    s = json.loads(summary_path.read_text(encoding="utf-8"))
    network = s.get("network") or {}
    rithmic_tcp = network.get("rithmic_tcp_65000") or {}
    gateway = network.get("gateway_ping") or {}
    cyclictest = s.get("cyclictest") or {}
    trial = s.get("trial_order_ack_appendix") or {}

    rithmic_tcp_p99_ms = rithmic_tcp.get("p99_ms")
    if not isinstance(rithmic_tcp_p99_ms, (int, float)):
        raise ValueError("CHI404 summary has no rithmic_tcp_65000 p99_ms")

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


def load_event_row(event_id: str, events_csv: Path) -> dict[str, Any]:
    events = load_and_parse_events(str(events_csv))
    row = events[events["event_id"] == event_id]
    if row.empty:
        raise SystemExit(f"event_id not found in events.csv: {event_id}")
    r = row.iloc[0]
    return {
        "event_id": event_id,
        "release_date": str(r["release_date"]),
        "release_time": str(r["release_time"]),
        "timezone": str(r["timezone"]),
        "window_name": str(r["window_name"]),
        "start_utc": r["start_utc"].isoformat(),
        "end_utc": r["end_utc"].isoformat(),
        "symbols": r["parsed_symbols"],
        "primary_symbol": r["parsed_symbols"][0],
    }


def _serialize_backtest_result(res: BacktestResult) -> dict[str, Any]:
    return {
        "hypothesis_id": res.hypothesis_id,
        "net_pnl_usd": round(res.net_pnl, 4),
        "num_trades": res.num_trades,
        "win_rate": round(res.win_rate, 4),
        "expectancy_usd": round(res.expectancy, 4),
        "adverse_selection_ticks": round(res.adverse_selection_ticks, 4),
        "tail_loss_usd": round(res.tail_loss, 4),
    }


def run_event_accurate_mbo(raw, latency_ms: float) -> dict[str, Any]:
    hyps = get_active_hypotheses()
    bt = SignalBacktester(signal_threshold=0.15)
    results = bt.run_all_hypotheses(hyps, raw, latency_ms=latency_ms)
    by_id = {h.hyp_id: h.name for h in hyps}
    serialized = []
    for hyp_id, res in sorted(results.items()):
        row = _serialize_backtest_result(res)
        row["hypothesis_name"] = by_id.get(hyp_id, "")
        serialized.append(row)

    with_trades = [r for r in serialized if r["num_trades"] > 0]
    with_trades.sort(key=lambda r: abs(r["net_pnl_usd"]), reverse=True)
    hyp5 = next((r for r in serialized if r["hypothesis_id"] == 5), None)

    return {
        "engine": "event_accurate_mbo",
        "description": "SignalBacktester: full MBO MarketStatePipeline, per-hypothesis signals",
        "feature_path": "mbo_pipeline",
        "signal_threshold": 0.15,
        "hypothesis_count": len(hyps),
        "hypotheses_with_trades": len(with_trades),
        "total_trades_all_hypotheses": sum(r["num_trades"] for r in serialized),
        "top_by_abs_pnl": with_trades[:5],
        "hyp_5_spread_blowout": hyp5,
        "all_hypotheses": serialized,
    }


def write_report(
    out_dir: Path,
    event: dict[str, Any],
    npz_path: Path,
    event_count: int,
    chi404: dict[str, Any],
    hft_result: dict[str, Any],
    mbo_result: dict[str, Any],
    latency_ms: float,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "scenario": f"{event['event_id']} event replay",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "event_id": event["event_id"],
        "release_date": event["release_date"],
        "release_time_et": event["release_time"],
        "window_utc": f"{event['start_utc']} to {event['end_utc']}",
        "symbol": event["primary_symbol"],
        "npz_path": _relative_repo_path(npz_path),
        "npz_source": "Databento MBO (trusted research lake)",
        "events": event_count,
        "live_orders_sent": False,
        "chi404_measured_speed": chi404,
        "backtest_latency_ms": latency_ms,
        "backtest_latency_note": "TCP connect p99; order submit→ack not measured until Stage 3 paper harness",
        "primary_research_engine": "event_accurate_mbo",
        "engines": {
            "hftbacktest_loop": {
                "engine": "hftbacktest_loop",
                "description": "ReplayRunner + MBO-synced CombinedHypothesisStrategy (queue-realistic LIMIT fills)",
                "feature_path": "mbo_pipeline_synced_to_hbt.current_timestamp",
                "aggregation": "max_abs",
                "signal_threshold": 0.15,
                "result": hft_result,
            },
            "event_accurate_mbo": mbo_result,
        },
    }
    result_path = out_dir / "result.json"
    result_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    hft = hft_result
    hyp5 = mbo_result.get("hyp_5_spread_blowout") or {}
    md_lines = [
        f"# Event replay: {event['event_id']}",
        "",
        f"- **release_date:** {event['release_date']}",
        f"- **window UTC:** {event['start_utc']} → {event['end_utc']}",
        f"- **NPZ:** `{payload['npz_path']}` ({event_count} events)",
        f"- **latency:** {latency_ms:.4f} ms ({chi404['backtest_latency_source']})",
        f"- **CHI404 probe:** {chi404.get('probe_run_id')}",
        f"- **primary research engine:** event_accurate_mbo",
        "",
        "## CHI404 measured speed",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| CPU loaded p99 | {chi404.get('cpu_loaded_p99_us')} µs |",
        f"| Gateway ping p99 | {chi404.get('gateway_ping_p99_ms')} ms |",
        f"| Rithmic TCP p99 | {chi404.get('rithmic_tcp_65000_p99_ms'):.4f} ms |",
        "| Order ack p99 | not measured |",
        "",
        "## Engine 1: hftbacktest_loop (queue-realistic)",
        "",
        "MBO features synced to `hbt.current_timestamp`; max-abs aggregation; threshold 0.15.",
        "",
        f"- steps: {hft.get('steps')}",
        f"- balance: {hft.get('balance')}",
        f"- num_trades: {hft.get('num_trades')}",
        f"- position: {hft.get('position')}",
        "",
        "## Engine 2: event_accurate_mbo (research path)",
        "",
        "SignalBacktester with full MarketStatePipeline; per-hypothesis evaluation.",
        "",
        f"- hypotheses with trades: {mbo_result.get('hypotheses_with_trades')} / {mbo_result.get('hypothesis_count')}",
        f"- total trades (all hyps): {mbo_result.get('total_trades_all_hypotheses')}",
        f"- HYP_5 trades: {hyp5.get('num_trades', 0)}",
        f"- HYP_5 net PnL: ${hyp5.get('net_pnl_usd', 0)}",
        "",
        "## Limits",
        "",
        "- Zero trades on the old depth-only mean@0.25 path was a wiring issue, not missing edge.",
        "- hftbacktest_loop and event_accurate_mbo measure different fill models; compare explicitly.",
        "- Replay body is Databento MBO for the macro event window, not Rithmic historical tape.",
    ]
    (out_dir / "report.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description="Event-driven replay with CHI404-measured latency")
    p.add_argument("--event-id", required=True)
    p.add_argument("--npz", type=Path, default=None, help="Override NPZ; default from events.csv symbol")
    p.add_argument("--events-csv", type=Path, default=DEFAULT_EVENTS_CSV)
    p.add_argument("--chi404-summary", type=Path, default=DEFAULT_CHI404_SUMMARY)
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output dir (default research_cards/<event_id>_replay)",
    )
    p.add_argument("--latency-ms", type=float, default=None, help="Override CHI404 TCP p99")
    p.add_argument("--tick-size", type=float, default=0.25)
    p.add_argument(
        "--skip-hftbacktest",
        action="store_true",
        help="Skip slow HftBacktest loop; run event_accurate_mbo only",
    )
    args = p.parse_args()

    event = load_event_row(args.event_id, args.events_csv.resolve())
    npz_path = args.npz.resolve() if args.npz else resolve_event_npz(args.event_id, _REPO)
    if not npz_path.is_file():
        raise SystemExit(f"NPZ missing: {npz_path}")

    raw = load_npz_events(str(npz_path))
    if len(raw) == 0:
        raise SystemExit(f"NPZ empty: {npz_path}")

    chi404 = load_chi404_speed(args.chi404_summary.resolve())
    latency_ms = float(args.latency_ms) if args.latency_ms is not None else chi404["backtest_latency_ms"]

    out_dir = args.out or (_REPO / "research_cards" / f"{args.event_id}_replay")

    print(f"event_id={args.event_id} release_date={event['release_date']}", flush=True)
    print(f"NPZ={npz_path} events={len(raw)}", flush=True)
    print(f"latency={latency_ms:.4f} ms probe={chi404.get('probe_run_id')}", flush=True)

    if args.skip_hftbacktest:
        hft_result = {"skipped": True}
        print("Skipping hftbacktest_loop (--skip-hftbacktest)", flush=True)
    else:
        print("=== engine 1: hftbacktest_loop ===", flush=True)
        runner = ReplayRunner(str(npz_path), tick_size=args.tick_size)
        hft_result = runner.run_replay(latency_ms=latency_ms, use_combined_strategy=True)
        if "error" in hft_result:
            print(json.dumps(hft_result, indent=2), flush=True)
            return 1
        print(json.dumps(hft_result, indent=2), flush=True)

    print("=== engine 2: event_accurate_mbo ===", flush=True)
    mbo_result = run_event_accurate_mbo(raw, latency_ms)
    print(
        f"hypotheses_with_trades={mbo_result['hypotheses_with_trades']} "
        f"total_trades={mbo_result['total_trades_all_hypotheses']} "
        f"HYP_5_trades={(mbo_result.get('hyp_5_spread_blowout') or {}).get('num_trades', 0)}",
        flush=True,
    )

    write_report(out_dir, event, npz_path, len(raw), chi404, hft_result, mbo_result, latency_ms)
    print(f"Wrote {out_dir / 'result.json'}", flush=True)
    print(f"Wrote {out_dir / 'report.md'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
