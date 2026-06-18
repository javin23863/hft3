"""Shared L3 orphan event filter (single implementation for builder + prepared data)."""

from __future__ import annotations

import numpy as np

ADD_ORDER_EVENT = 10
CANCEL_ORDER_EVENT = 11
MODIFY_ORDER_EVENT = 12
FILL_EVENT = 13
DEPTH_EVENT = 1


def is_l3_data(events: np.ndarray) -> bool:
    ev_types = (events["ev"].astype(np.int64)) & 0xFF
    has_add = bool(np.any(ev_types == ADD_ORDER_EVENT))
    has_depth = bool(np.any(ev_types == DEPTH_EVENT))
    return has_add and not has_depth


def filter_l3_orphans(events: np.ndarray) -> np.ndarray:
    ev_types = (events["ev"].astype(np.int64)) & 0xFF
    known_ids: set = set()
    keep = np.ones(len(events), dtype=bool)
    for i in range(len(events)):
        et = int(ev_types[i])
        oid = int(events[i]["order_id"])
        if et == ADD_ORDER_EVENT:
            known_ids.add(oid)
        elif et in (CANCEL_ORDER_EVENT, MODIFY_ORDER_EVENT, FILL_EVENT):
            if oid not in known_ids:
                keep[i] = False
    return events[keep]
