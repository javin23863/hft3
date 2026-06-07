"""θ sign convention: T_local_true = T_nominal - θ."""
from __future__ import annotations

from crypto_lane.src.align.clock_sync import (
    SyncTimestamps,
    compute_rtt_ms,
    compute_theta_ms,
    local_true_time_ns,
)


def test_theta_positive_when_remote_ahead():
    ts = SyncTimestamps(
        t1_local_send_ns=0,
        t2_remote_recv_ns=5_000_000,
        t3_remote_send_ns=5_000_000,
        t4_local_recv_ns=4_000_000,
    )
    theta = compute_theta_ms(ts)
    rtt = compute_rtt_ms(ts)
    assert rtt == 4.0
    assert theta == 3.0
    assert local_true_time_ns(4_000_000, theta) == 1_000_000


def test_theta_zero_symmetric():
    ts = SyncTimestamps(
        t1_local_send_ns=0,
        t2_remote_recv_ns=1_000_000,
        t3_remote_send_ns=1_000_000,
        t4_local_recv_ns=2_000_000,
    )
    assert compute_theta_ms(ts) == 0.0
    assert compute_rtt_ms(ts) == 2.0
