"""Derive HftBacktest NPZ from validated MBO release paths — downstream only.

Also provides ``derive_npz_from_vix_release`` for OPRA VIX.OPT cmbp-1 quote data.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

from data_system.src.event_data_resolver import VIX_OPT_SYMBOL
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
    from data_system.src.npz_resolver import npz_filename, npz_root

    out_dir = npz_dir or npz_root(repo_root)
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


def derive_npz_from_vix_release(
    repo_root: Path,
    release_id: str,
    *,
    npz_dir: Path | None = None,
) -> Path | None:
    """Convert VIX.OPT cmbp-1 DBN to NPZ quote array. Returns None if empty."""
    slot = release_slot_dir(repo_root, release_id, VIX_OPT_SYMBOL)
    raw = raw_dbn_path(slot)
    if not raw.is_file() or raw.stat().st_size == 0:
        return None
    import databento as db
    import numpy as np
    store = db.DBNStore.from_file(str(raw))
    rows = []
    for rec in store:
        rows.append((
            int(rec.ts_event),
            int(rec.ts_recv),
            float(rec.bid_px_00) / 1e9 if hasattr(rec, 'bid_px_00') and rec.bid_px_00 is not None else 0.0,
            float(rec.ask_px_00) / 1e9 if hasattr(rec, 'ask_px_00') and rec.ask_px_00 is not None else 0.0,
            float(rec.bid_sz_00) if hasattr(rec, 'bid_sz_00') and rec.bid_sz_00 is not None else 0.0,
            float(rec.ask_sz_00) if hasattr(rec, 'ask_sz_00') and rec.ask_sz_00 is not None else 0.0,
            int(rec.instrument_id),
            int(rec.publisher_id),
        ))
    if not rows:
        return None
    dt = np.dtype([
        ('ts_event', np.int64),
        ('ts_recv', np.int64),
        ('bid_px', np.float64),
        ('ask_px', np.float64),
        ('bid_sz', np.float64),
        ('ask_sz', np.float64),
        ('instrument_id', np.uint64),
        ('publisher_id', np.uint32),
    ])
    arr = np.array(rows, dtype=dt)
    from data_system.src.npz_resolver import npz_root

    out_dir = npz_dir or npz_root(repo_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{VIX_OPT_SYMBOL}_{release_id}_quotes.npz"
    np.savez_compressed(target, quotes=arr)
    return target if target.is_file() else None
