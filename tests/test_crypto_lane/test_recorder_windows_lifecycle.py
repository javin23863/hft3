"""Recorder lifecycle tests: graceful file close on stop and periodic heartbeat."""
from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from unittest.mock import MagicMock

from crypto_lane.src.data_io.binance_l2_recorder import BinanceL2Recorder
from crypto_lane.src.data_io.kraken_l3_recorder import KrakenL3Recorder


class _FakeWS:
    def __init__(self, send_message, ping_call_count, ping_event):
        self._send_message = send_message
        self._recv_count = 0
        self._ping_call_count = ping_call_count
        self._ping_event = ping_event

    async def send(self, _payload):
        return None

    async def recv(self):
        if self._recv_count == 0:
            self._recv_count += 1
            return self._send_message()
        raise asyncio.TimeoutError()

    async def ping(self):
        if self._ping_call_count is not None:
            self._ping_call_count[0] += 1
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        if self._ping_event is not None:
            async def resolve_when_set():
                await self._ping_event.wait()
                if not fut.done():
                    fut.set_result(None)
            loop.create_task(resolve_when_set())
        else:
            fut.set_result(None)
        return fut


class _FakeCM:
    def __init__(self, send_message, ping_call_count, ping_event):
        self._ws = _FakeWS(send_message, ping_call_count, ping_event)

    async def __aenter__(self):
        return self._ws

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _build_fake_ws_module(send_message, ping_call_count=None, ping_event=None):
    fake_ws = MagicMock()

    def fake_connect(*args, **kwargs):
        return _FakeCM(send_message, ping_call_count, ping_event)

    fake_ws.connect = fake_connect
    fake_ws.exceptions = MagicMock()
    fake_ws.exceptions.WebSocketException = Exception
    fake_ws.ConnectionClosed = type("ConnectionClosed", (Exception,), {})
    return fake_ws


def _binance_message():
    return '{"e":"depthUpdate","s":"BTCUSDT","U":1,"u":2,"b":[],"a":[]}'


def _kraken_message():
    return '{"channel":"book","type":"snapshot","symbol":"XBT/USD","data":{"bids":[],"asks":[]}}'


@contextmanager
def _patch_binance_websockets(send_message, ping_call_count=None, ping_event=None):
    import crypto_lane.src.data_io.binance_l2_recorder as mod
    fake = _build_fake_ws_module(send_message, ping_call_count, ping_event)
    original = mod.websockets
    mod.websockets = fake
    try:
        yield fake
    finally:
        mod.websockets = original


@contextmanager
def _patch_kraken_websockets(send_message, ping_call_count=None, ping_event=None):
    import crypto_lane.src.data_io.kraken_l3_recorder as mod
    fake = _build_fake_ws_module(send_message, ping_call_count, ping_event)
    original = mod.websockets
    mod.websockets = fake
    try:
        yield fake
    finally:
        mod.websockets = original


def test_binance_recorder_closes_files_on_keyboard_interrupt(tmp_path):
    recorder = BinanceL2Recorder(symbols=["btcusdt"], output_dir=tmp_path)
    raised_once = [False]

    def runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def main():
                original_handle = recorder._handle_message

                async def raising_handle(ws, msg):
                    if not raised_once[0]:
                        raised_once[0] = True
                        recorder._stop_requested = True
                        raise KeyboardInterrupt("simulated")
                    await original_handle(ws, msg)

                recorder._handle_message = raising_handle  # type: ignore[assignment]
                return await recorder.run(duration_s=None)
            try:
                loop.run_until_complete(main())
            except KeyboardInterrupt:
                pass
        finally:
            loop.close()

    with _patch_binance_websockets(send_message=_binance_message):
        t = threading.Thread(target=runner)
        t.start()
        t.join(timeout=5.0)

    assert not t.is_alive(), "binance recorder thread did not exit"
    assert recorder._files == {}, f"files not closed: {list(recorder._files.keys())}"


def test_kraken_recorder_closes_files_on_keyboard_interrupt(tmp_path):
    recorder = KrakenL3Recorder(symbols=["BTC/USD"], output_dir=tmp_path)
    raised_once = [False]

    def runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def main():
                original_handle = recorder.handle_message

                async def raising_handle(msg):
                    if not raised_once[0]:
                        raised_once[0] = True
                        recorder._stop_requested = True
                        raise KeyboardInterrupt("simulated")
                    await original_handle(msg)

                recorder.handle_message = raising_handle  # type: ignore[assignment]
                return await recorder.run(duration_s=None)
            try:
                loop.run_until_complete(main())
            except KeyboardInterrupt:
                pass
        finally:
            loop.close()

    with _patch_kraken_websockets(send_message=_kraken_message):
        t = threading.Thread(target=runner)
        t.start()
        t.join(timeout=5.0)

    assert not t.is_alive(), "kraken recorder thread did not exit"
    assert recorder._files == {}, f"files not closed: {list(recorder._files.keys())}"


def test_kraken_recorder_sends_periodic_ping(tmp_path, monkeypatch):
    import crypto_lane.src.data_io.kraken_l3_recorder as kraken_mod
    monkeypatch.setattr(kraken_mod, "HEARTBEAT_INTERVAL_S", 0.1)

    ping_count = [0]
    ping_event = asyncio.Event()
    ping_event.set()

    recorder = KrakenL3Recorder(symbols=["BTC/USD"], output_dir=tmp_path)

    def runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def main():
                async def stop_after():
                    await asyncio.sleep(1.0)
                    recorder._stop_requested = True
                loop.create_task(stop_after())
                return await recorder.run(duration_s=None)
            loop.run_until_complete(main())
        finally:
            loop.close()

    with _patch_kraken_websockets(
        send_message=_kraken_message,
        ping_call_count=ping_count,
        ping_event=ping_event,
    ):
        t = threading.Thread(target=runner)
        t.start()
        t.join(timeout=5.0)

    assert not t.is_alive(), "kraken recorder did not finish in time"
    assert ping_count[0] >= 3, f"expected >= 3 pings in 1s with 0.1s interval, got {ping_count[0]}"
