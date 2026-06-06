"""Resolve paid/local data roots for CME MBO NPZ discovery."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, List, Tuple

MANIFEST_REL = Path("packages") / "data_system" / "config" / "mbo_pilot_basket_20260605_manifest.json"


def _repo_root(repo_root: Path | None = None) -> Path:
    if repo_root is not None:
        return repo_root.resolve()
    try:
        from hft3_bootstrap import repo_root as bootstrap_root

        return bootstrap_root()
    except ImportError:
        return Path.cwd().resolve()


@lru_cache(maxsize=1)
def _load_manifest(repo_key: str) -> dict[str, Any] | None:
    manifest_path = Path(repo_key) / MANIFEST_REL
    if not manifest_path.is_file():
        alt = Path(repo_key) / "data_system" / "config" / "mbo_pilot_basket_20260605_manifest.json"
        manifest_path = alt if alt.is_file() else manifest_path
    if not manifest_path.is_file():
        return None
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def paid_data_root(repo_root: Path | None = None) -> Path:
    """Canonical paid-data directory (parent of npz/, raw/, etc.)."""
    env = os.environ.get("HFT3_PAID_DATA_ROOT", "").strip()
    if env:
        return Path(env).resolve()

    root = _repo_root(repo_root)
    manifest = _load_manifest(str(root))
    if manifest:
        repo_base = Path(str(manifest.get("repository_root", root)))
        local_paths = manifest.get("local_paths") or {}
        runnable = str(local_paths.get("runnable_npz_dir", "data/npz/"))
        # runnable_npz_dir is relative to repository_root; parent is data root
        npz_dir = (repo_base / runnable).resolve()
        if npz_dir.name == "npz":
            return npz_dir.parent
        return npz_dir

    return (root / "data").resolve()


def npz_search_dirs(repo_root: Path | None = None) -> List[Path]:
    """Ordered NPZ directories to search (repo first, then paid canonical store)."""
    root = _repo_root(repo_root)
    dirs: List[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        resolved = path.resolve()
        key = str(resolved).lower()
        if key in seen:
            return
        seen.add(key)
        dirs.append(resolved)

    _add(root / "data" / "npz")
    paid = paid_data_root(root)
    _add(paid / "npz")
    return dirs


def verify_mbo_release_lane(
    repo_root: Path,
    event_id: str,
    requested_symbol: str,
) -> dict[str, Any]:
    """Check canonical MBO release path validation status if present."""
    from mbo_release_lane.npz_adapter import is_release_valid
    from mbo_release_lane.storage import load_release_event_path, release_slot_dir

    slot = release_slot_dir(repo_root, event_id, requested_symbol)
    manifest = load_release_event_path(slot)
    if manifest is None:
        return {"lane_present": False, "validation_status": None}
    rep = manifest.get("release_event_path", {})
    status = rep.get("validation_status")
    return {
        "lane_present": True,
        "validation_status": status,
        "event_count": rep.get("event_count"),
        "slot_dir": str(slot),
        "ok": status == "valid",
    }


def verify_data_for_event(
    repo_root: Path,
    event_id: str,
    requested_symbol: str,
    parsed_symbols: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Fail-closed check that runnable MBO NPZ exists for an event/symbol."""
    from data_system.src.npz_resolver import npz_filename, resolve_npz_for_event

    parsed = parsed_symbols or (requested_symbol,)
    path, present, sym_used = resolve_npz_for_event(repo_root, event_id, requested_symbol, parsed)
    expected_name = npz_filename(requested_symbol, event_id)
    paid_root = paid_data_root(repo_root)
    sync_cmd = (
        f'python scripts/download_mbo_release_data.py --download --derive-npz '
        f'&& python scripts/paid_data_inventory.py --repo-root "{repo_root}" '
        f'--source-root "{paid_root}" --sync'
    )
    lane = verify_mbo_release_lane(repo_root, event_id, requested_symbol)
    lane_ok = lane.get("ok") if lane.get("lane_present") else None
    npz_ok = present
    if lane.get("lane_present"):
        ok = lane_ok is True and npz_ok
    else:
        ok = npz_ok
    return {
        "event_id": event_id,
        "symbol": requested_symbol,
        "symbol_used": sym_used,
        "npz_path": str(path),
        "present": present,
        "expected_filename": expected_name,
        "search_dirs": [str(d) for d in npz_search_dirs(repo_root)],
        "paid_data_root": str(paid_root),
        "sync_command": sync_cmd,
        "mbo_release_lane": lane,
        "ok": ok,
    }
