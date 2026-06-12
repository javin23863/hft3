"""Thin decode layer: DBN MBO -> plain numpy arrays for fixing-window consumption.

Verified against databento_dbn 0.79.0 (installed package):
  databento_dbn.Action.TRADE == 'T'  (int 84) — aggressor-level trade record
  databento_dbn.Action.FILL  == 'F'  (int 70) — per-maker fill; ALWAYS paired
      with a 'T' record at the same ts_event; excluded to avoid double-counting.
  databento_dbn.Side.ASK  == 'A' — aggressor hit the ask (demand taking supply) -> BUY aggressor
  databento_dbn.Side.BID  == 'B' — aggressor hit the bid (supply taking demand) -> SELL aggressor
  databento_dbn.Side.NONE == 'N' — unknown side -> aggressor_sign = 0
  databento_dbn.FIXED_PRICE_SCALE == 1_000_000_000 — raw price / scale = float USD

Timestamp field: ts_event (exchange-reported UTC nanoseconds). Using ts_event rather
than ts_recv prevents latency-dependent lookahead (ts_recv adds gateway receive lag).

Only action=='T' rows are returned. F-rows carry per-maker-fill detail; they always
share ts_event with their parent T-row and represent the same liquidity, so including
them would double-count volume.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_trades_from_dbn(path: str | Path) -> dict[str, np.ndarray]:
    """Load MBO trade records from a .dbn.zst file into plain numpy arrays.

    Filters to action == 'T' (aggressor-level trades only; F-fills excluded).
    Prices are converted from Databento fixed-point (1e-9 scale) to float64.

    Returns a dict with keys:
        ts_ns          (int64)   exchange timestamp, UTC nanoseconds (ts_event)
        price          (float64) trade price in USD (raw / FIXED_PRICE_SCALE)
        size           (float64) trade size (quantity)
        aggressor_sign (int8)    +1 buy-aggressor (Side.ASK), -1 sell-aggressor
                                 (Side.BID), 0 unknown (Side.NONE)

    Returns empty arrays (length 0) when the file has no trade records.
    """
    import databento
    import databento_dbn

    _SCALE = databento_dbn.FIXED_PRICE_SCALE  # 1_000_000_000

    store = databento.DBNStore.from_file(str(path))

    ts_list: list[int] = []
    px_list: list[float] = []
    sz_list: list[float] = []
    sign_list: list[int] = []

    for rec in store:
        if rec.action != databento_dbn.Action.TRADE:
            continue
        ts_list.append(rec.ts_event)
        px_list.append(rec.price / _SCALE)
        sz_list.append(float(rec.size))
        side = rec.side
        if side == databento_dbn.Side.ASK:
            sign_list.append(1)    # buyer lifted the ask
        elif side == databento_dbn.Side.BID:
            sign_list.append(-1)   # seller hit the bid
        else:
            sign_list.append(0)    # Side.NONE — unknown

    n = len(ts_list)
    return {
        "ts_ns": np.array(ts_list, dtype=np.int64),
        "price": np.array(px_list, dtype=np.float64),
        "size": np.array(sz_list, dtype=np.float64),
        "aggressor_sign": np.array(sign_list, dtype=np.int8),
    }
