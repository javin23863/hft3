"""Shim: map normalized equity NDJSON (SessionTick) -> MBOEvent -> full pipeline."""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, List

import numpy as np

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages"))

from features_engine.src.features.feature_index import vector_to_feature_dict
from features_engine.src.features.mbo_features import MBOEvent, MBOFeatureExtractor
from equities_lane.src.config_loader import load_universe
from equities_lane.src.features.feature_registry import ablation_modules, run_feature_pipeline
from equities_lane.src.ingest.session_io import load_session
from equities_lane.src.models import SessionTick
from equities_lane.src.types import DegradedModeFlags


# ---------------------------------------------------------------------------
# MBOEvent adapter — maps normalized MBP-1 ticks into synthetic MBO stream
# ---------------------------------------------------------------------------

@dataclass
class BookState:
    """Tracks the best bid/ask levels from normalized MBP-1 ticks."""
    bid_px: float = 0.0
    bid_sz: int = 0
    ask_px: float = 0.0
    ask_sz: int = 0
    _order_counter: int = 0
    _bid_order_id: int = 1
    _ask_order_id: int = 2

    def reset(self) -> None:
        self.bid_px = 0.0
        self.bid_sz = 0
        self.ask_px = 0.0
        self.ask_sz = 0
        self._order_counter = 0

    def next_bid_oid(self) -> int:
        self._order_counter += 1
        return self._order_counter * 10 + 1

    def next_ask_oid(self) -> int:
        self._order_counter += 1
        return self._order_counter * 10 + 2


def session_tick_to_mbo_events(tick: SessionTick, state: BookState) -> list[MBOEvent]:
    """Map a single normalized SessionTick to one or more synthetic MBOEvents."""
    events: list[MBOEvent] = []

    if tick.event == "quote":
        # --- Bid side ---
        if tick.bid_px > 0:
            if tick.bid_px != state.bid_px:
                if state.bid_px > 0:
                    events.append(MBOEvent(
                        timestamp_ns=tick.ts_ns,
                        order_id=state._bid_order_id,
                        action="CANCEL",
                        side="B",
                        price=state.bid_px,
                        size=state.bid_sz,
                    ))
                oid = state.next_bid_oid()
                events.append(MBOEvent(
                    timestamp_ns=tick.ts_ns,
                    order_id=oid,
                    action="ADD",
                    side="B",
                    price=tick.bid_px,
                    size=tick.bid_sz,
                ))
                state._bid_order_id = oid
                state.bid_px = tick.bid_px
                state.bid_sz = tick.bid_sz
            elif tick.bid_sz != state.bid_sz:
                diff = tick.bid_sz - state.bid_sz
                action = "ADD" if diff > 0 else "CANCEL"
                events.append(MBOEvent(
                    timestamp_ns=tick.ts_ns,
                    order_id=state._bid_order_id,
                    action=action,
                    side="B",
                    price=tick.bid_px,
                    size=abs(diff),
                ))
                state.bid_sz = tick.bid_sz

        # --- Ask side ---
        if tick.ask_px > 0:
            if tick.ask_px != state.ask_px:
                if state.ask_px > 0:
                    events.append(MBOEvent(
                        timestamp_ns=tick.ts_ns,
                        order_id=state._ask_order_id,
                        action="CANCEL",
                        side="A",
                        price=state.ask_px,
                        size=state.ask_sz,
                    ))
                oid = state.next_ask_oid()
                events.append(MBOEvent(
                    timestamp_ns=tick.ts_ns,
                    order_id=oid,
                    action="ADD",
                    side="A",
                    price=tick.ask_px,
                    size=tick.ask_sz,
                ))
                state._ask_order_id = oid
                state.ask_px = tick.ask_px
                state.ask_sz = tick.ask_sz
            elif tick.ask_sz != state.ask_sz:
                diff = tick.ask_sz - state.ask_sz
                action = "ADD" if diff > 0 else "CANCEL"
                events.append(MBOEvent(
                    timestamp_ns=tick.ts_ns,
                    order_id=state._ask_order_id,
                    action=action,
                    side="A",
                    price=tick.ask_px,
                    size=abs(diff),
                ))
                state.ask_sz = tick.ask_sz

    elif tick.event == "trade" and tick.trade_px is not None and tick.trade_sz is not None:
        side = "B" if tick.aggressor == "buy" else "A"
        events.append(MBOEvent(
            timestamp_ns=tick.ts_ns,
            order_id=state._order_counter * 10 + 3,
            action="TRADE",
            side=side,
            price=tick.trade_px,
            size=tick.trade_sz,
        ))
        state._order_counter += 1

    return events


