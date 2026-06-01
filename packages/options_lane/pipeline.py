"""CLI for put/call parity options lane."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from options_lane.src.backtest.multi_leg_backtester import (
    MultiLegParityBacktester,
    write_research_card,
)
from options_lane.src.config_loader import load_group_by_id, load_universe
from options_lane.src.ingest.databento_options import collect_download_specs, discover_groups
from options_lane.src.ingest.quote_aligner import align_quotes
from options_lane.src.ingest.quotes_io import load_quote_ndjson
from options_lane.src.parity_engine import compute_violation, is_actionable


def cmd_discover(args: argparse.Namespace) -> int:
    groups = discover_groups(args.config)
    jobs = collect_download_specs(args.config)
    payload = {
        "groups": [g.id for g in groups],
        "download_jobs": jobs,
    }
    print(json.dumps(payload, indent=2))
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    group = load_group_by_id(args.config, args.group)
    quotes_path = Path(args.quotes)
    quotes = load_quote_ndjson(quotes_path)
    bt = MultiLegParityBacktester(latency_ms=args.latency_ms)
    result = bt.run(group, quotes)
    _, _, paths = load_universe(args.config)
    out_dir = Path(args.output_dir) if args.output_dir else paths["reports_root"]
    card = write_research_card(result, out_dir)
    print(json.dumps({"card": str(card), "net_pnl": result.net_pnl, "num_arbs": result.num_arbs}, indent=2))
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    group = load_group_by_id(args.config, args.group)
    quotes = load_quote_ndjson(args.quotes)
    snapshots = align_quotes(group, quotes)
    violations = []
    for snap in snapshots:
        v = compute_violation(group, snap)
        if v and is_actionable(v, group):
            violations.append(
                {
                    "timestamp_ns": v.timestamp_ns,
                    "residual": v.residual,
                    "edge_ticks": v.edge_ticks,
                }
            )
    print(json.dumps({"group_id": group.id, "violations": violations}, indent=2))
    return 0


def cmd_fixture_backtest(args: argparse.Namespace) -> int:
    """Run backtest on committed fixture (no API key)."""
    fixture = _PKG_ROOT / "options_lane" / "fixtures" / args.fixture
    args.quotes = str(fixture)
    args.group = args.group or "example_same_ul"
    args.config = str(_PKG_ROOT / "options_lane" / "config" / "parity_universe.yaml")
    return cmd_backtest(args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="options_lane.pipeline")
    parser.add_argument(
        "--config",
        default=str(_PKG_ROOT / "options_lane" / "config" / "parity_universe.yaml"),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_disc = sub.add_parser("discover", help="List parity groups and download jobs")
    p_disc.set_defaults(func=cmd_discover)

    p_bt = sub.add_parser("backtest", help="Backtest parity arb on quote NDJSON")
    p_bt.add_argument("--group", required=True)
    p_bt.add_argument("--quotes", required=True)
    p_bt.add_argument("--output-dir", default=None)
    p_bt.add_argument("--latency-ms", type=float, default=1.0)
    p_bt.set_defaults(func=cmd_backtest)

    p_scan = sub.add_parser("scan", help="Scan quotes for actionable violations")
    p_scan.add_argument("--group", required=True)
    p_scan.add_argument("--quotes", required=True)
    p_scan.set_defaults(func=cmd_scan)

    p_fix = sub.add_parser("fixture-backtest", help="Backtest committed fixture")
    p_fix.add_argument("--fixture", default="violation_futures_quotes.ndjson")
    p_fix.add_argument("--group", default="example_same_ul")
    p_fix.add_argument("--output-dir", default=None)
    p_fix.add_argument("--latency-ms", type=float, default=1.0)
    p_fix.set_defaults(func=cmd_fixture_backtest)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
