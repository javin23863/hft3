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
    return np.load(path)["data"]


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
