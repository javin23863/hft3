"""NDJSON → NPZ converter for Coinbase Exchange ``full`` channel (true MBO)."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
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
SOURCE_FEED = "coinbase_exchange_full"


def _order_id_int(order_id: str) -> int:
    digest = hashlib.sha256(order_id.encode("utf-8")).hexdigest()
    return int(digest[:15], 16)


def _parse_time_ns(msg: Dict[str, Any], fallback_ns: int) -> int:
    raw = msg.get("time")
    if not raw:
        return fallback_ns
    try:
        return int(datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp() * 1_000_000_000)
    except ValueError:
        return fallback_ns


def _side_flag(side: str) -> int:
    return BUY_EVENT if str(side).lower() == "buy" else SELL_EVENT


def convert_ndjson_to_npz(
    ndjson_path: Path,
    npz_path: Path,
    *,
    start_time_ns: int = 1_000_000_000,
) -> Path:
    ndjson_path = Path(ndjson_path)
    npz_path = Path(npz_path)
    npz_path.parent.mkdir(parents=True, exist_ok=True)

    events: List[Tuple] = []
    seen_open: set[str] = set()

    with open(ndjson_path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            msg_type = msg.get("type")
            if msg_type not in {"open", "change", "done"}:
                continue

            ts_ns = _parse_time_ns(msg, start_time_ns + idx)
            order_id = str(msg.get("order_id") or "")
            if not order_id:
                continue
            oid = _order_id_int(order_id)
            side = _side_flag(msg.get("side", "buy"))
            price = float(msg.get("price") or 0.0)
            if price <= 0:
                continue

            if msg_type == "open":
                qty = float(msg.get("remaining_size") or msg.get("size") or 0.0)
                if qty <= 0:
                    continue
                seen_open.add(order_id)
                events.append(
                    (ADD_ORDER_EVENT | side | EXCH_EVENT | LOCAL_EVENT, ts_ns, ts_ns, price, qty, oid, 0, 0.0)
                )
            elif msg_type == "change":
                new_size = float(msg.get("new_size") or 0.0)
                if order_id not in seen_open:
                    continue
                if new_size <= 0:
                    events.append(
                        (CANCEL_ORDER_EVENT | side | EXCH_EVENT | LOCAL_EVENT, ts_ns, ts_ns, price, 0.0, oid, 0, 0.0)
                    )
                else:
                    events.append(
                        (
                            MODIFY_ORDER_EVENT | side | EXCH_EVENT | LOCAL_EVENT,
                            ts_ns,
                            ts_ns,
                            price,
                            new_size,
                            oid,
                            0,
                            0.0,
                        )
                    )
            elif msg_type == "done":
                reason = str(msg.get("reason") or "")
                if order_id in seen_open and reason in {"canceled", "cancelled"}:
                    events.append(
                        (CANCEL_ORDER_EVENT | side | EXCH_EVENT | LOCAL_EVENT, ts_ns, ts_ns, price, 0.0, oid, 0, 0.0)
                    )
                    seen_open.discard(order_id)

    if not events:
        raise ValueError(f"No MBO events parsed from {ndjson_path}")

    data = np.array(_normalize_replay_clock(events, start_time_ns), dtype=event_dtype)
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
                "honest_label": "Coinbase Exchange full channel — order-level MBO with native order IDs",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return meta_path


def convert_ndjson_to_npz_with_meta(
    ndjson_path: Path,
    npz_path: Path,
    *,
    symbol: str = "",
    start_time_ns: int = 1_000_000_000,
) -> Path:
    result = convert_ndjson_to_npz(ndjson_path, npz_path, start_time_ns=start_time_ns)
    event_count = int(len(np.load(result)["data"]))
    write_replay_meta(result, symbol=symbol, ndjson_source=ndjson_path, event_count=event_count)
    return result


def _routing_npz_path(symbol: str) -> Path:
    from crypto_lane.src.ingest.paths import data_root

    safe = symbol.replace("/", "_").replace("-", "_")
    return data_root().parent / "replay" / "hftbacktest" / "crypto" / "coinbase" / safe


def cmd_convert_coinbase_mbo(args: Any) -> int:
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
        npz_path = routing_dir / f"{safe}_mbo.npz"
    else:
        npz_path = ndjson_path.with_suffix(".npz")

    result = convert_ndjson_to_npz_with_meta(
        ndjson_path,
        npz_path,
        symbol=args.routing_symbol or "",
        start_time_ns=args.start_time_ns,
    )
    print(json.dumps({"input": str(ndjson_path), "output": str(result)}))
    return 0
