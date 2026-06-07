"""Derive VIX sensor levels from OPRA VIX.OPT cmbp-1 release slots."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from data_system.src.data_roots import paid_data_root
from data_system.src.event_data_resolver import (
    VIX_OPT_SYMBOL,
    resolve_sensor_for_event,
    resolve_vix_raw_for_event,
    sensor_filename,
    sensor_search_dirs,
)
from economic_event_universe.snapshot_offsets import default_snapshot_offsets_sec
from mbo_release_lane.storage import raw_dbn_path, release_slot_dir, validate_dbn_readable, write_json

logger = logging.getLogger(__name__)

VIX_ATM_STRIKE_SENSOR = "VIX_ATM_STRIKE"

_STRIKE_RE = re.compile(r"([CP])(\d{8})$")
_FIXED_PRICE_SCALE = 1_000_000_000.0


def _parse_strike_cp(symbol: str) -> Optional[Tuple[float, str]]:
    """Parse OPRA VIX option symbol tail for strike and call/put."""
    sym = str(symbol or "").strip().upper()
    m = _STRIKE_RE.search(sym.replace(" ", ""))
    if not m:
        return None
    cp, strike_raw = m.group(1), m.group(2)
    strike = int(strike_raw) / 1000.0
    return strike, cp


def _scale_price(px: Any) -> Optional[float]:
    try:
        v = float(px) if px is not None else float("nan")
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    if v == 0:
        return 0.0
    # Databento DBN fixed-point prices (OPRA cbbo/cmbp-1) are scaled by 1e9.
    if abs(v) >= 1_000_000:
        v /= _FIXED_PRICE_SCALE
    return v


def _px_mid(bid: Any, ask: Any) -> Optional[float]:
    b = _scale_price(bid)
    a = _scale_price(ask)
    if b is None and a is None:
        return None
    if b is None:
        return a
    if a is None:
        return b
    if b <= 0 and a <= 0:
        return None
    return (b + a) / 2.0


def _build_instrument_symbol_map(store: Any) -> Dict[int, str]:
    """Map databento instrument_id -> OPRA symbol from DBN metadata.mappings."""
    meta = getattr(store, "metadata", None)
    mappings = getattr(meta, "mappings", None) if meta is not None else None
    if not mappings:
        return {}
    out: Dict[int, str] = {}
    for opra_sym, entries in mappings.items():
        if not isinstance(entries, list):
            entries = [entries]
        for entry in entries:
            if isinstance(entry, dict):
                iid_raw = entry.get("symbol") or entry.get("instrument_id")
            else:
                iid_raw = getattr(entry, "symbol", None) or getattr(entry, "instrument_id", None)
            if iid_raw is None:
                continue
            try:
                out[int(iid_raw)] = str(opra_sym).strip().upper()
            except (TypeError, ValueError):
                continue
    return out


def _record_fields(rec: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in (
        "ts_event",
        "bid_px",
        "ask_px",
        "bid_px_00",
        "ask_px_00",
        "symbol",
        "instrument_id",
        "pretty_symbol",
    ):
        if hasattr(rec, key):
            out[key] = getattr(rec, key)
    if hasattr(rec, "levels") and rec.levels:
        lvl = rec.levels[0]
        out["bid_px"] = getattr(lvl, "bid_px", out.get("bid_px"))
        out["ask_px"] = getattr(lvl, "ask_px", out.get("ask_px"))
    sym = out.get("pretty_symbol") or out.get("symbol") or out.get("instrument_id")
    out["symbol_str"] = str(sym) if sym is not None else ""
    return out


def _load_cmbp_quotes(raw_path: Path) -> List[Tuple[int, str, Optional[float], Optional[float]]]:
    import databento as db

    store = db.DBNStore.from_file(str(raw_path))
    iid_map = _build_instrument_symbol_map(store)
    rows: List[Tuple[int, str, Optional[float], Optional[float]]] = []
    for rec in store:
        fields = _record_fields(rec)
        ts = fields.get("ts_event")
        if ts is None:
            continue
        ts_ns = int(ts)
        sym = fields["symbol_str"]
        iid = fields.get("instrument_id")
        if iid is not None:
            mapped = iid_map.get(int(iid))
            if mapped:
                sym = mapped
        bid = fields.get("bid_px") or fields.get("bid_px_00")
        ask = fields.get("ask_px") or fields.get("ask_px_00")
        rows.append((ts_ns, sym, _px_mid(bid, ask), None))
    return rows


def _atm_vix_strike(quotes_by_strike: Dict[float, Dict[str, float]]) -> Optional[float]:
    """ATM strike (not VIX index) from call/put parity on OPRA VIX.OPT quotes."""
    best_strike: Optional[float] = None
    best_score = float("inf")
    for strike, sides in quotes_by_strike.items():
        if "C" not in sides and "P" not in sides:
            continue
        call_mid = sides.get("C")
        put_mid = sides.get("P")
        if call_mid is None and put_mid is None:
            continue
        if call_mid is not None and put_mid is not None:
            score = abs(call_mid - put_mid)
        else:
            score = 999.0
        if score < best_score:
            best_score = score
            best_strike = strike
    return best_strike


def _quotes_at_ts(
    quotes: List[Tuple[int, str, Optional[float], Optional[float]]],
    snap_ns: int,
) -> Dict[float, Dict[str, float]]:
    """Latest quote per strike/cp at or before snap_ns."""
    by_key: Dict[Tuple[float, str], Tuple[int, float]] = {}
    for ts_ns, sym, mid, _ in quotes:
        if ts_ns > snap_ns or mid is None:
            continue
        parsed = _parse_strike_cp(sym)
        if not parsed:
            continue
        strike, cp = parsed
        key = (strike, cp)
        prev = by_key.get(key)
        if prev is None or ts_ns >= prev[0]:
            by_key[key] = (ts_ns, mid)

    by_strike: Dict[float, Dict[str, float]] = {}
    for (strike, cp), (_, mid) in by_key.items():
        by_strike.setdefault(strike, {})[cp] = mid
    return by_strike


def derive_sensors_from_vix_raw(
    raw_path: Path,
    event_id: str,
    anchor_ns: int,
    *,
    offsets_sec: Tuple[int, ...] | None = None,
) -> pd.DataFrame:
    offsets = offsets_sec or default_snapshot_offsets_sec()
    quotes = _load_cmbp_quotes(raw_path)
    rows: List[dict[str, Any]] = []
    for off in offsets:
        snap_ns = anchor_ns + off * 1_000_000_000
        by_strike = _quotes_at_ts(quotes, snap_ns)
        strike = _atm_vix_strike(by_strike)
        rows.append(
            {
                "event_id": event_id,
                "offset_sec": int(off),
                "sensor": VIX_ATM_STRIKE_SENSOR,
                "level": float(strike) if strike is not None else float("nan"),
                "ts_ns": int(snap_ns),
            }
        )
    return pd.DataFrame(rows)


def _sensor_df_has_finite_level(sensor_df: pd.DataFrame) -> bool:
    if sensor_df.empty or "level" not in sensor_df.columns:
        return False
    levels = sensor_df["level"]
    return bool(levels.notna().any() and levels.map(lambda v: v == v).any())


def has_deriveable_vix_raw(repo_root: Path, event_id: str) -> bool:
    from mbo_release_lane.storage import dbn_has_quote_records

    raw, ok = resolve_vix_raw_for_event(repo_root, event_id)
    return ok and raw.is_file() and raw.stat().st_size > 0 and dbn_has_quote_records(raw)


def derive_sensor_parquet_for_event(
    repo_root: Path,
    event_id: str,
    *,
    anchor_ns: int | None = None,
    output_dir: Path | None = None,
) -> Path | None:
    if not has_deriveable_vix_raw(repo_root, event_id):
        logger.warning("No deriveable VIX raw for %s", event_id)
        return None

    _, present = resolve_sensor_for_event(repo_root, event_id)
    if present:
        path, _ = resolve_sensor_for_event(repo_root, event_id)
        return path

    raw, _ = resolve_vix_raw_for_event(repo_root, event_id)
    if anchor_ns is None:
        from data_system.src.events_parser import load_and_parse_events

        csv = repo_root / "packages" / "data_system" / "config" / "events.csv"
        if not csv.is_file():
            csv = repo_root / "data_system" / "config" / "events.csv"
        df = load_and_parse_events(str(csv))
        row = df[df["event_id"] == event_id]
        if row.empty:
            logger.warning("event_id not in events.csv: %s", event_id)
            return None
        anchor_ns = int(pd.Timestamp(row.iloc[0]["anchor_utc"]).tz_convert("UTC").value)

    sensor_df = derive_sensors_from_vix_raw(raw, event_id, anchor_ns)
    if sensor_df.empty or not _sensor_df_has_finite_level(sensor_df):
        logger.warning("All-NaN sensor levels for %s — not writing parquet", event_id)
        return None

    paid = paid_data_root(repo_root)
    default_dir = paid / "sensors" if paid != (repo_root / "data").resolve() else repo_root / "data" / "sensors"
    out_dir = output_dir or default_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / sensor_filename(event_id)
    sensor_df.to_parquet(target, index=False)

    slot = release_slot_dir(repo_root, event_id, VIX_OPT_SYMBOL)
    write_json(
        slot / "sensor_derived.json",
        {
            "event_id": event_id,
            "sensor_path": str(target),
            "derived_at_utc": datetime.now(timezone.utc).isoformat(),
            "algorithm": "atm_strike_proxy",
            "sensor_name": VIX_ATM_STRIKE_SENSOR,
            "level_semantics": "atm_strike_not_vix_index",
        },
    )
    return target


def write_minimal_vix_release_manifest(
    repo_root: Path,
    event_id: str,
    *,
    window_start: str,
    window_end: str,
    event_count: int = 1,
) -> None:
    from mbo_release_lane.storage import build_release_event_path, write_json as wj

    slot = release_slot_dir(repo_root, event_id, VIX_OPT_SYMBOL)
    raw = raw_dbn_path(slot)
    if raw.is_file() and raw.stat().st_size > 0:
        validation = "valid" if validate_dbn_readable(raw) else "invalid"
    else:
        validation = "unknown" if event_count > 0 else "empty"
    payload = build_release_event_path(
        release_id=event_id,
        release_name=event_id,
        scheduled_release_timestamp=window_start,
        actual_release_timestamp=window_start,
        symbol=VIX_OPT_SYMBOL,
        venue="OPRA",
        window_start=window_start,
        window_end=window_end,
        events_ref=str(raw_dbn_path(slot)),  # noqa: same module
        event_count=event_count,
        first_sequence=None,
        last_sequence=None,
        sequence_gap_count=0,
        source_vendor="databento",
        dataset_id="OPRA.PILLAR",
        validation_status=validation,
    )
    wj(slot / "release_event_path.json", payload)

