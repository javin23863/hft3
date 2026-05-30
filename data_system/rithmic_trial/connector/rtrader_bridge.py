from __future__ import annotations

import csv
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from ..config import TrialConfig
from ..platform import is_windows
from .base import ConnectorInterface

ALL_EVENT_TYPES = {
    "trade",
    "quote",
    "depth",
    "order_submit",
    "order_ack",
    "fill",
    "cancel",
    "reject",
    "position",
    "account",
}


def _parse_csv_row(row: dict[str, str], cfg: TrialConfig) -> dict[str, Any] | None:
    lowered = {k.strip().lower(): v.strip() for k, v in row.items() if k}
    event_type = lowered.get("event_type") or lowered.get("type") or lowered.get("action")
    if not event_type:
        if "fill" in lowered or lowered.get("status", "").lower() == "filled":
            event_type = "fill"
        elif lowered.get("status", "").lower() in ("ack", "acknowledged", "working"):
            event_type = "order_ack"
        elif lowered.get("side") and lowered.get("qty"):
            event_type = "order_submit"
        elif lowered.get("last") or lowered.get("price"):
            event_type = "trade"
        elif lowered.get("bid") or lowered.get("ask"):
            event_type = "quote"
    if not event_type:
        return None

    ev: dict[str, Any] = {
        "event_type": event_type.lower(),
        "symbol": lowered.get("symbol") or cfg.symbol,
        "exchange": lowered.get("exchange") or cfg.exchange,
        "contract": lowered.get("contract") or cfg.contract,
    }
    for src, dst in (
        ("price", "price"),
        ("last", "price"),
        ("qty", "size"),
        ("size", "size"),
        ("side", "side"),
        ("order_id", "order_id"),
        ("fill_id", "fill_id"),
        ("sequence", "sequence"),
    ):
        if src in lowered and lowered[src]:
            ev[dst] = lowered[src]
    if "bid" in lowered:
        ev["bid_price"] = float(lowered["bid"])
    if "ask" in lowered:
        ev["ask_price"] = float(lowered["ask"])
    if "exchange_time" in lowered:
        ev["exchange_timestamp"] = lowered["exchange_time"]
    return ev


def _parse_comma_log_line(line: str, cfg: TrialConfig) -> dict[str, Any] | None:
    """R|Trader export: timestamp,Trade,MES,5000.00,1"""
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 4:
        return None
    type_map = {
        "trade": "trade",
        "quote": "quote",
        "fill": "fill",
        "ack": "order_ack",
        "cancel": "cancel",
        "reject": "reject",
    }
    raw_type = parts[1].lower()
    event_type = type_map.get(raw_type)
    if not event_type:
        return None
    sym = parts[2] or cfg.symbol
    try:
        price = float(parts[3])
    except ValueError:
        return None
    ev: dict[str, Any] = {
        "event_type": event_type,
        "symbol": sym,
        "exchange": cfg.exchange,
        "price": price,
        "raw_line": line,
    }
    if len(parts) >= 5 and parts[4]:
        try:
            ev["size"] = float(parts[4])
        except ValueError:
            pass
    return ev


def _parse_log_line(line: str, cfg: TrialConfig) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    if line.startswith("{"):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None
    if "," in line and re.match(r"^\d{4}-\d{2}-\d{2}", line):
        ev = _parse_comma_log_line(line, cfg)
        if ev:
            return ev
    m = re.search(
        r"(?P<type>TRADE|QUOTE|FILL|ACK|CANCEL|REJECT).*?(?P<sym>[A-Z0-9.]+).*?(?P<px>[\d.]+)",
        line,
        re.I,
    )
    if not m:
        return None
    type_map = {
        "TRADE": "trade",
        "QUOTE": "quote",
        "FILL": "fill",
        "ACK": "order_ack",
        "CANCEL": "cancel",
        "REJECT": "reject",
    }
    try:
        price = float(m.group("px"))
    except ValueError:
        return None
    return {
        "event_type": type_map.get(m.group("type").upper(), "trade"),
        "symbol": m.group("sym") or cfg.symbol,
        "exchange": cfg.exchange,
        "price": price,
        "raw_line": line,
    }