def iter_mbo_events(ticks: list[SessionTick], max_events: int | None = None) -> Iterator[MBOEvent]:
    """Iterate mapped MBOEvents from a list of SessionTicks."""
    state = BookState()
    emitted = 0
    for tick in ticks:
        for ev in session_tick_to_mbo_events(tick, state):
            yield ev
            emitted += 1
            if max_events is not None and emitted >= max_events:
                return


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

@dataclass
class PredictionResult:
    symbol: str
    session_date: str
    tick_count: int
    mbo_event_count: int
    feature_vector: np.ndarray | None = None
    raw_features: dict[str, float] | None = None
    equity_features: list[dict[str, Any]] | None = None
    backtest_result: dict[str, Any] | None = None
    mbo_feature_stats: dict[str, float] | None = None
    wall_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "symbol": self.symbol,
            "session_date": self.session_date,
            "tick_count": self.tick_count,
            "mbo_event_count": self.mbo_event_count,
            "wall_s": round(self.wall_s, 3),
        }
        if self.backtest_result:
            d["backtest"] = {
                "net_pnl": self.backtest_result.get("net_pnl"),
                "num_trades": self.backtest_result.get("num_trades"),
                "max_drawdown": self.backtest_result.get("max_drawdown"),
                "turnover": self.backtest_result.get("turnover"),
                "tail_loss": self.backtest_result.get("tail_loss"),
            }
        if self.mbo_feature_stats:
            d["feature_stats"] = self.mbo_feature_stats
        return d


def run_session_pipeline(
    ticks: list[SessionTick],
    meta_degraded: DegradedModeFlags | None = None,
    *,
    ablation: str | None = None,
    max_events: int | None = None,
) -> PredictionResult:
    """Run full MBO + equity feature extraction on one session."""
    symbol = getattr(ticks[0], "symbol") if hasattr(ticks[0], "symbol") else "UNKNOWN"
    session_date = getattr(ticks[0], "session_date") if hasattr(ticks[0], "session_date") else ""

    t0 = time.perf_counter()

    # Map to MBOEvents
    mbo_events = list(iter_mbo_events(ticks, max_events=max_events))
    mbo_count = len(mbo_events)

    # Run MBOFeatureExtractor
    extractor = MBOFeatureExtractor(rolling_window_ns=1_000_000_000)
    vectors: list[np.ndarray] = []
    for ev in mbo_events:
        vec = extractor.process_event(ev)
        vectors.append(vec)

    avg_vec = np.mean(vectors, axis=0) if vectors else np.zeros(64)
    feat_dict = vector_to_feature_dict(avg_vec)

    mbo_stats = {
        "total_mbo_events": mbo_count,
        "add_count": sum(1 for e in mbo_events if e.action == "ADD"),
        "cancel_count": sum(1 for e in mbo_events if e.action == "CANCEL"),
        "trade_count": sum(1 for e in mbo_events if e.action == "TRADE"),
        "modify_count": sum(1 for e in mbo_events if e.action == "MODIFY"),
    }
    for name, idx in [
        ("avg_spread", 15), ("avg_book_imbalance_l1", 35),
        ("avg_microprice", 37), ("avg_mid_price", 40),
    ]:
        if idx < len(avg_vec):
            mbo_stats[name] = float(avg_vec[idx])

    # Run equity lane structural features
    degraded = meta_degraded or DegradedModeFlags()
    _, universe, _ = load_universe(
        str(_REPO / "packages" / "equities_lane" / "config" / "universe.yaml")
    )
    toggles = universe.features.with_ablation(ablation) if ablation else universe.features
    equity_snaps = run_feature_pipeline(ticks, universe, degraded, ablation=ablation)

    wall_s = time.perf_counter() - t0

    return PredictionResult(
        symbol=symbol,
        session_date=session_date,
        tick_count=len(ticks),
        mbo_event_count=mbo_count,
        feature_vector=avg_vec,
        raw_features=feat_dict,
        equity_features=[s.to_dict() for s in equity_snaps],
        mbo_feature_stats=mbo_stats,
        wall_s=wall_s,
    )


