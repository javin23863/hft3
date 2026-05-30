"""Align multi-symbol feeds on unified timeline (no lookahead)."""

from __future__ import annotations

from typing import Dict, Iterator, List, Tuple

import numpy as np

from features_engine.src.features.mbo_features import MBOEvent
from features_engine.src.features.npz_feed import iter_mbo_events


def merge_feeds(feeds: Dict[str, np.ndarray]) -> Iterator[Tuple[str, MBOEvent]]:
    """K-way merge on local_ts; equal timestamps processed in symbol sort order."""
    iters: Dict[str, Iterator[MBOEvent]] = {
        sym: iter_mbo_events(arr) for sym, arr in feeds.items()
    }
    heap: List[Tuple[int, str, MBOEvent]] = []
    for sym, it in iters.items():
        try:
            ev = next(it)
            heap.append((ev.timestamp_ns, sym, ev))
        except StopIteration:
            pass
    heap.sort()
    while heap:
        ts, sym, ev = heap.pop(0)
        yield sym, ev
        try:
            nxt = next(iters[sym])
            heap.append((nxt.timestamp_ns, sym, nxt))
            heap.sort()
