"""NDJSON → NPZ converter for Binance L2 depth recordings.

Handles the Binance diff depth protocol:
  1. (optional) REST snapshot provides initial book + lastUpdateId
  2. Diff events with U/u sequence numbers are buffered until in sync
  3. Real exchange timestamps (E field) used when available
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
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
class BinanceOrderBook:
    bids: Dict[float, float] = field(default_factory=dict)
    asks: Dict[float, float] = field(default_factory=dict)

    def load_snapshot(self, data: Dict[str, Any], ts_ns: int) -> List[Tuple]:
        events: List[Tuple] = []
        self.bids.clear()
        self.asks.clear()
        oid = 0

        for price_str, qty_str in data.get("bids", []):
            price = float(price_str)
            qty = float(qty_str)
            if qty > 0:
                self.bids[price] = qty
                events.append((ADD_ORDER_EVENT | BUY_EVENT | EXCH_EVENT | LOCAL_EVENT, ts_ns, ts_ns, price, qty, oid, 0, 0.0))
                oid += 1

        for price_str, qty_str in data.get("asks", []):
            price = float(price_str)
            qty = float(qty_str)
            if qty > 0:
                self.asks[price] = qty
                events.append((ADD_ORDER_EVENT | SELL_EVENT | EXCH_EVENT | LOCAL_EVENT, ts_ns, ts_ns, price, qty, oid, 0, 0.0))
                oid += 1

        return events

    def apply_depth_update(self, data: Dict[str, Any], ts_ns: int) -> List[Tuple]:
        events: List[Tuple] = []
        oid = max(len(self.bids), len(self.asks)) + 1

        for side_key, side_store, buy_flag in [
            ("b", self.bids, BUY_EVENT),
            ("a", self.asks, SELL_EVENT),
        ]:
            entries = data.get(side_key, [])
            for i, entry in enumerate(entries):
                price = float(entry[0])
                qty = float(entry[1])
                oid += i

                if qty > 0:
                    side_store[price] = qty
                    events.append((ADD_ORDER_EVENT | buy_flag | EXCH_EVENT | LOCAL_EVENT, ts_ns, ts_ns, price, qty, oid, 0, 0.0))
                elif price in side_store:
                    del side_store[price]
                    events.append((CANCEL_ORDER_EVENT | buy_flag | EXCH_EVENT | LOCAL_EVENT, ts_ns, ts_ns, price, 0.0, oid, 0, 0.0))

        return events


def _fetch_snapshot(symbol: str, limit: int = 100) -> Optional[Dict[str, Any]]:
    """Fetch a REST order book snapshot from Binance."""
    import urllib.request
    url = f"https://api.binance.com/api/v3/depth?symbol={symbol.upper()}&limit={limit}"
    try:
        resp = urllib.request.urlopen(url, timeout=15)
        return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def convert_ndjson_to_npz(
    ndjson_path: Path,
    npz_path: Path,
    *,
    snapshot_path: Optional[Path] = None,
    symbol: Optional[str] = None,
    start_time_ns: int = 1_000_000_000,
    step_ns: int = 1_000_000,
) -> Path:
    ndjson_path = Path(ndjson_path)
    npz_path = Path(npz_path)
    npz_path.parent.mkdir(parents=True, exist_ok=True)

    book = BinanceOrderBook()
    all_events: List[Tuple] = []
    last_update_id: int = 0
    in_sync = False
    pending: List[Dict[str, Any]] = []

    if snapshot_path:
        snap = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    elif symbol:
        snap = _fetch_snapshot(symbol)
    else:
        snap = None

    if snap:
        last_update_id = int(snap.get("lastUpdateId", 0))
        snap_ts = int(snap.get("E", 0)) * 1_000_000 or start_time_ns
        all_events.extend(book.load_snapshot(snap, snap_ts))

    with open(ndjson_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)

            if msg.get("e") != "depthUpdate":
                continue

            first_id = int(msg.get("U", 0))
            final_id = int(msg.get("u", 0))
            event_time_ms = int(msg.get("E", 0))

            if event_time_ms > 0:
                ts_ns = event_time_ms * 1_000_000
            else:
                ts_ns = start_time_ns

            if snap:
                if not in_sync:
                    if first_id <= last_update_id and final_id >= last_update_id + 1:
                        pending.append(msg)
                        for p in pending:
                            all_events.extend(book.apply_depth_update(p, ts_ns))
                        pending.clear()
                        in_sync = True
                    elif final_id <= last_update_id:
                        continue
                    else:
                        pending.append(msg)
                else:
                    all_events.extend(book.apply_depth_update(msg, ts_ns))
            else:
                all_events.extend(book.apply_depth_update(msg, ts_ns))

    if not all_events:
        raise ValueError(f"No events parsed from {ndjson_path}")

    data = np.array(_normalize_replay_clock(all_events, start_time_ns), dtype=event_dtype)
    np.savez_compressed(npz_path, data=data)
    return npz_path


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
    from crypto_lane.src.ingest.paths import data_root
    return data_root().parent / "replay" / "hftbacktest" / "crypto" / "binance" / symbol.lower()


def cmd_convert_binance_l2(args: Any) -> int:
    ndjson_path = Path(args.ndjson)
    if not ndjson_path.exists():
        print(f"ERROR: NDJSON file not found: {ndjson_path}", file=sys.stderr)
        return 1

    if args.output:
        npz_path = Path(args.output)
    elif args.routing_symbol:
        routing_dir = _routing_npz_path(args.routing_symbol)
        routing_dir.mkdir(parents=True, exist_ok=True)
        npz_path = routing_dir / f"{args.routing_symbol.lower()}_l2.npz"
    else:
        npz_path = ndjson_path.with_suffix(".npz")

    snapshot_path = Path(args.snapshot) if args.snapshot else None
    should_fetch = (
        not snapshot_path
        and bool(args.routing_symbol)
        and not args.no_fetch
    )
    if should_fetch:
        print(f"Fetching REST snapshot for {args.routing_symbol}...", file=sys.stderr)

    result = convert_ndjson_to_npz(
        ndjson_path,
        npz_path,
        snapshot_path=snapshot_path,
        symbol=args.routing_symbol if should_fetch else None,
        start_time_ns=args.start_time_ns,
        step_ns=args.step_ns,
    )
    print(json.dumps({"input": str(ndjson_path), "output": str(result)}))
    return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(prog="binance_l2_converter")
    parser.add_argument("ndjson", type=str)
    parser.add_argument("--output", default=None)
    parser.add_argument("--routing-symbol", default=None)
    parser.add_argument("--snapshot", default=None, help="Path to REST snapshot JSON (fetched live if not provided)")
    parser.add_argument("--no-fetch", action="store_true", help="Skip live snapshot fetch even with --routing-symbol")
    parser.add_argument("--start-time-ns", type=int, default=1_000_000_000)
    parser.add_argument("--step-ns", type=int, default=1_000_000)
    args = parser.parse_args()
    raise SystemExit(cmd_convert_binance_l2(args))
