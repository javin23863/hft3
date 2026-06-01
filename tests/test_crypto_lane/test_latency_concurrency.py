"""Concurrency safety tests for latency profile writers and live-probe fallback."""
from __future__ import annotations

import asyncio
import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from crypto_lane.src.align.latency_profile import (
    NodeLatencyProfile,
    VenueLatencyProfile,
    measure_live_ws_rtt,
    save_node_profile,
    save_venue_profile,
)


def _make_fake_ws_factory(pong_event: asyncio.Event):
    """Build a fake websockets.connect replacement whose ws.ping() blocks on pong_event."""

    class FakeWS:
        async def ping(self):
            await pong_event.wait()
            fut: asyncio.Future = asyncio.get_event_loop().create_future()
            fut.set_result(None)
            return fut

    class FakeCM:
        async def __aenter__(self):
            return FakeWS()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    return FakeCM


def test_measure_live_ws_rtt_awaits_pong(tmp_path, monkeypatch):
    monkeypatch.setattr("crypto_lane.src.align.latency_profile.venue_profiles_path", lambda: tmp_path / "venue_profiles.json")
    monkeypatch.setattr("crypto_lane.src.align.latency_profile.latency_dir", lambda: tmp_path)

    async def scenario():
        loop = asyncio.get_event_loop()
        pong_event = asyncio.Event()

        def fake_connect(url, **kwargs):
            return _make_fake_ws_factory(pong_event)()

        mock_websockets = MagicMock()
        mock_websockets.connect = fake_connect
        mock_websockets.exceptions = MagicMock()
        mock_websockets.exceptions.WebSocketException = Exception

        with patch.dict("sys.modules", {"websockets": mock_websockets}):
            task = loop.create_task(measure_live_ws_rtt("binance_perp", timeout_s=5.0))
            await asyncio.sleep(0.2)
            pong_event.set()
            profile = await task
            return profile

    profile = asyncio.run(scenario())
    assert profile.source.startswith("live_measured:"), profile.source


def _spawn_venue_writers(venues, barrier: threading.Barrier):
    threads = []
    for venue in venues:
        t = threading.Thread(
            target=_write_venue,
            args=(venue, barrier),
        )
        threads.append(t)
    return threads


def _write_venue(venue: str, barrier: threading.Barrier):
    profile = VenueLatencyProfile(
        venue=venue,
        theta_exch_ms=float(venue.count("a")),
        ws_rtt_ms=7.0,
        source=f"unit_test:{venue}",
    )
    barrier.wait()
    save_venue_profile(profile)


def test_save_venue_profile_is_concurrency_safe(tmp_path, monkeypatch):
    target = tmp_path / "venue_profiles.json"
    target.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("crypto_lane.src.align.latency_profile.venue_profiles_path", lambda: target)
    monkeypatch.setattr("crypto_lane.src.align.latency_profile.latency_dir", lambda: tmp_path)

    venues = [f"venue_{i}" for i in range(8)]
    barrier = threading.Barrier(len(venues))
    threads = _spawn_venue_writers(venues, barrier)
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    raw = json.loads(target.read_text(encoding="utf-8"))
    venues_saved = set((raw.get("venues") or {}).keys())
    assert venues_saved == set(venues), f"missing venues: {set(venues) - venues_saved}"


def _write_node(marker: int, barrier: threading.Barrier):
    profile = NodeLatencyProfile(
        theta_node_ms=float(marker),
        network_latency_ms=5.0 + marker,
        processing_latency_ms=2.0,
        node_rtt_ms=10.0 + marker,
        source=f"unit_test:{marker}",
    )
    barrier.wait()
    save_node_profile(profile)


def test_save_node_profile_is_concurrency_safe(tmp_path, monkeypatch):
    target = tmp_path / "node_profile.json"
    target.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("crypto_lane.src.align.latency_profile.node_profile_path", lambda: target)
    monkeypatch.setattr("crypto_lane.src.align.latency_profile.latency_dir", lambda: tmp_path)

    n = 8
    barrier = threading.Barrier(n)
    threads = [
        threading.Thread(target=_write_node, args=(i, barrier))
        for i in range(n)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    raw = json.loads(target.read_text(encoding="utf-8"))
    assert "theta_node_ms" in raw
    assert "source" in raw
    assert raw["source"].startswith("unit_test:")
    marker = int(raw["source"].rsplit(":", 1)[-1])
    assert raw["network_latency_ms"] == pytest.approx(5.0 + marker)
    assert raw["node_rtt_ms"] == pytest.approx(10.0 + marker)


def test_measure_live_ws_rtt_propagates_cancellation(tmp_path, monkeypatch):
    # asyncio.CancelledError is BaseException (not Exception) and must propagate so callers
    # can cancel cleanly. Catching it would silently return a synthetic profile, hiding
    # the cancellation from the parent task.
    monkeypatch.setattr("crypto_lane.src.align.latency_profile.venue_profiles_path", lambda: tmp_path / "venue_profiles.json")
    monkeypatch.setattr("crypto_lane.src.align.latency_profile.latency_dir", lambda: tmp_path)

    def fake_connect(*args, **kwargs):
        raise asyncio.CancelledError()

    mock_websockets = MagicMock()
    mock_websockets.connect = fake_connect
    mock_websockets.exceptions = MagicMock()
    mock_websockets.exceptions.WebSocketException = Exception

    with patch.dict("sys.modules", {"websockets": mock_websockets}):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(measure_live_ws_rtt("binance_perp"))


def test_measure_live_ws_rtt_falls_back_on_generic_exception(tmp_path, monkeypatch):
    monkeypatch.setattr("crypto_lane.src.align.latency_profile.venue_profiles_path", lambda: tmp_path / "venue_profiles.json")
    monkeypatch.setattr("crypto_lane.src.align.latency_profile.latency_dir", lambda: tmp_path)

    def fake_connect(*args, **kwargs):
        raise RuntimeError("boom")

    mock_websockets = MagicMock()
    mock_websockets.connect = fake_connect
    mock_websockets.exceptions = MagicMock()
    mock_websockets.exceptions.WebSocketException = Exception

    with patch.dict("sys.modules", {"websockets": mock_websockets}):
        profile = asyncio.run(measure_live_ws_rtt("binance_perp"))
        assert profile.source == f"synthetic_calibrated:binance_perp", profile.source
