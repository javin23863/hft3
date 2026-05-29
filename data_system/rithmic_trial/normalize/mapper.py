from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..config import TrialConfig
from ..schema.normalized_v1 import REQUIRED_FIELDS, SCHEMA_VERSION


def _parse_exchange_ts(raw: dict[str, Any]) -> int | None:
    for key in ("exchange_timestamp_ns", "exchange_timestamp", "exchange_time"):
        if key not in raw or raw[key] in (None, ""):
            continue
        val = raw[key]
        if isinstance(val, (int, float)):
            v = int(val)
            return v if v > 1_000_000_000_000 else v * 1_000_000_000
        s = str(val).strip()
        if s.isdigit():
            v = int(s)
            return v if v > 1_000_000_000_000 else v * 1_000_000_000
    return None


def normalize_event(raw: dict[str, Any], cfg: TrialConfig) -> dict[str, Any]:
    now = time.time_ns()
    recv = int(raw.get("local_receive_timestamp_ns") or now)
    ev: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source": raw.get("source", cfg.source),
        "capture_environment": raw.get("capture_environment", cfg.capture_environment),
        "symbol": raw.get("symbol") or cfg.symbol,
        "contract": raw.get("contract") or cfg.contract,
        "exchange": raw.get("exchange") or cfg.exchange,
        "event_type": str(raw.get("event_type", "unknown")).lower(),
        "exchange_timestamp_ns": _parse_exchange_ts(raw),
        "local_receive_timestamp_ns": recv,
        "local_write_timestamp_ns": int(raw.get("local_write_timestamp_ns") or now),
    }
    for key in (
        "price",
        "size",
        "side",
        "order_id",
        "fill_id",
        "sequence",
        "bid_price",
        "ask_price",
        "bid_size",
        "ask_size",
        "bid_levels",
        "ask_levels",
        "gateway_metadata",
    ):
        if key in raw and raw[key] not in (None, ""):
            ev[key] = raw[key]
    return ev


def normalize_file(raw_path: Path, cfg: TrialConfig, out_path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    events: list[dict[str, Any]] = []
    warnings: list[str] = []
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as out_f:
        for line in raw_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            ev = normalize_event(raw, cfg)
            missing = [f for f in REQUIRED_FIELDS if ev.get(f) in (None, "")]
            if missing:
                warnings.append(f"missing required fields {missing} in event {ev.get('event_type')}")
            events.append(ev)
            out_f.write(json.dumps(ev, sort_keys=True) + "\n")
    return events, warnings
