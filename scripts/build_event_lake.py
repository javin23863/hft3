"""Build the event-window MBO lake.

For every (event, symbol) pair in events.csv × target symbol list, this
script either verifies that a clean NPZ already exists or downloads the
Databento MBO window, converts it to NPZ, and verifies the result.

Modes
-----
dry-run (default / no --confirm-purchase)
    Call estimate_cost for every missing pair, print a cost table, print
    grand total and remaining budget.  No data is downloaded.  A Databento
    API key is required for cost estimation; if the key is absent the table
    will note "no key – estimate unavailable" but will not abort.

--confirm-purchase
    Perform the actual download + convert + verify cycle.  Requires
    DATABENTO_API_KEY in the environment.

--manifest-only
    Rescan every NPZ that is already on disk and rewrite manifest.json.
    No network calls; no API key required.

Usage examples
--------------
    # see what is missing and what it would cost
    python scripts/build_event_lake.py --dry-run

    # filter to CPI events, two symbols only
    python scripts/build_event_lake.py --dry-run --event-type CPI --symbols MES.v.0,ES.v.0

    # actually download (requires DATABENTO_API_KEY)
    python scripts/build_event_lake.py --confirm-purchase

    # rebuild manifest from whatever NPZ files are already on disk
    python scripts/build_event_lake.py --manifest-only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Repo path bootstrap (mirrors how other scripts in this directory work)
# ---------------------------------------------------------------------------
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths  # noqa: E402

setup_repo_paths()

import numpy as np  # noqa: E402

from data_system.src.events_parser import load_and_parse_events  # noqa: E402
from data_system.src.lake_manifest import MANIFEST_REL_PATH  # noqa: E402
from data_system.src.npz_resolver import npz_path_for  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_EVENTS_CSV = _REPO / "packages" / "data_system" / "config" / "events.csv"
DEFAULT_SYMBOLS = ("MES.v.0", "ES.v.0", "NQ.v.0", "MNQ.v.0", "ZN.v.0")
DATASET = "GLBX.MDP3"
SCHEMA = "mbo"
STYPE_IN = "continuous"


# ---------------------------------------------------------------------------
# NPZ helpers
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_npz(path: Path) -> int:
    """Return event_count if the NPZ is valid (has 'data' key with len>0), else raise."""
    arr = np.load(str(path), allow_pickle=False)
    if "data" not in arr:
        raise ValueError(f"NPZ missing 'data' key: {path}")
    n = len(arr["data"])
    if n == 0:
        raise ValueError(f"NPZ 'data' array is empty: {path}")
    return n


def _npz_ok(path: Path) -> int | None:
    """Return event_count if the file exists and loads cleanly, otherwise None."""
    if not path.is_file():
        return None
    try:
        return _verify_npz(path)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Manifest construction
# ---------------------------------------------------------------------------


def _manifest_record(
    repo_root: Path,
    event_id: str,
    symbol: str,
    npz_path: Path,
    event_count: int,
) -> dict[str, Any]:
    try:
        path_str = npz_path.relative_to(repo_root).as_posix()
    except ValueError:
        # Lake root relocated outside the repo via HFT3_NPZ_ROOT.
        path_str = str(npz_path)
    return {
        "event_id": event_id,
        "symbol": symbol,
        "npz_path": path_str,
        "event_count": event_count,
        "sha256": _sha256(npz_path),
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }


def _write_manifest(repo_root: Path, records: list[dict[str, Any]]) -> Path:
    from data_system.src.lake_manifest import manifest_path as _manifest_path

    manifest_path = _manifest_path(repo_root)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(records, indent=2, sort_keys=True), encoding="utf-8"
    )
    return manifest_path


def scan_existing_npz(
    repo_root: Path,
    events_df: Any,  # pandas DataFrame from load_and_parse_events
    target_symbols: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Scan all (event, symbol) paths and return manifest records for valid NPZs."""
    records: list[dict[str, Any]] = []
    for _, row in events_df.iterrows():
        event_id = row["event_id"]
        event_symbols = set(row["parsed_symbols"])
        for sym in target_symbols:
            if sym not in event_symbols:
                continue
            path = npz_path_for(repo_root, event_id, sym)
            count = _npz_ok(path)
            if count is not None:
                records.append(_manifest_record(repo_root, event_id, sym, path, count))
    return records


