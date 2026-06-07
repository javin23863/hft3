"""HOT-universe MBO backfill over the 720-window pilot catalog."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, List, Optional

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
for sub in ("packages", "apps"):
    p = str(_REPO / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

try:
    from dotenv import load_dotenv

    load_dotenv(_REPO / ".env")
    import os

    if not os.getenv("DATABENTO_API_KEY"):
        env_path = _REPO / ".env"
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("DATABENTO_API_KEY="):
                    os.environ["DATABENTO_API_KEY"] = line.split("=", 1)[1].strip()
                    break
except ImportError:
    pass

logger = logging.getLogger(__name__)

CATALOG_PATH = _REPO / "packages" / "data_system" / "config" / "mbo_pilot_window_catalog.json"
PILOT_MANIFEST_PATH = _REPO / "packages" / "data_system" / "config" / "mbo_pilot_basket_20260605_manifest.json"

DEFAULT_BATCHES: tuple[tuple[str, ...], ...] = (
    ("M2K.v.0", "YM.v.0", "MYM.v.0", "ZT.v.0", "ZF.v.0"),
    ("UB.v.0", "SR3.v.0", "ZQ.v.0", "CL.v.0", "MCL.v.0"),
    ("NG.v.0", "GC.v.0", "MGC.v.0", "HG.v.0", "6E.v.0"),
    ("VX.v.0", "VX.v.1", "RB.v.0", "HO.v.0", "SI.v.0", "6J.v.0", "6B.v.0"),
    ("6A.v.0", "6C.v.0", "ZC.v.0", "ZS.v.0", "ZW.v.0"),
    ("KE.v.0", "ZL.v.0", "ZM.v.0"),
)


@dataclass(frozen=True)
class SlotTask:
    symbol: str
    event_id: str
    event_type: str
    release_date: str
    start_utc: Any
    end_utc: Any


def _as_datetime(value: Any):
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime()
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def load_window_catalog(path: Path = CATALOG_PATH) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("windows") or [])


def load_no_market_event_ids(path: Path = PILOT_MANIFEST_PATH) -> set[str]:
    if not path.is_file():
        return set()
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return set(manifest.get("no_market_windows") or [])


def missing_hot_symbols(repo: Path) -> list[str]:
    inv_path = repo / "runtime" / "data_audits" / "hfc3_mbo_cross_asset_inventory.json"
    if not inv_path.is_file():
        return []
    inv = json.loads(inv_path.read_text(encoding="utf-8"))
    return sorted(
        {
            str(row["research_symbol"])
            for row in inv.get("instruments") or []
            if row.get("mbo_status") == "MBO_MISSING" and str(row.get("research_symbol", "")).endswith(".v.0")
        }
    )


def iter_slot_tasks(
    repo: Path,
    symbols: Iterable[str],
    *,
    skip_no_market: bool = True,
    catalog: Optional[list[dict[str, Any]]] = None,
) -> list[SlotTask]:
    from data_system.src.npz_resolver import resolve_npz_for_event

    windows = catalog if catalog is not None else load_window_catalog()
    no_market = load_no_market_event_ids() if skip_no_market else set()
    tasks: list[SlotTask] = []
    for symbol in symbols:
        for win in windows:
            event_id = str(win["event_id"])
            if skip_no_market and event_id in no_market:
                continue
            _, present, _ = resolve_npz_for_event(repo, event_id, symbol, (symbol,))
            if present:
                continue
            tasks.append(
                SlotTask(
                    symbol=symbol,
                    event_id=event_id,
                    event_type=str(win.get("event_type", "")),
                    release_date=str(win.get("release_date", "")),
                    start_utc=win["start_utc"],
                    end_utc=win["end_utc"],
                )
            )
    return tasks


def _event_spec(task: SlotTask):
    from workbench.src.data.event_catalog import EventSpec

    return EventSpec(
        event_id=task.event_id,
        event_type=task.event_type,
        release_date=task.release_date,
        event_context="TIGHT",
        symbol=task.symbol,
        npz_path=Path(),
        npz_present=False,
        start_utc=task.start_utc,
        end_utc=task.end_utc,
        parsed_symbols=(task.symbol,),
    )


def estimate_tasks(tasks: list[SlotTask], *, sample_per_symbol: bool = False) -> dict[str, Any]:
    from workbench.src.data.catalog_backfill import resolve_download_symbol
    from data_system.src.databento_client import DatabentoResearchClient

    if not tasks:
        return {"pending_slots": 0, "estimated_cost_usd": 0.0, "unpriced": 0, "by_symbol": {}}

    try:
        client = DatabentoResearchClient()
    except ValueError as exc:
        return {
            "pending_slots": len(tasks),
            "estimated_cost_usd": 0.0,
            "unpriced": len(tasks),
            "error": str(exc),
            "by_symbol": {},
        }

    use_sample = sample_per_symbol or len(tasks) > 500
    total = 0.0
    unpriced = 0
    by_symbol: dict[str, float] = {}
    if use_sample:
        by_sym_tasks: dict[str, list[SlotTask]] = {}
        for task in tasks:
            by_sym_tasks.setdefault(task.symbol, []).append(task)
        for sym, sym_tasks in sorted(by_sym_tasks.items()):
            try:
                _, unit_cost = resolve_download_symbol(client, _event_spec(sym_tasks[0]))
                sym_total = unit_cost * len(sym_tasks)
                total += sym_total
                by_symbol[sym] = sym_total
            except Exception as exc:
                unpriced += len(sym_tasks)
                logger.warning("unpriced symbol %s: %s", sym, exc)
        method = "sample_per_symbol"
    else:
        method = "full"
        for task in tasks:
            try:
                _, cost = resolve_download_symbol(client, _event_spec(task))
                total += cost
                by_symbol[task.symbol] = by_symbol.get(task.symbol, 0.0) + cost
            except Exception as exc:
                unpriced += 1
                logger.warning("unpriced %s %s: %s", task.symbol, task.event_id, exc)
    return {
        "pending_slots": len(tasks),
        "estimated_cost_usd": round(total, 4),
        "unpriced": unpriced,
        "estimate_method": method,
        "by_symbol": {k: round(v, 4) for k, v in sorted(by_symbol.items())},
    }


def download_tasks(
    repo: Path,
    tasks: list[SlotTask],
    *,
    max_cost_usd: Optional[float],
    run_id: str,
    override_hard_limit: bool = True,
) -> dict[str, Any]:
    from backtest_pipeline.src.converter import DatabentoConverter
    from data_system.src.databento_client import DatabentoResearchClient
    from workbench.src.data.catalog_backfill import resolve_download_symbol

    if not tasks:
        return {"downloaded": [], "failed": [], "spent_usd": 0.0}

    est = estimate_tasks(tasks)
    if max_cost_usd is not None and est["estimated_cost_usd"] > max_cost_usd:
        raise RuntimeError(
            f"Estimated cost ${est['estimated_cost_usd']:.2f} exceeds max ${max_cost_usd:.2f}"
        )

    client = DatabentoResearchClient()
    raw_root = repo / "data" / "raw" / "databento_mbo" / run_id
    raw_root.mkdir(parents=True, exist_ok=True)
    converter = DatabentoConverter(str(repo / "data" / "npz"))

    downloaded: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    spent = 0.0

    for task in tasks:
        if max_cost_usd is not None and spent >= max_cost_usd:
            break
        start = _as_datetime(task.start_utc)
        end = _as_datetime(task.end_utc)
        try:
            sym_used, cost = resolve_download_symbol(client, _event_spec(task))
            if max_cost_usd is not None and spent + float(cost) > max_cost_usd:
                break
            dest = raw_root / f"{task.event_id}_{sym_used}_mbo.dbn.zst"
            dbn_path = client.download_event_window(
                event_id=task.event_id,
                symbols=[sym_used],
                start_utc=start,
                end_utc=end,
                requested_symbol=task.symbol,
                output_path=str(dest),
                override_hard_limit=override_hard_limit,
                override_operating_cap=True,
            )
            converter.convert_file(dbn_path, sym_used)
            spent += float(cost)
            downloaded.append(
                {
                    "symbol": task.symbol,
                    "symbol_used": sym_used,
                    "event_id": task.event_id,
                    "cost_usd": cost,
                    "dbn_path": dbn_path,
                }
            )
        except Exception as exc:
            failed.append(
                {
                    "symbol": task.symbol,
                    "event_id": task.event_id,
                    "error": str(exc),
                }
            )
            logger.error("download failed %s %s: %s", task.symbol, task.event_id, exc)

    return {
        "downloaded": downloaded,
        "failed": failed,
        "spent_usd": round(spent, 4),
        "estimated_cost_usd": est["estimated_cost_usd"],
    }


def write_report(repo: Path, report: dict[str, Any], run_id: str) -> Path:
    out_dir = repo / "runtime" / "data_downloads"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_id}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_REPO)
    parser.add_argument("--symbols", default="", help="Comma-separated research symbols")
    parser.add_argument("--from-inventory", action="store_true", help="All MBO_MISSING .v.0 symbols")
    parser.add_argument("--batch", type=int, default=0, help="1-6 predefined batch from plan")
    parser.add_argument("--estimate", action="store_true")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--skip-no-market", action="store_true", default=True)
    parser.add_argument("--include-no-market", action="store_true")
    parser.add_argument("--max-cost-usd", type=float, default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    repo = args.repo_root.resolve()
    skip_no_market = not args.include_no_market

    if args.batch:
        if args.batch < 1 or args.batch > len(DEFAULT_BATCHES):
            raise SystemExit(f"--batch must be 1..{len(DEFAULT_BATCHES)}")
        symbols = list(DEFAULT_BATCHES[args.batch - 1])
    elif args.from_inventory:
        symbols = missing_hot_symbols(repo)
    elif args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        raise SystemExit("Provide --symbols, --from-inventory, or --batch N")

    run_id = args.run_id or f"mbo_hot_universe_{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    tasks = iter_slot_tasks(repo, symbols, skip_no_market=skip_no_market)
    est = estimate_tasks(tasks)

    report: dict[str, Any] = {
        "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
        "skip_no_market": skip_no_market,
        "pending_slots": len(tasks),
        "estimate": est,
    }

    if args.estimate and not args.download:
        report["mode"] = "estimate"
        path = write_report(repo, report, run_id)
        report["report_path"] = str(path)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"pending_slots={len(tasks)}")
            print(f"estimated_cost_usd={est['estimated_cost_usd']:.4f}")
            print(f"unpriced={est.get('unpriced', 0)}")
            print(f"wrote {path}")
        return 0

    if args.download:
        result = download_tasks(
            repo,
            tasks,
            max_cost_usd=args.max_cost_usd,
            run_id=run_id,
        )
        report["mode"] = "download"
        report["result"] = result
        path = write_report(repo, report, run_id)
        report["report_path"] = str(path)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(
                f"downloaded={len(result['downloaded'])} failed={len(result['failed'])} "
                f"spent_usd={result['spent_usd']:.4f}"
            )
            print(f"wrote {path}")
        return 0 if not result["failed"] else 1

    raise SystemExit("Specify --estimate and/or --download")


if __name__ == "__main__":
    raise SystemExit(main())
