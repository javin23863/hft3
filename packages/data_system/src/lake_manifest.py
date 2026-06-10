"""Reusable loader for the MBO event-lake manifest.

The manifest at ``data/npz/manifest.json`` is the sweep runner's input: a
list of records describing every verified NPZ in the lake.  This module
provides ``load_manifest`` so other packages import it rather than
re-implementing the JSON load path.

Schema per record::

    {
        "event_id":    str,       # e.g. "CPI_2024_09_11_TIGHT"
        "symbol":      str,       # e.g. "MES.v.0"
        "npz_path":    str,       # repo-relative path, e.g. "data/npz/MES.v.0_CPI_…_mbo.npz"
        "event_count": int,       # len(np.load(path)['data'])
        "sha256":      str,       # hex digest of the .npz file
        "created_utc": str,       # ISO-8601 UTC timestamp
    }
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MANIFEST_REL_PATH = "data/npz/manifest.json"


def load_manifest(repo_root: Path) -> list[dict[str, Any]]:
    """Return the list of manifest records from *repo_root*/data/npz/manifest.json.

    Returns an empty list when the file does not exist so callers can treat a
    missing manifest the same as an empty lake.
    """
    path = repo_root / MANIFEST_REL_PATH
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))
