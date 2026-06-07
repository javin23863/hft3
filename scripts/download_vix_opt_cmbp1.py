#!/usr/bin/env python3
"""Download OPRA VIX.OPT parent cmbp-1 for CME priority macro event windows."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

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
except ImportError:
    pass

logger = logging.getLogger(__name__)

DATASET = "OPRA.PILLAR"
SCHEMA = "cmbp-1"
SYMBOL = "VIX.OPT"
STYPE_IN = "parent"
MANIFEST_PATH = _REPO / "data" / "mbo_release" / "vix_opt_manifest.parquet"
REPORT_PATH = _REPO / "runtime" / "data_downloads" / "vix_opt_cmbp1_download_report.json"
HIST = "https://hist.databento.com/v0"
# OPRA.PILLAR cmbp-1 availability (per metadata.get_dataset_range)
CMBP1_START = datetime(2023, 3, 28, tzinfo=timezone.utc)


def _as_datetime(value):
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime()
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def _http_cost(api_key: str, start, end) -> float:
    r = requests.get(
        f"{HIST}/metadata.get_cost",
        params={
            "dataset": DATASET,
            "schema": SCHEMA,
            "symbols": SYMBOL,
            "stype_in": STYPE_IN,
            "start": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%S"),
            "encoding": "json",
        },
        auth=(api_key, ""),
        timeout=120,
    )
    r.raise_for_status()
    return float(r.json())


def _http_download(api_key: str, start, end, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(
        f"{HIST}/timeseries.get_range",
        params={
            "dataset": DATASET,
            "schema": SCHEMA,
            "symbols": SYMBOL,
            "stype_in": STYPE_IN,
            "start": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%S"),
            "encoding": "dbn",
        },
        auth=(api_key, ""),
        timeout=300,
        stream=True,
    ) as r:
        r.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    fh.write(chunk)


def _slot_dir(event_id: str) -> Path:
    from mbo_release_lane.storage import release_slot_dir

    return release_slot_dir(_REPO, event_id, SYMBOL)


def _append_manifest(record: dict) -> None:
    from data_system.src.manifest_io import append_manifest_record

    append_manifest_record(MANIFEST_PATH, record)


def download_window(api_key: str, window, *, refresh: bool = False, skip_cost: bool = False, record_manifest: bool = True) -> dict:
    event_id = window.event_id
    start = _as_datetime(window.start_utc)
    end = _as_datetime(window.end_utc)
    if start < CMBP1_START:
        return {
            "event_id": event_id,
            "status": "skipped_pre_cmbp1",
            "reason": f"cmbp-1 available from {CMBP1_START.date()}",
        }
    from mbo_release_lane.sensor_adapter import write_minimal_vix_release_manifest
    from mbo_release_lane.storage import raw_dbn_path

    slot = _slot_dir(event_id)
    raw = raw_dbn_path(slot)
    meta = slot / "slot.json"

    if raw.is_file() and raw.stat().st_size > 0 and not refresh:
        from mbo_release_lane.storage import dbn_has_quote_records, validate_dbn_readable

        if validate_dbn_readable(raw) and dbn_has_quote_records(raw):
            return {"event_id": event_id, "status": "skipped_existing", "path": str(raw)}

    cost = 0.0 if skip_cost else _http_cost(api_key, start, end)
    _http_download(api_key, start, end, raw)
    size = raw.stat().st_size if raw.is_file() else 0
    record = {
        "event_id": event_id,
        "event_type": window.event_type,
        "release_date": window.release_date,
        "symbols": str([SYMBOL]),
        "requested_symbol": SYMBOL,
        "resolved_symbol": SYMBOL,
        "start_utc": start,
        "end_utc": end,
        "cost": cost,
        "duration_seconds": (end - start).total_seconds(),
        "cost_per_symbol_minute": cost / max((end - start).total_seconds() / 60.0, 1e-9),
        "output_path": str(raw),
        "dataset": DATASET,
        "schema": SCHEMA,
        "download_time": datetime.now(timezone.utc),
        "bytes": size,
    }
    if record_manifest:
        _append_manifest(record)
    meta.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    write_minimal_vix_release_manifest(
        _REPO,
        event_id,
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        event_count=1 if size > 0 else 0,
    )
    return {"event_id": event_id, "status": "downloaded", "cost": cost, "bytes": size}


def _slot_needs_work(event_id: str) -> bool:
    from mbo_release_lane.storage import dbn_has_quote_records, raw_dbn_path, validate_dbn_readable

    raw = raw_dbn_path(_slot_dir(event_id))
    if not raw.is_file() or raw.stat().st_size == 0:
        return True
    if not validate_dbn_readable(raw):
        return True
    return not dbn_has_quote_records(raw)


def _corrupt_vix_event_ids(windows) -> list[str]:
    """All post-cutoff windows with present but unreadable VIX raw DBN."""
    from data_system.src.event_data_resolver import resolve_vix_raw_for_event

    out: list[str] = []
    for w in windows:
        start = _as_datetime(w.start_utc)
        if start < CMBP1_START:
            continue
        raw_path, raw_ok = resolve_vix_raw_for_event(_REPO, w.event_id)
        if raw_path.is_file() and raw_path.stat().st_size > 0 and not raw_ok:
            out.append(w.event_id)
    return out


def main() -> int:
    from economic_event_universe.events_csv_builder import resolve_download_scope_windows
    from mbo_release_lane.constants import PRIORITY_DOWNLOAD_EVENT_TYPES
    from mbo_release_lane.download import filter_windows_by_event_type, resolve_download_exclusions

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument(
        "--refresh-corrupt",
        action="store_true",
        help="Re-download corrupt VIX DBN slots listed in priority_lane_coverage.json",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=32, help="Parallel download workers")
    parser.add_argument("--skip-cost", action="store_true", default=True, help="Skip get_cost (faster)")
    parser.add_argument("--record-manifest", action="store_true", default=False, help="Append manifest per slot (slower)")
    parser.add_argument(
        "--shard-index", type=int, default=0, help="Shard index 0..shard-count-1"
    )
    parser.add_argument("--shard-count", type=int, default=1)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    api_key = os.getenv("DATABENTO_API_KEY")
    if args.download and not api_key:
        logger.error("DATABENTO_API_KEY must be set")
        return 1

    windows = resolve_download_scope_windows(_REPO, "macro_releases")
    windows = filter_windows_by_event_type(
        windows, exclude_event_types=resolve_download_exclusions()
    )
    windows = [w for w in windows if w.event_type in PRIORITY_DOWNLOAD_EVENT_TYPES]
    if args.shard_count > 1:
        windows = [w for i, w in enumerate(windows) if i % args.shard_count == args.shard_index]

    existing = sum(
        1
        for w in windows
        if (_slot_dir(w.event_id) / "raw.dbn.zst").is_file()
        and (_slot_dir(w.event_id) / "raw.dbn.zst").stat().st_size > 0
    )

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": DATASET,
        "schema": SCHEMA,
        "symbol": SYMBOL,
        "stype_in": STYPE_IN,
        "windows_total": len(windows),
        "windows_existing": existing,
        "windows_pending": len(windows) - existing,
        "downloaded": [],
        "errors": [],
        "skipped_pre_cmbp1": 0,
    }

    if not args.download:
        logger.info(
            "Dry run: %d windows (%d existing, %d pending). Pass --download to pull.",
            len(windows),
            existing,
            len(windows) - existing,
        )
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 0

    todo = [w for w in windows if _slot_needs_work(w.event_id)]
    corrupt_ids: set[str] = set()
    if args.refresh_corrupt:
        corrupt_ids = set(_corrupt_vix_event_ids(windows))
        if corrupt_ids:
            logger.info("Refreshing %d corrupt/unreadable VIX slots", len(corrupt_ids))
        refresh_windows = [w for w in windows if w.event_id in corrupt_ids]
        todo_by_id = {w.event_id: w for w in todo}
        for w in refresh_windows:
            todo_by_id[w.event_id] = w
        todo = list(todo_by_id.values())
    if args.limit:
        todo = todo[: args.limit]

    def _one(window):
        return download_window(
            api_key,
            window,
            refresh=args.refresh or window.event_id in corrupt_ids,
            skip_cost=args.skip_cost,
            record_manifest=args.record_manifest,
        )

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(_one, w): w for w in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            window = futures[fut]
            try:
                row = fut.result()
                if row.get("status") == "skipped_pre_cmbp1":
                    report["skipped_pre_cmbp1"] += 1
                    continue
                report["downloaded"].append(row)
                logger.info(
                    "[%d/%d] %s %s cost=%s bytes=%s",
                    i,
                    len(todo),
                    window.event_id,
                    row["status"],
                    row.get("cost"),
                    row.get("bytes"),
                )
            except Exception as exc:
                err = {"event_id": window.event_id, "error": str(exc)}
                report["errors"].append(err)
                logger.error("Failed %s: %s", window.event_id, exc)

    report["windows_existing"] = sum(
        1
        for w in windows
        if (_slot_dir(w.event_id) / "raw.dbn.zst").is_file()
        and (_slot_dir(w.event_id) / "raw.dbn.zst").stat().st_size > 0
    )
    report["windows_pending"] = len(windows) - report["windows_existing"]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info(
        "Done: downloaded=%d errors=%d existing=%d/%d",
        len(report["downloaded"]),
        len(report["errors"]),
        report["windows_existing"],
        len(windows),
    )
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
