"""Latency profile resolver tests."""
from __future__ import annotations

from crypto_lane.src.align.latency_profile import resolve_theta_exch


def test_resolve_theta_exch_from_backtest_ws_rtt():
    bt = {"latency_assumptions": {"ws_rtt_ms": 10.0, "ws_rtt_tracking": True}, "venues": ["binance_perp"]}
    profile = resolve_theta_exch("binance_perp", bt)
    assert profile.ws_rtt_ms == 10.0
    assert profile.source.startswith("backtest_calibrated:")
