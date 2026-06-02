"""Replay MBP-10 depth records through aggregated book + imbalance engine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from features_engine.src.imbalance.classification import DataClass
from features_engine.src.imbalance.engine import ImbalanceEngine
from features_engine.src.imbalance.mbp_book import MBP10Book, apply_mbp10_record
from features_engine.src.imbalance.snapshot_collect import SnapshotCollector


def iter_mbp10_ndjson(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def replay_mbp10_file(
    path: Path,
    *,
    collector: Optional[SnapshotCollector] = None,
) -> List[Dict[str, Any]]:
    """Apply MBP-10 updates; return final book imbalance snapshot dict."""
    book = MBP10Book()
    engine = ImbalanceEngine(DataClass.MBP_10, window_ms=[100, 250, 500, 1000, 5000])
    engine.book = book  # shared aggregated book
    last_snap: Dict[str, Any] = {}
    for rec in iter_mbp10_ndjson(path):
        apply_mbp10_record(book, rec)
        ts_ns = int(rec.get("ts_ns", rec.get("timestamp_ns", 0)))
        snap = engine._snapshot_from_book(ts_ns, window_phase="continuous")
        last_snap = snap.to_dict()
        if collector:
            collector.maybe_record(last_snap)
    return last_snap
