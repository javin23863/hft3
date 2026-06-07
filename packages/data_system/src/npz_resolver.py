"""Shared NPZ path resolution with listing-date symbol fallback (handoff PDF §8)."""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from data_system.src.data_roots import npz_search_dirs

# Primary symbols for listing-date fallback after requested symbol
PDF_PRIMARY_FALLBACK_ORDER: tuple[str, ...] = ("ES.v.0", "MNQ.v.0", "NQ.v.0")


def npz_filename(symbol: str, event_id: str) -> str:
    return f"{symbol}_{event_id}_mbo.npz"


def npz_path_for(repo_root: Path, event_id: str, symbol: str) -> Path:
    return repo_root / "data" / "npz" / npz_filename(symbol, event_id)


def candidate_npz_symbols(requested_symbol: str, parsed_symbols: tuple[str, ...]) -> List[str]:
    candidates = [requested_symbol]
    for sym in PDF_PRIMARY_FALLBACK_ORDER:
        if sym in parsed_symbols and sym not in candidates:
            candidates.append(sym)
    return candidates


def _find_npz_in_dirs(npz_dirs: List[Path], event_id: str, symbol: str) -> Path | None:
    name = npz_filename(symbol, event_id)
    for npz_dir in npz_dirs:
        candidate = npz_dir / name
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def resolve_npz_for_event(
    repo_root: Path,
    event_id: str,
    requested_symbol: str,
    parsed_symbols: tuple[str, ...],
) -> Tuple[Path, bool, str]:
    """Return (path, present, symbol_used). Searches repo and paid data roots."""
    npz_dirs = npz_search_dirs(repo_root)
    for sym in candidate_npz_symbols(requested_symbol, parsed_symbols):
        hit = _find_npz_in_dirs(npz_dirs, event_id, sym)
        if hit is not None:
            return hit, True, sym
    return npz_path_for(repo_root, event_id, requested_symbol), False, requested_symbol
