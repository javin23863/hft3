"""Crypto-specific HftBacktest asset builder — configurable tick/lot size, percentage fees, partial fills."""

from __future__ import annotations

from typing import Dict, Tuple

from hftbacktest import BacktestAsset, HashMapMarketDepthBacktest


SYMBOL_CONFIGS: Dict[str, Dict] = {
    "BTCUSDT": {"tick_size": 0.1, "lot_size": 0.001},
    "ETHUSDT": {"tick_size": 0.01, "lot_size": 0.01},
    "SOLUSDT": {"tick_size": 0.01, "lot_size": 0.1},
}

BINANCE_PERP_FEES: Dict[str, Tuple[float, float]] = {
    "default": (0.0008, 0.0010),
    "vip0": (0.0008, 0.0010),
    "vip1": (0.00072, 0.0009),
    "vip2": (0.0006, 0.0008),
    "vip3": (0.00048, 0.0005),
}

KRAKEN_SPOT_FEES: Dict[str, Tuple[float, float]] = {
    "default": (0.0025, 0.0040),
    "tier1": (0.0020, 0.0035),
    "tier2": (0.0014, 0.0024),
    "tier3": (0.0012, 0.0022),
}

CRYPTO_SYMBOLS = set(SYMBOL_CONFIGS.keys())
KRAKEN_L3_SYMBOLS = {"BTC/USD", "ETH/USD", "SOL/USD"}


def _build(
    data_path: str,
    lat_ns: int,
    queue_model_type: str,
    tick_size: float,
    lot_size: float,
    maker_fee: float,
    taker_fee: float,
) -> HashMapMarketDepthBacktest:
    asset = BacktestAsset()
    asset.data(data_path)
    asset.tick_size(tick_size)
    asset.lot_size(lot_size)
    from backtest_pipeline.src.hft_backtest_builder import _apply_constant_latency
    _apply_constant_latency(asset, lat_ns, lat_ns)
    asset.partial_fill_exchange()
    asset.trading_value_fee_model(maker_fee, taker_fee)
    if queue_model_type == "SquareProbQueueModel":
        asset.power_prob_queue_model2(2)
    elif queue_model_type == "LogProbQueueModel2":
        asset.log_prob_queue_model2()
    else:
        raise ValueError(f"Unsupported queue model: {queue_model_type}")
    return HashMapMarketDepthBacktest([asset])


def build_binance_hftbacktest(
    data_path: str,
    *,
    symbol: str = "BTCUSDT",
    latency_ms: float = 50.0,
    fee_tier: str = "default",
) -> HashMapMarketDepthBacktest:
    cfg = SYMBOL_CONFIGS.get(symbol)
    if cfg is None:
        raise ValueError(f"Unsupported Binance symbol: {symbol}")
    fees = BINANCE_PERP_FEES.get(fee_tier, BINANCE_PERP_FEES["default"])
    return _build(
        data_path=data_path,
        lat_ns=int(latency_ms * 1_000_000),
        queue_model_type="SquareProbQueueModel",
        tick_size=cfg["tick_size"],
        lot_size=cfg["lot_size"],
        maker_fee=fees[0],
        taker_fee=fees[1],
    )


KRAKEN_SYMBOL_CONFIGS = {
    "BTC/USD": {"tick_size": 0.1, "lot_size": 0.001},
    "ETH/USD": {"tick_size": 0.01, "lot_size": 0.01},
    "SOL/USD": {"tick_size": 0.01, "lot_size": 0.1},
}


def build_kraken_hftbacktest(
    data_path: str,
    *,
    symbol: str = "BTC/USD",
    latency_ms: float = 50.0,
    fee_tier: str = "default",
) -> HashMapMarketDepthBacktest:
    cfg = KRAKEN_SYMBOL_CONFIGS.get(symbol)
    if cfg is None:
        raise ValueError(f"Unsupported Kraken symbol: {symbol}. Supported: {list(KRAKEN_SYMBOL_CONFIGS.keys())}")
    fees = KRAKEN_SPOT_FEES.get(fee_tier, KRAKEN_SPOT_FEES["default"])
    return _build(
        data_path=data_path,
        lat_ns=int(latency_ms * 1_000_000),
        queue_model_type="LogProbQueueModel2",
        tick_size=cfg["tick_size"],
        lot_size=cfg["lot_size"],
        maker_fee=fees[0],
        taker_fee=fees[1],
    )
