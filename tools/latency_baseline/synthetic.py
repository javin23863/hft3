"""Synthetic latency baseline generator used for no-broker validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .recorder import LatencyRecorder


@dataclass
class SyntheticConfig:
    repo_root: Path
    run_id: str
    environment: str = "synthetic"
    broker: str = "none"
    venue: str = "synthetic"
    symbol: str = "SYN"
    strategy_id: str = "latency_probe"
    model_id: str = "synthetic_model"
    trade_manager_id: str = "synthetic_trade_manager"
    duration_seconds: float = 30.0
    samples: int | None = None


def synthetic_sample_count(duration_seconds: float, samples: int | None = None) -> int:
    if samples is not None:
        return max(1, int(samples))
    return max(1, int(float(duration_seconds) * 10))


def run_synthetic(config: SyntheticConfig) -> tuple[Path, list[dict]]:
    recorder = LatencyRecorder(
        repo_root=config.repo_root,
        run_id=config.run_id,
        environment=config.environment,
        broker=config.broker,
        venue=config.venue,
        symbol=config.symbol,
        strategy_id=config.strategy_id,
        model_id=config.model_id,
        trade_manager_id=config.trade_manager_id,
    )
    records: list[dict] = []
    base_ns = 1_000_000_000
    spacing_ns = 1_000_000
    count = synthetic_sample_count(config.duration_seconds, config.samples)
    for idx in range(count):
        action = ("new", "cancel", "replace")[idx % 3]
        start = base_ns + idx * spacing_ns
        timestamps = _timestamps_for_action(action, start, idx)
        record = recorder.write_sample(
            order_action=action,
            side="buy" if idx % 2 == 0 else "sell",
            order_type="limit",
            quantity=1,
            timestamps=timestamps,
            success=True,
        )
        records.append(record)
    return recorder.sample_path(), records


def _timestamps_for_action(action: str, start_ns: int, idx: int) -> dict[str, int | None]:
    feature_us = 10 + (idx % 5)
    decision_us = 20 + (idx % 7)
    risk_us = 8 + (idx % 3)
    order_ready_us = 6 + (idx % 4)
    send_us = 5 + (idx % 6)
    ack_us = 900 + (idx % 50)
    tick = start_ns
    features = tick + feature_us * 1000
    decision = features + decision_us * 1000
    risk = decision + risk_us * 1000
    order_ready = risk + order_ready_us * 1000
    order_send = order_ready + send_us * 1000
    if action == "new":
        return {
            "market_event_received_ts": tick,
            "features_ready_ts": features,
            "decision_ready_ts": decision,
            "risk_check_ready_ts": risk,
            "order_ready_ts": order_ready,
            "order_send_ts": order_send,
            "ack_received_ts": order_send + ack_us * 1000,
        }
    if action == "cancel":
        cancel_send = decision + (15 + idx % 5) * 1000
        return {
            "market_event_received_ts": tick,
            "features_ready_ts": features,
            "decision_ready_ts": decision,
            "cancel_send_ts": cancel_send,
            "cancel_ack_received_ts": cancel_send + (700 + idx % 40) * 1000,
        }
    replace_send = decision + (18 + idx % 6) * 1000
    return {
        "market_event_received_ts": tick,
        "features_ready_ts": features,
        "decision_ready_ts": decision,
        "replace_send_ts": replace_send,
        "replace_ack_received_ts": replace_send + (800 + idx % 45) * 1000,
    }