def write_prediction_output(
    results: list[PredictionResult],
    out_dir: Path,
    run_id: str,
) -> Path:
    """Write prediction outputs to research_cards/equities/prediction_*."""
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sessions": [r.to_dict() for r in results],
    }

    (out_dir / "prediction_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    md_lines = [
        f"# Stocks Lane Prediction — {run_id}",
        "",
        f"**Sessions:** {len(results)}",
        f"**Generated:** {summary['timestamp']}",
        "",
        "## Per-Session Results",
        "",
    ]
    for r in results:
        d = r.to_dict()
        md_lines.append(f"### {d['symbol']} ({d['session_date']})")
        md_lines.append(f"- Ticks: {d['tick_count']} → MBO events: {d['mbo_event_count']}")
        md_lines.append(f"- Wall time: {d['wall_s']}s")
        bt = d.get("backtest")
        if bt:
            md_lines.append(f"- PnL: {bt['net_pnl']:.2f} | Trades: {bt['num_trades']}")
            md_lines.append(f"- Max DD: {bt['max_drawdown']:.4f} | Turnover: {bt['turnover']:.2f}")
        feat = d.get("feature_stats", {})
        if feat:
            md_lines.append(f"- MBO events: ADD={feat.get('add_count')} CANCEL={feat.get('cancel_count')} TRADE={feat.get('trade_count')}")
            md_lines.append(f"- Avg spread={feat.get('avg_spread', 'N/A')} mid={feat.get('avg_mid_price', 'N/A')}")
        md_lines.append("")

    (out_dir / "prediction_report.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(f"  => wrote {out_dir / 'prediction_summary.json'}")
    print(f"  => wrote {out_dir / 'prediction_report.md'}")
    return out_dir


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _has_meta_header(path: Path) -> bool:
    """Quick check: first line of NDJSON must have _type: meta."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            first = f.readline().strip()
            return '"meta"' in first or '"_type": "meta"' in first or "'_type': 'meta'" in first
    except Exception:
        return False


def discover_sessions() -> list[dict[str, Any]]:
    """Discover available session NDJSON files (skips auction files, missing meta)."""
    sessions: list[dict[str, Any]] = []

    fixture = _REPO / "packages" / "equities_lane" / "fixtures" / "low_float_session_v1.ndjson"
    if fixture.exists():
        sessions.append({
            "source": "fixture",
            "path": str(fixture),
            "label": "fixture_low_float_v1",
        })

    normalized_dir = _REPO / "data" / "equities" / "normalized"
    if normalized_dir.exists():
        for f in sorted(normalized_dir.glob("*.ndjson")):
            if "auction" in f.stem.lower():
                continue
            if not _has_meta_header(f):
                continue
            stem = f.stem
            parts = stem.split("_", 1)
            sessions.append({
                "source": "normalized",
                "path": str(f),
                "label": stem,
                "symbol": parts[0] if len(parts) > 0 else stem,
                "date": parts[1] if len(parts) > 1 else "",
            })

    return sessions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_stocks_lane",
        description="Map normalized equity ticks -> MBOEvent -> full pipeline",
    )
    parser.add_argument("--session-path", help="Path to a specific NDJSON session file")
    parser.add_argument("--fixture", action="store_true", help="Use the fixture session")
    parser.add_argument("--all", action="store_true", help="Run on all discovered sessions")
    parser.add_argument("--ablation", help="Ablation module (ofi, vpin, hawkes, hmm)")
    parser.add_argument("--max-events", type=int, default=200_000, help="Max synthetic MBO events per session")
    parser.add_argument("--out-dir", help="Output directory (default: research_cards/equities/prediction_<run_id>)")
    parser.add_argument("--no-backtest", action="store_true", help="Skip backtest, only run feature extraction")
    parser.add_argument("--discover", action="store_true", help="List available sessions and exit")
    args = parser.parse_args(argv)

    if args.discover:
        sessions = discover_sessions()
        print(json.dumps(sessions, indent=2))
        return 0

    # Resolve session paths
    session_paths: list[Path] = []
    if args.session_path:
        session_paths.append(Path(args.session_path))
    if args.fixture:
        fixture = _REPO / "packages" / "equities_lane" / "fixtures" / "low_float_session_v1.ndjson"
        if not fixture.exists():
            print(f"Fixture not found: {fixture}")
            return 1
        session_paths.append(fixture)
    if args.all:
        for s in discover_sessions():
            session_paths.append(Path(s["path"]))

    if not session_paths:
        parser.print_help()
        print("\nNo sessions specified. Use --fixture, --session-path, or --all.")
        return 1

    run_id = f"prediction_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    out_dir = Path(args.out_dir) if args.out_dir else (
        _REPO / "research_cards" / "equities" / run_id
    )

    results: list[PredictionResult] = []

    for sp in session_paths:
        print(f"\n{'='*60}")
        print(f"Session: {sp.name}")
        print(f"{'='*60}")

        try:
            meta, ticks = load_session(str(sp))
        except Exception as e:
            print(f"  SKIP: load_session failed: {e}")
            continue
        print(f"  ticks={len(ticks)} degraded={meta.degraded.degraded_mode}")

        result = run_session_pipeline(
            ticks,
            meta_degraded=meta.degraded,
            ablation=args.ablation,
            max_events=args.max_events,
        )
        result.symbol = meta.symbol
        result.session_date = meta.session_date

        print(f"  mbo_events={result.mbo_event_count}")
        print(f"  wall_s={result.wall_s:.3f}")

        if not args.no_backtest:
            from equities_lane.src.config_loader import load_universe
            from equities_lane.src.backtest.low_float_backtester import LowFloatBacktester

            _, universe, _ = load_universe(
                str(_REPO / "packages" / "equities_lane" / "config" / "universe.yaml")
            )
            bt = LowFloatBacktester(universe)
            bt_result = bt.run(str(sp), ablation=args.ablation, allow_degraded=True)
            result.backtest_result = bt_result.to_dict()
            print(f"  backtest: pnl={bt_result.net_pnl:.2f} trades={bt_result.num_trades}")

        results.append(result)

    print(f"\n{'='*60}")
    print(f"Writing results to {out_dir}")
    write_prediction_output(results, out_dir, run_id)

    # Summary table
    print(f"\n{'Symbol':<20} {'Ticks':>8} {'MBO':>8} {'PnL':>10} {'Trades':>8} {'Wall(s)':>8}")
    print("-" * 62)
    for r in results:
        pnl = r.backtest_result.get("net_pnl", 0) if r.backtest_result else 0
        trades = r.backtest_result.get("num_trades", 0) if r.backtest_result else 0
        print(f"{r.symbol:<20} {r.tick_count:>8} {r.mbo_event_count:>8} {pnl:>10.2f} {trades:>8} {r.wall_s:>8.3f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