# ---------------------------------------------------------------------------
# Download + convert path
# ---------------------------------------------------------------------------


def _require_api_key() -> str:
    key = os.getenv("DATABENTO_API_KEY", "")
    if not key:
        print(
            "error: DATABENTO_API_KEY environment variable is not set.\n"
            "Set it before running with --confirm-purchase or to enable cost estimation.",
            file=sys.stderr,
        )
        sys.exit(1)
    return key


def _download_and_convert(
    repo_root: Path,
    event_id: str,
    symbol: str,
    start_utc: datetime,
    end_utc: datetime,
    override_hard_limit: bool,
    override_operating_cap: bool,
) -> Path:
    """Download DBN.ZST, convert to NPZ at the canonical path, return the path."""
    from data_system.src.databento_client import DatabentoResearchClient  # noqa: PLC0415
    from backtest_pipeline.src.converter import DatabentoConverter  # noqa: PLC0415

    client = DatabentoResearchClient()

    npz_dir = repo_root / "data" / "npz"
    npz_dir.mkdir(parents=True, exist_ok=True)
    target_npz = npz_path_for(repo_root, event_id, symbol)

    # Download to a temp file so we can clean it up regardless of outcome.
    with tempfile.NamedTemporaryFile(
        suffix=f"_{event_id}_{symbol}.dbn.zst",
        dir=repo_root / "data",
        delete=False,
    ) as tmp:
        dbn_path = Path(tmp.name)

    try:
        client.download_event_window(
            event_id=event_id,
            symbols=[symbol],
            start_utc=start_utc,
            end_utc=end_utc,
            dataset=DATASET,
            schema=SCHEMA,
            stype_in=STYPE_IN,
            requested_symbol=symbol,
            output_path=str(dbn_path),
            override_hard_limit=override_hard_limit,
            override_operating_cap=override_operating_cap,
        )

        # Convert using hftbacktest databento convert; DatabentoConverter puts the
        # file under output_dir with the name "{symbol}_{dbn_basename}.npz".
        # We want the canonical name from npz_path_for, so we rename afterward.
        converter = DatabentoConverter(output_dir=str(npz_dir))
        converted = Path(converter.convert_file(str(dbn_path), symbol))

        if converted != target_npz:
            converted.rename(target_npz)

    finally:
        if dbn_path.is_file():
            dbn_path.unlink(missing_ok=True)

    return target_npz


# ---------------------------------------------------------------------------
# Cost estimation helpers (no download)
# ---------------------------------------------------------------------------


def _try_estimate_cost(
    symbol: str,
    start_utc: datetime,
    end_utc: datetime,
) -> float | None:
    """Return float cost or None if key absent / API error (non-fatal in dry-run)."""
    if not os.getenv("DATABENTO_API_KEY"):
        return None
    try:
        from data_system.src.databento_client import DatabentoResearchClient  # noqa: PLC0415

        client = DatabentoResearchClient()
        return client.estimate_cost(
            symbols=[symbol],
            start_utc=start_utc,
            end_utc=end_utc,
            dataset=DATASET,
            schema=SCHEMA,
            stype_in=STYPE_IN,
        )
    except Exception as exc:  # noqa: BLE001
        return None


def _remaining_budget() -> float | None:
    """Return remaining Databento budget or None if manifest.parquet absent."""
    try:
        from data_system.src.budget_manager import BudgetManager  # noqa: PLC0415

        bm = BudgetManager()
        return bm.get_remaining_credit()
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------


def _load_events(events_csv: Path, event_type_filter: str | None) -> Any:
    """Load events.csv and optionally filter by event_type."""
    import pandas as pd  # noqa: PLC0415

    df = load_and_parse_events(str(events_csv))
    if event_type_filter:
        df = df[df["event_type"] == event_type_filter].reset_index(drop=True)
        if df.empty:
            print(
                f"warning: no events matched --event-type={event_type_filter!r}",
                file=sys.stderr,
            )
    return df


