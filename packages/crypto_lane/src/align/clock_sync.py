"""Clock drift estimation via 4-timestamp handshake and WebSocket RTT."""
from __future__ import annotations

from dataclasses import dataclass

from crypto_lane.src.types import ClockOffset


@dataclass(frozen=True)
class SyncTimestamps:
    t1_local_send_ns: int
    t2_remote_recv_ns: int
    t3_remote_send_ns: int
    t4_local_recv_ns: int


def compute_rtt_ms(ts: SyncTimestamps) -> float:
    """RTT = (T4 - T1) - (T3 - T2) in milliseconds."""
    rtt_ns = (ts.t4_local_recv_ns - ts.t1_local_send_ns) - (ts.t3_remote_send_ns - ts.t2_remote_recv_ns)
    return max(0.0, rtt_ns / 1_000_000.0)


def compute_theta_ms(ts: SyncTimestamps) -> float:
    """θ = ((T2 - T1) + (T3 - T4)) / 2 in milliseconds."""
    theta_ns = ((ts.t2_remote_recv_ns - ts.t1_local_send_ns) + (ts.t3_remote_send_ns - ts.t4_local_recv_ns)) / 2.0
    return theta_ns / 1_000_000.0


def local_true_time_ns(nominal_local_ns: int, theta_ms: float) -> int:
    """T_local_true = T_local_nominal - θ."""
    return int(nominal_local_ns - theta_ms * 1_000_000.0)


def one_way_latency_ms(rtt_ms: float) -> float:
    """One-way propagation δ_net from round-trip measurement."""
    return max(0.0, rtt_ms / 2.0)


def exchange_offset_from_ws_rtt(
    ping_send_ns: int,
    pong_recv_ns: int,
    *,
    venue: str,
    server_processing_ns: int = 0,
) -> ClockOffset:
    """
    WebSocket ping/pong RTT tracking for θ_exch.

    Uses symmetric assumption: remote recv/send at midpoint of RTT.
    """
    rtt_ns = pong_recv_ns - ping_send_ns
    half = rtt_ns // 2
    ts = SyncTimestamps(
        t1_local_send_ns=ping_send_ns,
        t2_remote_recv_ns=ping_send_ns + half,
        t3_remote_send_ns=ping_send_ns + half + server_processing_ns,
        t4_local_recv_ns=pong_recv_ns,
    )
    return ClockOffset(
        theta_ms=compute_theta_ms(ts),
        rtt_ms=compute_rtt_ms(ts),
        sample_time_ns=pong_recv_ns,
        source=f"ws_rtt:{venue}",
    )


def node_offset_from_handshake(ts: SyncTimestamps, *, source: str = "ntp") -> ClockOffset:
    return ClockOffset(
        theta_ms=compute_theta_ms(ts),
        rtt_ms=compute_rtt_ms(ts),
        sample_time_ns=ts.t4_local_recv_ns,
        source=source,
    )
