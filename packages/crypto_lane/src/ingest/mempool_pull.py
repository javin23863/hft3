"""Pull BTC mempool snapshots via tunneled bitcoind RPC."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from crypto_lane.src.align.latency_profile import measure_node_profile_from_btc, save_node_profile
from crypto_lane.src.ingest.btc_rpc import BtcRpc
from crypto_lane.src.ingest.paths import bronze_dir, ensure_data_dirs


@dataclass(frozen=True)
class MempoolSnapshot:
    node_observation_time: int
    timestamp_iso: str
    mempool_bytes: int
    mempool_max_bytes: int
    mempool_tx_count: int
    min_fee_sat: float
    btc_blockspace_stress_score: float
    node_clock_drift_ms: float
    network_latency_ms: float
    processing_latency_ms: float
    exchange_clock_drift_ms: float
    estimated_latency_ms: float

    def to_bronze_row(self) -> dict[str, object]:
        usage = self.mempool_bytes / max(self.mempool_max_bytes, 1)
        return {
            "timestamp": self.timestamp_iso,
            "node_observation_time": self.node_observation_time,
            "bytes": self.mempool_bytes,
            "usage_bytes": self.mempool_bytes,
            "size_txs": self.mempool_tx_count,
            "mempool_min_fee": self.min_fee_sat / 1e8,
            "blockspace_stress_score": self.btc_blockspace_stress_score,
            "usage_ratio": usage,
        }


DEFAULT_MEMPOOL_MAX_BYTES = 300_000_000


def _fee_sat_per_vbyte(fee_btc_kvb: float) -> float:
    if fee_btc_kvb <= 0:
        return 0.0
    return fee_btc_kvb * 1e8 / 1000.0


def snapshot_from_rpc(client: BtcRpc | None = None, *, tunnel_rtt_ms: float | None = None) -> MempoolSnapshot:
    c = client or BtcRpc()
    t0 = time.perf_counter()
    info = c.getmempoolinfo()
    fee_btc_kvb = c.estimatesmartfee(6)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    now_ms = int(time.time() * 1000)
    usage = info.usage / max(DEFAULT_MEMPOOL_MAX_BYTES, 1)
    min_fee_sat = _fee_sat_per_vbyte(max(info.mempool_min_fee, fee_btc_kvb))

    node_profile = measure_node_profile_from_btc(tunnel_rtt_ms=tunnel_rtt_ms)
    save_node_profile(node_profile)

    processing_ms = max(node_profile.processing_latency_ms, elapsed_ms)
    network_ms = node_profile.network_latency_ms
    theta_node = node_profile.theta_node_ms
    estimated = network_ms + processing_ms + abs(theta_node)

    return MempoolSnapshot(
        node_observation_time=now_ms,
        timestamp_iso=datetime.fromtimestamp(now_ms / 1000, tz=UTC).isoformat(),
        mempool_bytes=int(info.usage),
        mempool_max_bytes=DEFAULT_MEMPOOL_MAX_BYTES,
        mempool_tx_count=int(info.size),
        min_fee_sat=min_fee_sat,
        btc_blockspace_stress_score=min(1.0, usage * 1.2),
        node_clock_drift_ms=theta_node,
        network_latency_ms=network_ms,
        processing_latency_ms=processing_ms,
        exchange_clock_drift_ms=0.0,
        estimated_latency_ms=estimated,
    )


def write_mempool_snapshot(snapshot: MempoolSnapshot, out_dir: Path | None = None) -> Path:
    ensure_data_dirs()
    root = out_dir or bronze_dir() / "bitcoind" / "mempool"
    root.mkdir(parents=True, exist_ok=True)
    day = snapshot.timestamp_iso[:10]
    path = root / f"{day}_mempool_snapshot.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(snapshot)) + "\n")
    return path


def pull_live_mempool(*, samples: int = 1, interval_minutes: int = 15, tunnel_rtt_ms: float | None = None) -> list[MempoolSnapshot]:
    client = BtcRpc()
    out: list[MempoolSnapshot] = []
    for i in range(samples):
        snap = snapshot_from_rpc(client, tunnel_rtt_ms=tunnel_rtt_ms)
        write_mempool_snapshot(snap)
        out.append(snap)
        if i + 1 < samples:
            time.sleep(max(1, interval_minutes * 60))
    return out


def load_mempool_bronze(start: datetime, end: datetime) -> pl.DataFrame:
    root = bronze_dir() / "bitcoind" / "mempool"
    if not root.is_dir():
        return pl.DataFrame()
    rows: list[dict[str, object]] = []
    for path in sorted(root.glob("*_mempool_snapshot.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            ts = datetime.fromisoformat(str(row["timestamp_iso"]).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            if start <= ts.replace(tzinfo=UTC) <= end:
                rows.append(row)
    if not rows:
        return pl.DataFrame()
    return pl.DataFrame(rows)


def pull_mempool_backfill(*, hours: int = 24, interval_minutes: int = 15, tunnel_rtt_ms: float | None = None) -> int:
    """Sample live mempool at interval; returns snapshot count written."""
    samples = max(1, (hours * 60) // max(1, interval_minutes))
    return len(pull_live_mempool(samples=samples, interval_minutes=interval_minutes, tunnel_rtt_ms=tunnel_rtt_ms))
