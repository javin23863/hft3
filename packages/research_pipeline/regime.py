"""Regime labeling and grouped edge-performance helpers."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence


@dataclass(frozen=True)
class RegimeThresholds:
    low: float
    high: float
    low_label: str = "low"
    middle_label: str = "normal"
    high_label: str = "high"

    def __post_init__(self) -> None:
        if not math.isfinite(self.low) or not math.isfinite(self.high):
            raise ValueError("thresholds must be finite")
        if self.low > self.high:
            raise ValueError("low threshold must be <= high threshold")


def quantile(values: Sequence[float], q: float) -> float:
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be in [0, 1]")
    vals = sorted(float(v) for v in values)
    if not vals or not all(math.isfinite(v) for v in vals):
        raise ValueError("values must be non-empty and finite")
    if len(vals) == 1:
        return vals[0]
    pos = q * (len(vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    weight = pos - lo
    return vals[lo] * (1.0 - weight) + vals[hi] * weight


def label_value(value: float, thresholds: RegimeThresholds) -> str:
    x = float(value)
    if not math.isfinite(x):
        raise ValueError("value must be finite")
    if x <= thresholds.low:
        return thresholds.low_label
    if x >= thresholds.high:
        return thresholds.high_label
    return thresholds.middle_label


def label_regimes(
    values: Sequence[float],
    thresholds: RegimeThresholds | None = None,
    *,
    low_quantile: float = 1.0 / 3.0,
    high_quantile: float = 2.0 / 3.0,
) -> list[str]:
    vals = [float(v) for v in values]
    if thresholds is None:
        thresholds = RegimeThresholds(quantile(vals, low_quantile), quantile(vals, high_quantile))
    return [label_value(v, thresholds) for v in vals]


def group_performance(returns: Sequence[float], regimes: Sequence[str]) -> dict[str, dict[str, float | int]]:
    vals = [float(v) for v in returns]
    labels = [str(r) for r in regimes]
    if len(vals) != len(labels):
        raise ValueError("returns and regimes must have the same length")
    if not all(math.isfinite(v) for v in vals):
        raise ValueError("returns must be finite")
    grouped: dict[str, list[float]] = {}
    for value, label in zip(vals, labels):
        grouped.setdefault(label, []).append(value)
    out: dict[str, dict[str, float | int]] = {}
    for label, xs in grouped.items():
        count = len(xs)
        total = sum(xs)
        wins = sum(1 for x in xs if x > 0.0)
        losses = sum(1 for x in xs if x < 0.0)
        out[label] = {
            "count": count,
            "mean": total / count,
            "total": total,
            "win_rate": wins / count,
            "loss_rate": losses / count,
        }
    return out


def group_performance_by_label(returns: Sequence[float], labels: Sequence[str]) -> dict[str, dict[str, float]]:
    """Compatibility wrapper returning float-valued grouped metrics."""

    grouped = group_performance(returns, labels)
    return {
        label: {metric: float(value) for metric, value in metrics.items()}
        for label, metrics in grouped.items()
    }


__all__ = [
    "RegimeThresholds",
    "group_performance",
    "group_performance_by_label",
    "label_regimes",
    "label_value",
    "quantile",
]
