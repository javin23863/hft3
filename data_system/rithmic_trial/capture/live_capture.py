from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import TrialConfig
from ..schema.normalized_v1 import SCHEMA_VERSION


def _utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class LiveCapture:
    def __init__(self, cfg: TrialConfig, date: str | None = None, symbol: str | None = None) -> None:
        self.cfg = cfg
        self.date = date or _utc_date()
        self.symbol = symbol or cfg.symbol
        self.out_dir = cfg.raw_dir(self.date, self.symbol)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.raw_path = self.out_dir / "events.ndjson"
        self.manifest_path = self.out_dir / "manifest.json"
        self._count = 0
        self._first_ts: int | None = None
        self._last_ts: int | None = None
        self._types: set[str] = set()
        self._start = datetime.now(timezone.utc).isoformat()

    def append_raw(self, events: list[dict[str, Any]]) -> int:
        if not events:
            return 0
        with self.raw_path.open("a", encoding="utf-8") as f:
            for ev in events:
                ev = dict(ev)
                ev.setdefault("source", self.cfg.source)
                ev.setdefault("capture_environment", self.cfg.capture_environment)
                ev.setdefault("local_write_timestamp_ns", time.time_ns())
                ts = ev.get("local_receive_timestamp_ns") or ev.get("local_write_timestamp_ns")
                if ts is not None:
                    ts = int(ts)
                    self._first_ts = ts if self._first_ts is None else min(self._first_ts, ts)
                    self._last_ts = ts if self._last_ts is None else max(self._last_ts, ts)
                if ev.get("event_type"):
                    self._types.add(str(ev["event_type"]))
                f.write(json.dumps(ev, sort_keys=True) + "\n")
                self._count += 1
        return len(events)

    def finalize(
        self,
        detected_types: set[str],
        missing_types: list[str],
        limitations: dict[str, Any],
    ) -> Path:
        end = datetime.now(timezone.utc).isoformat()
        payload = {
            "schema_version": SCHEMA_VERSION,
            "source": self.cfg.source,
            "capture_environment": self.cfg.capture_environment,
            "symbol": self.symbol,
            "exchange": self.cfg.exchange,
            "row_count": self._count,
            "first_timestamp_ns": self._first_ts,
            "last_timestamp_ns": self._last_ts,
            "checksum_sha256": _sha256(self.raw_path) if self.raw_path.exists() else None,
            "capture_start_time": self._start,
            "capture_end_time": end,
            "detected_event_types": sorted(detected_types or self._types),
            "missing_event_types": missing_types,
            "known_limitations": limitations,
            "raw_file": str(self.raw_path),
        }
        self.manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return self.manifest_path
