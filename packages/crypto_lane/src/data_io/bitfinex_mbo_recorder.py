"""Bitfinex WebSocket raw book (R0) recorder — true order-level MBO.

Public feed (no API key): wss://api.bitfinex.com/ws/2
See: https://docs.bitfinex.com/reference/ws-public-books
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import websockets

logger = logging.getLogger(__name__)

BITFINEX_WS_URL = "wss://api.bitfinex.com/ws/2"
DEFAULT_SYMBOLS = ["tBTCUSD", "tETHUSD", "tSOLUSD"]
RECONNECT_DELAY_S = 5.0

PERP_TO_BITFINEX = {
    "BTCUSDT": "tBTCUSD",
    "ETHUSDT": "tETHUSD",
    "SOLUSDT": "tSOLUSD",
    "BTC-USD": "tBTCUSD",
    "ETH-USD": "tETHUSD",
    "SOL-USD": "tSOLUSD",
}


def _resolve_output_dir() -> Path:
    from crypto_lane.src.ingest.paths import data_root

    return data_root() / "bitfinex_mbo_raw"


def _session_filename(symbol: str, output_dir: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe = symbol.replace("/", "_").lstrip("t")
    return output_dir / f"bitfinex_mbo_{safe}_{ts}.ndjson"


def _normalize_symbols(raw: List[str]) -> List[str]:
    out: List[str] = []
    for sym in raw:
        s = sym.strip()
        if not s:
            continue
        mapped = PERP_TO_BITFINEX.get(s.upper(), s)
        if not mapped.startswith("t"):
            mapped = f"t{mapped.upper()}"
        if mapped not in out:
            out.append(mapped)
    return out or list(DEFAULT_SYMBOLS)


class BitfinexMboRecorder:
    """Records Bitfinex R0 (raw order book) messages to NDJSON."""

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        output_dir: Optional[Path] = None,
        book_len: int = 100,
    ):
        self.symbols = _normalize_symbols(symbols or DEFAULT_SYMBOLS)
        self.output_dir = output_dir or _resolve_output_dir()
        self.book_len = book_len
        self._running = True
        self._stop_requested = False
        self._files: Dict[str, Any] = {}
        self._msg_counts: Dict[str, int] = {}
        self._chan_to_symbol: Dict[int, str] = {}

    def _write_line(self, symbol: str, data: Dict[str, Any]) -> None:
        line = json.dumps(data, ensure_ascii=False, default=str)
        self._files[symbol].write(line + "\n")
        self._files[symbol].flush()
        self._msg_counts[symbol] = self._msg_counts.get(symbol, 0) + 1

    async def _subscribe(self, ws: Any, symbol: str) -> None:
        sub = {
            "event": "subscribe",
            "channel": "book",
            "symbol": symbol,
            "prec": "R0",
            "freq": "F0",
            "len": str(self.book_len),
        }
        await ws.send(json.dumps(sub))

    async def handle_message(self, msg: str) -> None:
        try:
            data = json.loads(msg)
        except json.JSONDecodeError:
            return

        if isinstance(data, dict):
            event = data.get("event")
            if event == "subscribed" and data.get("channel") == "book":
                chan_id = int(data["chanId"])
                symbol = str(data.get("symbol") or "")
                self._chan_to_symbol[chan_id] = symbol
            elif event == "error":
                logger.warning("Bitfinex WS error: %s", data.get("msg", data))
            return

        if not isinstance(data, list) or len(data) != 2:
            return

        chan_id = int(data[0])
        payload = data[1]
        symbol = self._chan_to_symbol.get(chan_id)
        if not symbol or symbol not in self._files:
            return

        record: Dict[str, Any] = {
            "symbol": symbol,
            "chan_id": chan_id,
            "_local_ts_utc": datetime.now(timezone.utc).isoformat(),
        }

        if isinstance(payload, list) and payload and isinstance(payload[0], list):
            record["type"] = "snapshot"
            record["orders"] = payload
        elif isinstance(payload, list) and len(payload) == 3:
            record["type"] = "update"
            record["order_id"], record["price"], record["amount"] = payload
        else:
            return

        self._write_line(symbol, record)

    async def run(self, duration_s: Optional[float] = None) -> Dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()

        try:
            self._files = {
                sym: open(_session_filename(sym, self.output_dir), "w", encoding="utf-8")
                for sym in self.symbols
            }
            while self._running and not self._stop_requested:
                try:
                    async with websockets.connect(BITFINEX_WS_URL, ping_interval=20, ping_timeout=20) as ws:
                        for sym in self.symbols:
                            await self._subscribe(ws, sym)
                        while self._running and not self._stop_requested:
                            if duration_s and (time.monotonic() - start) > duration_s:
                                self._running = False
                                break
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                            except asyncio.TimeoutError:
                                continue
                            await self.handle_message(raw)
                except websockets.ConnectionClosed:
                    if duration_s and (time.monotonic() - start) > duration_s:
                        break
                    await asyncio.sleep(RECONNECT_DELAY_S)
                except OSError as exc:
                    logger.warning("Bitfinex WS connection error: %s", exc)
                    await asyncio.sleep(RECONNECT_DELAY_S)
        finally:
            for fh in self._files.values():
                try:
                    fh.close()
                except Exception:
                    pass
            self._files = {}

        return {
            "symbols": self.symbols,
            "output_dir": str(self.output_dir),
            "duration_s": time.monotonic() - start,
            "messages_per_symbol": dict(self._msg_counts),
        }


def cmd_record_bitfinex_mbo(args: Any) -> int:
    symbols = args.symbols.split(",") if args.symbols else DEFAULT_SYMBOLS
    output_dir = Path(args.output_dir) if args.output_dir else None
    recorder = BitfinexMboRecorder(
        symbols=symbols,
        output_dir=output_dir,
        book_len=int(getattr(args, "book_len", 100)),
    )
    summary = asyncio.run(recorder.run(duration_s=args.duration))
    print(json.dumps(summary))
    if not any(summary.get("messages_per_symbol", {}).values()):
        print("ERROR: no MBO messages recorded", file=sys.stderr)
        return 1
    return 0
