#!/usr/bin/env python3
"""Trial run: PDF_MODEL_4 hybrid execution via ReplayRunner on real Databento MBO NPZ."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for path in (_REPO, _REPO / "packages", _REPO / "apps"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backtest.adapters.rithmic_replay_loader import resolve_event_npz
from backtest_pipeline.src.chi404_latency import (
    BACKTEST_LATENCY_NOTE,
    DEFAULT_CHI404_SUMMARY,
    resolve_replay_latency_ms,
)
from backtest_pipeline.src.event_meta import load_event_row
from backtest_pipeline.src.pdf_hybrid_strategy import HybridExecutionStrategy
from backtest_pipeline.src.runner import QUEUE_MODELS, ReplayRunner
from features_engine.src.features.npz_feed import load_npz_events
from hft3.validation.research_stamp import build_certification_stamp, format_stamp_footer


DEFAULT_EVENTS_CSV = _REPO / "data_system" / "config" / "events.csv"


def _relative_repo_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_REPO.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def run_pdf_hybrid_replay(
    *,
    event_id: str,
    npz_path: Path,
    event_meta: dict,
    tick_size: float,
    latency_ms: float,
    latency_source: str,
    queue_model: str,
    step_ns: int,
    use_ofi: bool = True,
    use_vpin: bool = True,
    chi404_measured_speed: dict | None = None,
) -> dict:
    raw = load_npz_events(str(npz_path))
    strategy = HybridExecutionStrategy(
        str(npz_path),
        tick_size=tick_size,
        latency_ms=latency_ms,
        event_meta=event_meta,
        use_ofi=use_ofi,
        use_vpin=use_vpin,
    )
    runner = ReplayRunner(str(npz_path), tick_size=tick_size)
    result = runner.run_replay(
        model_logic_callback=strategy.on_step,
        latency_ms=latency_ms,
        queue_model=queue_model,
        step_ns=step_ns,
        use_combined_strategy=False,
    )
    return {
        "model_id": "PDF_MODEL_4",
        "engine": "pdf_hybrid",
        "description": "ReplayRunner + HybridExecutionStrategy (PDF_MODEL_1/3/4, TRADE VPIN)",
        "event_id": event_id,
        "defensive_mode": strategy.defensive_mode,
        "use_ofi": use_ofi,
        "use_vpin": use_vpin,
        "npz_path": _relative_repo_path(npz_path),
        "npz_source": "Databento MBO (trusted research lake)",
        "events": len(raw),
        "latency_ms": latency_ms,
        "latency_source": latency_source,
        "backtest_latency_note": BACKTEST_LATENCY_NOTE,
        "chi404_measured_speed": chi404_measured_speed,
        "queue_model": queue_model,
        "step_ns": step_ns,
        "result": result,
    }


def write_research_card(out_dir: Path, payload: dict, event_meta: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = build_certification_stamp(
        event_id=event_meta["event_id"],
        instrument=event_meta.get("primary_symbol", ""),
        model_id="PDF_MODEL_4",
        latency_band=payload.get("latency_ms"),
        queue_model=payload.get("queue_model"),
        execution_mode="REPLAY",
        execution_adapter_mode="legacy_hbt_callback",
    )
    card = {
        "scenario": f"{event_meta['event_id']} PDF hybrid replay",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "event_id": event_meta["event_id"],
        "release_date": event_meta["release_date"],
        "release_time_et": event_meta["release_time"],
        "window_utc": event_meta["window_utc"],
        "symbol": event_meta["primary_symbol"],
        "primary_research_engine": "pdf_hybrid",
        "engines": {"pdf_hybrid": payload},
        "certification_stamp": stamp,
        "certification_footer": format_stamp_footer(stamp),
    }
    (out_dir / "result.json").write_text(json.dumps(card, indent=2), encoding="utf-8")

    res = payload.get("result") or {}
    lines = [
        f"# PDF_MODEL_4 hybrid replay — {event_meta['event_id']}",
        "",
        f"- NPZ: `{payload.get('npz_path')}` ({payload.get('events')} events)",
        f"- Latency: {payload.get('latency_ms')} ms ({payload.get('latency_source', 'unknown')})",
        f"- Latency note: {payload.get('backtest_latency_note', BACKTEST_LATENCY_NOTE)}",
        f"- Queue model: {payload.get('queue_model')}",
        f"- Defensive mode: `{payload.get('defensive_mode')}` (use_ofi={payload.get('use_ofi')}, use_vpin={payload.get('use_vpin')})",
        "",
        "## Result",
        "",
        f"- steps: {res.get('steps')}",
        f"- balance: {res.get('balance')}",
        f"- fee: {res.get('fee')}",
        f"- num_trades: {res.get('num_trades')}",
        f"- position: {res.get('position')}",
        "",
        "Dependencies: PDF_MODEL_1 (OFI) → PDF_MODEL_3 (VPIN from TRADE vol) → PDF_MODEL_4.",
        "",
        f"_{format_stamp_footer(stamp)}_",
    ]
    if "error" in res:
        lines.extend(["", f"**Error:** {res['error']}"])
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description="PDF_MODEL_4 hybrid hftbacktest trial (real NPZ only)")
    p.add_argument("--event-id", required=True, help="Explicit catalog event id from events.csv")
    p.add_argument("--events-csv", type=Path, default=DEFAULT_EVENTS_CSV)
    p.add_argument("--npz", type=Path, default=None, help="Explicit NPZ path (must exist)")
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
        help="Output dir (default research_cards/PDF_MODEL_4_hybrid_replay)",
    )
    p.add_argument("--use-ofi", dest="use_ofi", action="store_true", default=True)
    p.add_argument("--no-ofi", dest="use_ofi", action="store_false")
    p.add_argument("--use-vpin", dest="use_vpin", action="store_true", default=True)
    p.add_argument("--no-vpin", dest="use_vpin", action="store_false")
    args = p.parse_args()

    event_meta = load_event_row(args.event_id, args.events_csv.resolve())
    try:
        npz_path = args.npz.resolve() if args.npz else resolve_event_npz(args.event_id, _REPO)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        print(
            "Prepare real MBO NPZ via:\n"
            "  python scripts/run_offline_pipeline.py --skip-download "
            f"--event-id {args.event_id}",
            file=sys.stderr,
        )
        return 1

    if not npz_path.is_file():
        print(f"NPZ missing: {npz_path}", file=sys.stderr)
        print(
            f"Run: python scripts/run_offline_pipeline.py --skip-download --event-id {args.event_id}",
            file=sys.stderr,
        )
        return 1

    latency_ms, latency_source, chi404_meta = resolve_replay_latency_ms(
        latency_ms=args.latency_ms,
        chi404_summary=args.chi404_summary,
    )

    print(
        f"event_id={args.event_id} NPZ={npz_path} "
        f"latency={latency_ms:.4f} ms ({latency_source})",
        flush=True,
    )
    payload = run_pdf_hybrid_replay(
        event_id=args.event_id,
        npz_path=npz_path,
        event_meta=event_meta,
        tick_size=args.tick_size,
        latency_ms=latency_ms,
        latency_source=latency_source,
        chi404_measured_speed=chi404_meta,
        queue_model=args.queue_model,
        step_ns=args.step_ns,
        use_ofi=args.use_ofi,
        use_vpin=args.use_vpin,
    )

    if "error" in payload["result"]:
        print(json.dumps(payload["result"], indent=2), flush=True)
        return 1

    out_dir = args.out or (_REPO / "research_cards" / "PDF_MODEL_4_hybrid_replay")
    write_research_card(out_dir, payload, event_meta)
    print(json.dumps(payload["result"], indent=2), flush=True)
    print(f"Wrote {out_dir / 'result.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
