"""Shared HftBacktest asset builder (latency bands + queue models)."""
from __future__ import annotations

from hftbacktest import BacktestAsset, HashMapMarketDepthBacktest

from backtest_pipeline.src.fee_model import FeeModel

LATENCY_BANDS_MS = [0.5, 1.0, 2.0, 5.0, 10.0]
QUEUE_MODEL_BUILDERS = {
    "LogProbQueueModel2": lambda a: a.log_prob_queue_model2(),
    "SquareProbQueueModel": lambda a: a.power_prob_queue_model2(2),
}
QUEUE_MODELS = list(QUEUE_MODEL_BUILDERS.keys())


def build_hftbacktest(
    data_path: str,
    *,
    latency_ms: float = 1.0,
    queue_model_type: str = "LogProbQueueModel2",
    tick_size: float = 0.25,
    lot_size: float = 1.0,
    product: str = "MES",
) -> HashMapMarketDepthBacktest:
    if queue_model_type not in QUEUE_MODEL_BUILDERS:
        raise ValueError(f"Unsupported queue model: {queue_model_type}")

    fee_model = FeeModel(product=product)
    latency_ns = int(latency_ms * 1_000_000)
    asset = BacktestAsset()
    asset.data(data_path)
    asset.tick_size(tick_size)
    asset.lot_size(lot_size)
    asset.constant_latency(latency_ns, latency_ns)
    asset.no_partial_fill_exchange()
    asset.trading_value_fee_model(0.0, fee_model.get_fee_per_contract())
    QUEUE_MODEL_BUILDERS[queue_model_type](asset)
    return HashMapMarketDepthBacktest([asset])