def run_manifest_only(
    repo_root: Path,
    events_csv: Path,
    target_symbols: tuple[str, ...],
    event_type_filter: str | None,
) -> int:
    """Rescan existing NPZs, rewrite manifest.json.  No network calls."""
    df = _load_events(events_csv, event_type_filter)
    records = scan_existing_npz(repo_root, df, target_symbols)
    manifest_path = _write_manifest(repo_root, records)
    print(
        f"manifest-only: found {len(records)} valid NPZ(s). "
        f"Manifest written to {manifest_path}."
    )
    return 0


def run_dry_run(
    repo_root: Path,
    events_csv: Path,
    target_symbols: tuple[str, ...],
    event_type_filter: str | None,
) -> int:
    """Print cost estimates for missing pairs; no downloads."""
    df = _load_events(events_csv, event_type_filter)
    has_key = bool(os.getenv("DATABENTO_API_KEY"))

    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        event_id = row["event_id"]
        event_symbols = set(row["parsed_symbols"])
        start_utc: datetime = row["start_utc"]
        end_utc: datetime = row["end_utc"]
        for sym in target_symbols:
            if sym not in event_symbols:
                continue
            path = npz_path_for(repo_root, event_id, sym)
            count = _npz_ok(path)
            if count is not None:
                rows.append(
                    {
                        "event_id": event_id,
                        "symbol": sym,
                        "window": f"{start_utc.isoformat()} / {end_utc.isoformat()}",
                        "status": "exists",
                        "est_cost_usd": None,
                    }
                )
            else:
                est = _try_estimate_cost(sym, start_utc, end_utc)
                rows.append(
                    {
                        "event_id": event_id,
                        "symbol": sym,
                        "window": f"{start_utc.isoformat()} / {end_utc.isoformat()}",
                        "status": "missing",
                        "est_cost_usd": est,
                    }
                )

    # Print table
    col_event = max((len(r["event_id"]) for r in rows), default=8)
    col_sym = max((len(r["symbol"]) for r in rows), default=6)
    col_win = 50
    header = (
        f"{'event_id':<{col_event}}  {'symbol':<{col_sym}}  "
        f"{'window':<{col_win}}  {'status':<8}  est_cost_usd"
    )
    print(header)
    print("-" * len(header))

    grand_total = 0.0
    n_missing = 0
    n_no_estimate = 0
    for r in rows:
        cost_str = "n/a"
        if r["est_cost_usd"] is not None:
            grand_total += r["est_cost_usd"]
            cost_str = f"${r['est_cost_usd']:.4f}"
        elif r["status"] == "missing":
            n_no_estimate += 1
            cost_str = "(no key)" if not has_key else "(error)"
        if r["status"] == "missing":
            n_missing += 1
        print(
            f"{r['event_id']:<{col_event}}  {r['symbol']:<{col_sym}}  "
            f"{r['window']:<{col_win}}  {r['status']:<8}  {cost_str}"
        )

    print()
    print(f"Missing pairs:       {n_missing}")
    if n_no_estimate:
        note = "set DATABENTO_API_KEY to get estimates" if not has_key else "estimate API error"
        print(f"Without estimate:    {n_no_estimate}  ({note})")
    print(f"Grand total est.:    ${grand_total:.4f}")

    remaining = _remaining_budget()
    if remaining is not None:
        print(f"Remaining budget:    ${remaining:.2f}")

    if not has_key and n_missing > 0:
        print(
            "\nNote: DATABENTO_API_KEY is not set. "
            "Cost estimates require the key but no data will be downloaded."
        )

    return 0


