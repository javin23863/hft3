"""Normalize raw DBN or fixture into lane NDJSON."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from equities_lane.src.ingest.session_io import save_session
from equities_lane.src.ingest.session_meta import build_session_meta
from equities_lane.src.models import DailyBar, SessionTick
from equities_lane.src.types import DegradedModeFlags, SessionMeta


def normalize_fixture(
    fixture_path: Path,
    output_path: Path,
    *,
    degraded: bool = True,
) -> Path:
    """Copy committed fixture to normalized path (idempotent for CI)."""
    meta, ticks = _load_raw_ndjson(fixture_path)
    if degraded and not meta.degraded.degraded_mode:
        meta.degraded.degraded_mode = True
        meta.degraded.assumptions.append("mbp-1 proxy book; no L3 order IDs")
    return save_session(output_path, meta, ticks)


def normalize_dbn(
    raw_path: Path,
    output_path: Path,
    symbol: str,
    session_date: str,
    *,
    schema: str = "mbo",
    daily_bars: list[DailyBar] | None = None,
    daily_bars_path: str | Path | None = None,
) -> Path:
    """DBN to NDJSON with real prior_close and premarket_open (no synthetic gap)."""
    if schema != "mbo":
        raise ValueError(f"L3-only lane: normalize requires schema=mbo, got {schema!r}")
    ticks = _try_decode_dbn(raw_path, schema)
    if not ticks:
        raise ValueError(f"Could not decode DBN: {raw_path}")

    meta = build_session_meta(
        symbol,
        session_date,
        ticks,
        daily_bars_path=str(daily_bars_path) if daily_bars_path else None,
        daily_bars=daily_bars,
        schema=schema,
    )
    return save_session(output_path, meta, ticks)


def _load_raw_ndjson(path: Path) -> tuple[SessionMeta, list[SessionTick]]:
    from equities_lane.src.ingest.session_io import load_session

    return load_session(path)


def _try_decode_dbn(raw_path: Path, schema: str) -> list[SessionTick]:
    try:
        import databento as db
    except ImportError:
        print(f"Warning: databento module not installed, cannot decode {raw_path}", file=sys.stderr)
        return []

    store = db.DBNStore.from_file(str(raw_path))
    if schema == "mbo":
        return _decode_mbo_store(store)
    return _decode_mbp_store(store)


def _decode_mbp_store(store) -> list[SessionTick]:
    ticks: list[SessionTick] = []
    for rec in store:
        ts_ns = _ts_ns(rec)
        bid_px = _px(getattr(rec, "bid_px_00", None))
        ask_px = _px(getattr(rec, "ask_px_00", None))
        bid_sz = int(getattr(rec, "bid_sz_00", 0) or 0)
        ask_sz = int(getattr(rec, "ask_sz_00", 0) or 0)
        trade_px, trade_sz, aggressor = _trade_fields(rec)
        if bid_px <= 0 and trade_px is None:
            continue
        ticks.append(
            SessionTick(
                ts_ns=ts_ns,
                bid_px=bid_px or (trade_px or 0.0),
                bid_sz=bid_sz,
                ask_px=ask_px or ((trade_px or 0.0) + 0.01),
                ask_sz=ask_sz,
                trade_px=trade_px,
                trade_sz=trade_sz,
                aggressor=aggressor,
                event="trade" if trade_px else "quote",
            )
        )
    return ticks


def _decode_mbo_store(store) -> list[SessionTick]:
    """Decode MBO: emit trades + periodic BBO snapshots from dataframe."""
    try:
        df = store.to_df()
    except Exception as exc:
        raise ValueError(f"MBO decode failed (L3-only lane): {exc}") from exc
    if df is None or df.empty:
        raise ValueError("MBO decode returned empty dataframe (L3-only lane)")

    df = df.reset_index()
    ticks: list[SessionTick] = []
    best_bid = 0.0
    best_ask = 0.0
    bid_sz = 0
    ask_sz = 0

    for _, row in df.iterrows():
        ts_ns = int(pd_timestamp_ns(row))
        action = str(row.get("action", "") or "").upper()
        side = str(row.get("side", "") or "").upper()
        price = _px(row.get("price"))
        size = int(row.get("size", 0) or 0)

        if action == "T" or action == "TRADE":
            ticks.append(
                SessionTick(
                    ts_ns=ts_ns,
                    bid_px=best_bid,
                    bid_sz=bid_sz,
                    ask_px=best_ask if best_ask > 0 else price + 0.01,
                    ask_sz=ask_sz,
                    trade_px=price,
                    trade_sz=size,
                    aggressor="buy" if side == "B" else ("sell" if side == "A" else None),
                    event="trade",
                )
            )
            continue

        if side == "B" and price > 0:
            best_bid = price
            bid_sz = size
        elif side == "A" and price > 0:
            best_ask = price
            ask_sz = size

        if best_bid > 0 or best_ask > 0:
            ticks.append(
                SessionTick(
                    ts_ns=ts_ns,
                    bid_px=best_bid,
                    bid_sz=bid_sz,
                    ask_px=best_ask if best_ask > 0 else best_bid + 0.01,
                    ask_sz=ask_sz,
                    event="quote",
                )
            )
    return ticks


def pd_timestamp_ns(row) -> int:
    for key in ("ts_event", "datetime", "ts_recv"):
        if key in row.index:
            val = row[key]
            if hasattr(val, "value"):
                return int(val.value)
            return int(val)
    return 0


def _ts_ns(rec) -> int:
    ts = getattr(rec, "ts_event", None)
    if ts is not None:
        if hasattr(ts, "value"):
            return int(ts.value)
        return int(ts)
    return 0


def _px(val) -> float:
    if val is None or val == 0:
        return 0.0
    v = float(val)
    if v > 1e6:
        return v / 1e9
    return v


def _trade_fields(rec) -> tuple[float | None, int | None, str | None]:
    if hasattr(rec, "price") and getattr(rec, "price", 0):
        side = getattr(rec, "side", None)
        ag = None
        if side is not None:
            s = str(side).upper()
            ag = "buy" if s in ("B", "BUY", "A") else "sell"
        return _px(rec.price), int(getattr(rec, "size", 0) or 0), ag
    return None, None, None


def write_meta_sidecar(path: Path, meta: SessionMeta) -> None:
    path.write_text(json.dumps(meta.to_dict(), indent=2), encoding="utf-8")
