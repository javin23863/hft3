"""WS-0.4a fixing-window MBO pull — owner-approved 2026-06-13 (~$95, post-May-2023).

Pulls 10-minute ES futures MBO windows (14:55-15:05 CT) for every option-expiry
day from 2023-05-01 to 2026-06-12 into the canonical lake options lane.
Each window is a separate budget-gated request (~$0.12) recorded in the spend
ledger. Idempotent: existing files are skipped, so the script can be re-run
after interruption. Dates already covered by lake NPZ (ES.v.0) are skipped.

Default action is a dry run. Paid pulls require --download.

Run download with:
  HFT3_MANIFEST_PATH=C:\\hft3-lake\\manifest.parquet
  PYTHONPATH=<worktree>;<worktree>\\packages
  python scripts\\pull_fixing_windows.py --date 2026-06-12 --download
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _p in (str(_REPO), str(_REPO / "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from options_data.src.backfill_planner import plan_fixing_windows

OUT_DIR = r"C:\hft3-lake\options\fixing_mbo"
LOG = os.path.join(OUT_DIR, "pull_log.txt")
# ES.v.0 futures MBO already in lake for these expiry dates (inventory 2026-06-13)
ALREADY_COVERED = {"2024-09-18", "2025-06-20"}
DatabentoResearchClient = None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pull or inspect ES options fixing-window Databento gaps."
    )
    parser.add_argument(
        "schema",
        nargs="?",
        default="trades",
        choices=("trades", "mbo"),
        help="Databento schema to use for missing windows (default: trades).",
    )
    parser.add_argument(
        "--date",
        dest="single_date",
        help="Limit to one fixing-window date (YYYY-MM-DD).",
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned/skipped windows only; no client, log, download, or manifest writes.",
    )
    actions.add_argument(
        "--estimate-cost",
        action="store_true",
        help="Estimate Databento cost for missing windows only; no downloads or manifest writes.",
    )
    actions.add_argument(
        "--download",
        action="store_true",
        help="Explicitly download missing windows.",
    )
    parser.add_argument(
        "--override-operating-cap",
        action="store_true",
        default=False,
        help="Pass through to DatabentoResearchClient.download_event_window.",
    )
    return parser.parse_args(argv)


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _destinations(window_date: str, schema: str) -> tuple[str, str]:
    mbo_dest = os.path.join(OUT_DIR, f"ES_fixing_{window_date}.dbn.zst")
    dest = (
        mbo_dest
        if schema == "mbo"
        else os.path.join(OUT_DIR, f"ES_fixing_trades_{window_date}.dbn.zst")
    )
    return dest, mbo_dest


def _load_windows(single_date: str | None = None) -> tuple[list[dict], list[tuple[dict, str]]]:
    windows = plan_fixing_windows(date(2023, 5, 1), date(2026, 6, 12))
    if single_date is not None:
        try:
            date.fromisoformat(single_date)
        except ValueError as exc:
            raise SystemExit(f"--date must be YYYY-MM-DD, got {single_date!r}") from exc
        windows = [w for w in windows if w["date"] == single_date]
        if not windows:
            raise SystemExit(f"no fixing window planned for {single_date}")

    skipped: list[tuple[dict, str]] = []
    actionable: list[dict] = []
    for w in windows:
        if w["date"] in ALREADY_COVERED:
            skipped.append((w, "already covered by lake NPZ"))
        else:
            actionable.append(w)
    return actionable, skipped


def _partition_missing(windows: list[dict], schema: str) -> tuple[list[dict], list[tuple[dict, str]]]:
    missing: list[dict] = []
    skipped: list[tuple[dict, str]] = []
    for w in windows:
        d = w["date"]
        dest, mbo_dest = _destinations(d, schema)
        if os.path.exists(dest):
            skipped.append((w, f"exists {dest}"))
        elif os.path.exists(mbo_dest):
            skipped.append((w, f"exists {mbo_dest}"))
        else:
            missing.append(w)
    return missing, skipped


def _print_plan(missing: list[dict], skipped: list[tuple[dict, str]], schema: str) -> None:
    print(
        f"PLAN schema={schema} missing={len(missing)} skipped={len(skipped)}",
        flush=True,
    )
    for w, reason in skipped:
        print(f"SKIP {w['date']} {reason}", flush=True)
    for w in missing:
        dest, _ = _destinations(w["date"], schema)
        print(f"PLAN {w['date']} {w['start_utc']} -> {w['end_utc']} {dest}", flush=True)


def _as_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _make_client():
    global DatabentoResearchClient
    if DatabentoResearchClient is None:
        from data_system.src.databento_client import DatabentoResearchClient as _Client

        DatabentoResearchClient = _Client
    return DatabentoResearchClient()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    # Schema choice (2026-06-13 lesson): "trades" is the default. The original
    # MBO run was killed at 275/782 — every intraday-start streaming MBO request
    # is billed with a synthetic full-book snapshot (~0.45 GB uncompressed) that
    # metadata.get_cost does NOT include (~7x cost blowout). The fixing study
    # consumes trades only; "trades" has no snapshot and bills as estimated
    # (~$0.08/window measured).
    schema = args.schema
    action = (
        "estimate-cost"
        if args.estimate_cost
        else "download"
        if args.download
        else "dry-run"
    )
    windows, skipped_covered = _load_windows(args.single_date)
    windows, skipped_existing = _partition_missing(windows, schema)
    skipped = skipped_covered + skipped_existing

    if action == "dry-run":
        _print_plan(windows, skipped, schema)
        return 0

    if action == "estimate-cost":
        if not windows:
            print(
                f"ESTIMATE_TOTAL windows=0 skipped={len(skipped)} cost=$0.0000",
                flush=True,
            )
            return 0
        c = _make_client()
        total = 0.0
        for w in windows:
            cost = c.estimate_cost(
                symbols=["ES.v.0"],
                start_utc=_as_datetime(w["start_utc"]),
                end_utc=_as_datetime(w["end_utc"]),
                schema=schema,
                stype_in="continuous",
            )
            total += cost
            print(f"ESTIMATE {w['date']} ${cost:.4f}", flush=True)
        print(
            f"ESTIMATE_TOTAL windows={len(windows)} skipped={len(skipped)} cost=${total:.4f}",
            flush=True,
        )
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    c = _make_client()
    log(f"START {len(windows)} windows schema={schema}, ledger=${c.budget._calculate_total_used():.2f}")

    done = failed = 0
    skipped_count = len(skipped)
    for i, w in enumerate(windows):
        d = w["date"]
        # An existing MBO window already contains the trades (adapter filters
        # action==T), so either file satisfies the date.
        dest, _ = _destinations(d, schema)
        start = _as_datetime(w["start_utc"])
        end = _as_datetime(w["end_utc"])
        for attempt in (1, 2, 3):
            try:
                c.download_event_window(
                    event_id=f"OPT_FIXWIN_ES_{d}",
                    symbols=["ES.v.0"],
                    start_utc=start,
                    end_utc=end,
                    schema=schema,
                    stype_in="continuous",
                    output_path=dest,
                    override_operating_cap=args.override_operating_cap,
                )
                done += 1
                break
            except Exception as e:  # noqa: BLE001 — log and retry; vendor 5xx happen
                log(f"RETRY {d} attempt {attempt}: {type(e).__name__}: {e}")
                time.sleep(10 * attempt)
        else:
            failed += 1
            log(f"FAILED {d} after 3 attempts")
        if (i + 1) % 25 == 0:
            log(f"progress {i + 1}/{len(windows)} done={done} failed={failed} skipped={skipped_count}")

    total = c.budget._calculate_total_used()
    log(f"END done={done} failed={failed} skipped={skipped_count} ledger=${total:.2f}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
