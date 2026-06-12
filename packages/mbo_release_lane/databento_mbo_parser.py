"""Parse Databento MBO DBN into canonical normalized events."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator

from mbo_release_lane.constants import DEFAULT_DATASET_ID, PARSER_VERSION, SOURCE_VENDOR


def _decimal_str(value: Any) -> str:
    if value is None:
        return "0"
    try:
        if isinstance(value, Decimal):
            return str(value)
        # Databento fixed-point prices are often int with implicit scale
        if isinstance(value, int):
            return str(Decimal(value) / Decimal("1e9"))
        return str(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return str(value)


def _normalize_action(raw: Any) -> str:
    s = str(raw).strip().lower()
    mapping = {
        "a": "add",
        "add": "add",
        "c": "cancel",
        "cancel": "cancel",
        "m": "modify",
        "modify": "modify",
        "t": "trade",
        "trade": "trade",
        "f": "fill",
        "fill": "fill",
        "r": "delete",
        "clear": "delete",
        "delete": "delete",
    }
    out = mapping.get(s, s)
    if out == "fill":
        return "fill"
    return out


def _normalize_side(raw: Any) -> str:
    s = str(raw).strip().upper()
    if s in ("A", "ASK", "S", "SELL"):
        return "A"
    if s in ("B", "BID", "BUY"):
        return "B"
    return s or "N"


def _record_to_event(
    rec: Any,
    *,
    release_id: str,
    symbol: str,
    dataset_id: str,
) -> dict[str, Any]:
    ts_event = int(getattr(rec, "ts_event", 0) or 0)
    ts_recv = int(getattr(rec, "ts_recv", 0) or ts_event)
    seq = int(getattr(rec, "sequence", 0) or 0)
    oid = int(getattr(rec, "order_id", 0) or 0)
    size = int(getattr(rec, "size", 0) or 0)
    price_raw = getattr(rec, "price", 0)
    action = _normalize_action(getattr(rec, "action", ""))
    side = _normalize_side(getattr(rec, "side", ""))
    instrument_id = int(getattr(rec, "instrument_id", 0) or 0)

    raw_msg = {}
    for attr in ("rtype", "publisher_id", "flags", "channel_id", "action", "side"):
        if hasattr(rec, attr):
            raw_msg[attr] = getattr(rec, attr)

    return {
        "release_id": release_id,
        "symbol": symbol,
        "venue": "GLBX",
        "instrument_id": instrument_id,
        "sequence_number": seq,
        "exchange_timestamp": ts_event,
        "receive_timestamp": ts_recv,
        "event_type": action,
        "order_id": oid,
        "side": side,
        "price": _decimal_str(price_raw),
        "size": str(size),
        "remaining_size": str(getattr(rec, "size", size) or size),
        "trade_id": str(getattr(rec, "trade_id", "") or ""),
        "match_id": str(getattr(rec, "match_id", "") or ""),
        "action": action,
        "raw_message": json.dumps(raw_msg, default=str),
        "source_vendor": SOURCE_VENDOR,
        "dataset_id": dataset_id,
        "parser_version": PARSER_VERSION,
    }


def iter_databento_mbo_events(
    path: Path,
    *,
    release_id: str,
    symbol: str,
    dataset_id: str = DEFAULT_DATASET_ID,
) -> Iterator[dict[str, Any]]:
    try:
        import databento as db
    except ImportError as exc:
        raise RuntimeError("databento required to parse MBO DBN") from exc
    store = db.DBNStore.from_file(str(path))
    for rec in store:
        yield _record_to_event(
            rec,
            release_id=release_id,
            symbol=symbol,
            dataset_id=dataset_id,
        )


def parse_databento_mbo_file(
    path: Path,
    *,
    release_id: str,
    symbol: str,
    dataset_id: str = DEFAULT_DATASET_ID,
) -> list[dict[str, Any]]:
    events = list(
        iter_databento_mbo_events(
            path,
            release_id=release_id,
            symbol=symbol,
            dataset_id=dataset_id,
        )
    )
    events.sort(key=lambda e: (e["sequence_number"], e["exchange_timestamp"]))
    return events
