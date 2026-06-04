"""NDJSON → NPZ converter for Kraken L3 order book recordings."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from hftbacktest.types import (
    ADD_ORDER_EVENT,
    BUY_EVENT,
    CANCEL_ORDER_EVENT,
    EXCH_EVENT,
    LOCAL_EVENT,
    SELL_EVENT,
    event_dtype,
)


@dataclass
class KrakenOrderBook:
    bids: Dict[float, float] = field(default_factory=dict)
    asks: Dict[float, float] = field(default_factory=dict)
    next_order_id: int = 0

    @staticmethod
    def _extract_ts_ns(entry: tuple, fallback_ns: int) -> int:
        """Extract exchange timestamp in nanoseconds from Kraken book entry third field."""
        if len(entry) >= 3:
            try:
                return int(Decimal(str(entry[2])) * Decimal("1000000000"))
            except (ValueError, InvalidOperation, IndexError):
                pass
        return fallback_ns

    def apply_snapshot(self, data: Dict[str, Any], fallback_ns: int = 1_000_000_000) -> List[Tuple]:
        events: List[Tuple] = []
        self.bids.clear()
        self.asks.clear()
        self.next_order_id = 0

        for entry in data.get("bs", []):
            price = float(entry[0])
            qty = float(entry[1])
            ts_ns = fallback_ns
            if qty > 0:
                self.bids[price] = qty
                events.append((ADD_ORDER_EVENT | BUY_EVENT | EXCH_EVENT | LOCAL_EVENT, ts_ns, ts_ns, price, qty, self.next_order_id, 0, 0.0))
                self.next_order_id += 1

        for entry in data.get("as", []):
            price = float(entry[0])
            qty = float(entry[1])
            ts_ns = fallback_ns
            if qty > 0:
                self.asks[price] = qty
                events.append((ADD_ORDER_EVENT | SELL_EVENT | EXCH_EVENT | LOCAL_EVENT, ts_ns, ts_ns, price, qty, self.next_order_id, 0, 0.0))
                self.next_order_id += 1

        return events

    def apply_update(self, data: Dict[str, Any], fallback_ns: int = 1_000_000_000) -> List[Tuple]:
        events: List[Tuple] = []

        for side_key, side_store, buy_flag in [("b", self.bids, BUY_EVENT), ("a", self.asks, SELL_EVENT)]:
            entries = data.get(side_key, [])
            for entry in entries:
                price = float(entry[0])
                qty = float(entry[1])
                ts_ns = fallback_ns

                if qty > 0:
                    side_store[price] = qty
                    events.append((ADD_ORDER_EVENT | buy_flag | EXCH_EVENT | LOCAL_EVENT, ts_ns, ts_ns, price, qty, self.next_order_id, 0, 0.0))
                    self.next_order_id += 1
                elif price in side_store:
                    del side_store[price]

        return events


def convert_ndjson_to_npz(
    ndjson_path: Path,
    npz_path: Path,
    *,
    start_time_ns: int = 1_000_000_000,
    step_ns: int = 1_000_000,
) -> Path:
    ndjson_path = Path(ndjson_path)
    npz_path = Path(npz_path)
    npz_path.parent.mkdir(parents=True, exist_ok=True)

    book = KrakenOrderBook()
    all_events: List[Tuple] = []
    ticks: int = 0

    with open(ndjson_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            fallback_ns = _message_ts_ns(msg, start_time_ns + ticks * step_ns)
            msg_type = msg.get("type", "update")
            data = msg.get("data", {})

            if msg_type == "snapshot":
                batch = book.apply_snapshot(data, fallback_ns=fallback_ns)
            else:
                batch = book.apply_update(data, fallback_ns=fallback_ns)

            all_events.extend(batch)
            ticks += 1

    if not all_events:
        raise ValueError(f"No events parsed from {ndjson_path}")

    data = np.array(_normalize_replay_clock(all_events, start_time_ns), dtype=event_dtype)
    np.savez_compressed(npz_path, data=data)
    return npz_path


def _message_ts_ns(msg: Dict[str, Any], fallback_ns: int) -> int:
    raw = msg.get("timestamp_utc")
    if not raw:
        return fallback_ns
    try:
        return int(datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp() * 1_000_000_000)
    except ValueError:
        return fallback_ns


def _normalize_replay_clock(events: List[Tuple], start_time_ns: int) -> List[Tuple]:
    events = sorted(events, key=lambda row: (int(row[2]), int(row[1]), int(row[0])))
    if not events:
        return []
    base_local = int(events[0][2])
    normalized: List[Tuple] = []
    last_local = start_time_ns - 1
    for ev, exch_ts, local_ts, px, qty, oid, ival, fval in events:
        local_norm = start_time_ns + max(0, int(local_ts) - base_local)
        if local_norm <= last_local:
            local_norm = last_local + 1
        last_local = local_norm
        exch_norm = start_time_ns + max(0, int(exch_ts) - base_local)
        if exch_norm > local_norm:
            exch_norm = local_norm
        normalized.append((ev, exch_norm, local_norm, px, qty, oid, ival, fval))
    return normalized


def _routing_npz_path(symbol: str) -> Path:
    """Returns the path where asset_class_routing expects the NPZ file for a symbol.

    Routing path: <data_root>/data/replay/hftbacktest/crypto/kraken/<symbol>/
    """
    from crypto_lane.src.ingest.paths import data_root
    safe = symbol.replace("/", "_").replace("-", "_")
    return data_root().parent / "replay" / "hftbacktest" / "crypto" / "kraken" / safe


def cmd_convert_kraken_l3(args: Any) -> int:
    ndjson_path = Path(args.ndjson)
    if not ndjson_path.exists():
        print(f"ERROR: NDJSON file not found: {ndjson_path}", file=sys.stderr)
        return 1

    if args.output:
        npz_path = Path(args.output)
    elif args.routing_symbol:
        routing_dir = _routing_npz_path(args.routing_symbol)
        routing_dir.mkdir(parents=True, exist_ok=True)
        safe = args.routing_symbol.replace("/", "_").replace("-", "_")
        npz_path = routing_dir / f"{safe}_l3.npz"
    else:
        npz_path = ndjson_path.with_suffix(".npz")

    result = convert_ndjson_to_npz(
        ndjson_path,
        npz_path,
        start_time_ns=args.start_time_ns,
        step_ns=args.step_ns,
    )
    print(json.dumps({"input": str(ndjson_path), "output": str(result)}))
    return 0


if __name__ == "__main__":
    import argparse
    import json
    parser = argparse.ArgumentParser(prog="kraken_l3_converter")
    parser.add_argument("ndjson", type=str, help="Path to NDJSON file")
    parser.add_argument("--output", default=None, help="Explicit NPZ output path")
    parser.add_argument("--routing-symbol", default=None, help="Symbol (BTC/USD etc.) — outputs to routing-expected dir")
    parser.add_argument("--start-time-ns", type=int, default=1_000_000_000)
    parser.add_argument("--step-ns", type=int, default=1_000_000)
    args = parser.parse_args()
    raise SystemExit(cmd_convert_kraken_l3(args))
