"""Feature store helpers: path resolution, hashing, manifest I/O, and load/verify."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np

from .npz_resolver import npz_root

from features_engine.src.features.feature_index import (
    FEATURE_DIM,
    FEATURE_NAME_TO_INDEX,
)

FEATURE_VERSION = "fs_v1"


# ---------------------------------------------------------------------------
# Root resolution
# ---------------------------------------------------------------------------

def feature_store_root(repo_root: Path) -> Path:
    override = os.environ.get("HFT3_FEATURE_ROOT", "").strip()
    if override:
        return Path(override)
    return npz_root(repo_root).parent / "features"


# ---------------------------------------------------------------------------
# Content / schema hashing
# ---------------------------------------------------------------------------

def feature_index_hash() -> str:
    """sha256 over sorted name:idx lines of FEATURE_NAME_TO_INDEX + DIM sentinel."""
    h = hashlib.sha256()
    for name in sorted(FEATURE_NAME_TO_INDEX):
        h.update(f"{name}:{int(FEATURE_NAME_TO_INDEX[name])}\n".encode())
    h.update(f"DIM:{FEATURE_DIM}\n".encode())
    return h.hexdigest()


def content_hash(arrays: dict[str, np.ndarray]) -> str:
    """sha256 over sorted-key array bytes with key name + dtype + shape mixed in."""
    h = hashlib.sha256()
    for key in sorted(arrays):
        arr = arrays[key]
        h.update(key.encode())
        h.update(str(arr.dtype).encode())
        h.update(str(arr.shape).encode())
        h.update(arr.tobytes())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def store_path(root: Path, symbol: str, event_id: str) -> Path:
    return root / symbol / f"{symbol}_{event_id}_features_v1.npz"


def manifest_path(root: Path) -> Path:
    return root / "feature_manifest.jsonl"


# ---------------------------------------------------------------------------
# Manifest I/O
# ---------------------------------------------------------------------------

def load_manifest(root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Return {(symbol, event_id): record}; last entry wins on duplicates."""
    mp = manifest_path(root)
    result: dict[tuple[str, str], dict[str, Any]] = {}
    if not mp.is_file():
        return result
    with mp.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                result[(rec["symbol"], rec["event_id"])] = rec
            except (json.JSONDecodeError, KeyError):
                continue
    return result


def append_manifest(root: Path, record: dict[str, Any]) -> None:
    mp = manifest_path(root)
    mp.parent.mkdir(parents=True, exist_ok=True)
    with mp.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


# ---------------------------------------------------------------------------
# Store load / verify
# ---------------------------------------------------------------------------

def load_store(path: Path) -> dict[str, Any]:
    """Load arrays + scalar attrs.  Hard-asserts feature_index_hash matches current code."""
    with np.load(str(path), allow_pickle=False) as arch:
        stored_hash = str(arch.get("feature_index_hash", np.array("")))
        expected = feature_index_hash()
        if stored_hash != expected:
            raise ValueError(
                f"feature store schema drift: stored={stored_hash!r} expected={expected!r} path={path}"
            )
        ts = arch["ts"]
        if not np.all(np.diff(ts) >= 0):
            raise ValueError(f"feature store ts not monotonic: {path}")
        result: dict[str, Any] = {}
        for k in arch.files:
            arr = arch[k]
            result[k] = arr.item() if arr.ndim == 0 else arr
    return result
