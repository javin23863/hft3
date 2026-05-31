"""Clock sync RTT tests."""
from __future__ import annotations

from crypto_lane.src.align.clock_sync import (
    SyncTimestamps,
    compute_rtt_ms,
    compute_theta_ms,
    exchange_offset_from_ws_rtt,
)


def test_rtt_and_theta_symmetric():
    ts = SyncTimestamps(
        t1_local_send_ns=0,
        t2_remote_recv_ns=50_000_000,
        t3_remote_send_ns=50_000_000,
        t4_local_recv_ns=100_000_000,
    )
    assert abs(compute_rtt_ms(ts) - 100.0) < 1e-6
    assert abs(compute_theta_ms(ts)) < 1e-6


def test_ws_rtt_offset():
    off = exchange_offset_from_ws_rtt(0, 100_000_000, venue="binance_perp")
    assert off.rtt_ms > 0
    assert off.source.startswith("ws_rtt:")
