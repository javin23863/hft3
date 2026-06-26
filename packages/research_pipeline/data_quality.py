"""Data-quality pre-check for NPZ lake files.

Validates that NPZ files contain the data needed to produce OHLCV bars
for backtesting.  The hft3 lake stores MBO (market-by-order) event arrays
in the ``data`` member, not pre-built OHLCV candles; the screening path
builds 1-minute bars from these events at load time (see
``features_engine.src.features.npz_feed.load_npz_events`` and
``vectorbt_adapter._default_data_loader``).  A file that exists but has an
empty or malformed ``data`` array will cause ``no_ohlcv_data`` errors deep
in the screening pipeline — this module catches that *before* dispatch.

Usage
-----
    from research_pipeline.data_quality import check_npz_ohlcv

    ok, reason = check_npz_ohlcv(Path("/data/npz/ZN.v.0_EIA_NATGAS_..._mbo.npz"))
    if not ok:
        print(f"bad NPZ: {reason}")
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple

import numpy as np

# --- Exception -----------------------------------------------------------


class NoOHLCVDataError(ValueError):
    """Raised when an NPZ file cannot produce OHLCV bars.

    Distinguishable from generic ValueError so callers can separate
    data-quality failures from algorithmic failures.
    """


# --- Core check ----------------------------------------------------------

_REQUIRED_MBO_FIELDS = frozenset({"ev", "local_ts", "px", "qty", "order_id"})

_OHLCV_KEYS = ("open", "high", "low", "close", "volume")


def check_npz_ohlcv(path: Path) -> Tuple[bool, str]:
    """Validate that an NPZ file can produce OHLCV bars.

    Supports two layouts:

    1. **MBO event array** (hft3 lake format): a ``data`` member holding a
       structured numpy array with fields
       ``{ev, local_ts, px, qty, order_id}``.  Must have ≥2 rows to build
       at least one bar.
    2. **Pre-built OHLCV**: members ``open/high/low/close/volume``, each
       non-zero-length.

    Returns ``(True, "")`` when the file is valid.  Returns
    ``(False, reason)`` when the file is missing, unreadable, empty, or
    lacks the required arrays.  Corrupt-file exceptions are *not*
    swallowed — they propagate to the caller with the path as context.

    Parameters
    ----------
    path : Path
        Filesystem path to the ``.npz`` file.
    """
    path = Path(path)
    if not path.is_file():
        return False, f"file_not_found:{path}"

    try:
        with np.load(path) as archive:
            # --- Layout 2: pre-built OHLCV arrays ---
            if all(k in archive for k in _OHLCV_KEYS):
                for k in _OHLCV_KEYS:
                    arr = archive[k]
                    if arr.size == 0:
                        return False, f"no_ohlcv_data: empty {k} array"
                return True, ""

            # --- Layout 1: MBO event array (hft3 lake) ---
            if "data" not in archive:
                return False, "no_ohlcv_data: missing 'data' member"

            raw = archive["data"]
            if raw.dtype.names is None:
                return False, "no_ohlcv_data: 'data' is not a structured array"

            missing = _REQUIRED_MBO_FIELDS - set(raw.dtype.names)
            if missing:
                return False, f"no_ohlcv_data: missing fields {sorted(missing)}"

            if raw.size < 2:
                return False, f"no_ohlcv_data: only {raw.size} events (need ≥2 to build a bar)"

            return True, ""

    except (OSError, ValueError, KeyError) as exc:
        # Propagate with path context — do not swallow.
        raise NoOHLCVDataError(f"{path}: {type(exc).__name__}: {exc}") from exc