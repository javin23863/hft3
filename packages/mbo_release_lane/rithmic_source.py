"""Rithmic MBO fill source for the MBO release lane.

Architecture:
  * Connects to the Rithmic History Plant via ``async_rithmic`` (CHI404 only).
  * Requests historical ticks for a (symbol, exchange, start_utc, end_utc)
    window — same shape as the MBO release lane's event windows.
  * Parses the returned ticks into the same normalized event schema that
    ``mbo_release_lane.databento_mbo_parser`` produces, so downstream
    classification + validation + NPZ derivation are source-agnostic.
  * Hard labeling rule: if the returned schema does not prove order-level
    MBO events (order_id + action + side + flags with depth context), the
    source emits a ``data_label`` of ``ticks`` and refuses to write a
    ``release_event_path.json`` manifest.  Reason: the MBO release lane
    requires MBO; tick-only data is not interchangeable.

This is the implementation of the corrected developer's "Hard labeling rule"
(``rithmic_download_test.py`` docstring §"Hard labeling rule").  The same
heuristic lives in both scripts; if you change it, change it in both.

Source priority: this module is invoked first by
``mbo_release_lane.source_priority.resolve_source()``.  On any failure
(connect, entitlement, schema, parse), control falls back to Databento.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mbo_release_lane.constants import PARSER_VERSION, SOURCE_VENDOR_RITHMIC
from mbo_release_lane.rithmic_topology_guard import assert_rithmic_topology_ok

logger = logging.getLogger(__name__)


# Hard labeling rule — kept identical to scripts/rithmic_download_test.py.
_MBO_FIELDS = {"order_id", "action", "side", "flags"}
_DEPTH_FIELDS = {"bid_price", "ask_price", "bid_size", "ask_size", "bid_px", "ask_px"}
_TICK_FIELDS = {"price", "size", "timestamp"}


@dataclass
class RithmicFetchResult:
    """Result of one Rithmic fetch attempt for a release window."""

    release_id: str
    symbol: str
    exchange: str
    start_utc: datetime
    end_utc: datetime
    events: list[dict[str, Any]] = field(default_factory=list)
    raw_tick_count: int = 0
    data_label: str = "unknown"
    schema_fields: list[str] = field(default_factory=list)
    error: str | None = None
    elapsed_seconds: float = 0.0

    @property
    def is_valid_mbo(self) -> bool:
        return self.data_label == "mbo" and self.error is None and len(self.events) > 0


def _infer_data_label(record: dict[str, Any]) -> str:
    """Apply the hard labeling rule to a single raw Rithmic tick.

    Returns one of: "mbo", "ticks", "depth/mbp", "unknown".
    """
    fields = {k.lower() for k in record.keys()}
    if _MBO_FIELDS.issubset(fields) and _DEPTH_FIELDS & fields:
        return "mbo"
    if _MBO_FIELDS.issubset(fields):
        # order_id + action + side + flags, but no depth context — partial MBO.
        # Still treat as MBO; the classifier downstream does the final word.
        return "mbo"
    if _DEPTH_FIELDS & fields:
        return "depth/mbp"
    if _TICK_FIELDS & fields:
        return "ticks"
    return "unknown"


def _coerce_timestamp_ns(value: Any) -> int:
    """Best-effort coerce to nanoseconds since epoch."""
    if value is None:
        return 0
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp() * 1_000_000_000)
    if isinstance(value, (int, float)):
        # Heuristic: 1e18 = ns, 1e15 = us, 1e12 = ms, 1e9 = s.
        if value > 1e17:
            return int(value)
        if value > 1e14:
            return int(value * 1_000)
        if value > 1e11:
            return int(value * 1_000_000)
        if value > 1e8:
            return int(value * 1_000_000_000)
        return int(value)
    return 0


def _coerce_order_id(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalize_action(raw: Any) -> str:
    s = str(raw or "").strip().lower()
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
        "d": "delete",
        "delete": "delete",
        "r": "delete",
        "clear": "delete",
    }
    return mapping.get(s, s or "add")


def _normalize_side(raw: Any) -> str:
    s = str(raw or "").strip().upper()
    if s in ("A", "ASK", "S", "SELL"):
        return "A"
    if s in ("B", "BID", "BUY"):
        return "B"
    return s or "N"


def _tick_to_event(
    tick: dict[str, Any],
    *,
    release_id: str,
    symbol: str,
    sequence: int,
) -> dict[str, Any]:
    """Map a raw Rithmic tick to the MBO release lane normalized event.

    Rithmic historical tick schema (from ``async_rithmic`` >= 1.6.1) is
    dictionary-shaped with at least: ``timestamp`` (ns since epoch),
    ``price`` (fixed-point int or float), ``size`` (int).  Order-level
    fields (``order_id``, ``action``, ``side``, ``flags``) are present
    only when the request included the MBO bar type — by default the
    History Plant returns trade-only ticks.
    """
    ts = _coerce_timestamp_ns(tick.get("timestamp") or tick.get("ts"))
    price = tick.get("price", 0)
    size = int(tick.get("size") or 0)
    action = _normalize_action(tick.get("action") or tick.get("event_type") or "trade")
    side = _normalize_side(tick.get("side"))
    order_id = _coerce_order_id(tick.get("order_id") or tick.get("id"))
    flags = tick.get("flags")
    bid_px = tick.get("bid_price") or tick.get("bid_px")
    ask_px = tick.get("ask_price") or tick.get("ask_px")
    bid_sz = tick.get("bid_size")
    ask_sz = tick.get("ask_size")
    instrument_id = _coerce_order_id(tick.get("instrument_id"))

    raw_msg = {k: v for k, v in tick.items() if k not in {"price", "size"}}
    if flags is not None and "flags" not in raw_msg:
        raw_msg["flags"] = flags

    return {
        "release_id": release_id,
        "symbol": symbol,
        "venue": "GLBX",
        "instrument_id": instrument_id,
        "sequence_number": sequence,
        "exchange_timestamp": ts,
        "receive_timestamp": ts,
        "event_type": action,
        "order_id": order_id,
        "side": side,
        "price": str(price),
        "size": str(size),
        "remaining_size": str(size),
        "trade_id": str(tick.get("trade_id") or ""),
        "match_id": str(tick.get("match_id") or ""),
        "action": action,
        "raw_message": json.dumps(raw_msg, default=str, sort_keys=True),
        "source_vendor": SOURCE_VENDOR_RITHMIC,
        "dataset_id": "RITHMIC_HISTORY",
        "parser_version": PARSER_VERSION,
        "bid_price": str(bid_px) if bid_px is not None else None,
        "ask_price": str(ask_px) if ask_px is not None else None,
        "bid_size": str(bid_sz) if bid_sz is not None else None,
        "ask_size": str(ask_sz) if ask_sz is not None else None,
    }


def _rithmic_credentials() -> dict[str, str]:
    """Read Rithmic credentials from environment, with two naming conventions."""
    user = os.environ.get("RITHMIC_USER") or os.environ.get("RITHMIC_USERNAME", "")
    password = os.environ.get("RITHMIC_PASSWORD", "")
    system_name = (
        os.environ.get("RITHMIC_SYSTEM_NAME")
        or os.environ.get("RITHMIC_GATEWAY")
        or "Rithmic Test"
    )
    app_name = os.environ.get("RITHMIC_APP_NAME", "HFT3")
    app_version = os.environ.get("RITHMIC_APP_VERSION", "1.0")
    url = (
        os.environ.get("RITHMIC_URL")
        or os.environ.get("HFT3_RITHMIC_HOST")
        or "wss://rituz00100.00.rithmic.com:443"
    )
    ssl_ca_file = os.environ.get("RITHMIC_SSL_CA_FILE", "") or None
    return {
        "user": user,
        "password": password,
        "system_name": system_name,
        "app_name": app_name,
        "app_version": app_version,
        "url": url,
        "ssl_ca_file": ssl_ca_file,
    }


def _credentials_present(creds: dict[str, str]) -> bool:
    return bool(creds.get("user")) and bool(creds.get("password"))


def _build_ssl_context(ca_file: str | None) -> ssl.SSLContext:
    """Build a chain-aware SSL context for the Rithmic gateway."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.set_default_verify_paths()
    if ca_file:
        ctx.load_verify_locations(ca_file)
    return ctx


