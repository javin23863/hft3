#!/usr/bin/env python3
"""Resumable bulk downloader and converter for PROP_REOPEN historical tape.

Downloads all PROP_REOPEN windows (2018-2025) from Databento GLBX.MDP3 MBO,
converts each to per-symbol NPZ files compatible with the HftBacktest lake.

Output filename: {symbol}_{event_id}_mbo.npz  (matches NPZ_PATTERN in build_feature_store.py)

The script is safe to re-run: any window where all requested symbols already
have an NPZ in the lake is skipped immediately (resume-on-interruption).

Usage examples
--------------
Dry-run (price only, no downloads):
    python scripts/download_prop_reopen_tape.py --dry-run --max-windows 10

Smoke test (2 windows, keep raw DBN):
    python scripts/download_prop_reopen_tape.py --max-windows 2 --keep-dbn

Full run (background):
    python scripts/download_prop_reopen_tape.py
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap repo paths so package imports work when run as a script.
# ---------------------------------------------------------------------------
_REPO = Path(__file__).resolve().parents[1]
for _p in [str(_REPO), str(_REPO / "packages")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from data_system.src.keystore import load_keys  # noqa: E402
from data_system.src.events_parser import load_and_parse_events  # noqa: E402
from backtest_pipeline.src.converter import DatabentoConverter  # noqa: E402
from data_system.src.databento_client import DatabentoResearchClient  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EMBARGO_DATE = "2026-01-01"  # Skip any event with release_date >= this (research guard)

ALL_SYMBOLS = [
    "MES.v.0",
    "MNQ.v.0",
    "ES.v.0",
    "NQ.v.0",
    "RTY.v.0",
    "ZN.v.0",
    "ZB.v.0",
    "MGC.v.0",
    "GC.v.0",
    "MCL.v.0",
    "CL.v.0",
]

EVENTS_CSV = _REPO / "packages" / "data_system" / "config" / "events.csv"

DEFAULT_RAW_DIR = str(_REPO / "data" / "raw" / "prop_reopen")

# HFT3_NPZ_ROOT env override mirrors npz_resolver.npz_root() logic
DEFAULT_NPZ_ROOT = os.environ.get("HFT3_NPZ_ROOT", str(_REPO / "data" / "npz"))

RECEIPT_DIR = _REPO / "runtime" / "databento"
RECEIPT_PATH = RECEIPT_DIR / "prop_reopen_download_receipt.json"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("prop_reopen_dl")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_missing_symbols(npz_root: Path, event_id: str, symbols: list[str]) -> list[str]:
    """Return subset of symbols that do NOT yet have an NPZ in the lake."""
    missing = []
    for sym in symbols:
        expected = npz_root / f"{sym}_{event_id}_mbo.npz"
        if not expected.is_file():
            missing.append(sym)
    return missing


def all_present(npz_root: Path, event_id: str, symbols: list[str]) -> bool:
    """True when every requested symbol already has a lake NPZ."""
    return len(resolve_missing_symbols(npz_root, event_id, symbols)) == 0


def write_receipt(path: Path, summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=str)
    log.info("Receipt written: %s", path)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resumable bulk PROP_REOPEN tape downloader + converter.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--symbols",
        default=",".join(ALL_SYMBOLS),
        help=(
            "Comma-separated list of symbols to download "
            f"(default: all {len(ALL_SYMBOLS)} futures)."
        ),
    )
    parser.add_argument(
        "--max-windows",
        type=int,
        default=0,
        metavar="N",
        help="Stop after N windows (0 = all). Useful for smoke tests.",
    )
    parser.add_argument(
        "--raw-dir",
        default=DEFAULT_RAW_DIR,
        help="Directory for downloaded .dbn.zst files (default: data/raw/prop_reopen).",
    )
    parser.add_argument(
        "--npz-root",
        default=DEFAULT_NPZ_ROOT,
        help="Lake NPZ root directory (default: HFT3_NPZ_ROOT or C:\\...\\data\\npz).",
    )
    parser.add_argument(
        "--cost-cap",
        type=float,
        default=100.0,
        metavar="USD",
        help=(
            "Abort before any download that would push cumulative spend over this "
            "amount in USD (default: 100.0)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Price every window but do not download or convert anything.",
    )
    parser.add_argument(
        "--keep-dbn",
        action="store_true",
        help="Keep .dbn.zst files after conversion (default: delete to save disk).",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()

    # Credentials — loads from Desktop/keys.env; never prints the key value.
    load_keys()
    log.info("Credentials loaded.")

    # Symbol list
    symbols: list[str] = [s.strip() for s in args.symbols.split(",") if s.strip()]
    log.info("Target symbols (%d): %s", len(symbols), symbols)

    # Directories
    raw_dir = Path(args.raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    npz_root = Path(args.npz_root)
    npz_root.mkdir(parents=True, exist_ok=True)
    log.info("Raw DBN dir : %s", raw_dir)
    log.info("NPZ lake    : %s", npz_root)

    # Load and filter events
    log.info("Loading PROP_REOPEN events from %s", EVENTS_CSV)
    all_events = load_and_parse_events(str(EVENTS_CSV))
    events = all_events[all_events["event_type"] == "PROP_REOPEN"].copy()
    events = events.sort_values("release_date").reset_index(drop=True)

    # Embargo guard: skip events on or after 2026-01-01
    pre_embargo = events[events["release_date"] < EMBARGO_DATE]
    embargoed_count = len(events) - len(pre_embargo)
    if embargoed_count:
        log.warning("Skipping %d embargoed events (release_date >= %s)", embargoed_count, EMBARGO_DATE)
    events = pre_embargo.reset_index(drop=True)

    total_windows = len(events)
    if args.max_windows and args.max_windows > 0:
        events = events.iloc[: args.max_windows]
        log.info(
            "Max-windows cap: processing %d of %d total PROP_REOPEN windows.",
            len(events),
            total_windows,
        )
    else:
        log.info("Processing all %d PROP_REOPEN windows.", total_windows)

    # Instantiate client and converter
    client = DatabentoResearchClient()
    converter = DatabentoConverter(output_dir=str(npz_root))

    # Counters for summary
    windows_processed = 0
    windows_empty = 0
    windows_skipped_existing = 0
    symbols_npz_written = 0
    running_cost = 0.0

    # Per-symbol file count for receipt
    per_symbol_counts: dict[str, int] = {sym: 0 for sym in symbols}

    n_windows = len(events)

    for idx, row in events.iterrows():
        win_num = int(idx) + 1  # 1-based display index within the (possibly capped) slice
        event_id: str = str(row["event_id"])
        start_utc: datetime = row["start_utc"].to_pydatetime()
        end_utc: datetime = row["end_utc"].to_pydatetime()

        # Step 1 — Resume: skip if every symbol already has an NPZ.
        if all_present(npz_root, event_id, symbols):
            log.debug("[%d/%d] %s — all symbols present, skipping.", win_num, n_windows, event_id)
            windows_skipped_existing += 1
            continue

        missing_symbols = resolve_missing_symbols(npz_root, event_id, symbols)

        # Step 2 — Price the window for missing symbols.
        try:
            window_cost = client.estimate_cost(
                symbols=missing_symbols,
                start_utc=start_utc,
                end_utc=end_utc,
            )
        except Exception as exc:
            log.warning("[%d/%d] %s — cost estimate failed (%s); skipping.", win_num, n_windows, event_id, exc)
            windows_empty += 1
            continue

        if window_cost == 0.0:
            # No data for this window (holiday, weekend, early close, etc.)
            log.info(
                "[%d/%d] %s — $0.00 (no data / holiday), skipping.",
                win_num,
                n_windows,
                event_id,
            )
            windows_empty += 1
            continue

        # Step 3 — Cumulative cost guard.
        if running_cost + window_cost > args.cost_cap:
            log.warning(
                "Cost cap of $%.4f would be exceeded (running=$%.4f + window=$%.4f). "
                "Stopping cleanly. Re-run to resume from window %d/%d (%s).",
                args.cost_cap,
                running_cost,
                window_cost,
                win_num,
                n_windows,
                event_id,
            )
            break

        if args.dry_run:
            log.info(
                "[%d/%d] %s — DRY RUN $%.5f (cumulative would be $%.5f)",
                win_num,
                n_windows,
                event_id,
                window_cost,
                running_cost + window_cost,
            )
            running_cost += window_cost
            windows_processed += 1
            continue

        # Step 4 — Download: single get_range call for all missing symbols.
        dbn_path = raw_dir / f"{event_id}_mbo.dbn.zst"
        try:
            actual_dest = client.download_event_window(
                event_id=event_id,
                symbols=missing_symbols,
                start_utc=start_utc,
                end_utc=end_utc,
                output_path=str(dbn_path),
                override_operating_cap=True,   # we enforce our own --cost-cap
                override_hard_limit=False,      # $10 hard limit still guards against anomalies
            )
        except Exception as exc:
            log.error(
                "[%d/%d] %s — download failed: %s. Skipping window.",
                win_num,
                n_windows,
                event_id,
                exc,
            )
            windows_empty += 1
            continue

        # Ensure we hold a reference to the actual file path the client wrote.
        # download_event_window returns dest; cross-check against our intended path.
        dbn_path = Path(actual_dest) if actual_dest else dbn_path

        # Step 5 — Convert: one NPZ per symbol, filtering the multi-symbol DBN.
        converted_this_window: list[str] = []
        for sym in missing_symbols:
            try:
                out_path = converter.convert_file(str(dbn_path), sym)
                converted_this_window.append(sym)
                per_symbol_counts[sym] = per_symbol_counts.get(sym, 0) + 1
                symbols_npz_written += 1
                log.debug("  Wrote %s", out_path)
            except Exception as exc:
                # Thin/no records for this symbol in this window — not a fatal error.
                log.warning(
                    "  [%s] convert failed for %s — %s (no records, skipping symbol).",
                    event_id,
                    sym,
                    exc,
                )

        running_cost += window_cost
        windows_processed += 1

        log.info(
            "[%d/%d] %s  cost=$%.5f  converted=%s  running_total=$%.4f",
            win_num,
            n_windows,
            event_id,
            window_cost,
            converted_this_window if converted_this_window else "[]",
            running_cost,
        )

        # Step 6 — Optionally delete the raw DBN to save disk.
        if not args.keep_dbn and dbn_path.is_file():
            try:
                dbn_path.unlink()
                log.debug("  Deleted raw DBN: %s", dbn_path)
            except OSError as exc:
                log.warning("  Could not delete %s: %s", dbn_path, exc)

    # ---------------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------------
    summary = {
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "dry_run": args.dry_run,
        "windows_processed": windows_processed,
        "windows_empty": windows_empty,
        "windows_skipped_existing": windows_skipped_existing,
        "symbols_npz_written": symbols_npz_written,
        "total_cost_usd": round(running_cost, 6),
        "npz_root": str(npz_root),
        "per_symbol_file_counts": per_symbol_counts,
    }

    print("\n" + "=" * 70)
    print("PROP_REOPEN DOWNLOAD SUMMARY")
    print("=" * 70)
    print(f"  Windows processed   : {windows_processed}")
    print(f"  Windows empty (no data / holiday): {windows_empty}")
    print(f"  Windows skipped (already in lake): {windows_skipped_existing}")
    print(f"  NPZ files written   : {symbols_npz_written}")
    print(f"  Total cost (USD)    : ${running_cost:.6f}")
    print(f"  NPZ lake            : {npz_root}")
    if not args.dry_run:
        write_receipt(RECEIPT_PATH, summary)
    print("=" * 70)


if __name__ == "__main__":
    main()
