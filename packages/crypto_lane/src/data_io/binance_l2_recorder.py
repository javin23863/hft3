"""Binance L2 order book depth WebSocket recorder — saves raw messages as NDJSON."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import websockets

BINANCE_WS_URL = "wss://stream.binance.com:9443/ws"

DEFAULT_SYMBOLS = ["btcusdt", "ethusdt", "solusdt"]
RECONNECT_DELAY_S = 5.0
HEARTBEAT_INTERVAL_S = 15.0


def _resolve_output_dir() -> Path:
    from crypto_lane.src.ingest.paths import data_root
    return data_root() / "binance_l2_raw"


def _session_filename(symbol: str, output_dir: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return output_dir / f"binance_l2_{symbol}_{ts}.ndjson"


class BinanceL2Recorder:
    """Records Binance L2 order book diff depth messages to NDJSON.

    Connects to Binance public WebSocket, subscribes to the
    <symbol>@depth@100ms stream per symbol, and writes each received
    message as a JSON line. Supports graceful shutdown via SIGINT.
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        output_dir: Optional[Path] = None,
    ):
        self.symbols = [s.lower() for s in (symbols or DEFAULT_SYMBOLS)]
        self.output_dir = output_dir or _resolve_output_dir()
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

    async def _handle_message(self, ws: Any, msg: str) -> None:
        try:
            data = json.loads(msg)
        except json.JSONDecodeError:
            return

        if isinstance(data, dict) and data.get("e") == "depthUpdate":
            raw_symbol = data.get("s", "").lower()
            if raw_symbol in self._files:
                data["_local_ts_utc"] = datetime.now(timezone.utc).isoformat()
                self._write_line(raw_symbol, data)

    async def _subscribe(self, ws: Any) -> None:
        streams = [f"{sym}@depth@100ms" for sym in self.symbols]
        sub = {
            "method": "SUBSCRIBE",
            "params": streams,
            "id": 1,
        }
        await ws.send(json.dumps(sub))

    async def run(self, duration_s: Optional[float] = None) -> Dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._files = {sym: open(_session_filename(sym, self.output_dir), "w", encoding="utf-8") for sym in self.symbols}
        self._start_time = time.monotonic()
        self._setup_signal_handlers()

        session_start = datetime.now(timezone.utc).isoformat()
        last_heartbeat = time.monotonic()

        try:
            while self._running and not self._stop_requested:
                try:
                    async with websockets.connect(BINANCE_WS_URL) as ws:
                        await self._subscribe(ws)

                        while self._running and not self._stop_requested:
                            if duration_s and (time.monotonic() - self._start_time) > duration_s:
                                self._running = False
                                break

                            if time.monotonic() - last_heartbeat > HEARTBEAT_INTERVAL_S:
                                last_heartbeat = time.monotonic()
                                try:
                                    pong_waiter = await ws.ping()
                                    await asyncio.wait_for(pong_waiter, timeout=2.0)
                                except (asyncio.TimeoutError, Exception):
                                    pass

                            try:
                                msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                                await self._handle_message(ws, msg)
                            except asyncio.TimeoutError:
                                continue

                except (websockets.ConnectionClosed, OSError):
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
            "symbols": self.symbols,
            "output_dir": str(self.output_dir),
            "total_messages": total_msgs,
            "messages_per_symbol": dict(self._msg_counts),
        }


def cmd_record_binance_l2(args: Any) -> int:
    symbols = args.symbols.split(",") if args.symbols else DEFAULT_SYMBOLS
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = _resolve_output_dir()

    recorder = BinanceL2Recorder(
        symbols=symbols,
        output_dir=output_dir,
    )
    result = asyncio.run(recorder.run(duration_s=args.duration))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(prog="binance_l2_recorder")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--duration", type=float, default=None)
    args = parser.parse_args()
    raise SystemExit(cmd_record_binance_l2(args))