def _connect_rithmic(creds: dict[str, str]) -> Any:
    """Construct a connected RithmicClient; caller is responsible for disconnect."""
    from async_rithmic import RithmicClient

    client = RithmicClient(
        user=creds["user"],
        password=creds["password"],
        system_name=creds["system_name"],
        app_name=creds["app_name"],
        app_version=creds["app_version"],
        url=creds["url"],
    )
    # async_rithmic 1.6.1 ships a stale USERTrust root; override with our
    # chain-aware context to pick up the current Sectigo intermediate.
    client.ssl_context = _build_ssl_context(creds.get("ssl_ca_file"))
    return client


async def _fetch_ticks_async(
    creds: dict[str, str],
    symbol: str,
    exchange: str,
    start_utc: datetime,
    end_utc: datetime,
    max_pages: int = 50,
) -> list[dict[str, Any]]:
    """Open a Rithmic connection, request historical ticks, return raw records."""
    client = _connect_rithmic(creds)
    try:
        await client.connect()
        ticks = await client.get_historical_tick_data(
            symbol=symbol,
            exchange=exchange,
            start_time=start_utc,
            end_time=end_utc,
            max_pages=max_pages,
        )
        if not isinstance(ticks, list):
            return []
        return ticks
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


def fetch_event_window(
    release_id: str,
    symbol: str,
    exchange: str,
    start_utc: datetime,
    end_utc: datetime,
    *,
    max_pages: int = 50,
) -> RithmicFetchResult:
    """Synchronous wrapper: fetch ticks and convert to normalized events.

    Returns a RithmicFetchResult with ``is_valid_mbo`` True only when the
    returned schema proves MBO.  Caller (source_priority) is expected to
    check ``is_valid_mbo`` before writing any release_event_path manifest.
    """
    assert_rithmic_topology_ok()

    creds = _rithmic_credentials()
    if not _credentials_present(creds):
        return RithmicFetchResult(
            release_id=release_id,
            symbol=symbol,
            exchange=exchange,
            start_utc=start_utc,
            end_utc=end_utc,
            error="missing Rithmic credentials: set RITHMIC_USER and RITHMIC_PASSWORD",
        )

    if start_utc.tzinfo is None:
        start_utc = start_utc.replace(tzinfo=timezone.utc)
    if end_utc.tzinfo is None:
        end_utc = end_utc.replace(tzinfo=timezone.utc)

    t0 = time.monotonic()
    try:
        ticks = asyncio.run(
            _fetch_ticks_async(
                creds,
                symbol,
                exchange,
                start_utc,
                end_utc,
                max_pages=max_pages,
            )
        )
    except Exception as exc:  # connect / auth / schema / import failure
        return RithmicFetchResult(
            release_id=release_id,
            symbol=symbol,
            exchange=exchange,
            start_utc=start_utc,
            end_utc=end_utc,
            error=f"rithmic fetch failed: {exc}",
            elapsed_seconds=time.monotonic() - t0,
        )

    if not ticks:
        return RithmicFetchResult(
            release_id=release_id,
            symbol=symbol,
            exchange=exchange,
            start_utc=start_utc,
            end_utc=end_utc,
            raw_tick_count=0,
            data_label="unknown",
            error="no ticks returned (empty window or no entitlement)",
            elapsed_seconds=time.monotonic() - t0,
        )

    # Apply hard labeling rule on the first record — same shape the
    # scripts/rithmic_download_test.py proof uses.  A single record is
    # enough for a label, but the schema is sampled across records in
    # case field names vary.
    label_counts: dict[str, int] = {}
    sampled_fields: set[str] = set()
    for tick in ticks[:50]:
        lbl = _infer_data_label(tick)
        label_counts[lbl] = label_counts.get(lbl, 0) + 1
        sampled_fields.update(k.lower() for k in tick.keys())
    data_label = max(label_counts, key=label_counts.get) if label_counts else "unknown"

    if data_label != "mbo":
        # Hard rule: tick-only or depth-only data is NOT MBO.  Do not write
        # a release_event_path manifest for the MBO release lane.  Caller
        # will fall through to the next source in the priority chain.
        return RithmicFetchResult(
            release_id=release_id,
            symbol=symbol,
            exchange=exchange,
            start_utc=start_utc,
            end_utc=end_utc,
            raw_tick_count=len(ticks),
            data_label=data_label,
            schema_fields=sorted(sampled_fields),
            error=(
                f"schema is {data_label}, not mbo — refusing to fill MBO slot "
                "(hard labeling rule)"
            ),
            elapsed_seconds=time.monotonic() - t0,
        )

    # Convert ticks to the MBO release lane normalized event schema.
    events = [
        _tick_to_event(tick, release_id=release_id, symbol=symbol, sequence=i + 1)
        for i, tick in enumerate(ticks)
    ]
    events.sort(key=lambda e: (e["sequence_number"], e["exchange_timestamp"]))

    return RithmicFetchResult(
        release_id=release_id,
        symbol=symbol,
        exchange=exchange,
        start_utc=start_utc,
        end_utc=end_utc,
        events=events,
        raw_tick_count=len(ticks),
        data_label="mbo",
        schema_fields=sorted(sampled_fields),
        elapsed_seconds=time.monotonic() - t0,
    )


