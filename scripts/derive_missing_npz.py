#!/usr/bin/env python3
"""Derive NPZ for all valid mbo_release slots missing local NPZ."""

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


def _tasks(repo: Path) -> list[tuple[str, str]]:
    from data_system.src.event_data_resolver import VIX_OPT_SYMBOL, mbo_release_search_roots
    from data_system.src.npz_resolver import resolve_npz_for_event
    from economic_event_universe.registry import default_cme_symbols
    from mbo_release_lane.npz_adapter import has_deriveable_mbo
    from mbo_release_lane.storage import iter_release_manifest_paths

    syms = set(default_cme_symbols())
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for root in mbo_release_search_roots(repo):
        for manifest in iter_release_manifest_paths(root):
            try:
                eid, sym = manifest.parts[-3], manifest.parts[-2]
            except (IndexError, OSError):
                continue
            if sym == VIX_OPT_SYMBOL.replace("/", "_") or sym not in syms:
                continue
            key = (eid, sym)
            if key in seen:
                continue
            seen.add(key)
            try:
                if not has_deriveable_mbo(repo, eid, sym):
                    continue
                _, present, _ = resolve_npz_for_event(repo, eid, sym, tuple(syms))
            except OSError:
                continue
            if not present:
                out.append(key)
    return out


def _derive_one(repo: Path, release_id: str, symbol: str) -> dict:
    from mbo_release_lane.npz_adapter import derive_npz_from_release

    try:
        npz = derive_npz_from_release(repo, release_id, symbol)
    except Exception as exc:
        return {
            "release_id": release_id,
            "symbol": symbol,
            "ok": False,
            "path": None,
            "error": str(exc)[:200],
        }
    if npz is None:
        return {
            "release_id": release_id,
            "symbol": symbol,
            "ok": False,
            "path": None,
            "error": "derive returned None",
        }
    return {"release_id": release_id, "symbol": symbol, "ok": True, "path": str(npz)}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    tasks = _tasks(_REPO)
    print(json.dumps({"missing_npz_valid_slots": len(tasks)}))
    if not tasks:
        return 0

    ok = 0
    failures: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futs = [pool.submit(_derive_one, _REPO, eid, sym) for eid, sym in tasks]
        for fut in as_completed(futs):
            res = fut.result()
            if res["ok"]:
                ok += 1
            else:
                failures.append(res)
            if ok and ok % 50 == 0:
                print(f"derived {ok}...", flush=True)
    print(json.dumps({"derived_ok": ok, "total": len(tasks), "failures": failures}))
    return 0 if ok == len(tasks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
