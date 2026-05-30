#!/usr/bin/env python3
"""Defensive-layer ablation: run PDF_MODEL_4 with all use_ofi x use_vpin combinations."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from backtest.adapters.rithmic_replay_loader import resolve_event_npz
from backtest_pipeline.src.chi404_latency import DEFAULT_CHI404_SUMMARY, resolve_replay_latency_ms
from backtest_pipeline.src.event_meta import load_event_row
from backtest_pipeline.src.pdf_hybrid_ablation import run_defensive_ablation_matrix
from backtest_pipeline.src.runner import QUEUE_MODELS

DEFAULT_EVENTS_CSV = _REPO / "data_system" / "config" / "events.csv"


def write_ablation_report(out_dir: Path, matrix: dict, event_meta: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    card = {
        "scenario": f"{event_meta['event_id']} PDF defensive ablation",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "event_id": event_meta["event_id"],
        "window_utc": event_meta["window_utc"],
        "symbol": event_meta["primary_symbol"],
        "primary_research_engine": "pdf_hybrid_ablation",
        "ablation": matrix,
    }
    (out_dir / "result.json").write_text(json.dumps(card, indent=2), encoding="utf-8")

    lines = [
        f"# PDF_MODEL_4 defensive ablation — {event_meta['event_id']}",
        "",
        "| mode | use_ofi | use_vpin | net_pnl_after_fee | trades | cancel | refresh | mean_vpin |",
        "|------|---------|----------|-------------------|--------|--------|---------|-----------|",
    ]
    for mode in matrix.get("modes", []):
        m = mode.get("metrics", {})
        lines.append(
            f"| {mode.get('mode_id')} | {mode.get('use_ofi')} | {mode.get('use_vpin')} | "
            f"{m.get('net_pnl_after_fee', m.get('net_pnl', 0)):.2f} | {m.get('num_trades', 0)} | "
            f"{m.get('cancel_count', 0):.0f} | {m.get('quote_refresh_count', 0):.0f} | "
            f"{m.get('mean_vpin', 0):.4f} |"
        )
    lines.extend(
        [
            "",
            "Modes:",
            "- `as_baseline`: pure Avellaneda-Stoikov (both flags off)",
            "- `ofi_only`: OFI drift only",
            "- `vpin_only`: VPIN lambda/toxic (unit OFI probe; no book OFI)",
            "- `hybrid_full`: both defensive layers",
            "",
            "`net_pnl` is ending balance; `net_pnl_after_fee` = balance - fee.",
        ]
    )
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description="PDF_MODEL_4 defensive ablation matrix (real NPZ only)")
    p.add_argument("--event-id", default="CPI_2024_09_11_TIGHT")
    p.add_argument("--events-csv", type=Path, default=DEFAULT_EVENTS_CSV)
    p.add_argument("--npz", type=Path, default=None)
    p.add_argument(
        "--chi404-summary",
        type=Path,
        default=DEFAULT_CHI404_SUMMARY,
        help="CHI404 latency_summary.json (default when --latency-ms omitted)",
    )
    p.add_argument(
        "--latency-ms",
        type=float,
        default=None,
        help="Override replay latency; default from CHI404 summary",
    )
    p.add_argument("--queue-model", default="LogProbQueueModel2", choices=QUEUE_MODELS)
    p.add_argument("--step-ns", type=int, default=100_000)
    p.add_argument("--tick-size", type=float, default=0.25)
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output dir (default research_cards/PDF_MODEL_4_defensive_ablation)",
    )
    args = p.parse_args()

    event_meta = load_event_row(args.event_id, args.events_csv.resolve())
    try:
        npz_path = args.npz.resolve() if args.npz else resolve_event_npz(args.event_id, _REPO)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        print(
            "Prepare real MBO NPZ via:\n"
            f"  python scripts/run_offline_pipeline.py --skip-download --event-id {args.event_id}",
            file=sys.stderr,
        )
        return 1

    if not npz_path.is_file():
        print(f"NPZ missing: {npz_path}", file=sys.stderr)
        return 1

    latency_ms, latency_source = resolve_replay_latency_ms(
        latency_ms=args.latency_ms,
        chi404_summary=args.chi404_summary,
    )

    print(f"Running 4-mode ablation on {npz_path} (latency={latency_ms:.4f} ms, {latency_source}) ...", flush=True)
    matrix = run_defensive_ablation_matrix(
        npz_path=npz_path,
        event_meta=event_meta,
        tick_size=args.tick_size,
        latency_ms=latency_ms,
        queue_model=args.queue_model,
        step_ns=args.step_ns,
    )
    matrix["latency_source"] = latency_source

    errors: list[str] = []
    for mode in matrix.get("modes", []):
        m = mode.get("metrics", {})
        if "error" in m:
            errors.append(f"{mode.get('mode_id')}: {m['error']}")
            print(f"{mode['mode_id']}: ERROR {m['error']}", flush=True)
            continue
        print(
            f"{mode['mode_id']}: pnl_after_fee={m.get('net_pnl_after_fee', m['net_pnl']):.2f} "
            f"trades={m['num_trades']} cancel={m.get('cancel_count', 0):.0f} "
            f"refresh={m.get('quote_refresh_count', 0):.0f}",
            flush=True,
        )

    out_dir = args.out or (_REPO / "research_cards" / "PDF_MODEL_4_defensive_ablation")
    write_ablation_report(out_dir, matrix, event_meta)
    print(f"Wrote {out_dir / 'result.json'}", flush=True)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
