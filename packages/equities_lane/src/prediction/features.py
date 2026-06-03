"""Feature engineering pipeline for runner hazard prediction.

Computes point-in-time features from daily bars, float metadata, and
auxiliary sources. All features use only information available at or
before the prediction timestamp.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..models import DailyBar, FloatRecord
from .types import FeatureVector, ModelConfig, SnapshotType


def compute_all_features(
    symbol: str,
    bars: list[DailyBar],
    idx: int,
    float_rec: FloatRecord | None,
    config: ModelConfig,
    snapshot_type: SnapshotType = SnapshotType.DAILY_CLOSE,
) -> FeatureVector | None:
    if idx < config.lookback_days or idx >= len(bars):
        return None

    bar = bars[idx]
    if bar.close < config.min_price or bar.close > config.max_price:
        return None

    lookback = bars[max(0, idx - config.lookback_days):idx + 1]
    window = bars[max(0, idx - config.feature_window_days):idx + 1]

    feats: dict[str, float] = {}

    _supply_features(feats, bar, float_rec, config)
    _liquidity_features(feats, lookback, window, bar)
    _accumulation_features(feats, lookback, window, bar)
    _orderflow_features(feats, lookback, bar)
    _price_structure_features(feats, lookback, bar)
    _volume_structure_features(feats, lookback, window)
    _impact_sensitivity_features(feats, lookback, bar)
    _interaction_features(feats, bar, float_rec, lookback, window)

    return FeatureVector(
        symbol=symbol,
        date=bar.date,
        snapshot_type=snapshot_type,
        features=feats,
    )


def _supply_features(
    feats: dict[str, float],
    bar: DailyBar,
    float_rec: FloatRecord | None,
    config: ModelConfig,
) -> None:
    if float_rec is not None:
        float_shares = float_rec.float_shares
        outstanding = float_rec.outstanding_shares
        feats["float_shares"] = float_shares
        feats["float_pct_outstanding"] = (
            float_shares / outstanding if outstanding > 0 else 1.0
        )
        feats["float_constraint"] = 1.0 - min(
            float_shares / config.max_float_shares, 1.0
        )
        feats["dollar_float"] = float_shares * bar.close
    else:
        feats["float_shares"] = 0.0
        feats["float_pct_outstanding"] = 0.0
        feats["float_constraint"] = 0.0
        feats["dollar_float"] = 0.0

    feats["price_level"] = bar.close
    feats["log_price"] = math.log(max(bar.close, 0.01))


def _liquidity_features(
    feats: dict[str, float],
    lookback: list[DailyBar],
    window: list[DailyBar],
    bar: DailyBar,
) -> None:
    if len(lookback) < 5:
        feats["spread_to_price"] = 0.0
        feats["atr_20"] = 0.0
        feats["atr_compression"] = 0.0
        feats["range_compression"] = 0.0
        feats["dollar_volume"] = 0.0
        feats["dollar_volume_compression"] = 0.0
        return

    ranges = np.array(
        [(b.high - b.low) for b in lookback], dtype=np.float64
    )
    closes = np.array([b.close for b in lookback], dtype=np.float64)
    volumes = np.array([b.volume for b in lookback], dtype=np.float64)

    atr_5 = float(np.mean(ranges[-5:]))
    atr_20 = float(np.mean(ranges[-20:])) if len(ranges) >= 20 else atr_5

    feats["atr_5"] = atr_5
    feats["atr_20"] = atr_20
    feats["atr_compression"] = (
        1.0 - (atr_5 / atr_20) if atr_20 > 1e-8 else 0.0
    )

    recent_range = bar.high - bar.low
    avg_range = float(np.mean(ranges[-10:])) if len(ranges) >= 10 else recent_range
    feats["range_compression"] = (
        1.0 - (recent_range / avg_range) if avg_range > 1e-8 else 0.0
    )

    feats["spread_to_price"] = (
        (bar.high - bar.low) / bar.close if bar.close > 0 else 0.0
    )

    dollar_vol = bar.close * bar.volume
    avg_dollar_vol = float(
        np.mean(closes[-20:] * volumes[-20:])
    ) if len(closes) >= 20 else dollar_vol
    feats["dollar_volume"] = dollar_vol
    feats["dollar_volume_compression"] = (
        1.0 - (dollar_vol / avg_dollar_vol) if avg_dollar_vol > 0 else 0.0
    )

    if len(window) >= 5:
        win_ranges = np.array(
            [(b.high - b.low) for b in window], dtype=np.float64
        )
        feats["range_std"] = float(np.std(win_ranges))
    else:
        feats["range_std"] = 0.0


def _accumulation_features(
    feats: dict[str, float],
    lookback: list[DailyBar],
    window: list[DailyBar],
    bar: DailyBar,
) -> None:
    if len(lookback) < 20:
        feats["volume_zscore_20"] = 0.0
        feats["dollar_volume_zscore_20"] = 0.0
        feats["float_turnover"] = 0.0
        feats["float_turnover_accel"] = 0.0
        feats["close_location_value"] = 0.5
        feats["up_volume_ratio"] = 0.5
        feats["vwap_hold_strength"] = 0.0
        feats["higher_lows_count"] = 0.0
        feats["price_volume_divergence"] = 0.0
        return

    volumes = np.array([b.volume for b in lookback], dtype=np.float64)
    closes = np.array([b.close for b in lookback], dtype=np.float64)
    highs = np.array([b.high for b in lookback], dtype=np.float64)
    lows = np.array([b.low for b in lookback], dtype=np.float64)

    vol_mean = float(np.mean(volumes[-20:]))
    vol_std = float(np.std(volumes[-20:]))
    feats["volume_zscore_20"] = (
        (bar.volume - vol_mean) / vol_std if vol_std > 0 else 0.0
    )

    dvols = closes * volumes
    dvol_mean = float(np.mean(dvols[-20:]))
    dvol_std = float(np.std(dvols[-20:]))
    feats["dollar_volume_zscore_20"] = (
        (bar.close * bar.volume - dvol_mean) / dvol_std
        if dvol_std > 0
        else 0.0
    )

    feats["close_location_value"] = (
        (bar.close - bar.low) / (bar.high - bar.low)
        if (bar.high - bar.low) > 1e-8
        else 0.5
    )

    up_vol = 0.0
    total_vol = 0.0
    for i in range(-5, 0):
        if closes[i] >= closes[i - 1]:
            up_vol += volumes[i]
        total_vol += volumes[i]
    feats["up_volume_ratio"] = up_vol / total_vol if total_vol > 0 else 0.5

    if len(volumes) >= 10:
        vol_5 = float(np.mean(volumes[-5:]))
        vol_10 = float(np.mean(volumes[-10:-5]))
        feats["float_turnover_accel"] = (
            (vol_5 - vol_10) / vol_10 if vol_10 > 0 else 0.0
        )
    else:
        feats["float_turnover_accel"] = 0.0

    vwap_approx = float(np.mean(closes[-5:]))
    feats["vwap_hold_strength"] = (
        (bar.close - vwap_approx) / vwap_approx if vwap_approx > 0 else 0.0
    )

    higher_lows = 0
    for i in range(-4, 0):
        if lows[i] > lows[i - 1]:
            higher_lows += 1
    feats["higher_lows_count"] = float(higher_lows)

    price_change_5 = (closes[-1] - closes[-5]) / closes[-5] if closes[-5] > 0 else 0.0
    vol_change_5 = (
        (float(np.mean(volumes[-5:])) - float(np.mean(volumes[-10:-5])))
        / float(np.mean(volumes[-10:-5]))
        if len(volumes) >= 10 and float(np.mean(volumes[-10:-5])) > 0
        else 0.0
    )
    feats["price_volume_divergence"] = vol_change_5 - price_change_5


def _orderflow_features(
    feats: dict[str, float],
    lookback: list[DailyBar],
    bar: DailyBar,
) -> None:
    if len(lookback) < 5:
        feats["trade_sign_imbalance"] = 0.0
        feats["aggressive_buy_proxy"] = 0.5
        feats["buy_pressure_5d"] = 0.5
        return

    closes = np.array([b.close for b in lookback], dtype=np.float64)
    volumes = np.array([b.volume for b in lookback], dtype=np.float64)

    buy_vol = 0.0
    sell_vol = 0.0
    for i in range(-5, 0):
        if closes[i] >= closes[i - 1]:
            buy_vol += volumes[i]
        else:
            sell_vol += volumes[i]
    total = buy_vol + sell_vol
    feats["trade_sign_imbalance"] = (
        (buy_vol - sell_vol) / total if total > 0 else 0.0
    )

    feats["aggressive_buy_proxy"] = (
        (bar.close - bar.low) / (bar.high - bar.low)
        if (bar.high - bar.low) > 1e-8
        else 0.5
    )

    feats["buy_pressure_5d"] = buy_vol / total if total > 0 else 0.5


def _price_structure_features(
    feats: dict[str, float],
    lookback: list[DailyBar],
    bar: DailyBar,
) -> None:
    closes = np.array([b.close for b in lookback], dtype=np.float64)

    if len(closes) >= 5:
        ret_5d = (closes[-1] - closes[-5]) / closes[-5] if closes[-5] > 0 else 0.0
        feats["return_5d"] = ret_5d
    else:
        feats["return_5d"] = 0.0

    if len(closes) >= 10:
        ret_10d = (closes[-1] - closes[-10]) / closes[-10] if closes[-10] > 0 else 0.0
        feats["return_10d"] = ret_10d
    else:
        feats["return_10d"] = 0.0

    if len(closes) >= 20:
        ret_20d = (closes[-1] - closes[-20]) / closes[-20] if closes[-20] > 0 else 0.0
        feats["return_20d"] = ret_20d
        sma_20 = float(np.mean(closes[-20:]))
        feats["dist_sma_20"] = (bar.close - sma_20) / sma_20 if sma_20 > 0 else 0.0
    else:
        feats["return_20d"] = 0.0
        feats["dist_sma_20"] = 0.0

    if len(closes) >= 60:
        high_60 = float(np.max(closes[-60:]))
        low_60 = float(np.min(closes[-60:]))
        feats["dist_60d_high"] = (
            (bar.close - high_60) / high_60 if high_60 > 0 else 0.0
        )
        feats["dist_60d_low"] = (
            (bar.close - low_60) / low_60 if low_60 > 0 else 0.0
        )
        feats["position_in_range_60d"] = (
            (bar.close - low_60) / (high_60 - low_60)
            if (high_60 - low_60) > 1e-8
            else 0.5
        )
    else:
        feats["dist_60d_high"] = 0.0
        feats["dist_60d_low"] = 0.0
        feats["position_in_range_60d"] = 0.5

    if len(closes) >= 5:
        returns = np.diff(closes[-6:]) / closes[-6:-1]
        returns = np.where(np.isfinite(returns), returns, 0.0)
        feats["volatility_5d"] = float(np.std(returns))
    else:
        feats["volatility_5d"] = 0.0

    if len(closes) >= 20:
        returns_20 = np.diff(closes[-21:]) / closes[-21:-1]
        returns_20 = np.where(np.isfinite(returns_20), returns_20, 0.0)
        feats["volatility_20d"] = float(np.std(returns_20))
        feats["vol_compression"] = (
            1.0 - (feats["volatility_5d"] / feats["volatility_20d"])
            if feats["volatility_20d"] > 1e-8
            else 0.0
        )
    else:
        feats["volatility_20d"] = 0.0
        feats["vol_compression"] = 0.0


def _volume_structure_features(
    feats: dict[str, float],
    lookback: list[DailyBar],
    window: list[DailyBar],
) -> None:
    volumes = np.array([b.volume for b in lookback], dtype=np.float64)

    if len(volumes) >= 20:
        vol_20 = float(np.mean(volumes[-20:]))
        feats["volume_ma_20"] = vol_20
        feats["rvol"] = (
            volumes[-1] / vol_20 if vol_20 > 0 else 1.0
        )
    else:
        feats["volume_ma_20"] = float(np.mean(volumes)) if len(volumes) > 0 else 0.0
        feats["rvol"] = 1.0

    if len(volumes) >= 10:
        vol_5 = float(np.mean(volumes[-5:]))
        vol_10 = float(np.mean(volumes[-10:-5]))
        feats["volume_momentum"] = vol_5 / vol_10 if vol_10 > 0 else 1.0
    else:
        feats["volume_momentum"] = 1.0

    if len(volumes) >= 20:
        feats["volume_skew_20"] = float(_skewness(volumes[-20:]))
        feats["volume_kurt_20"] = float(_kurtosis(volumes[-20:]))
    else:
        feats["volume_skew_20"] = 0.0
        feats["volume_kurt_20"] = 0.0


def _impact_sensitivity_features(
    feats: dict[str, float],
    lookback: list[DailyBar],
    bar: DailyBar,
) -> None:
    if len(lookback) < 10:
        feats["impact_sensitivity"] = 0.0
        feats["impact_sensitivity_trend"] = 0.0
        return

    impacts = []
    for i in range(-10, 0):
        b = lookback[i]
        prev = lookback[i - 1]
        ret = (b.close - prev.close) / prev.close if prev.close > 0 else 0.0
        signed_dvol = b.close * b.volume
        if signed_dvol > 0:
            impacts.append(abs(ret) / signed_dvol * 1e6)
        else:
            impacts.append(0.0)

    impacts_arr = np.array(impacts, dtype=np.float64)
    feats["impact_sensitivity"] = float(np.mean(impacts_arr))

    if len(impacts_arr) >= 6:
        recent = float(np.mean(impacts_arr[-3:]))
        older = float(np.mean(impacts_arr[:-3]))
        feats["impact_sensitivity_trend"] = (
            (recent - older) / older if older > 1e-12 else 0.0
        )
    else:
        feats["impact_sensitivity_trend"] = 0.0


def _interaction_features(
    feats: dict[str, float],
    bar: DailyBar,
    float_rec: FloatRecord | None,
    lookback: list[DailyBar],
    window: list[DailyBar],
) -> None:
    fc = feats.get("float_constraint", 0.0)
    vol_z = feats.get("volume_zscore_20", 0.0)
    liq_frag = feats.get("atr_compression", 0.0)
    impact = feats.get("impact_sensitivity", 0.0)
    accum = feats.get("price_volume_divergence", 0.0)

    feats["supply_x_abnormal_volume"] = fc * vol_z
    feats["supply_x_liquidity_fragility"] = fc * liq_frag
    feats["supply_x_impact_sensitivity"] = fc * impact
    feats["supply_x_accumulation"] = fc * accum

    if float_rec is not None and float_rec.float_shares > 0:
        avg_vol = float(np.mean([b.volume for b in lookback[-20:]])) if len(lookback) >= 20 else bar.volume
        feats["float_turnover_rate"] = avg_vol / float_rec.float_shares
    else:
        feats["float_turnover_rate"] = 0.0


def _skewness(arr: np.ndarray) -> float:
    n = len(arr)
    if n < 3:
        return 0.0
    mean = float(np.mean(arr))
    std = float(np.std(arr))
    if std < 1e-12:
        return 0.0
    return float(np.mean(((arr - mean) / std) ** 3))


def _kurtosis(arr: np.ndarray) -> float:
    n = len(arr)
    if n < 4:
        return 0.0
    mean = float(np.mean(arr))
    std = float(np.std(arr))
    if std < 1e-12:
        return 0.0
    return float(np.mean(((arr - mean) / std) ** 4)) - 3.0


FEATURE_NAMES: list[str] = [
    "float_shares",
    "float_pct_outstanding",
    "float_constraint",
    "dollar_float",
    "price_level",
    "log_price",
    "atr_5",
    "atr_20",
    "atr_compression",
    "range_compression",
    "spread_to_price",
    "dollar_volume",
    "dollar_volume_compression",
    "range_std",
    "volume_zscore_20",
    "dollar_volume_zscore_20",
    "float_turnover_accel",
    "close_location_value",
    "up_volume_ratio",
    "vwap_hold_strength",
    "higher_lows_count",
    "price_volume_divergence",
    "trade_sign_imbalance",
    "aggressive_buy_proxy",
    "buy_pressure_5d",
    "return_5d",
    "return_10d",
    "return_20d",
    "dist_sma_20",
    "dist_60d_high",
    "dist_60d_low",
    "position_in_range_60d",
    "volatility_5d",
    "volatility_20d",
    "vol_compression",
    "volume_ma_20",
    "rvol",
    "volume_momentum",
    "volume_skew_20",
    "volume_kurt_20",
    "impact_sensitivity",
    "impact_sensitivity_trend",
    "supply_x_abnormal_volume",
    "supply_x_liquidity_fragility",
    "supply_x_impact_sensitivity",
    "supply_x_accumulation",
    "float_turnover_rate",
]
