"""Latency profile resolver and live probe tests."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from crypto_lane.src.align.latency_profile import (
    MAX_CLOCK_DRIFT_MS,
    VenueLatencyProfile,
    calibrate_ws_rtt,
    load_venue_profiles,
    measure_live_ws_rtt,
    measure_node_profile_from_btc,
    resolve_theta_exch,
    save_venue_profile,
    venue_profiles_path,
)


def test_resolve_theta_exch_from_backtest_ws_rtt(tmp_path, monkeypatch):
    monkeypatch.setattr("crypto_lane.src.align.latency_profile.venue_profiles_path", lambda: tmp_path / "venue_profiles.json")
    bt = {"latency_assumptions": {"ws_rtt_ms": 10.0, "ws_rtt_tracking": True}, "venues": ["binance_perp"]}
    profile = resolve_theta_exch("binance_perp", bt)
    assert profile.ws_rtt_ms == 10.0
    assert profile.source.startswith("backtest_calibrated:")


def test_measure_node_profile_clamps_theta():
    node = measure_node_profile_from_btc()
    assert abs(node.theta_node_ms) <= MAX_CLOCK_DRIFT_MS


def test_measure_live_ws_rtt_saves_profile(tmp_path, monkeypatch):
    monkeypatch.setattr("crypto_lane.src.align.latency_profile.venue_profiles_path", lambda: tmp_path / "venue_profiles.json")
    monkeypatch.setattr("crypto_lane.src.align.latency_profile.latency_dir", lambda: tmp_path)

    # ws.ping() must return an awaitable that resolves on pong
    async def run_test():
        pong_future: asyncio.Future = asyncio.get_event_loop().create_future()
        pong_future.set_result(None)

        async def fake_ping():
            return pong_future

        mock_ws = AsyncMock()
        mock_ws.ping = fake_ping

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        mock_websockets = MagicMock()
        mock_websockets.connect = MagicMock(return_value=mock_cm)
        mock_websockets.exceptions = MagicMock()
        mock_websockets.exceptions.WebSocketException = Exception

        with patch.dict("sys.modules", {"websockets": mock_websockets}):
            profile = await measure_live_ws_rtt("binance_perp")
            assert profile.source.startswith("live_measured:")
            assert profile.venue == "binance_perp"

    asyncio.run(run_test())


def test_measure_live_ws_rtt_fallback_on_oserror():
    mock_websockets = MagicMock()
    mock_websockets.connect = MagicMock(side_effect=OSError("refused"))
    mock_websockets.exceptions = MagicMock()
    mock_websockets.exceptions.WebSocketException = OSError

    with patch.dict("sys.modules", {"websockets": mock_websockets}):
        profile = asyncio.run(measure_live_ws_rtt("binance_perp"))
        assert profile.source.startswith("synthetic_calibrated:")


def test_measure_live_ws_rtt_unknown_venue():
    profile = asyncio.run(measure_live_ws_rtt("unknown_venue_xyz"))
    assert profile.source.startswith("synthetic_calibrated:")


def test_calibrate_ws_rtt_saved_profile_is_loaded(tmp_path, monkeypatch):
    monkeypatch.setattr("crypto_lane.src.align.latency_profile.venue_profiles_path", lambda: tmp_path / "venue_profiles.json")
    monkeypatch.setattr("crypto_lane.src.align.latency_profile.latency_dir", lambda: tmp_path)
    calibrate_ws_rtt("binance_perp", ws_rtt_ms=8.0)
    loaded = resolve_theta_exch("binance_perp")
    assert loaded.source.startswith("synthetic_calibrated:")
    assert abs(loaded.ws_rtt_ms - 8.0) < 1e-6


def test_backtest_calibrated_is_not_saved(tmp_path, monkeypatch):
    monkeypatch.setattr("crypto_lane.src.align.latency_profile.venue_profiles_path", lambda: tmp_path / "venue_profiles.json")
    monkeypatch.setattr("crypto_lane.src.align.latency_profile.latency_dir", lambda: tmp_path)
    bt = {"latency_assumptions": {"ws_rtt_ms": 10.0}, "venues": ["binance_perp"]}
    profile = resolve_theta_exch("binance_perp", bt)
    assert profile.source.startswith("backtest_calibrated:")
    assert not (tmp_path / "venue_profiles.json").is_file(), "backtest_calibrated should not save artifact"