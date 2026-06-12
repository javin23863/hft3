#!/usr/bin/env python3
"""Repair the corrupt NPZ flagged by build_lake_catalog's quarantine list.

For every entry in <npz_root>/catalog_quarantine.json:
  1. move the bad file to <lake>/quarantine/npz/ (never delete primaries' kin)
  2. if the event's mbo_release slot has a deriveable raw -> re-derive the NPZ
     locally (free, via mbo_release_lane.npz_adapter)
  3. otherwise leave it absent: the resumable event-tape downloader treats a
     missing NPZ as a gap and re-pulls the window on its next pass (paid,
     pennies per window). Those are listed in runtime/corrupt_npz_redownload.json.

    python scripts/remediate_corrupt_npz.py [--limit N]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _p in [str(_REPO), str(_REPO / "packages")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from data_system.src.npz_resolver import npz_root, lake_root  # noqa: E402
from mbo_release_lane.npz_adapter import derive_npz_from_release, has_deriveable_mbo  # noqa: E402

_NAME_RE = re.compile(r"^(?P<symbol>[A-Z0-9]+\.[A-Za-z0-9.]+?)_(?P<event_id>.+?)_(?:mbo|quotes)\.npz$")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    nroot = npz_root(_REPO)
    qdir = lake_root(_REPO) / "quarantine" / "npz"
    qdir.mkdir(parents=True, exist_ok=True)
    entries = json.loads((nroot / "catalog_quarantine.json").read_text(encoding="utf-8"))
    if args.limit:
        entries = entries[: args.limit]

    rederived, redownload, unparseable, missing = [], [], [], []
    for e in entries:
        p = Path(e["npz_path"])
        if not p.is_file():
            missing.append(p.name)
            continue
        m = _NAME_RE.match(p.name)
        sym = e.get("symbol") or (m.group("symbol") if m else "")
        eid = e.get("event_id") or (m.group("event_id") if m else "")
        if not sym or not eid:
            unparseable.append(p.name)
            continue

        shutil.move(str(p), str(qdir / p.name))
        if has_deriveable_mbo(_REPO, eid, sym):
            out = derive_npz_from_release(_REPO, eid, sym)
            if out and out.is_file():
                rederived.append(p.name)
                continue
        redownload.append({"event_id": eid, "symbol": sym, "file": p.name})
        print(f"re-download queue: {p.name}")

    report = {
        "rederived": len(rederived),
        "redownload_queued": len(redownload),
        "unparseable": unparseable,
        "already_missing": missing,
        "redownload": redownload,
    }
    out_path = _REPO / "runtime" / "corrupt_npz_redownload.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nrederived={len(rederived)} redownload={len(redownload)} "
          f"unparseable={len(unparseable)} missing={len(missing)} -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
