"""Shared NPZ path resolution with listing-date symbol fallback (handoff PDF §8)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Tuple

# Primary symbols for listing-date fallback after requested symbol
PDF_PRIMARY_FALLBACK_ORDER: tuple[str, ...] = ("ES.v.0", "MNQ.v.0", "NQ.v.0")


def npz_root(repo_root: Path) -> Path:
    """Resolve the NPZ lake root.

    HFT3_NPZ_ROOT overrides the default <repo>/data/npz so the multi-gigabyte
    event lake can live outside the working clone (e.g. the paid-data mirror)
    without copying it into every checkout.
    """
    override = os.environ.get("HFT3_NPZ_ROOT", "").strip()
    if override:
        return Path(override)
    return repo_root / "data" / "npz"


def npz_path_for(repo_root: Path, event_id: str, symbol: str) -> Path:
    return npz_root(repo_root) / f"{symbol}_{event_id}_mbo.npz"


def candidate_npz_symbols(requested_symbol: str, parsed_symbols: tuple[str, ...]) -> List[str]:
    candidates = [requested_symbol]
    for sym in PDF_PRIMARY_FALLBACK_ORDER:
        if sym in parsed_symbols and sym not in candidates:
            candidates.append(sym)
    return candidates


def resolve_npz_for_event(
    repo_root: Path,
    event_id: str,
    requested_symbol: str,
    parsed_symbols: tuple[str, ...],
) -> Tuple[Path, bool, str]:
    """Return (path, present, symbol_used)."""
    for sym in candidate_npz_symbols(requested_symbol, parsed_symbols):
        path = npz_path_for(repo_root, event_id, sym)
        if path.is_file():
            return path, True, sym
    return npz_path_for(repo_root, event_id, requested_symbol), False, requested_symbol
