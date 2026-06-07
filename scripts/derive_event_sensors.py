#!/usr/bin/env python3
"""Derive sensor parquet for all VIX.OPT cmbp-1 slots missing normalized sensors."""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()


def _tasks(repo: Path) -> list[str]:
    from data_system.src.event_data_resolver import (
        CMBP1_START,
        resolve_sensor_for_event,
        resolve_vix_raw_for_event,
    )
    from economic_event_universe.events_csv_builder import resolve_download_scope_windows
    from mbo_release_lane.constants import PRIORITY_DOWNLOAD_EVENT_TYPES
    from mbo_release_lane.download import filter_windows_by_event_type, resolve_download_exclusions

    windows = resolve_download_scope_windows(repo, "macro_releases")
    windows = filter_windows_by_event_type(
        windows, exclude_event_types=resolve_download_exclusions()
    )
    windows = [w for w in windows if w.event_type in PRIORITY_DOWNLOAD_EVENT_TYPES]

    out: list[str] = []
    for w in windows:
        start = w.start_utc
        if hasattr(start, "to_pydatetime"):
            start = start.to_pydatetime()
        if start.tzinfo is None:
            import datetime as dt

            start = start.replace(tzinfo=dt.timezone.utc)
        if start < CMBP1_START:
            continue
        _, sensor_ok = resolve_sensor_for_event(repo, w.event_id)
        if sensor_ok:
            continue
        _, raw_ok = resolve_vix_raw_for_event(repo, w.event_id)
        from mbo_release_lane.sensor_adapter import has_deriveable_vix_raw

        if has_deriveable_vix_raw(repo, w.event_id):
            out.append(w.event_id)
    return out


def _derive_one(repo: Path, event_id: str) -> dict:
    from mbo_release_lane.sensor_adapter import derive_sensor_parquet_for_event

    try:
        path = derive_sensor_parquet_for_event(repo, event_id)
    except Exception as exc:
        return {"event_id": event_id, "ok": False, "path": None, "error": str(exc)[:200]}
    if path is None:
        return {"event_id": event_id, "ok": False, "path": None, "error": "derive returned None"}
    return {"event_id": event_id, "ok": True, "path": str(path)}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    tasks = _tasks(_REPO)
    if args.limit:
        tasks = tasks[: args.limit]
    print(json.dumps({"missing_sensor_with_raw": len(tasks)}))
    if not tasks:
        return 0

    ok = 0
    errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futs = [pool.submit(_derive_one, _REPO, eid) for eid in tasks]
        for fut in as_completed(futs):
            res = fut.result()
            if res["ok"]:
                ok += 1
            else:
                errors.append(res)
            if ok and ok % 25 == 0:
                print(f"derived {ok}...", flush=True)
    print(json.dumps({"derived_ok": ok, "total": len(tasks), "errors": errors}))
    return 0 if ok == len(tasks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