class RTraderBridgeConnector(ConnectorInterface):
    """Watch R|Trader Pro export/log files (Wine on CHI404 only)."""

    def __init__(self, cfg: TrialConfig) -> None:
        self.cfg = cfg
        self.platform = cfg.rtrader.get("platform") or "linux"
        self.wine_prefix = Path(cfg.rtrader.get("wine_prefix", "/root/.wine-rtrader"))
        self.watch_dirs = [Path(p) for p in cfg.rtrader.get("watch_dirs", [])]
        self.export_globs = cfg.rtrader.get("export_globs", ["**/*.csv", "**/*.ndjson"])
        self.log_globs = cfg.rtrader.get("log_globs", ["**/*.log", "**/*.cur.txt"])
        self._file_offsets: dict[str, int] = {}
        self._csv_rows_seen: dict[str, int] = {}
        self._pending: list[dict[str, Any]] = []
        self._detected: set[str] = set()

    def connect(self) -> None:
        if self.watch_dirs:
            return
        if is_windows():
            raise RuntimeError(
                "RTraderBridgeConnector is for CHI404 (Wine) only. "
                "Do not run live Rithmic capture on a Windows workstation — see AGENTS.md § Topology."
            )
        candidates = [
            self.wine_prefix / "drive_c" / "users" / "root" / "Documents",
            self.wine_prefix / "drive_c" / "Program Files" / "Rithmic",
            self.wine_prefix / "drive_c" / "Program Files (x86)" / "Rithmic",
            self.wine_prefix / "drive_c" / "Program Files (x86)" / "Rithmic Trader Pro",
        ]
        self.watch_dirs = [p for p in candidates if p.exists()]
        if not self.watch_dirs:
            raise FileNotFoundError(
                "No R|Trader watch directories found. "
                "On CHI404: run infrastructure/chi404/08_rtrader_wine_setup.sh and set rtrader.watch_dirs "
                "in data_system/config/rithmic_trial.yaml from logs/rtrader/rtrader_discovery.json"
            )

    def _iter_files(self) -> list[Path]:
        files: list[Path] = []
        for base in self.watch_dirs:
            if not base.exists():
                continue
            for pattern in self.export_globs + self.log_globs:
                files.extend(base.glob(pattern))
        return sorted(set(files))

    def _ingest_tail_text(self, path: Path, cfg: TrialConfig) -> None:
        key = str(path)
        try:
            size = path.stat().st_size
        except OSError:
            return
        offset = self._file_offsets.get(key, 0)
        if size < offset:
            offset = 0
        if size == offset:
            return
        with path.open("r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            chunk = f.read()
            self._file_offsets[key] = f.tell()
        for line in chunk.splitlines():
            ev = _parse_log_line(line, cfg)
            if ev:
                self._pending.append(ev)
                self._detected.add(str(ev.get("event_type", "unknown")))

    def _ingest_csv(self, path: Path, cfg: TrialConfig) -> None:
        key = str(path)
        try:
            with path.open(newline="", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
        except OSError:
            return
        start = self._csv_rows_seen.get(key, 0)
        for row in rows[start:]:
            ev = _parse_csv_row(row, cfg)
            if ev:
                self._pending.append(ev)
                self._detected.add(str(ev.get("event_type", "unknown")))
        self._csv_rows_seen[key] = len(rows)

    def _ingest_ndjson_tail(self, path: Path) -> None:
        key = str(path)
        try:
            size = path.stat().st_size
        except OSError:
            return
        offset = self._file_offsets.get(key, 0)
        if size < offset:
            offset = 0
        if size == offset:
            return
        with path.open("r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            chunk = f.read()
            self._file_offsets[key] = f.tell()
        for line in chunk.splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
                self._pending.append(ev)
                self._detected.add(str(ev.get("event_type", "unknown")))
            except json.JSONDecodeError:
                continue

    def _ingest_file(self, path: Path) -> None:
        name = path.name.lower()
        if name.endswith("_probe.txt"):
            return
        suffix = path.suffix.lower()
        if suffix == ".csv":
            self._ingest_csv(path, self.cfg)
        elif suffix == ".ndjson":
            self._ingest_ndjson_tail(path)
        else:
            self._ingest_tail_text(path, self.cfg)

    def poll_events(self) -> list[dict[str, Any]]:
        for path in self._iter_files():
            try:
                self._ingest_file(path)
            except OSError:
                continue
        if not self._pending:
            return []
        batch = self._pending
        self._pending = []
        now = time.time_ns()
        gateway = self.cfg.rithmic.get("gateway") or self.cfg.rtrader.get("gateway")
        meta = {
            "gateway": gateway,
            "environment": self.cfg.rithmic.get("environment"),
        }
        out = []
        for ev in batch:
            ev = dict(ev)
            ev.setdefault("local_receive_timestamp_ns", now)
            if gateway and "gateway_metadata" not in ev:
                ev["gateway_metadata"] = meta
            out.append(ev)
            now += 1000
        return out

    def detected_event_types(self) -> set[str]:
        return set(self._detected)

    def limitations(self) -> dict[str, Any]:
        detected = self.detected_event_types()
        missing = sorted(ALL_EVENT_TYPES - detected) if detected else sorted(ALL_EVENT_TYPES)
        return {
            "connector": "rtrader_bridge",
            "platform": self.platform,
            "wine_prefix": str(self.wine_prefix),
            "watch_dirs": [str(p) for p in self.watch_dirs],
            "detected_event_types": sorted(detected),
            "missing_event_types": missing,
            "gateway": self.cfg.rithmic.get("gateway") or self.cfg.rtrader.get("gateway"),
            "environment": self.cfg.rithmic.get("environment"),
            "note": "Tail-follow capture for unattended mode; full MBO depth unlikely via R|Trader exports",
        }

    def close(self) -> None:
        self._pending.clear()
