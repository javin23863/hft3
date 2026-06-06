"""Kraken L3 order book WebSocket recorder — saves raw messages as NDJSON."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import websockets

logger = logging.getLogger(__name__)

KRAKEN_WS_URL = "wss://ws.kraken.com"
KRAKEN_WS_AUTH_URL = "wss://ws-auth.kraken.com"

DEFAULT_SYMBOLS = ["BTC/USD", "ETH/USD", "SOL/USD"]
RECONNECT_DELAY_S = 5.0
HEARTBEAT_INTERVAL_S = 15.0

# Kraken uses XBT as the primary pair name for Bitcoin in its API
_SYMBOL_MAP = {"BTC/USD": "XBT/USD"}


def _map_symbol(symbol: str) -> str:
    return _SYMBOL_MAP.get(symbol, symbol)


def _is_book_data(data: Any) -> bool:
    return isinstance(data, dict) and bool({"a", "b", "as", "bs", "bids", "asks"} & data.keys())


def _resolve_output_dir() -> Path:
    from crypto_lane.src.ingest.paths import data_root
    return data_root() / "kraken_l3_raw"


def _session_filename(symbol: str, output_dir: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe = symbol.replace("/", "_").replace("-", "_")
    return output_dir / f"kraken_l3_{safe}_{ts}.ndjson"


class KrakenL3Recorder:
    """Records Kraken WS book-depth messages to NDJSON files.

    Connects to Kraken public WebSocket, subscribes to the aggregated
    ``book`` channel (price/qty levels — not order-level MBO), and writes
    each received message as a JSON line.
    Supports graceful shutdown via SIGINT/SIGTERM.

    Handles both v1 (list) and v2 (dict) Kraken WebSocket API formats,
    maps BTC/USD to XBT/USD for the subscription, and silently skips
    checksum-only messages.
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        output_dir: Optional[Path] = None,
        depth: int = 1000,
    ):
        self.user_symbols = symbols or DEFAULT_SYMBOLS
        self.kraken_symbols = [_map_symbol(s) for s in self.user_symbols]
        self.output_dir = output_dir or _resolve_output_dir()
        self.depth = depth
        self._running = True
        self._stop_requested = False
        self._files: Dict[str, Any] = {}
        self._msg_counts: Dict[str, int] = {}
        self._start_time: float = 0.0

    def _setup_signal_handlers(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if sys.platform == "win32":
            # asyncio on Windows does not support add_signal_handler; rely on try/finally in run()
            return
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._request_stop)
            except NotImplementedError:
                pass

    def _request_stop(self) -> None:
        self._stop_requested = True

    def _close_files(self) -> None:
        for fh in self._files.values():
            try:
                fh.close()
            except Exception:
                pass
        self._files = {}

    def _write_line(self, symbol: str, data: Dict[str, Any]) -> None:
        line = json.dumps(data, ensure_ascii=False, default=str)
        self._files[symbol].write(line + "\n")
        self._files[symbol].flush()
        self._msg_counts[symbol] = self._msg_counts.get(symbol, 0) + 1

    def _kraken_to_user_symbol(self, kraken_symbol: str) -> Optional[str]:
        for us, ks in zip(self.user_symbols, self.kraken_symbols):
            if ks == kraken_symbol:
                return us
        return None

    async def _subscribe(self, ws: Any) -> None:
        sub = {
            "event": "subscribe",
            "pair": self.kraken_symbols,
            "subscription": {"name": "book", "depth": self.depth},
        }
        await ws.send(json.dumps(sub))

    async def handle_message(self, msg: str) -> None:
        """Parse a Kraken WebSocket message and write book data as JSON lines.

        Handles both v1 and v2 Kraken WebSocket API formats:
        - v1 book data: list [channel_data, channel_name, symbol]
        - v2 book data: dict {channel, type, data, symbol}
        - Subscription responses: dict {event: subscriptionStatus, ...}
        - Heartbeat/error events: silently ignored
        - Checksum-only messages: silently skipped
        """
        try:
            data = json.loads(msg)
        except json.JSONDecodeError:
            return

        # --- Handle dict-type messages ---
        if isinstance(data, dict):
            event = data.get("event", "")
            if event == "heartbeat":
                return
            if event == "subscriptionStatus":
                status = data.get("status", "unknown")
                pair = data.get("pair", "?")
                msg_text = data.get("errorMessage", "")
                if status == "subscribed":
                    print(f"Kraken subscribed to {pair}", file=sys.stderr)
                else:
                    print(f"Kraken subscription {status} for {pair}: {msg_text}", file=sys.stderr)
                return
            if event == "error":
                print(f"Kraken error: {data.get('errorMessage', msg)}", file=sys.stderr)
                return

            # v2 book data: dict with channel and type fields
            channel = data.get("channel", "")
            msg_type = data.get("type", "")
            if channel == "book" and msg_type in ("snapshot", "update"):
                book_data = data.get("data", {})
                symbol = data.get("symbol", "")
                user_symbol = self._kraken_to_user_symbol(symbol)
                if user_symbol and user_symbol in self._files and _is_book_data(book_data):
                    converted = {}
                    if "bids" in book_data:
                        converted["bs" if msg_type == "snapshot" else "b"] = book_data["bids"]
                    if "asks" in book_data:
                        converted["as" if msg_type == "snapshot" else "a"] = book_data["asks"]
                    self._write_line(user_symbol, {
                        "type": msg_type,
                        "data": converted,
                        "channel": channel,
                        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    })
                return

            return

        # --- Handle list-type messages (v1 format) ---
        # Kraken v1 book messages: [channelID, {book_data}, channelName, symbol]
        # channelID is an integer; the actual book data is the second element.
        if isinstance(data, list) and len(data) >= 3:
            channel_data = data[1] if (len(data) >= 4 and isinstance(data[0], int)) else data[0]
            channel_name = data[2] if (len(data) >= 4 and isinstance(data[0], int)) else (data[1] if len(data) > 1 else "book")
            symbol = data[3] if (len(data) >= 4 and isinstance(data[3], str)) else (data[-1] if isinstance(data[-1], str) else "")

            if not _is_book_data(channel_data):
                return

            user_symbol = self._kraken_to_user_symbol(symbol)
            if user_symbol and user_symbol in self._files:
                is_update = "a" in channel_data or "b" in channel_data
                self._write_line(user_symbol, {
                    "type": "update" if is_update else "snapshot",
                    "data": channel_data,
                    "channel": channel_name,
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                })

    async def run(self, duration_s: Optional[float] = None) -> Dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._files = {sym: open(_session_filename(sym, self.output_dir), "w", encoding="utf-8") for sym in self.user_symbols}
        self._start_time = time.monotonic()
        self._setup_signal_handlers()

        session_start = datetime.now(timezone.utc).isoformat()
        last_heartbeat = time.monotonic()
        last_ping_ns = time.monotonic_ns()

        try:
            while self._running and not self._stop_requested:
                try:
                    async with websockets.connect(KRAKEN_WS_URL) as ws:
                        await self._subscribe(ws)
                        while self._running and not self._stop_requested:
                            if duration_s and (time.monotonic() - self._start_time) > duration_s:
                                self._running = False
                                break

                            if time.monotonic() - last_heartbeat > HEARTBEAT_INTERVAL_S:
                                last_heartbeat = time.monotonic()

                            now_ns = time.monotonic_ns()
                            if (now_ns - last_ping_ns) >= HEARTBEAT_INTERVAL_S * 1_000_000_000:
                                last_ping_ns = now_ns
                                try:
                                    pong_waiter = await ws.ping()
                                    await asyncio.wait_for(pong_waiter, timeout=2.0)
                                except (asyncio.TimeoutError, websockets.exceptions.WebSocketException):
                                    logger.warning("Kraken heartbeat pong timeout; continuing")

                            try:
                                msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                                await self.handle_message(msg)
                            except asyncio.TimeoutError:
                                continue

                except websockets.ConnectionClosed:
                    if self._running and not self._stop_requested:
                        await asyncio.sleep(RECONNECT_DELAY_S)
                except OSError:
                    if self._running and not self._stop_requested:
                        await asyncio.sleep(RECONNECT_DELAY_S)
        finally:
            self._close_files()

        elapsed = time.monotonic() - self._start_time
        total_msgs = sum(self._msg_counts.values())
        return {
            "session_start": session_start,
            "session_end": datetime.now(timezone.utc).isoformat(),
            "elapsed_s": round(elapsed, 1),
            "symbols": self.user_symbols,
            "output_dir": str(self.output_dir),
            "total_messages": total_msgs,
            "messages_per_symbol": dict(self._msg_counts),
        }


def cmd_record_kraken_l3(args: Any) -> int:
    symbols = args.symbols.split(",") if args.symbols else DEFAULT_SYMBOLS
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = _resolve_output_dir()

    recorder = KrakenL3Recorder(
        symbols=symbols,
        output_dir=output_dir,
        depth=args.depth,
    )
    result = asyncio.run(recorder.run(duration_s=args.duration))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(prog="kraken_l3_recorder")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--depth", type=int, default=1000)
    parser.add_argument("--duration", type=float, default=None, help="Recording duration in seconds")
    args = parser.parse_args()
    raise SystemExit(cmd_record_kraken_l3(args))
