#!/usr/bin/env python3
"""Event-driven replay: events.csv event_id + Databento NPZ + CHI404 latency.

Runs adapter-backed replay engines:
  1. replay_execution_adapter — ReplaySession + CombinedHypothesisReplayStrategy (HftBacktest adapter)
  2. per_hypothesis_replay — ReplaySession matrix per hypothesis (replaces SignalBacktester fills)
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

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from backtest.adapters.rithmic_replay_loader import resolve_event_npz
from backtest_pipeline.src.chi404_latency import DEFAULT_CHI404_SUMMARY, load_chi404_speed, resolve_replay_latency_ms
from backtest_pipeline.src.event_meta import load_event_row
from backtest_pipeline.src.replay_matrix import run_all_hypotheses_replay
from backtest_pipeline.src.runner import ReplayRunner
from hft3.validation.research_stamp import build_certification_stamp, format_stamp_footer
from backtest_pipeline.src.signal_backtester import BacktestResult
from features_engine.src.features.npz_feed import load_npz_events
from features_engine.src.hypotheses.registry import get_active_hypotheses

DEFAULT_EVENTS_CSV = _REPO / "data_system" / "config" / "events.csv"


def _relative_repo_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_REPO.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


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


def run_per_hypothesis_replay(npz_path: str, latency_ms: float) -> dict[str, Any]:
    hyps = get_active_hypotheses()
    results = run_all_hypotheses_replay(hyps, npz_path, latency_ms=latency_ms)
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
        "engine": "per_hypothesis_replay",
        "description": "ReplaySession + HypothesisReplayStrategy per hypothesis (adapter-backed)",
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
    hyp_result: dict[str, Any],
    latency_ms: float,
    lifecycle_summary: dict[str, Any] | None = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = build_certification_stamp(
        event_id=event["event_id"],
        instrument=event["primary_symbol"],
        event_type=event.get("event_type", "macro"),
        latency_band=latency_ms,
        queue_model="LogProbQueueModel2",
        fee_model="FeeModel",
        execution_adapter_mode="hftbacktest_simulated_exchange",
        execution_mode="REPLAY",
        data_version="databento_mbo",
    )
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
        "live_broker_call_count": (lifecycle_summary or {}).get("live_broker_call_count", 0),
        "rithmic_order_call_count": (lifecycle_summary or {}).get("rithmic_order_call_count", 0),
        "chi404_measured_speed": chi404,
        "backtest_latency_ms": latency_ms,
        "backtest_latency_note": "Replay uses HftBacktestSimulatedExchangeAdapter; paper ack separate",
        "primary_research_engine": "replay_execution_adapter",
        "certification_stamp": stamp,
        "certification_footer": format_stamp_footer(stamp),
        "engines": {
            "replay_execution_adapter": {
                "engine": "replay_execution_adapter",
                "description": "ReplaySession + CombinedHypothesisReplayStrategy (HftBacktest adapter)",
                "feature_path": "mbo_pipeline_synced_to_replay_clock",
                "aggregation": "max_abs",
                "signal_threshold": 0.15,
                "result": hft_result,
                "order_lifecycle_summary": lifecycle_summary,
            },
            "per_hypothesis_replay": hyp_result,
        },
    }
    result_path = out_dir / "result.json"
    result_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    hft = hft_result
    hyp5 = hyp_result.get("hyp_5_spread_blowout") or {}
    md_lines = [
        f"# Event replay: {event['event_id']}",
        "",
        f"- **release_date:** {event['release_date']}",
        f"- **window UTC:** {event['start_utc']} → {event['end_utc']}",
        f"- **NPZ:** `{payload['npz_path']}` ({event_count} events)",
        f"- **latency:** {latency_ms:.4f} ms ({chi404['backtest_latency_source']})",
        f"- **CHI404 probe:** {chi404.get('probe_run_id')}",
        f"- **primary research engine:** replay_execution_adapter",
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
        "## Engine 1: replay_execution_adapter (combined, queue-realistic)",
        "",
        "MBO features synced to replay clock; max-abs aggregation; OrderIntent → HftBacktest adapter.",
        "",
        f"- steps: {hft.get('steps')}",
        f"- balance: {hft.get('balance')}",
        f"- num_trades: {hft.get('num_trades')}",
        f"- order_intent_count: {hft.get('order_intent_count')}",
        f"- position: {hft.get('position')}",
        "",
        "## Engine 2: per_hypothesis_replay (adapter-backed matrix)",
        "",
        "ReplaySession per hypothesis; replaces deprecated SignalBacktester fill sim.",
        "",
        f"- hypotheses with trades: {hyp_result.get('hypotheses_with_trades')} / {hyp_result.get('hypothesis_count')}",
        f"- total trades (all hyps): {hyp_result.get('total_trades_all_hypotheses')}",
        f"- HYP_5 trades: {hyp5.get('num_trades', 0)}",
        f"- HYP_5 net PnL: ${hyp5.get('net_pnl_usd', 0)}",
        "",
        "## Limits",
        "",
        "- Zero trades on the old depth-only mean@0.25 path was a wiring issue, not missing edge.",
        "- Combined and per-hyp paths both route OrderIntent through HftBacktestSimulatedExchangeAdapter.",
        "- Replay body is Databento MBO for the macro event window, not Rithmic historical tape.",
        "",
        f"_{format_stamp_footer(stamp)}_",
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
        "--skip-combined-replay",
        action="store_true",
        help="Skip combined ReplaySession loop; run per-hypothesis matrix only",
    )
    p.add_argument(
        "--skip-hftbacktest",
        action="store_true",
        help="Deprecated alias for --skip-combined-replay",
    )
    p.add_argument(
        "--engine",
        choices=("default", "pdf_hybrid"),
        default="default",
        help="default: adapter-backed combined + per-hyp replay; pdf_hybrid: PDF_MODEL_4 stack",
    )
    p.add_argument("--use-ofi", dest="use_ofi", action="store_true", default=True)
    p.add_argument("--no-ofi", dest="use_ofi", action="store_false")
    p.add_argument("--use-vpin", dest="use_vpin", action="store_true", default=True)
    p.add_argument("--no-vpin", dest="use_vpin", action="store_false")
    args = p.parse_args()

    if args.engine == "pdf_hybrid":
        import importlib.util

        _pdf_script = _REPO / "scripts" / "run_pdf_hybrid_replay.py"
        _spec = importlib.util.spec_from_file_location("run_pdf_hybrid_replay", _pdf_script)
        _pdf_mod = importlib.util.module_from_spec(_spec)
        assert _spec.loader is not None
        _spec.loader.exec_module(_pdf_mod)

        try:
            event_meta = load_event_row(args.event_id, args.events_csv.resolve())
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        try:
            npz_path = args.npz.resolve() if args.npz else resolve_event_npz(args.event_id, _REPO)
        except FileNotFoundError as exc:
            raise SystemExit(str(exc)) from exc
        if not npz_path.is_file():
            raise SystemExit(f"NPZ missing: {npz_path}")
        latency_ms, latency_source, chi404_meta = resolve_replay_latency_ms(
            latency_ms=args.latency_ms,
            chi404_summary=args.chi404_summary,
        )
        payload = _pdf_mod.run_pdf_hybrid_replay(
            event_id=args.event_id,
            npz_path=npz_path,
            event_meta=event_meta,
            tick_size=args.tick_size,
            latency_ms=latency_ms,
            latency_source=latency_source,
            chi404_measured_speed=chi404_meta,
            queue_model="LogProbQueueModel2",
            step_ns=100_000,
            use_ofi=args.use_ofi,
            use_vpin=args.use_vpin,
        )
        if "error" in payload["result"]:
            print(json.dumps(payload["result"], indent=2), flush=True)
            return 1
        out_dir = args.out or (_REPO / "research_cards" / "PDF_MODEL_4_hybrid_replay")
        _pdf_mod.write_research_card(out_dir, payload, event_meta)
        print(json.dumps(payload["result"], indent=2), flush=True)
        print(f"Wrote {out_dir / 'result.json'}", flush=True)
        return 0

    event = load_event_row(args.event_id, args.events_csv.resolve())
    npz_path = args.npz.resolve() if args.npz else resolve_event_npz(args.event_id, _REPO)
    if not npz_path.is_file():
        raise SystemExit(f"NPZ missing: {npz_path}")

    raw = load_npz_events(str(npz_path))
    if len(raw) == 0:
        raise SystemExit(f"NPZ empty: {npz_path}")

    chi404 = load_chi404_speed(args.chi404_summary.resolve())
    latency_ms, latency_source, chi404_meta = resolve_replay_latency_ms(
        latency_ms=args.latency_ms,
        chi404_summary=args.chi404_summary,
    )
    if chi404_meta is not None:
        chi404 = chi404_meta

    out_dir = args.out or (_REPO / "research_cards" / f"{args.event_id}_replay")

    print(f"event_id={args.event_id} release_date={event['release_date']}", flush=True)
    print(f"NPZ={npz_path} events={len(raw)}", flush=True)
    print(f"latency={latency_ms:.4f} ms ({latency_source}) probe={chi404.get('probe_run_id')}", flush=True)

    skip_combined = args.skip_combined_replay or args.skip_hftbacktest
    if skip_combined:
        hft_result = {"skipped": True}
        lifecycle_summary = None
        print("Skipping combined replay (--skip-combined-replay)", flush=True)
    else:
        print("=== engine 1: replay_execution_adapter ===", flush=True)
        runner = ReplayRunner(str(npz_path), tick_size=args.tick_size)
        hft_result = runner.run_replay(latency_ms=latency_ms, use_combined_strategy=True)
        if "error" in hft_result:
            print(json.dumps(hft_result, indent=2), flush=True)
            return 1
        lifecycle_summary = hft_result.get("order_lifecycle_summary")
        print(json.dumps(hft_result, indent=2), flush=True)

    print("=== engine 2: per_hypothesis_replay ===", flush=True)
    hyp_result = run_per_hypothesis_replay(str(npz_path), latency_ms)
    print(
        f"hypotheses_with_trades={hyp_result['hypotheses_with_trades']} "
        f"total_trades={hyp_result['total_trades_all_hypotheses']} "
        f"HYP_5_trades={(hyp_result.get('hyp_5_spread_blowout') or {}).get('num_trades', 0)}",
        flush=True,
    )

    write_report(
        out_dir, event, npz_path, len(raw), chi404, hft_result, hyp_result, latency_ms, lifecycle_summary
    )
    print(f"Wrote {out_dir / 'result.json'}", flush=True)
    print(f"Wrote {out_dir / 'report.md'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
