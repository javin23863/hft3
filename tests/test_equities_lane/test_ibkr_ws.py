"""Tests for IbkrWsSession and IbkrWebClient.connect_ws.

No real network — uses an injected async ws_connect stub that records sent
frames and feeds canned recv frames.

Async tests are driven with asyncio.run() inside sync test functions because
no pytest-asyncio plugin is configured in this repo.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from equities_lane.src.execution.ibkr_web_client import (
    GatewayAuth,
    IbkrWebClient,
    IbkrWsSession,
)


# ---------------------------------------------------------------------------
# Async WebSocket stub
# ---------------------------------------------------------------------------


class _FakeWs:
    """Minimal async WebSocket stub.

    Records all frames sent by the client.  Feeds ``recv_frames`` in order;
    raises ``StopAsyncIteration`` (propagated as an exception inside
    ``messages()``) when the queue is exhausted.
    """

    def __init__(self, recv_frames: list[str]) -> None:
        self.sent: list[str] = []
        self._recv_queue: list[str] = list(recv_frames)
        self.closed = False

    async def send(self, frame: str) -> None:
        self.sent.append(frame)

    async def recv(self) -> str:
        if not self._recv_queue:
            # Signal end-of-stream so messages() iterator stops cleanly.
            raise Exception("no more frames")
        return self._recv_queue.pop(0)

    async def close(self) -> None:
        self.closed = True


async def _make_ws_stub(recv_frames: list[str] | None = None) -> tuple[IbkrWsSession, _FakeWs]:
    """Build a connected IbkrWsSession backed by a _FakeWs stub."""
    stub = _FakeWs(recv_frames or [])

    async def _fake_connect(*args: Any, **kwargs: Any) -> _FakeWs:
        return stub

    sess = IbkrWsSession(stub)
    await sess._handshake("tok_test")
    return sess, stub


# ---------------------------------------------------------------------------
# connect_ws — session frame sent first
# ---------------------------------------------------------------------------


def test_connect_ws_sends_session_frame_first() -> None:
    """The very first frame sent must be the JSON session token."""
    TOKEN = "my_session_abc123"
    stub = _FakeWs([])

    async def _fake_connect(*args: Any, **kwargs: Any) -> _FakeWs:
        return stub

    async def _run() -> None:
        auth = GatewayAuth(session=object())  # session not used for WS
        client = IbkrWebClient(auth=auth)
        # Patch the ws_url so the ssl branch uses localhost (no real ssl context needed).
        client._auth.base_url = "https://localhost:5000/v1/api"
        session = await client.connect_ws(TOKEN, ws_connect=_fake_connect)
        return session

    asyncio.run(_run())
    assert len(stub.sent) >= 1
    first = json.loads(stub.sent[0])
    assert first == {"session": TOKEN}


def test_connect_ws_session_frame_not_logged(capsys: pytest.CaptureFixture) -> None:
    """Session token must not appear in stdout/stderr."""
    TOKEN = "secret_session_tok_xyz"
    stub = _FakeWs([])

    async def _fake_connect(*args: Any, **kwargs: Any) -> _FakeWs:
        return stub

    async def _run() -> None:
        auth = GatewayAuth(session=object())
        client = IbkrWebClient(auth=auth)
        client._auth.base_url = "https://localhost:5000/v1/api"
        await client.connect_ws(TOKEN, ws_connect=_fake_connect)

    asyncio.run(_run())
    captured = capsys.readouterr()
    assert TOKEN not in captured.out
    assert TOKEN not in captured.err


# ---------------------------------------------------------------------------
# Subscriptions — correct frames sent
# ---------------------------------------------------------------------------


def test_subscribe_orders_sends_sor_frame() -> None:
    async def _run() -> list[str]:
        sess, stub = await _make_ws_stub()
        await sess.subscribe_orders()
        return stub.sent

    sent = asyncio.run(_run())
    # sent[0] is the handshake frame; sent[1] is the subscription.
    assert "sor+{}" in sent


def test_subscribe_trades_sends_str_frame() -> None:
    async def _run() -> list[str]:
        sess, stub = await _make_ws_stub()
        await sess.subscribe_trades()
        return stub.sent

    sent = asyncio.run(_run())
    assert "str+{}" in sent


def test_subscribe_market_data_sends_smd_frame() -> None:
    async def _run() -> list[str]:
        sess, stub = await _make_ws_stub()
        await sess.subscribe_market_data(265598, [31, 84, 86])
        return stub.sent

    sent = asyncio.run(_run())
    smd_frames = [f for f in sent if f.startswith("smd+")]
    assert len(smd_frames) == 1
    frame = smd_frames[0]
    assert "265598" in frame
    payload = json.loads(frame.split("+", 2)[2])
    assert "fields" in payload
    assert "31" in payload["fields"]
    assert "84" in payload["fields"]
    assert "86" in payload["fields"]


def test_unsubscribe_orders_sends_uor_frame() -> None:
    async def _run() -> list[str]:
        sess, stub = await _make_ws_stub()
        await sess.unsubscribe_orders()
        return stub.sent

    sent = asyncio.run(_run())
    assert "uor+{}" in sent


def test_unsubscribe_trades_sends_ustr_frame() -> None:
    async def _run() -> list[str]:
        sess, stub = await _make_ws_stub()
        await sess.unsubscribe_trades()
        return stub.sent

    sent = asyncio.run(_run())
    assert "ustr+{}" in sent


def test_unsubscribe_market_data_sends_umd_frame() -> None:
    async def _run() -> list[str]:
        sess, stub = await _make_ws_stub()
        await sess.unsubscribe_market_data(265598)
        return stub.sent

    sent = asyncio.run(_run())
    umd_frames = [f for f in sent if f.startswith("umd+")]
    assert len(umd_frames) == 1
    assert "265598" in umd_frames[0]


# ---------------------------------------------------------------------------
# messages() iterator — parses JSON, wraps non-JSON
# ---------------------------------------------------------------------------


def test_messages_yields_parsed_json_dicts() -> None:
    canned = [
        '{"topic":"sor","args":{"order_id":"ord-1","status":"PreSubmitted"}}',
        '{"topic":"system","hb":1234567890}',
    ]

    async def _run() -> list[dict]:
        sess, _ = await _make_ws_stub(recv_frames=canned)
        results = []
        async for msg in sess.messages():
            results.append(msg)
        return results

    results = asyncio.run(_run())
    assert len(results) == 2
    assert results[0]["topic"] == "sor"
    assert results[1]["topic"] == "system"
    assert results[1]["hb"] == 1234567890


def test_messages_wraps_non_json_as_raw() -> None:
    canned = [
        "tic",           # raw heartbeat ack — not JSON
        '{"topic":"str","fill":{"price":100.5}}',
    ]

    async def _run() -> list[dict]:
        sess, _ = await _make_ws_stub(recv_frames=canned)
        results = []
        async for msg in sess.messages():
            results.append(msg)
        return results

    results = asyncio.run(_run())
    assert results[0] == {"raw": "tic"}
    assert results[1]["topic"] == "str"


def test_messages_stops_when_recv_raises() -> None:
    """messages() must return (not raise) when recv throws."""

    async def _run() -> list[dict]:
        sess, _ = await _make_ws_stub(recv_frames=[])  # empty → recv raises immediately
        results = []
        async for msg in sess.messages():
            results.append(msg)
        return results

    results = asyncio.run(_run())
    assert results == []


# ---------------------------------------------------------------------------
# ping()
# ---------------------------------------------------------------------------


def test_ping_sends_tic_frame() -> None:
    async def _run() -> list[str]:
        sess, stub = await _make_ws_stub()
        await sess.ping()
        return stub.sent

    sent = asyncio.run(_run())
    assert "tic" in sent


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------


def test_close_called_on_stub() -> None:
    async def _run() -> bool:
        sess, stub = await _make_ws_stub()
        await sess.close()
        return stub.closed

    assert asyncio.run(_run()) is True


# ---------------------------------------------------------------------------
# Full interaction sequence
# ---------------------------------------------------------------------------


def test_full_sequence_session_subscribe_messages_close() -> None:
    """Exercise the full happy-path: handshake → subscribe → messages → close."""
    TOKEN = "full_seq_token"
    CONID = 265598

    canned = [
        '{"topic":"sor","args":{"order_id":"ord-42","status":"Submitted"}}',
        '{"topic":"smd","conid":265598,"31":"150.25"}',
        "non_json_frame",
    ]

    async def _run() -> tuple[list[dict], list[str], bool]:
        stub = _FakeWs(canned)

        async def _fake_connect(*args: Any, **kwargs: Any) -> _FakeWs:
            return stub

        auth = GatewayAuth(session=object())
        client = IbkrWebClient(auth=auth)
        client._auth.base_url = "https://localhost:5000/v1/api"

        session = await client.connect_ws(TOKEN, ws_connect=_fake_connect)
        await session.subscribe_orders()
        await session.subscribe_trades()
        await session.subscribe_market_data(CONID, [31, 84, 86])

        msgs: list[dict] = []
        async for m in session.messages():
            msgs.append(m)

        await session.close()
        return msgs, stub.sent, stub.closed

    msgs, sent, closed = asyncio.run(_run())

    # Handshake was first.
    first_frame = json.loads(sent[0])
    assert first_frame == {"session": TOKEN}

    # Subscriptions were sent.
    assert "sor+{}" in sent
    assert "str+{}" in sent
    assert any(f.startswith("smd+265598") for f in sent)

    # Messages were parsed / wrapped correctly.
    assert msgs[0]["topic"] == "sor"
    assert msgs[1]["topic"] == "smd"
    assert msgs[2] == {"raw": "non_json_frame"}

    # Connection was closed.
    assert closed is True