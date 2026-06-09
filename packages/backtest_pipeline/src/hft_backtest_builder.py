"""Shared HftBacktest asset builder (latency bands + queue models)."""
from __future__ import annotations

from hftbacktest import BacktestAsset, HashMapMarketDepthBacktest

from backtest_pipeline.src.fee_model import FeeModel


def _apply_constant_latency(asset: BacktestAsset, entry_ns: int, resp_ns: int) -> None:
    """Set constant order latency using whichever method the installed hftbacktest exposes.

    hftbacktest 2.4+ renamed ``constant_latency`` to ``constant_order_latency`` (with a
    deprecation warning on the old name). hftbacktest 2.3 only ships the old name.
    Pick whichever the running build provides.
    """
    if hasattr(asset, "constant_order_latency"):
        asset.constant_order_latency(entry_ns, resp_ns)
    else:
        asset.constant_latency(entry_ns, resp_ns)


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
    _apply_constant_latency(asset, latency_ns, latency_ns)
    asset.no_partial_fill_exchange()
    # Per-contract fees (USD/contract) on both sides: CME charges exchange+clearing
    # for maker and taker alike. trading_value_fee_model expects fractional rates on
    # notional, not dollar amounts — using it here charged ~10% of notional per fill.
    fee = fee_model.get_fee_per_contract()
    asset.trading_qty_fee_model(fee, fee)
    QUEUE_MODEL_BUILDERS[queue_model_type](asset)
    return HashMapMarketDepthBacktest([asset])
