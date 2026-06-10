"""NDJSON → NPZ converter for Bitfinex R0 raw order book (true MBO)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
from hftbacktest.types import (
    ADD_ORDER_EVENT,
    BUY_EVENT,
    CANCEL_ORDER_EVENT,
    EXCH_EVENT,
    LOCAL_EVENT,
    MODIFY_ORDER_EVENT,
    SELL_EVENT,
    event_dtype,
)

REPLAY_META_VERSION = 1
MBO_DATA_CLASS = "L3_MBO"
MBO_EXEC_CLASS = "L3_VALIDATED"
SOURCE_FEED = "bitfinex_ws_r0"

BITFINEX_TO_ROUTING = {
    "tBTCUSD": "BTC_USD",
    "tETHUSD": "ETH_USD",
    "tSOLUSD": "SOL_USD",
}


def _side_flag(amount: float) -> int:
    return BUY_EVENT if amount > 0 else SELL_EVENT


def _parse_events_from_ndjson(
    ndjson_path: Path,
    start_time_ns: int,
    open_orders: Dict[int, int] | None = None,
) -> List[Tuple]:
    events: List[Tuple] = []
    if open_orders is None:
        open_orders = {}
    event_counter = 0

    def _next_ts() -> int:
        nonlocal event_counter
        event_counter += 1
        return start_time_ns + event_counter

    with open(ndjson_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)

            if msg.get("type") == "snapshot":
                if open_orders:
                    for oid, side in list(open_orders.items()):
                        ts_ns = _next_ts()
                        events.append(
                            (CANCEL_ORDER_EVENT | side | EXCH_EVENT | LOCAL_EVENT, ts_ns, ts_ns, 0.0, 0.0, oid, 0, 0.0)
                        )
                    open_orders.clear()
                for row in msg.get("orders") or []:
                    if not isinstance(row, list) or len(row) != 3:
                        continue
                    oid, price, amount = int(row[0]), float(row[1]), float(row[2])
                    if price == 0 or amount == 0:
                        continue
                    if oid in open_orders:
                        continue
                    qty = abs(amount)
                    side = _side_flag(amount)
                    open_orders[oid] = side
                    ts_ns = _next_ts()
                    events.append(
                        (ADD_ORDER_EVENT | side | EXCH_EVENT | LOCAL_EVENT, ts_ns, ts_ns, price, qty, oid, 0, 0.0)
                    )
                continue

            if msg.get("type") != "update":
                continue

            oid = int(msg["order_id"])
            price = float(msg["price"])
            amount = float(msg["amount"])
            ts_ns = _next_ts()

            if price == 0:
                if oid in open_orders:
                    side = open_orders.pop(oid)
                    events.append(
                        (CANCEL_ORDER_EVENT | side | EXCH_EVENT | LOCAL_EVENT, ts_ns, ts_ns, 0.0, 0.0, oid, 0, 0.0)
                    )
                continue

            qty = abs(amount)
            side = _side_flag(amount)
            if oid not in open_orders:
                open_orders[oid] = side
                events.append(
                    (ADD_ORDER_EVENT | side | EXCH_EVENT | LOCAL_EVENT, ts_ns, ts_ns, price, qty, oid, 0, 0.0)
                )
            else:
                open_orders[oid] = side
                events.append(
                    (
                        MODIFY_ORDER_EVENT | side | EXCH_EVENT | LOCAL_EVENT,
                        ts_ns,
                        ts_ns,
                        price,
                        qty,
                        oid,
                        0,
                        0.0,
                    )
                )

    return events


def _normalize_replay_clock(events: List[Tuple], start_time_ns: int) -> List[Tuple]:
    # Cancels must precede re-adds at equal ts; unique ts from _next_ts() guarantees safe ordering.
    events = sorted(events, key=lambda row: (int(row[2]), int(row[1]), int(row[0])))
    if not events:
        return []
    for i in range(len(events) - 1):
        if int(events[i][2]) == int(events[i + 1][2]):
            raise ValueError(f"Duplicate local_ts at indices {i} and {i + 1}: ts={events[i][2]}")
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


def convert_ndjson_to_npz(
    ndjson_path: Path | list[Path],
    npz_path: Path,
    *,
    start_time_ns: int = 1_000_000_000,
) -> Path:
    paths = [Path(ndjson_path)] if isinstance(ndjson_path, (str, Path)) else [Path(p) for p in ndjson_path]
    npz_path = Path(npz_path)
    npz_path.parent.mkdir(parents=True, exist_ok=True)

    events: List[Tuple] = []
    clock = start_time_ns
    shared_open_orders: Dict[int, int] = {}
    for path in paths:
        # clock advances _next_ts() offsets per-file; normalization re-bases all events globally.
        chunk = _parse_events_from_ndjson(path, clock, open_orders=shared_open_orders)
        if not chunk:
            continue
        events.extend(chunk)
        clock = int(chunk[-1][2]) + 1

    if not events:
        label = paths[0] if len(paths) == 1 else f"{len(paths)} files"
        raise ValueError(f"No MBO events parsed from {label}")

    data = np.array(_normalize_replay_clock(events, start_time_ns), dtype=event_dtype)
    np.savez_compressed(npz_path, data=data)
    return npz_path


def write_replay_meta(
    npz_path: Path,
    *,
    symbol: str,
    ndjson_source: Path,
    event_count: int,
) -> Path:
    meta_path = npz_path.with_name(npz_path.stem + ".meta.json")
    meta_path.write_text(
        json.dumps(
            {
                "replay_meta_version": REPLAY_META_VERSION,
                "data_class": MBO_DATA_CLASS,
                "source_feed": SOURCE_FEED,
                "execution_classification": MBO_EXEC_CLASS,
                "symbol": symbol,
                "ndjson_source": str(ndjson_source),
                "event_count": event_count,
                "honest_label": "Bitfinex R0 raw book — order-level MBO with native order IDs (public feed)",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return meta_path


def convert_ndjson_to_npz_with_meta(
    ndjson_path: Path | list[Path],
    npz_path: Path,
    *,
    symbol: str = "",
    start_time_ns: int = 1_000_000_000,
) -> Path:
    paths = [Path(ndjson_path)] if isinstance(ndjson_path, (str, Path)) else [Path(p) for p in ndjson_path]
    result = convert_ndjson_to_npz(paths, npz_path, start_time_ns=start_time_ns)
    event_count = int(len(np.load(result)["data"]))
    source_label = str(paths[0]) if len(paths) == 1 else f"{len(paths)} files under {paths[0].parent}"
    write_replay_meta(result, symbol=symbol, ndjson_source=Path(source_label), event_count=event_count)
    return result


def ndjson_paths_for_symbol(raw_dir: Path, bitfinex_symbol: str) -> list[Path]:
    safe = bitfinex_symbol.lstrip("t")
    return sorted(raw_dir.glob(f"bitfinex_mbo_{safe}_*.ndjson"), key=lambda p: p.stat().st_mtime)


def _routing_npz_path(routing_symbol: str) -> Path:
    from crypto_lane.src.ingest.paths import data_root

    safe = routing_symbol.replace("/", "_").replace("-", "_")
    return data_root().parent / "replay" / "hftbacktest" / "crypto" / "bitfinex" / safe


def cmd_convert_bitfinex_mbo(args: Any) -> int:
    raw_dir = Path(args.raw_dir) if getattr(args, "raw_dir", None) else None
    if getattr(args, "merge_all", False) and raw_dir:
        bfx_sym = args.bitfinex_symbol or ""
        if not bfx_sym and args.routing_symbol:
            for k, v in BITFINEX_TO_ROUTING.items():
                if v == args.routing_symbol:
                    bfx_sym = k
                    break
        if not bfx_sym:
            print("ERROR: --merge-all requires --bitfinex-symbol or --routing-symbol", file=sys.stderr)
            return 1
        ndjson_paths = ndjson_paths_for_symbol(raw_dir, bfx_sym)
        if not ndjson_paths:
            print(f"ERROR: no NDJSON under {raw_dir} for {bfx_sym}", file=sys.stderr)
            return 1
    else:
        if not args.ndjson:
            print("ERROR: ndjson path required unless --merge-all is set", file=sys.stderr)
            return 1
        ndjson_path = Path(args.ndjson)
        if not ndjson_path.exists():
            print(f"ERROR: NDJSON file not found: {ndjson_path}", file=sys.stderr)
            return 1
        ndjson_paths = [ndjson_path]

    routing_symbol = args.routing_symbol or ""
    if not routing_symbol:
        first_line = ndjson_paths[0].read_text(encoding="utf-8").splitlines()[0]
        bfx_sym = json.loads(first_line).get("symbol", "")
        routing_symbol = BITFINEX_TO_ROUTING.get(str(bfx_sym), str(bfx_sym).lstrip("t"))

    if args.output:
        npz_path = Path(args.output)
    else:
        routing_dir = _routing_npz_path(routing_symbol)
        routing_dir.mkdir(parents=True, exist_ok=True)
        safe = routing_symbol.replace("/", "_").replace("-", "_")
        npz_path = routing_dir / f"{safe}_mbo.npz"

    result = convert_ndjson_to_npz_with_meta(
        ndjson_paths,
        npz_path,
        symbol=routing_symbol,
        start_time_ns=args.start_time_ns,
    )
    print(
        json.dumps(
            {
                "inputs": [str(p) for p in ndjson_paths],
                "output": str(result),
                "events": int(len(np.load(result)["data"])),
            }
        )
    )
    return 0
