"""
Download imbalance-lane enrichments (not a single default event).

Covers:
  1. Every macro event in packages/data_system/config/events.csv → MBO NPZ + MBP-10 DBN
  2. Every pullable equities decadal session → MBO raw + auction imbalance NDJSON (if missing)
  3. Campaign NPZ backfill for selected workbench models

OPRA options, normalized session NDJSON, and daily OHLCV use the equities lane:
  python scripts/download_all_research_data.py
  or: python -m equities_lane.pipeline pull-decadal --resume --pull-options

Requires DATABENTO_API_KEY in .env.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, List, Tuple

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO / ".env")
except ImportError:
    pass


def _as_dt(value: Any):
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime()
    return value


def iter_macro_events():
    from data_system.src.events_parser import load_and_parse_events

    csv = _REPO / "packages" / "data_system" / "config" / "events.csv"
    for _, row in load_and_parse_events(str(csv)).iterrows():
        parsed = tuple(str(s) for s in row["parsed_symbols"])
        symbol = parsed[0] if parsed else "MES.v.0"
        yield row["event_id"], symbol, parsed, row


def mbo_npz_path(event_id: str, symbol: str) -> Tuple[Path, bool]:
    from data_system.src.npz_resolver import resolve_npz_for_event

    path, present, _ = resolve_npz_for_event(_REPO, event_id, symbol, (symbol,))
    return path, present


def mbp10_path(event_id: str, symbol: str) -> Path:
    return _REPO / "data" / "replay" / "mbp10" / f"{symbol}_{event_id}_mbp-10.dbn.zst"


def ensure_macro_mbo(event_id: str, symbol: str, parsed: tuple, row, *, max_cost: float | None) -> bool:
    path, present = mbo_npz_path(event_id, symbol)
    if present:
        return True
    dbn = _REPO / "data" / f"{event_id}_mbo.dbn.zst"
    if dbn.is_file():
        from backtest_pipeline.src.converter import DatabentoConverter
        from workbench.src.data.catalog_backfill import resolve_download_symbol
        from workbench.src.data.event_catalog import EventSpec
        from data_system.src.databento_client import DatabentoResearchClient

        client = DatabentoResearchClient()
        ev = EventSpec(
            event_id=event_id,
            event_type=str(row.get("event_type", "")),
            release_date=str(row["release_date"]),
            event_context=str(row.get("event_context", "")),
            symbol=symbol,
            npz_path=path,
            npz_present=False,
            start_utc=row["start_utc"],
            end_utc=row["end_utc"],
            parsed_symbols=parsed,
        )
        sym_used, _ = resolve_download_symbol(client, ev)
        print(f"  MBO CONVERT existing DBN {event_id} -> {path.name}")
        DatabentoConverter(str(_REPO / "data/npz")).convert_file(str(dbn), sym_used)
        return path.is_file()
    from workbench.src.data.catalog_backfill import download_events
    from workbench.src.data.event_catalog import EventSpec

    print(f"  MBO DOWNLOAD {event_id} ({symbol})", flush=True)
    ev = EventSpec(
        event_id=event_id,
        event_type=str(row.get("event_type", "")),
        release_date=str(row["release_date"]),
        event_context=str(row.get("event_context", "")),
        symbol=symbol,
        npz_path=path,
        npz_present=False,
        start_utc=row["start_utc"],
        end_utc=row["end_utc"],
        parsed_symbols=parsed,
    )
    done = download_events(_REPO, [ev], max_cost_usd=max_cost)
    return event_id in done and path.is_file()


def _resolve_symbol_for_download(symbol: str, parsed: tuple, row) -> str:
    from workbench.src.data.catalog_backfill import resolve_download_symbol
    from workbench.src.data.event_catalog import EventSpec
    from data_system.src.databento_client import DatabentoResearchClient

    client = DatabentoResearchClient()
    ev = EventSpec(
        event_id=str(row["event_id"]),
        event_type=str(row.get("event_type", "")),
        release_date=str(row["release_date"]),
        event_context=str(row.get("event_context", "")),
        symbol=symbol,
        npz_path=_REPO / "data" / "npz" / "placeholder.npz",
        npz_present=False,
        start_utc=row["start_utc"],
        end_utc=row["end_utc"],
        parsed_symbols=parsed,
    )
    sym_used, _ = resolve_download_symbol(client, ev)
    return sym_used


def ensure_macro_mbp10(event_id: str, symbol: str, parsed: tuple, row, *, max_cost: float | None) -> bool:
    dest = mbp10_path(event_id, symbol)
    if dest.is_file():
        return True
    from data_system.src.databento_client import DatabentoResearchClient

    client = DatabentoResearchClient()
    start = _as_dt(row["start_utc"])
    end = _as_dt(row["end_utc"])
    try:
        sym_used = _resolve_symbol_for_download(symbol, parsed, row)
    except Exception as exc:
        print(f"  MBP-10 SKIP {event_id}: symbology {exc}")
        return False
    try:
        cost = client.estimate_cost_mbp10([sym_used], start, end)
    except Exception as exc:
        print(f"  MBP-10 SKIP {event_id}: cost {exc}")
        return False
    if max_cost is not None and cost > max_cost:
        print(f"  MBP-10 SKIP {event_id}: cost ${cost:.2f} > cap")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  MBP-10 DOWNLOAD {event_id} {sym_used} (${cost:.4f})", flush=True)
    try:
        client.download_mbp10_window(
            event_id,
            [sym_used],
            start,
            end,
            output_path=str(dest),
            override_operating_cap=max_cost is not None,
        )
    except Exception as exc:
        if dest.is_file() and dest.stat().st_size > 0:
            return True
        print(f"  MBP-10 FAIL {event_id}: {exc}")
        return False
    return dest.is_file()


def download_all_macro(*, max_cost_per_event: float | None, mbo_only: bool, mbp_only: bool) -> dict:
    stats = {"mbo_ok": 0, "mbo_dl": 0, "mbp_ok": 0, "mbp_dl": 0, "mbo_fail": [], "mbp_fail": []}
    print(f"=== Macro events (events.csv) ===")
    for event_id, symbol, parsed, row in iter_macro_events():
        if not mbp_only:
            try:
                path, had = mbo_npz_path(event_id, symbol)
                if had:
                    stats["mbo_ok"] += 1
                elif ensure_macro_mbo(event_id, symbol, parsed, row, max_cost=max_cost_per_event):
                    stats["mbo_dl"] += 1
                    stats["mbo_ok"] += 1
                else:
                    stats["mbo_fail"].append(event_id)
            except Exception as exc:
                print(f"  MBO FAIL {event_id}: {exc}")
                stats["mbo_fail"].append(event_id)
        if not mbo_only:
            try:
                if mbp10_path(event_id, symbol).is_file():
                    stats["mbp_ok"] += 1
                elif ensure_macro_mbp10(
                    event_id, symbol, parsed, row, max_cost=max_cost_per_event
                ):
                    stats["mbp_dl"] += 1
                    stats["mbp_ok"] += 1
                else:
                    stats["mbp_fail"].append(event_id)
            except Exception as exc:
                print(f"  MBP-10 FAIL {event_id}: {exc}")
                stats["mbp_fail"].append(event_id)
    return stats


def iter_decadal_sessions():
    import yaml

    path = _REPO / "packages" / "equities_lane" / "config" / "decadal_runners.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = raw.get("defaults") or {}
    for sess in raw.get("sessions") or []:
        if sess.get("skip_pull"):
            continue
        yield sess, defaults


def auction_ndjson_path(symbol: str, session_date: str) -> Path:
    eid = f"{symbol}_{session_date.replace('-', '_')}"
    return _REPO / "data" / "equities" / "normalized" / f"{symbol}_{eid}_auction.ndjson"


def ensure_equities_session(sess: dict, defaults: dict) -> Tuple[bool, bool]:
    """Return (mbo_ok, auction_ok)."""
    from equities_lane.src.types import DecadalSession

    symbol = str(sess["symbol"])
    date = str(sess["date"])
    dataset = str(sess.get("dataset", "XNAS.ITCH"))
    mbo_ok = False
    auction_ok = False

    raw_root = _REPO / "data" / "equities" / "raw"
    session = DecadalSession(
        id=str(sess["id"]),
        symbol=symbol,
        date=date,
        dataset=dataset,
        schema=str(sess.get("schema", defaults.get("schema", "mbo"))),
        stype_in=str(sess.get("stype_in", defaults.get("stype_in", "raw_symbol"))),
        premarket_start=str(defaults.get("premarket_start", "04:00")),
        session_end=str(defaults.get("session_end", "16:00")),
    )
    raw_path = raw_root / session.raw_filename()
    if raw_path.is_file() and raw_path.stat().st_size > 0:
        mbo_ok = True
    else:
        try:
            from equities_lane.src.ingest.databento_equities import download_decadal_session

            print(f"  EQUITIES MBO {symbol} {date}")
            download_decadal_session(
                session,
                raw_root,
                override_operating_cap=True,
            )
            mbo_ok = raw_path.is_file()
        except Exception as exc:
            print(f"  EQUITIES MBO FAIL {symbol} {date}: {exc}")

    auc_dest = auction_ndjson_path(symbol, date)
    if auc_dest.is_file():
        auction_ok = True
    else:
        from equities_lane.src.ingest.normalize_auction_imbalance import write_normalized_auction_ndjson

        raw_candidates = [
            raw_root / f"{symbol}_{date}_imbalance.dbn.zst",
            raw_root / f"{symbol}_imbalance.dbn.zst",
        ]
        existing_raw = next((p for p in raw_candidates if p.is_file() and p.stat().st_size > 0), None)
        if existing_raw is not None:
            try:
                print(f"  EQUITIES AUCTION NORMALIZE {symbol} {date} from {existing_raw.name}")
                write_normalized_auction_ndjson(existing_raw, auc_dest)
                auction_ok = auc_dest.is_file()
            except Exception as exc:
                print(f"  EQUITIES AUCTION FAIL {symbol} {date}: normalize {exc}")
        if not auction_ok:
            try:
                from datetime import datetime, timezone

                from equities_lane.src.ingest.databento_auction_imbalance import pull_auction_imbalance

                day = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                start = day.replace(hour=13, minute=0)
                end = day.replace(hour=21, minute=0)
                print(f"  EQUITIES AUCTION PULL {symbol} {date}")
                raw = pull_auction_imbalance(
                    symbol,
                    start,
                    end,
                    dataset=dataset,
                    output_path=raw_root / f"{symbol}_{date}_imbalance.dbn.zst",
                )
                write_normalized_auction_ndjson(raw, auc_dest)
                auction_ok = auc_dest.is_file()
            except Exception as exc:
                print(f"  EQUITIES AUCTION FAIL {symbol} {date}: {exc}")

    return mbo_ok, auction_ok


def download_all_equities() -> dict:
    stats = {"sessions": 0, "mbo_ok": 0, "auction_ok": 0, "mbo_fail": [], "auction_fail": []}
    print("=== Equities decadal sessions (enrich) ===")
    for sess, defaults in iter_decadal_sessions():
        stats["sessions"] += 1
        sym = sess["symbol"]
        dt = sess["date"]
        mbo_ok, auc_ok = ensure_equities_session(sess, defaults)
        if mbo_ok:
            stats["mbo_ok"] += 1
        else:
            stats["mbo_fail"].append(f"{sym}:{dt}")
        if auc_ok:
            stats["auction_ok"] += 1
        else:
            stats["auction_fail"].append(f"{sym}:{dt}")
    return stats


def download_campaign_missing(models: List[str], symbol: str, max_cost: float | None) -> List[str]:
    from workbench.src.data.catalog_backfill import download_events, missing_for_campaign

    done_all: List[str] = []
    for model in models:
        missing = missing_for_campaign(_REPO, model, symbol)
        if not missing:
            print(f"=== Campaign {model}: 0 missing ===", flush=True)
            continue
        print(f"=== Campaign missing NPZ model={model} count={len(missing)} ===", flush=True)
        done = download_events(_REPO, missing, max_cost_usd=max_cost)
        done_all.extend(done)
    return done_all


def main() -> int:
    p = argparse.ArgumentParser(description="Download all imbalance research data")
    p.add_argument("--all", action="store_true", help="Macro events + equities decadal + campaign sweep")
    p.add_argument("--macro-only", action="store_true")
    p.add_argument("--equities-only", action="store_true")
    p.add_argument("--event-id", default=None, help="Single event (optional)")
    p.add_argument("--symbol", default="MES.v.0")
    p.add_argument("--with-mbp10", action="store_true")
    p.add_argument("--max-cost-usd", type=float, default=150.0, help="Per-event or total cap where enforced")
    p.add_argument(
        "--campaign-models",
        default="SPREAD_BLOWOUT_RECOMPRESSION,END_OF_DAY_FORCED_FLATTEN_FLOW,DEALER_HEDGING",
        help="Comma-separated workbench slugs for campaign NPZ sweep (python -m workbench list)",
    )
    args = p.parse_args()

    if not args.all and not args.macro_only and not args.equities_only and not args.event_id:
        args.all = True

    summary: dict = {}

    if args.event_id:
        from data_system.src.events_parser import load_and_parse_events

        evdf = load_and_parse_events(str(_REPO / "packages" / "data_system" / "config" / "events.csv"))
        row = evdf[evdf["event_id"] == args.event_id].iloc[0]
        parsed = tuple(str(s) for s in row["parsed_symbols"])
        sym = args.symbol or (parsed[0] if parsed else "MES.v.0")
        ensure_macro_mbo(args.event_id, sym, parsed, row, max_cost=args.max_cost_usd)
        if args.with_mbp10 or args.all:
            ensure_macro_mbp10(args.event_id, sym, parsed, row, max_cost=args.max_cost_usd)
        return 0

    if args.all or args.macro_only:
        models = [m.strip() for m in args.campaign_models.split(",") if m.strip()]
        summary["campaign_downloaded"] = download_campaign_missing(models, args.symbol, args.max_cost_usd)
        summary["macro"] = download_all_macro(
            max_cost_per_event=args.max_cost_usd,
            mbo_only=False,
            mbp_only=False,
        )

    if args.all or args.equities_only:
        summary["equities"] = download_all_equities()

    out = _REPO / "runtime" / "data_audits" / "imbalance_download_summary.json"
    import json

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