def run_confirm_purchase(
    repo_root: Path,
    events_csv: Path,
    target_symbols: tuple[str, ...],
    event_type_filter: str | None,
    override_hard_limit: bool,
    override_operating_cap: bool,
) -> int:
    """Download, convert, and verify missing pairs; update manifest."""
    _require_api_key()
    df = _load_events(events_csv, event_type_filter)

    n_ok = 0
    n_downloaded = 0
    n_failed = 0
    all_records: list[dict[str, Any]] = []

    for _, row in df.iterrows():
        event_id = row["event_id"]
        event_symbols = set(row["parsed_symbols"])
        start_utc: datetime = row["start_utc"]
        end_utc: datetime = row["end_utc"]

        for sym in target_symbols:
            if sym not in event_symbols:
                continue
            path = npz_path_for(repo_root, event_id, sym)
            count = _npz_ok(path)
            if count is not None:
                print(f"skip (exists): {event_id} / {sym}  ({count} events)")
                all_records.append(
                    _manifest_record(repo_root, event_id, sym, path, count)
                )
                n_ok += 1
                continue

            print(f"downloading:   {event_id} / {sym}  {start_utc.isoformat()} → {end_utc.isoformat()}")
            try:
                npz_path = _download_and_convert(
                    repo_root=repo_root,
                    event_id=event_id,
                    symbol=sym,
                    start_utc=start_utc,
                    end_utc=end_utc,
                    override_hard_limit=override_hard_limit,
                    override_operating_cap=override_operating_cap,
                )
                count = _verify_npz(npz_path)
                print(f"  verified: {count} events -> {npz_path.name}")
                all_records.append(
                    _manifest_record(repo_root, event_id, sym, npz_path, count)
                )
                n_downloaded += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
                n_failed += 1

    manifest_path = _write_manifest(repo_root, all_records)
    print(
        f"\nDone. existing={n_ok}  downloaded={n_downloaded}  failed={n_failed}  "
        f"total_records={len(all_records)}\n"
        f"Manifest: {manifest_path}"
    )
    return 0 if n_failed == 0 else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scripts.build_event_lake",
        description=(
            "Build the event-window MBO NPZ lake for a fixed universe of "
            "macro events × symbols.  Dry-run by default; pass "
            "--confirm-purchase to actually download."
        ),
    )
    p.add_argument(
        "--events-csv",
        type=Path,
        default=DEFAULT_EVENTS_CSV,
        help="Path to events.csv (default: packages/data_system/config/events.csv)",
    )
    p.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help="Comma-separated target symbols (default: MES.v.0,ES.v.0,NQ.v.0,MNQ.v.0,ZN.v.0)",
    )
    p.add_argument(
        "--event-type",
        default=None,
        metavar="TYPE",
        help="Filter to a single event_type (CPI / NFP / PROP_FLATTEN_TOPSTEP)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Estimate costs only; no download (default mode if --confirm-purchase absent)",
    )
    p.add_argument(
        "--confirm-purchase",
        action="store_true",
        help="Actually download missing windows (requires DATABENTO_API_KEY)",
    )
    p.add_argument(
        "--manifest-only",
        action="store_true",
        help="Rescan existing NPZ files and rewrite manifest.json; no network calls",
    )
    p.add_argument(
        "--override-hard-limit",
        action="store_true",
        help="Pass through to BudgetManager (use with caution)",
    )
    p.add_argument(
        "--override-operating-cap",
        action="store_true",
        help="Pass through to BudgetManager (use with caution)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    events_csv = Path(args.events_csv)
    if not events_csv.is_file():
        print(f"error: events CSV not found: {events_csv}", file=sys.stderr)
        return 2

    target_symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
    event_type_filter: str | None = args.event_type

    # --manifest-only is mutually exclusive with download flags
    if args.manifest_only:
        if args.confirm_purchase:
            print(
                "error: --manifest-only and --confirm-purchase are mutually exclusive.",
                file=sys.stderr,
            )
            return 2
        return run_manifest_only(_REPO, events_csv, target_symbols, event_type_filter)

    if args.confirm_purchase:
        return run_confirm_purchase(
            _REPO,
            events_csv,
            target_symbols,
            event_type_filter,
            override_hard_limit=args.override_hard_limit,
            override_operating_cap=args.override_operating_cap,
        )

    # Default: dry-run (also triggered by explicit --dry-run)
    return run_dry_run(_REPO, events_csv, target_symbols, event_type_filter)


if __name__ == "__main__":
    raise SystemExit(main())
