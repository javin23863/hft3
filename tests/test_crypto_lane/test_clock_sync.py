"""Clock sync RTT and NTP sign-convention tests."""
from __future__ import annotations

from crypto_lane.src.align.clock_sync import (
    SyncTimestamps,
    compute_rtt_ms,
    compute_theta_ms,
    exchange_offset_from_ws_rtt,
    one_way_latency_ms,
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


def test_theta_positive_when_remote_ahead():
    ts = SyncTimestamps(
        t1_local_send_ns=0,
        t2_remote_recv_ns=60_000_000,
        t3_remote_send_ns=65_000_000,
        t4_local_recv_ns=100_000_000,
    )
    theta = compute_theta_ms(ts)
    assert theta > 0, "θ must be positive when remote clock is ahead"
    rtt = compute_rtt_ms(ts)
    assert rtt > 0


def test_theta_negative_when_remote_behind():
    ts = SyncTimestamps(
        t1_local_send_ns=0,
        t2_remote_recv_ns=40_000_000,
        t3_remote_send_ns=45_000_000,
        t4_local_recv_ns=100_000_000,
    )
    theta = compute_theta_ms(ts)
    assert theta < 0, "θ must be negative when remote clock is behind"


def test_theta_hand_computed():
    ts = SyncTimestamps(
        t1_local_send_ns=0,
        t2_remote_recv_ns=60_000_000,
        t3_remote_send_ns=65_000_000,
        t4_local_recv_ns=100_000_000,
    )
    rtt = compute_rtt_ms(ts)
    theta = compute_theta_ms(ts)
    assert abs(rtt - 95.0) < 1e-6, "RTT = (T4-T1)-(T3-T2) = 100-5 = 95ms"
    assert abs(theta - 12.5) < 1e-6, "θ = ((60-0)+(65-100))/2 = 12.5ms"


def test_ws_rtt_offset():
    off = exchange_offset_from_ws_rtt(0, 100_000_000, venue="binance_perp")
    assert off.rtt_ms > 0
    assert off.source.startswith("ws_rtt:")


def test_one_way_latency_half_rtt():
    assert one_way_latency_ms(10.0) == 5.0
    assert one_way_latency_ms(0.0) == 0.0
