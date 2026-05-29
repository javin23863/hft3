from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from hftbacktest.types import (
    BUY_EVENT,
    EXCH_EVENT,
    LOCAL_EVENT,
    SELL_EVENT,
    TRADE_EVENT,
    event_dtype,
)


def _has_mbo(events: list[dict[str, Any]]) -> bool:
    return any(ev.get("event_type") in ("depth",) and ev.get("order_id") for ev in events)


def convert_to_npz(
    events: list[dict[str, Any]],
    out_path: Path,
    tick_size: float = 0.25,
) -> dict[str, Any]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not events:
        return {
            "status": "fail",
            "reason": "no events",
            "output_file": str(out_path),
        }

    if _has_mbo(events):
        return {
            "status": "fail",
            "reason": "partial MBO from trial not yet mapped to NPZ ADD/CANCEL events",
            "output_file": str(out_path),
            "note": "Use R|API connector for full MBO replay",
        }

    trade_events = [ev for ev in events if ev.get("event_type") == "trade" and ev.get("price")]
    if not trade_events:
        return {
            "status": "fail",
            "reason": "required trade events missing; cannot build replay NPZ",
            "output_file": str(out_path),
        }

    rows = []
    for i, ev in enumerate(trade_events):
        side_flag = BUY_EVENT if str(ev.get("side", "B")).upper().startswith("B") else SELL_EVENT
        local_ts = int(ev.get("local_receive_timestamp_ns") or 0)
        exch_ts = int(ev.get("exchange_timestamp_ns") or local_ts)
        rows.append(
            (
                TRADE_EVENT | side_flag | EXCH_EVENT | LOCAL_EVENT,
                exch_ts,
                local_ts,
                float(ev["price"]),
                float(ev.get("size") or 1),
                i + 1,
                0,
                0.0,
            )
        )

    arr = np.array(rows, dtype=event_dtype)
    np.savez_compressed(out_path, data=arr)
    return {
        "status": "pass",
        "mode": "trade_only",
        "event_count": len(rows),
        "output_file": str(out_path),
        "tick_size": tick_size,
        "limitations": ["Trade-only replay; no full MBO book from trial bridge"],
    }
