"""Build imbalance quality report from replay snapshots."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from features_engine.src.imbalance.quality import run_quality_checks


def build_quality_report_from_snapshots(
    snapshots: List[dict],
) -> Dict[str, Any]:
    if not snapshots:
        return {
            "passed": False,
            "results": [],
            "note": "no snapshots",
            "failure": "empty_imbalance_samples",
        }

    timestamps: List[int] = []
    spreads: List[float] = []
    book_states: List[str] = []
    feature_ts: List[int] = []

    for snap in snapshots:
        book = snap.get("book") or {}
        lineage = snap.get("lineage") or {}
        ts = int(lineage.get("timestamp_event_ns", 0))
        timestamps.append(ts)
        feature_ts.append(ts)
        spreads.append(float(book.get("spread") or 0.0))
        book_states.append(str(book.get("book_state", "ok")))

    report = run_quality_checks(
        timestamps_ns=timestamps,
        spreads=spreads,
        book_states=book_states,
        feature_timestamps_ns=feature_ts,
    )
    return report.to_dict()