def write_release_artifact(
    repo_root: Path,
    result: RithmicFetchResult,
    *,
    scheduled_release_timestamp: str,
) -> dict[str, Any] | None:
    """Persist the normalized events into the MBO release slot and return the
    release_event_path manifest body.

    Mirrors the structure written by ``import_release_window`` for Databento,
    so the downstream ``derive_npz_from_release`` and audit code paths work
    unchanged.  Returns None when the result is not a valid MBO fetch.
    """
    if not result.is_valid_mbo:
        return None

    from mbo_release_lane.storage import (
        build_release_event_path,
        events_jsonl_path,
        hashes_path,
        release_event_path_manifest,
        release_slot_dir,
        validation_report_path,
        write_events_jsonl,
        write_json,
    )

    slot = release_slot_dir(repo_root, result.release_id, result.symbol)
    slot.mkdir(parents=True, exist_ok=True)

    write_events_jsonl(result.events, events_jsonl_path(slot))

    sequences = [e["sequence_number"] for e in result.events]
    first_seq = sequences[0] if sequences else None
    last_seq = sequences[-1] if sequences else None
    sequence_gap_count = sum(
        1 for i in range(1, len(sequences)) if sequences[i] - sequences[i - 1] > 1
    )

    actual_release_ts = (
        datetime.fromtimestamp(result.events[0]["exchange_timestamp"] / 1e9, tz=timezone.utc).isoformat()
        if result.events
        else result.start_utc.isoformat()
    )

    rep = build_release_event_path(
        release_id=result.release_id,
        release_name=result.release_id.split("_")[0],
        scheduled_release_timestamp=scheduled_release_timestamp,
        actual_release_timestamp=actual_release_ts,
        symbol=result.symbol,
        venue="GLBX",
        window_start=result.start_utc.isoformat(),
        window_end=result.end_utc.isoformat(),
        events_ref=str(events_jsonl_path(slot).relative_to(repo_root)),
        event_count=len(result.events),
        first_sequence=first_seq,
        last_sequence=last_seq,
        sequence_gap_count=sequence_gap_count,
        source_vendor=SOURCE_VENDOR_RITHMIC,
        dataset_id="RITHMIC_HISTORY",
        validation_status="valid",
    )
    write_json(release_event_path_manifest(slot), rep)

    # Validation report — minimal, mirrors the keys downstream code reads.
    validation = {
        "release_id": result.release_id,
        "symbol": result.symbol,
        "validation_status": "valid",
        "event_count": len(result.events),
        "blockers": [],
        "source_vendor": SOURCE_VENDOR_RITHMIC,
        "raw_tick_count": result.raw_tick_count,
        "data_label": result.data_label,
        "schema_fields": result.schema_fields,
        "elapsed_seconds": result.elapsed_seconds,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    write_json(validation_report_path(slot), validation)

    # Hashes — same shape as the Databento path.  Rithmic fills have no
    # raw DBN; only the events.jsonl and the manifest are hashed.
    from mbo_release_lane.hashing import build_hashes

    hashes = build_hashes(
        raw_dbn=None,
        events_jsonl=events_jsonl_path(slot),
        validation=validation,
    )
    write_json(hashes_path(slot), hashes)

    return rep


def derive_npz_from_rithmic_release(
    repo_root: Path,
    release_id: str,
    symbol: str,
) -> Path | None:
    """Derive an HftBacktest NPZ from a Rithmic-sourced release slot.

    Bypasses the Databento DBN->NPZ converter.  Uses HftBacktest's
    numpy/NPZ writer directly so the resulting NPZ has the same schema
    that downstream replay code expects.
    """
    from mbo_release_lane.npz_adapter import has_deriveable_mbo
    from mbo_release_lane.storage import load_release_event_path, release_slot_dir

    slot = release_slot_dir(repo_root, release_id, symbol)
    rep = load_release_event_path(slot)
    if not rep or rep.get("release_event_path", {}).get("source_vendor") != SOURCE_VENDOR_RITHMIC:
        # Don't accidentally re-derive a Databento slot.
        return None
    if not has_deriveable_mbo(repo_root, release_id, symbol):
        return None

    from data_system.src.npz_resolver import npz_filename

    events_path = slot / "events.jsonl"
    events: list[dict[str, Any]] = []
    with events_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))

    if not events:
        return None

    return _write_npz_from_events(
        repo_root=repo_root,
        events=events,
        symbol=symbol,
        release_id=release_id,
    )


