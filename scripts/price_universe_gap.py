#!/usr/bin/env python3
"""Price the missing-tape gap across the FULL futures event universe (read-only).

For every event (pre-embargo) it finds the symbols whose lake NPZ is absent, then
SAMPLES windows per event_type and calls Databento metadata.get_cost (free, no
spend) to estimate $/window, and extrapolates to the full missing set. Prints a
per-event-type breakdown + grand total. No downloads, no charge.

    python scripts/price_universe_gap.py --sample-per-type 12
"""
from __future__ import annotations

import argparse
import collections
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _p in [str(_REPO), str(_REPO / "packages")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from data_system.src.keystore import load_keys  # noqa: E402
from data_system.src.events_parser import load_and_parse_events  # noqa: E402
from data_system.src.npz_resolver import npz_root  # noqa: E402
from data_system.src.databento_client import DatabentoResearchClient  # noqa: E402

EMBARGO = "2026-01-01"
EVENTS_CSV = _REPO / "packages" / "data_system" / "config" / "events.csv"
NPZ_ROOT = npz_root(_REPO)


def _present(event_id: str, sym: str) -> bool:
    p = NPZ_ROOT / f"{sym}_{event_id}_mbo.npz"
    return p.is_file() and p.stat().st_size >= 4096  # shells (<4KB) count as not-present


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample-per-type", type=int, default=12)
    args = ap.parse_args()

    load_keys()
    client = DatabentoResearchClient()
    ev = load_and_parse_events(str(EVENTS_CSV))
    ev = ev[ev["release_date"] < EMBARGO].copy().sort_values("release_date").reset_index(drop=True)

    # Build per-event missing-symbol set.
    by_type: dict[str, list] = collections.defaultdict(list)  # etype -> [(event_id, missing[], start, end)]
    missing_symbol_windows = collections.Counter()
    for _, row in ev.iterrows():
        eid = str(row["event_id"])
        etype = str(row["event_type"])
        syms = [s.strip() for s in str(row.get("symbols", "")).replace(";", ",").split(",") if s.strip()]
        missing = [s for s in syms if not _present(eid, s)]
        if not missing:
            continue
        by_type[etype].append((eid, missing, row["start_utc"].to_pydatetime(), row["end_utc"].to_pydatetime()))
        missing_symbol_windows[etype] += len(missing)

    print(f"NPZ root: {NPZ_ROOT}")
    print(f"event types with gaps: {len(by_type)}")
    grand_total = 0.0
    grand_missing_windows = 0
    grand_symbol_windows = 0
    rows = []
    for etype in sorted(by_type, key=lambda k: -len(by_type[k])):
        windows = by_type[etype]
        n_windows = len(windows)
        n_symwin = missing_symbol_windows[etype]
        grand_missing_windows += n_windows
        grand_symbol_windows += n_symwin
        # evenly-spaced sample
        step = max(1, n_windows // args.sample_per_type)
        sample = windows[::step][: args.sample_per_type]
        costs = []
        for eid, missing, start, end in sample:
            try:
                c = client.estimate_cost(symbols=missing, start_utc=start, end_utc=end)
                costs.append(float(c))
            except Exception:
                continue
        if not costs:
            avg = 0.0
        else:
            avg = sum(costs) / len(costs)
        est_type = avg * n_windows  # avg already over windows w/ their missing-symbol counts
        grand_total += est_type
        rows.append((etype, n_windows, n_symwin, avg, est_type))
        print(f"  {etype:26} windows {n_windows:5}  sym-win {n_symwin:6}  avg/win ${avg:.5f}  est ${est_type:8.2f}")

    print("-" * 70)
    print(f"TOTAL missing event-windows : {grand_missing_windows}")
    print(f"TOTAL missing symbol-windows: {grand_symbol_windows}")
    print(f"ESTIMATED COST TO FILL GAP  : ${grand_total:.2f}")
    print("(holiday/no-data windows price $0 and are auto-skipped on real download)")


if __name__ == "__main__":
    main()
