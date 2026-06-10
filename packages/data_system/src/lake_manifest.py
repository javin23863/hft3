"""Reusable loader for the MBO event-lake manifest.

The manifest at ``<npz_root>/manifest.json`` is the sweep runner's input: a
list of records describing every verified NPZ in the lake.  The lake root
defaults to ``<repo>/data/npz`` and can be relocated with the HFT3_NPZ_ROOT
environment variable (see ``data_system.src.npz_resolver.npz_root``) so the
multi-gigabyte lake lives outside the working clones.

Schema per record::

    {
        "event_id":    str,       # e.g. "CPI_2024_09_11_TIGHT"
        "symbol":      str,       # e.g. "MES.v.0"
        "npz_path":    str,       # repo-relative when under the repo,
                                  # absolute when the lake root is external
        "event_count": int,       # len(np.load(path)['data'])
        "sha256":      str,       # hex digest of the .npz file
        "created_utc": str,       # ISO-8601 UTC timestamp
    }
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from data_system.src.npz_resolver import npz_root

MANIFEST_REL_PATH = "data/npz/manifest.json"  # legacy constant (default root)


def manifest_path(repo_root: Path) -> Path:
    return npz_root(repo_root) / "manifest.json"


def resolve_npz_path(repo_root: Path, npz_path_str: str) -> Path:
    """Resolve a manifest npz_path entry: absolute as-is, relative under repo."""
    p = Path(npz_path_str)
    if p.is_absolute():
        return p
    return repo_root / p


def load_manifest(repo_root: Path) -> list[dict[str, Any]]:
    """Return the list of manifest records from the lake root.

    Returns an empty list when the file does not exist so callers can treat a
    missing manifest the same as an empty lake.
    """
    path = manifest_path(repo_root)
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))
