"""Latency profile resolver and live probe tests."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import json

from crypto_lane.src.align.latency_profile import (
    MAX_CLOCK_DRIFT_MS,
    VenueLatencyProfile,
    assert_promotion_rtt_source,
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


def test_measure_live_ws_rtt_fallback_on_oserror(tmp_path, monkeypatch):
    monkeypatch.setattr("crypto_lane.src.align.latency_profile.venue_profiles_path", lambda: tmp_path / "venue_profiles.json")
    monkeypatch.setattr("crypto_lane.src.align.latency_profile.latency_dir", lambda: tmp_path)
    mock_websockets = MagicMock()
    mock_websockets.connect = MagicMock(side_effect=OSError("refused"))
    mock_websockets.exceptions = MagicMock()
    mock_websockets.exceptions.WebSocketException = OSError

    with patch.dict("sys.modules", {"websockets": mock_websockets}):
        profile = asyncio.run(measure_live_ws_rtt("binance_perp"))
        assert profile.source.startswith("synthetic_calibrated:")


def test_measure_live_ws_rtt_unknown_venue(tmp_path, monkeypatch):
    monkeypatch.setattr("crypto_lane.src.align.latency_profile.venue_profiles_path", lambda: tmp_path / "venue_profiles.json")
    monkeypatch.setattr("crypto_lane.src.align.latency_profile.latency_dir", lambda: tmp_path)
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


def test_bitfinex_in_venue_url_map(tmp_path, monkeypatch):
    monkeypatch.setattr("crypto_lane.src.align.latency_profile.venue_profiles_path", lambda: tmp_path / "venue_profiles.json")
    monkeypatch.setattr("crypto_lane.src.align.latency_profile.latency_dir", lambda: tmp_path)
    import pytest

    async def run_test():
        captured_urls = []

        pong_future: asyncio.Future = asyncio.get_event_loop().create_future()
        pong_future.set_result(None)

        async def fake_ping():
            return pong_future

        mock_ws = AsyncMock()
        mock_ws.ping = fake_ping

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        def fake_connect(url, **kwargs):
            captured_urls.append(url)
            return mock_cm

        mock_websockets = MagicMock()
        mock_websockets.connect = MagicMock(side_effect=fake_connect)
        mock_websockets.exceptions = MagicMock()
        mock_websockets.exceptions.WebSocketException = Exception

        with patch.dict("sys.modules", {"websockets": mock_websockets}):
            profile = await measure_live_ws_rtt("bitfinex")
            assert profile.venue == "bitfinex"
            assert captured_urls == ["wss://api.bitfinex.com/ws/2"]

        # garbage venue falls back to synthetic (does not raise unknown-venue error)
        with patch.dict("sys.modules", {"websockets": mock_websockets}):
            fallback = await measure_live_ws_rtt("garbage_venue_xyz")
            assert fallback.source.startswith("synthetic_calibrated:")

    asyncio.run(run_test())


def test_k7_missing_source_field_hard_fails(tmp_path):
    import pytest
    profiles_file = tmp_path / "venue_profiles.json"
    profiles_file.write_text(
        json.dumps({"venues": {"binance_perp": {"theta_exch_ms": 1.0, "ws_rtt_ms": 2.0}}}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="No RTT profile found for venue 'binance_perp'"):
        assert_promotion_rtt_source("binance_perp", profiles_path=profiles_file)


def test_k7_synthetic_source_hard_fails(tmp_path):
    profiles_file = tmp_path / "venue_profiles.json"
    profiles_file.write_text(
        json.dumps({"venues": {"kraken_spot": {"theta_exch_ms": 1.0, "ws_rtt_ms": 2.0, "source": "synthetic_calibrated:whatever"}}}),
        encoding="utf-8",
    )
    import pytest
    with pytest.raises(RuntimeError, match="kraken_spot"):
        assert_promotion_rtt_source("kraken_spot", profiles_path=profiles_file)


def test_k7_live_measured_passes(tmp_path):
    profiles_file = tmp_path / "venue_profiles.json"
    profiles_file.write_text(
        json.dumps({"venues": {
            "binance_perp": {"theta_exch_ms": 1.0, "ws_rtt_ms": 2.0, "source": "live_measured:binance_perp"},
            "kraken_spot": {"theta_exch_ms": 1.0, "ws_rtt_ms": 2.0, "source": "ws_rtt:kraken_spot"},
        }}),
        encoding="utf-8",
    )
    assert_promotion_rtt_source("binance_perp", profiles_path=profiles_file)
    assert_promotion_rtt_source("kraken_spot", profiles_path=profiles_file)


def test_k7_missing_profile_hard_fails(tmp_path):
    profiles_file = tmp_path / "venue_profiles.json"
    profiles_file.write_text(json.dumps({"venues": {}}), encoding="utf-8")
    import pytest
    with pytest.raises(RuntimeError, match="no_such_venue"):
        assert_promotion_rtt_source("no_such_venue", profiles_path=profiles_file)