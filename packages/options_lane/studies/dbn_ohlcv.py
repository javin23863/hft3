"""Thin decode layer: DBN ohlcv-1m file -> plain numpy arrays.

Verified databento timestamp convention (databento_dbn 0.79.0)
---------------------------------------------------------------
OHLCVMsg.ts_event is the bar OPEN timestamp in UTC nanoseconds.
A bar labeled ts_event=T opens at T and closes at T+60s.
Example verified from real data (2024-01-02):
  ts_event=08:29 CT -> bar opens at 08:29, closes at 08:30 CT
  ts_event=14:29 CT -> bar opens at 14:29, closes at 14:30 CT
  ts_event=14:59 CT -> bar opens at 14:59, closes at 15:00 CT (last bar of session)

Price fixed-point encoding (verified from raw OHLCVMsg):
  OHLCVMsg.open/high/low/close are int64 in units of 1e-9 price points
  (i.e., stored value / 1e9 == price in index points).
  Example: raw close = 3750500000000 -> 3750.5 index points.
  The to_df() helper already divides; this module uses raw records
  for streaming efficiency and divides by 1e9 explicitly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np


_PRICE_SCALE = 1_000_000_000  # raw int / _PRICE_SCALE = price in index pts


def load_ohlcv_from_dbn(path: str | Path) -> dict[str, np.ndarray]:
    """Load an ohlcv-1m DBN file and return plain numpy arrays.

    Parameters
    ----------
    path : str or Path
        Path to a .dbn or .dbn.zst OHLCV file (any symbol, any date range).

    Returns
    -------
    dict with keys:
        ts_ns   : int64   UTC nanoseconds — bar OPEN time (ts_event convention)
        open    : float64 bar open price  (raw int64 / 1e9)
        high    : float64 bar high price
        low     : float64 bar low price
        close   : float64 bar close price (last trade price before bar close time)
        volume  : float64 bar volume (cast from uint64 for uniform dtype)

    Raises
    ------
    ImportError  if databento is not installed.
    FileNotFoundError if path does not exist.
    """
    try:
        import databento as db
    except ImportError as exc:
        raise ImportError("databento package required: pip install databento") from exc

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"DBN file not found: {path}")

    ts_list: list[int] = []
    open_list: list[float] = []
    high_list: list[float] = []
    low_list: list[float] = []
    close_list: list[float] = []
    vol_list: list[float] = []

    store = db.DBNStore.from_file(str(path))
    for rec in store:
        ts_list.append(rec.ts_event)
        open_list.append(rec.open / _PRICE_SCALE)
        high_list.append(rec.high / _PRICE_SCALE)
        low_list.append(rec.low / _PRICE_SCALE)
        close_list.append(rec.close / _PRICE_SCALE)
        vol_list.append(float(rec.volume))

    n = len(ts_list)
    if n == 0:
        return {
            "ts_ns": np.empty(0, dtype=np.int64),
            "open": np.empty(0, dtype=np.float64),
            "high": np.empty(0, dtype=np.float64),
            "low": np.empty(0, dtype=np.float64),
            "close": np.empty(0, dtype=np.float64),
            "volume": np.empty(0, dtype=np.float64),
        }

    return {
        "ts_ns": np.array(ts_list, dtype=np.int64),
        "open": np.array(open_list, dtype=np.float64),
        "high": np.array(high_list, dtype=np.float64),
        "low": np.array(low_list, dtype=np.float64),
        "close": np.array(close_list, dtype=np.float64),
        "volume": np.array(vol_list, dtype=np.float64),
    }
