#!/usr/bin/env python3
"""Build/refresh the lake-wide NPZ hash catalog at <npz_root>/manifest.json.

Schema per record matches data_system.src.lake_manifest (event_id, symbol,
npz_path, event_count, sha256, created_utc). Incremental: existing records are
kept when the file's size+mtime are unchanged; pass --full to rehash all.
Corrupt, unreadable, malformed, or empty NPZ are catalogued in
<npz_root>/catalog_quarantine.json instead, never in the main manifest.

    python scripts/build_lake_catalog.py [--full] [--workers N]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
for _p in [str(_REPO), str(_REPO / "packages")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from data_system.src.npz_resolver import npz_root  # noqa: E402

# {SYMBOL}_{EVENT_ID}_mbo.npz ; symbol may contain dots (ES.v.0, VIX.OPT)
_NAME_RE = re.compile(r"^(?P<symbol>[A-Z0-9]+\.[A-Za-z0-9.]+?)_(?P<event_id>.+?)_(?:mbo|quotes)\.npz$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _hash_and_count(path_str: str) -> dict:
    """Worker: sha256 + event_count for one NPZ. Returns record or error dict."""
    import numpy as np  # local import for spawn workers

    p = Path(path_str)
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    rec: dict = {"npz_path": str(p), "sha256": h.hexdigest(),
                 "size": p.stat().st_size, "mtime": p.stat().st_mtime}
    try:
        with np.load(p) as z:
            key = "data" if "data" in z.files else ("quotes" if "quotes" in z.files else None)
            rec["event_count"] = int(len(z[key])) if key else 0
            if key is None:
                rec["error"] = f"no data/quotes key (keys={z.files})"
            elif rec["event_count"] <= 0:
                rec["error"] = f"empty {key} array"
    except Exception as exc:  # corrupt zip / bad npz
        rec["event_count"] = -1
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


def _as_int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _catalog_entry(rec: dict[str, Any], now: str) -> tuple[dict[str, Any], bool]:
    name = Path(str(rec.get("npz_path") or "")).name
    m = _NAME_RE.match(name)
    event_count = _as_int(rec.get("event_count"))
    out = {
        "event_id": m.group("event_id") if m else "",
        "symbol": m.group("symbol") if m else "",
        "npz_path": str(rec.get("npz_path") or ""),
        "event_count": event_count,
        "sha256": str(rec.get("sha256") or ""),
        "created_utc": rec.get("created_utc", now),
        "size": _as_int(rec.get("size"), 0),
        "mtime": rec.get("mtime", 0),
    }
    reason = ""
    if rec.get("error"):
        reason = str(rec["error"])
    elif not m:
        reason = "unparseable filename"
    elif not _SHA256_RE.fullmatch(str(rec.get("sha256") or "")):
        reason = "invalid sha256"
    elif event_count <= 0:
        reason = "empty data/quotes array" if event_count == 0 else "invalid event_count"
    if reason:
        out["error"] = reason
        return out, True
    return out, False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="rehash everything")
    ap.add_argument("--workers", type=int, default=max(2, (os.cpu_count() or 4) - 2))
    args = ap.parse_args()

    root = npz_root(_REPO)
    manifest_file = root / "manifest.json"
    quarantine_file = root / "catalog_quarantine.json"

    old: dict[str, dict] = {}
    if manifest_file.is_file() and not args.full:
        for rec in json.loads(manifest_file.read_text(encoding="utf-8")):
            old[rec["npz_path"]] = rec

    files = sorted(p for p in root.glob("*.npz"))  # top level only: nested npz/npz is a known dup
    todo, kept = [], []
    for p in files:
        prev = old.get(str(p))
        st = p.stat()
        if prev and prev.get("size") == st.st_size and abs(prev.get("mtime", 0) - st.st_mtime) < 1:
            kept.append(prev)
        else:
            todo.append(str(p))
    print(f"lake root: {root}\nfiles={len(files)} cached={len(kept)} to_hash={len(todo)}")

    fresh: list[dict] = []
    if todo:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            for i, rec in enumerate(ex.map(_hash_and_count, todo, chunksize=16), 1):
                fresh.append(rec)
                if i % 2000 == 0:
                    print(f"  hashed {i}/{len(todo)}")

    now = datetime.now(timezone.utc).isoformat()
    records, quarantine = [], []
    for rec in kept + fresh:
        out, is_quarantined = _catalog_entry(rec, now)
        if is_quarantined:
            quarantine.append(out)
        else:
            records.append(out)

    tmp = manifest_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(records, indent=1), encoding="utf-8")
    os.replace(tmp, manifest_file)
    quarantine_file.write_text(json.dumps(quarantine, indent=1), encoding="utf-8")
    print(f"catalog: {len(records)} records -> {manifest_file}")
    print(f"quarantine: {len(quarantine)} entries -> {quarantine_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
