"""Derive HftBacktest NPZ from validated MBO release paths — downstream only."""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

from mbo_release_lane.storage import (
    load_release_event_path,
    raw_dbn_path,
    release_slot_dir,
)

logger = logging.getLogger(__name__)


def is_release_valid(repo_root: Path, release_id: str, symbol: str) -> bool:
    from data_system.src.event_data_resolver import mbo_release_search_roots

    for root in mbo_release_search_roots(repo_root):
        slot = root / release_id / symbol.replace("/", "_")
        data = load_release_event_path(slot)
        if data:
            rep = data.get("release_event_path", {})
            return rep.get("validation_status") == "valid"
    slot = release_slot_dir(repo_root, release_id, symbol)
    data = load_release_event_path(slot)
    if not data:
        return False
    rep = data.get("release_event_path", {})
    return rep.get("validation_status") == "valid"


def has_deriveable_mbo(repo_root: Path, release_id: str, symbol: str) -> bool:
    """True when raw DBN exists with a non-empty parsed event stream."""
    from data_system.src.event_data_resolver import mbo_release_search_roots
    from mbo_release_lane.storage import validate_dbn_readable

    safe_sym = symbol.replace("/", "_")
    for root in mbo_release_search_roots(repo_root):
        slot = root / release_id / safe_sym
        data = load_release_event_path(slot)
        raw = raw_dbn_path(slot)
        if not data or not raw.is_file() or raw.stat().st_size == 0:
            continue
        if not validate_dbn_readable(raw):
            continue
        rep = data.get("release_event_path", {})
        if int(rep.get("event_count", 0)) > 0:
            return True
    return False


def derive_npz_from_release(
    repo_root: Path,
    release_id: str,
    symbol: str,
    *,
    sym_used: str | None = None,
    npz_dir: Path | None = None,
) -> Path | None:
    """Convert raw DBN to HftBacktest NPZ. Returns None if no deriveable events."""
    if not has_deriveable_mbo(repo_root, release_id, symbol):
        logger.warning("Skipping NPZ derive — no deriveable MBO: %s %s", release_id, symbol)
        return None

    slot = release_slot_dir(repo_root, release_id, symbol)
    raw = raw_dbn_path(slot)
    if not raw.is_file():
        logger.warning("Missing raw DBN for %s", release_id)
        return None

    from backtest_pipeline.src.converter import DatabentoConverter
    from data_system.src.npz_resolver import npz_filename

    out_dir = npz_dir or (repo_root / "data" / "npz")
    out_dir.mkdir(parents=True, exist_ok=True)
    target_name = npz_filename(symbol, release_id)
    target = out_dir / target_name

    convert_sym = sym_used or symbol
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dbn = Path(tmp) / raw.name
        shutil.copy2(raw, tmp_dbn)
        conv = DatabentoConverter(tmp)
        produced = Path(conv.convert_file(str(tmp_dbn), convert_sym))
        if not produced.is_file():
            return None
        out_dir.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            target.unlink()
        shutil.move(str(produced), str(target))

    return target if target.is_file() else None
