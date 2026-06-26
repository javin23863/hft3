"""Load HftBacktest NPZ MBO events into pipeline MBOEvent objects."""
from __future__ import annotations

from typing import Iterator, List

import numpy as np
from hftbacktest.types import (
    ADD_ORDER_EVENT,
    CANCEL_ORDER_EVENT,
    FILL_EVENT,
    MODIFY_ORDER_EVENT,
    TRADE_EVENT,
    BUY_EVENT,
    SELL_EVENT,
)

from .mbo_features import MBOEvent

try:
    from research_pipeline.data_quality import NoOHLCVDataError
except ImportError:
    class NoOHLCVDataError(ValueError):
        """Fallback when research_pipeline is not on sys.path."""


def _event_type(ev: int) -> str | None:
    base = int(ev) & 0xFF
    if base == ADD_ORDER_EVENT:
        return "ADD"
    if base == CANCEL_ORDER_EVENT:
        return "CANCEL"
    if base == MODIFY_ORDER_EVENT:
        return "MODIFY"
    if base in (TRADE_EVENT, FILL_EVENT):
        return "TRADE"
    return None


def _event_side(ev: int) -> str:
    if int(ev) & BUY_EVENT:
        return "B"
    if int(ev) & SELL_EVENT:
        return "A"
    return "B"


def load_npz_events(path: str) -> np.ndarray:
    with np.load(path) as archive:
        if "data" not in archive:
            raise ValueError(f"NPZ {path} missing 'data' array; expected HftBacktest event_dtype")
        raw = archive["data"]
    if raw.dtype.names is None:
        raise ValueError(f"NPZ {path} data is not structured event array")
    required = {"ev", "local_ts", "px", "qty", "order_id"}
    missing = required - set(raw.dtype.names)
    if missing:
        raise ValueError(f"NPZ {path} missing fields: {sorted(missing)}")
    if len(raw) == 0:
        raise NoOHLCVDataError(f"NPZ {path} has zero events")
    return raw


def iter_mbo_events(
    raw: np.ndarray,
    skip_non_mbo: bool = True,
) -> Iterator[MBOEvent]:
    for row in raw:
        action = _event_type(int(row["ev"]))
        if action is None:
            if skip_non_mbo:
                continue
            continue
        yield MBOEvent(
            timestamp_ns=int(row["local_ts"]),
            order_id=int(row["order_id"]),
            action=action,
            side=_event_side(int(row["ev"])),
            price=float(row["px"]),
            size=int(row["qty"]),
        )


def events_list(path: str) -> List[MBOEvent]:
    return list(iter_mbo_events(load_npz_events(path)))