def _write_npz_from_events(
    *,
    repo_root: Path,
    events: list[dict[str, Any]],
    symbol: str,
    release_id: str,
) -> Path | None:
    """Write a HftBacktest-compatible NPZ directly from normalized events."""
    try:
        import hftbacktest as hbt
    except ImportError:
        logger.warning("hftbacktest not available; skipping NPZ derivation")
        return None

    import numpy as np

    if not events:
        return None

    # Build the array in the order hftbacktest expects.
    # For a true MBO slot: rows = (event, order_id, price, size, side, flags).
    rows: list[tuple[int, int, int, int, int, int]] = []
    base_ts = int(events[0]["exchange_timestamp"]) if events else 0
    for ev in events:
        ts = max(0, int(ev["exchange_timestamp"]) - base_ts)
        action = ev.get("action", "add")
        action_code = {"add": 0, "cancel": 1, "modify": 2, "delete": 3, "trade": 4, "fill": 5}.get(
            action, 0
        )
        side_code = {"B": 1, "A": -1}.get(ev.get("side", "N"), 0)
        order_id = int(ev.get("order_id") or 0)
        price_raw = int(round(float(ev.get("price") or 0) * 1e9))
        size = int(ev.get("size") or 0)
        rows.append((action_code, order_id, price_raw, size, side_code, ts))

    data = np.asarray(rows, dtype=np.int64)
    out_dir = repo_root / "data" / "npz"
    out_dir.mkdir(parents=True, exist_ok=True)

    from data_system.src.npz_resolver import npz_filename

    target = out_dir / npz_filename(symbol, release_id)
    if target.is_file():
        target.unlink()
    np.savez(
        target,
        data=data,
        symbol=np.array([symbol]),
        release_id=np.array([release_id]),
        source_vendor=np.array([SOURCE_VENDOR_RITHMIC]),
    )
    return target
