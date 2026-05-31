"""Venue and node latency profiles for PIT availability boundary."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from crypto_lane.src.align.clock_sync import (
    SyncTimestamps,
    exchange_offset_from_ws_rtt,
    node_offset_from_handshake,
    one_way_latency_ms,
)
from crypto_lane.src.config_loader import load_universe
from crypto_lane.src.ingest.paths import data_root


@dataclass(frozen=True)
class VenueLatencyProfile:
    venue: str
    theta_exch_ms: float
    ws_rtt_ms: float
    source: str


@dataclass(frozen=True)
class NodeLatencyProfile:
    theta_node_ms: float
    network_latency_ms: float
    processing_latency_ms: float
    node_rtt_ms: float
    source: str


def default_venue_from_backtest(backtest: dict[str, Any]) -> str:
    venues = backtest.get("venues") or []
    if "binance_perp" in venues:
        return "binance_perp"
    if venues:
        return str(venues[0])
    return "binance_perp"


def latency_dir() -> Path:
    return data_root() / "latency"


def venue_profiles_path() -> Path:
    return latency_dir() / "venue_profiles.json"


def node_profile_path() -> Path:
    return latency_dir() / "node_profile.json"


def resolve_theta_exch(venue: str, backtest: dict[str, Any] | None = None) -> VenueLatencyProfile:
    """
    Resolve θ_exch for a venue.

    Priority: saved live probe artifact → backtest-calibrated ws_rtt_ms synthesis.
    """
    saved = load_venue_profiles().get(venue)
    if saved is not None and not saved.source.startswith(
        ("synthetic_calibrated:", "backtest_calibrated:", "ws_rtt:")
    ):
        return saved

    bt = backtest or {}
    latency = bt.get("latency_assumptions") or {}
    ws_rtt_ms = float(latency.get("ws_rtt_ms", 5.0))
    ping_ns = 0
    pong_ns = int(ws_rtt_ms * 1_000_000)
    off = exchange_offset_from_ws_rtt(ping_ns, pong_ns, venue=venue)
    return VenueLatencyProfile(
        venue=venue,
        theta_exch_ms=off.theta_ms,
        ws_rtt_ms=off.rtt_ms,
        source=f"backtest_calibrated:{venue}",
    )


def resolve_node_latency(*, defaults: dict[str, Any] | None = None) -> NodeLatencyProfile:
    """Load node latency profile from artifact or universe defaults."""
    path = node_profile_path()
    if path.is_file():
        raw = json.loads(path.read_text(encoding="utf-8"))
        return NodeLatencyProfile(
            theta_node_ms=float(raw.get("theta_node_ms", 0.0)),
            network_latency_ms=float(raw.get("network_latency_ms", 5.0)),
            processing_latency_ms=float(raw.get("processing_latency_ms", 2.0)),
            node_rtt_ms=float(raw.get("node_rtt_ms", 10.0)),
            source=str(raw.get("source", "artifact")),
        )

    uni = load_universe()
    defs = defaults or uni.get("defaults") or {}
    network = float(defs.get("network_latency_ms", 5.0))
    processing = float(defs.get("processing_latency_ms", 2.0))
    return NodeLatencyProfile(
        theta_node_ms=float(defs.get("node_clock_drift_ms", 0.0)),
        network_latency_ms=network,
        processing_latency_ms=processing,
        node_rtt_ms=network * 2.0,
        source="universe_defaults",
    )


def measure_node_profile_from_btc(*, tunnel_rtt_ms: float | None = None) -> NodeLatencyProfile:
    """
    Estimate θ_node from bitcoind mediantime vs wall clock; δ_net from tunnel RTT/2.
    """
    theta_node_ms = 0.0
    source = "btc_median_time"
    try:
        from crypto_lane.src.ingest.btc_rpc import BtcRpc

        chain = BtcRpc().getblockchaininfo()
        wall_ms = int(time.time() * 1000)
        theta_node_ms = float(wall_ms - chain.median_time)
        source = "btc_median_time"
    except OSError:
        source = "btc_median_time_unavailable"

    if tunnel_rtt_ms is not None and tunnel_rtt_ms > 0:
        ts = SyncTimestamps(
            t1_local_send_ns=0,
            t2_remote_recv_ns=int(tunnel_rtt_ms * 500_000),
            t3_remote_send_ns=int(tunnel_rtt_ms * 500_000),
            t4_local_recv_ns=int(tunnel_rtt_ms * 1_000_000),
        )
        off = node_offset_from_handshake(ts, source="btc_tunnel_rtt")
        return NodeLatencyProfile(
            theta_node_ms=off.theta_ms if abs(off.theta_ms) > abs(theta_node_ms) else theta_node_ms,
            network_latency_ms=one_way_latency_ms(off.rtt_ms),
            processing_latency_ms=2.0,
            node_rtt_ms=off.rtt_ms,
            source="btc_tunnel_handshake",
        )

    node = resolve_node_latency()
    return NodeLatencyProfile(
        theta_node_ms=theta_node_ms if theta_node_ms else node.theta_node_ms,
        network_latency_ms=node.network_latency_ms,
        processing_latency_ms=node.processing_latency_ms,
        node_rtt_ms=node.node_rtt_ms,
        source=source,
    )


def calibrate_ws_rtt(venue: str, *, ws_rtt_ms: float | None = None) -> VenueLatencyProfile:
    """
    Synthetic replay calibration from supplied or default ws_rtt_ms.

    Not a live WebSocket probe — use only when no measured ping/pong artifact exists.
    """
    latency_dir().mkdir(parents=True, exist_ok=True)
    rtt = float(ws_rtt_ms if ws_rtt_ms is not None else 5.0)
    ping_ns = 0
    pong_ns = int(rtt * 1_000_000)
    off = exchange_offset_from_ws_rtt(ping_ns, pong_ns, venue=venue)
    profile = VenueLatencyProfile(
        venue=venue,
        theta_exch_ms=off.theta_ms,
        ws_rtt_ms=off.rtt_ms,
        source=f"synthetic_calibrated:{venue}",
    )
    save_venue_profile(profile)
    return profile


def probe_ws_rtt(venue: str, *, ws_rtt_ms: float | None = None) -> VenueLatencyProfile:
    """Deprecated alias for calibrate_ws_rtt."""
    return calibrate_ws_rtt(venue, ws_rtt_ms=ws_rtt_ms)


def load_venue_profiles() -> dict[str, VenueLatencyProfile]:
    path = venue_profiles_path()
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, VenueLatencyProfile] = {}
    for venue, row in (raw.get("venues") or {}).items():
        out[venue] = VenueLatencyProfile(
            venue=venue,
            theta_exch_ms=float(row["theta_exch_ms"]),
            ws_rtt_ms=float(row["ws_rtt_ms"]),
            source=str(row.get("source", "artifact")),
        )
    return out


def save_venue_profile(profile: VenueLatencyProfile) -> None:
    latency_dir().mkdir(parents=True, exist_ok=True)
    path = venue_profiles_path()
    doc: dict[str, Any] = {"venues": {}}
    if path.is_file():
        doc = json.loads(path.read_text(encoding="utf-8"))
    doc.setdefault("venues", {})[profile.venue] = asdict(profile)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def save_node_profile(profile: NodeLatencyProfile) -> None:
    latency_dir().mkdir(parents=True, exist_ok=True)
    node_profile_path().write_text(
        json.dumps(asdict(profile), indent=2),
        encoding="utf-8",
    )
