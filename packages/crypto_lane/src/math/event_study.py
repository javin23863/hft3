"""Event-study metrics: CAS and fee spike Z-score."""
from __future__ import annotations

from typing import Sequence

import numpy as np


def rolling_fee_zscore(fees: Sequence[float], window: int) -> float:
    x = np.asarray(fees, dtype=float)
    if x.size == 0:
        return 0.0
    w = x[-window:] if x.size >= window else x
    mu = float(np.mean(w))
    sigma = float(np.std(w, ddof=1)) if w.size > 1 else 1.0
    if sigma <= 0:
        return 0.0
    return float((x[-1] - mu) / sigma)


def fee_spike_event(fees: Sequence[float], window: int, threshold: float = 3.0) -> int:
    return int(rolling_fee_zscore(fees, window) > threshold)


def cumulative_abnormal_spread(
    spreads: Sequence[float],
    expected_by_regime: Sequence[float],
    t1: int,
    t2: int,
) -> float:
    s = np.asarray(spreads, dtype=float)
    e = np.asarray(expected_by_regime, dtype=float)
    n = min(s.size, e.size)
    if n == 0:
        return 0.0
    t1 = max(0, t1)
    t2 = min(n - 1, t2)
    if t2 < t1:
        return 0.0
    return float(np.sum(s[t1 : t2 + 1] - e[t1 : t2 + 1]))
