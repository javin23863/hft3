"""Runner event labeler — identifies runner events from daily bar history.

Labels each (symbol, date) as runner or non-runner based on forward MFE/MAE
across multiple horizons. Uses only point-in-time information for labeling.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..models import DailyBar
from .types import ModelConfig, RunnerLabel


@dataclass
class _ForwardWindow:
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    volumes: np.ndarray
    entry_price: float


def _compute_mfe_mae(fw: _ForwardWindow, horizon: int) -> tuple[float, float]:
    n = min(horizon, len(fw.highs))
    if n == 0:
        return 0.0, 0.0
    mfe = (np.max(fw.highs[:n]) - fw.entry_price) / fw.entry_price
    mae = (np.min(fw.lows[:n]) - fw.entry_price) / fw.entry_price
    return float(mfe), float(mae)


def _mfe_before_mae(fw: _ForwardWindow, horizon: int) -> bool:
    n = min(horizon, len(fw.highs))
    if n == 0:
        return False
    peak_idx = int(np.argmax(fw.highs[:n]))
    trough_idx = int(np.argmin(fw.lows[:n]))
    return peak_idx < trough_idx


def _detect_dilution_gap(
    bars: list[DailyBar], idx: int, horizon: int
) -> bool:
    n = min(horizon, len(bars) - idx - 1)
    for k in range(1, n + 1):
        j = idx + k
        gap_down = (bars[j].open - bars[j - 1].close) / bars[j - 1].close
        if gap_down < -0.10:
            vol_ratio = bars[j].volume / max(bars[j - 1].volume, 1.0)
            if vol_ratio > 2.0:
                return True
    return False


def _detect_halt(bars: list[DailyBar], idx: int, horizon: int) -> bool:
    n = min(horizon, len(bars) - idx - 1)
    for k in range(1, n + 1):
        j = idx + k
        bar_range = bars[j].high - bars[j].low
        if bar_range < 1e-8 and bars[j].volume < 100:
            return True
    return False


def _estimate_slippage(bars: list[DailyBar], idx: int) -> float:
    if idx < 1:
        return 0.005
    bar = bars[idx]
    spread_proxy = (bar.high - bar.low) / max(bar.close, 0.01)
    vol = bar.volume
    if vol < 10_000:
        return min(spread_proxy * 0.5, 0.05)
    if vol < 100_000:
        return min(spread_proxy * 0.25, 0.03)
    return min(spread_proxy * 0.10, 0.02)


def label_runner(
    symbol: str,
    bars: list[DailyBar],
    idx: int,
    config: ModelConfig,
) -> RunnerLabel | None:
    if idx < 0 or idx >= len(bars):
        return None
    remaining = len(bars) - idx - 1
    if remaining < config.horizons[-1]:
        return None

    bar = bars[idx]
    if bar.close < config.min_price or bar.close > config.max_price:
        return None

    entry_price = bar.close
    fwd_bars = bars[idx + 1:]
    fwd_highs = np.array([b.high for b in fwd_bars], dtype=np.float64)
    fwd_lows = np.array([b.low for b in fwd_bars], dtype=np.float64)
    fwd_closes = np.array([b.close for b in fwd_bars], dtype=np.float64)
    fwd_volumes = np.array([b.volume for b in fwd_bars], dtype=np.float64)

    fw = _ForwardWindow(fwd_highs, fwd_lows, fwd_closes, fwd_volumes, entry_price)

    mfe_1d, mae_1d = _compute_mfe_mae(fw, 1)
    mfe_2d, mae_2d = _compute_mfe_mae(fw, 2)
    mfe_5d, mae_5d = _compute_mfe_mae(fw, 5)

    threshold = config.runner_threshold_pct / 100.0
    is_runner = mfe_5d >= threshold

    if mfe_5d >= config.extreme_runner_threshold_pct / 100.0:
        runner_type = "extreme"
    elif mfe_5d >= threshold:
        runner_type = "standard"
    else:
        runner_type = "none"

    mfe_before_mae = _mfe_before_mae(fw, 5)
    dilution_gap = _detect_dilution_gap(bars, idx, 5)
    halt_event = _detect_halt(bars, idx, 5)
    slippage = _estimate_slippage(bars, idx)

    return RunnerLabel(
        symbol=symbol,
        event_date=bar.date,
        is_runner=is_runner,
        mfe_1d=mfe_1d,
        mae_1d=mae_1d,
        mfe_2d=mfe_2d,
        mae_2d=mae_2d,
        mfe_5d=mfe_5d,
        mae_5d=mae_5d,
        mfe_before_mae=mfe_before_mae,
        dilution_gap=dilution_gap,
        halt_event=halt_event,
        realized_slippage=slippage,
        runner_type=runner_type,
    )


def label_universe(
    symbol_bars: dict[str, list[DailyBar]],
    config: ModelConfig,
) -> list[RunnerLabel]:
    labels: list[RunnerLabel] = []
    for symbol, bars in symbol_bars.items():
        for idx in range(len(bars)):
            lbl = label_runner(symbol, bars, idx, config)
            if lbl is not None:
                labels.append(lbl)
    return labels
