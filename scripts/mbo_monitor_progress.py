#!/usr/bin/env python3
"""Emit priority MBO download progress JSON for the dual-host monitor."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()


def _billed(path: Path) -> tuple[set[tuple[str, str]], float]:
    import pandas as pd

    if not path.is_file():
        return set(), 0.0
    try:
        df = pd.read_parquet(path)
    except Exception:
        return set(), 0.0
    if "schema" in df.columns:
        df = df[df["schema"] == "mbo"]
    out: set[tuple[str, str]] = set()
    for _, row in df.iterrows():
        eid = str(row.get("event_id", "")).strip()
        req = str(row.get("requested_symbol", "") or row.get("symbols", "")).strip()
        sym = req.replace("[", "").replace("]", "").replace("'", "").split(",")[0].strip()
        if eid and sym:
            out.add((eid, sym))
    spend = float(df["cost"].sum()) if "cost" in df.columns and len(df) else 0.0
    return out, spend


def main() -> int:
    from economic_event_universe.events_csv_builder import resolve_download_scope_windows
    from economic_event_universe.registry import default_cme_symbols
    from mbo_release_lane.constants import PRIORITY_DOWNLOAD_EVENT_TYPES
    from mbo_release_lane.download import filter_windows_by_event_type

    repo = _REPO
    only = frozenset(PRIORITY_DOWNLOAD_EVENT_TYPES)
    windows = filter_windows_by_event_type(
        resolve_download_scope_windows(repo, "macro_releases", start_year=2018, end_year=2025),
        only_event_types=only,
    )
    all_slots = {(w.event_id, sym) for w in windows for sym in default_cme_symbols()}

    local_b, local_s = _billed(repo / "data" / "manifest.parquet")
    chi_b, chi_s = _billed(repo / "runtime" / "data_downloads" / "chi404_manifest.parquet")
    union = local_b | chi_b
    in_scope = union & all_slots

    print(
        json.dumps(
            {
                "billed_in_scope": len(in_scope),
                "total_slots": len(all_slots),
                "pct": round(100.0 * len(in_scope) / len(all_slots), 2) if all_slots else 0,
                "remaining": len(all_slots - union),
                "spent_usd": round(local_s + chi_s, 2),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
