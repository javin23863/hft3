#!/usr/bin/env python3
"""Find which event types are MISSING NPZ coverage across 7 CME symbols + fallback."""
from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

import pandas as pd

npz_dir = _REPO / "data" / "npz"
events_csv = _REPO / "packages" / "data_system" / "config" / "events.csv"
edf = pd.read_csv(events_csv)
edf["release_year"] = edf["release_date"].str[:4].astype(int)
edf["parsed_symbols"] = edf["symbols"].fillna("").str.split(",")
edf["parsed_symbols"] = edf["parsed_symbols"].apply(
    lambda lst: [s.strip() for s in lst] if isinstance(lst, list) else []
)

# Build eid -> {sym} map from NPZ files
eid_to_syms: dict[str, set[str]] = {}
for p in npz_dir.glob("*.npz"):
    name = p.stem
    parts = name.split("_")
    if len(parts) < 5:
        continue
    sym = parts[0]
    yyyy_idx = None
    for i, part in enumerate(parts):
        if re.match(r"^\d{4}$", part):
            yyyy_idx = i
            break
    if yyyy_idx is None or yyyy_idx + 3 >= len(parts):
        continue
    date = f"{parts[yyyy_idx]}_{parts[yyyy_idx+1]}_{parts[yyyy_idx+2]}"
    window = parts[yyyy_idx + 3]
    eid = "_".join(parts[1:yyyy_idx] + [date, window])
    eid_to_syms.setdefault(eid, set()).add(sym)

cme_syms = {"MES.v.0", "MNQ.v.0", "ES.v.0", "NQ.v.0", "ZN.v.0", "ZB.v.0", "RTY.v.0"}
FALLBACK = ("ES.v.0", "MNQ.v.0", "NQ.v.0")

def has_npz(eid: str, symbol: str) -> bool:
    syms = eid_to_syms.get(eid, set())
    if symbol in syms:
        return True
    for fb in FALLBACK:
        if fb in syms:
            return True
    return False

# Per event_type: total events vs events with NPZ (across all symbols)
etype_total: dict[str, int] = {}
etype_covered: dict[str, int] = {}
for _, row in edf.iterrows():
    eid = str(row["event_id"])
    etype = str(row["event_type"])
    syms = row["parsed_symbols"]
    for sym in syms:
        if sym not in cme_syms:
            continue
        etype_total.setdefault(etype, 0)
        etype_total[etype] += 1
        if has_npz(eid, sym):
            etype_covered.setdefault(etype, 0)
            etype_covered[etype] += 1

# Report: event types sorted by coverage % (lowest first)
print("Event types by NPZ coverage (with fallback):")
print(f"{'Event Type':<35} {'Symbol-slots':>12} {'Covered':>8} {'Pct':>6}")
print("-" * 65)
rows = []
for etype in sorted(etype_total):
    t = etype_total[etype]
    c = etype_covered.get(etype, 0)
    pct = 100.0 * c / t if t else 0
    rows.append((etype, t, c, pct))
for etype, t, c, pct in sorted(rows, key=lambda r: (r[3], r[1]), reverse=False):
    print(f"{etype:<35} {t:>12} {c:>8} {pct:>5.1f}%")

# Identify 0% types to download
zero_pct = [r for r in rows if r[3] == 0]
partial = [r for r in rows if r[3] > 0 and r[3] < 100]
complete = [r for r in rows if r[3] == 100]

print(f"\n{'=' * 65}")
print(f"0%: {len(zero_pct)} types  |  partial: {len(partial)} types  |  100%: {len(complete)} types")

if zero_pct:
    names = [r[0] for r in zero_pct]
    print(f"\nZero-percent types (NEED DOWNLOAD):")
    print(" ".join(names))
    print(f"\nDownload command:")
    print(f"  --only-event-type {' --only-event-type '.join(names)}")

if partial:
    names = [r[0] for r in partial]
    print(f"\nPartially covered types (could fill gaps):")
    for n, t, c, pct in partial:
        print(f"  {n}: {c}/{t} ({pct:.1f}%)")

# Summary: how many new symbols slots would be filled if we download zero-pct types
zero_slots = sum(r[1] for r in zero_pct)
print(f"\nTotal unfilled symbol-slots from zero-pct types: {zero_slots}")
print(f"At ~0.001/slot: ~${zero_slots * 0.001:.2f} estimated cost")