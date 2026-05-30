"""PDF_MODEL_3 — VPIN, BVC with Student-t, volume-time buckets."""

from __future__ import annotations

import math
from collections import deque
from typing import Any, Deque, List, Optional

import numpy as np

from .base import BaseStructuralModel, ModelOutput
from .types import VPINToxicityOutput


import math
from collections import deque
from typing import Any, Deque, List, Optional

import numpy as np

try:
    from scipy.stats import t as student_t_dist
except ImportError:  # pragma: no cover
    student_t_dist = None

from .base import BaseStructuralModel, ModelOutput
from .types import VPINToxicityOutput


def _betainc_cf(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a,b) — Lentz continued fraction (both branches)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_beta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(a * math.log(x) + b * math.log(1.0 - x) - ln_beta) / a

    def _cf() -> float:
        f = 1.0
        c = 1.0
        d = 1.0 - (a + b) * x / (a + 1.0)
        if abs(d) < 1e-30:
            d = 1e-30
        d = 1.0 / d
        f *= d
        for m in range(1, 200):
            m2 = 2 * m
            aa = m * (b - m) * x / ((a + m2 - 1) * (a + m2))
            d = 1.0 + aa * d
            if abs(d) < 1e-30:
                d = 1e-30
            c = 1.0 + aa / c
            if abs(c) < 1e-30:
                c = 1e-30
            d = 1.0 / d
            f *= d * c
            aa = -(a + m) * (a + b + m) * x / ((a + m2) * (a + m2 + 1))
            d = 1.0 + aa * d
            if abs(d) < 1e-30:
                d = 1e-30
            c = 1.0 + aa / c
            if abs(c) < 1e-30:
                c = 1e-30
            d = 1.0 / d
            delta = d * c
            f *= delta
            if abs(delta - 1.0) < 1e-10:
                break
        return f

    if x < (a + 1.0) / (a + b + 2.0):
        return front * _cf()
    return 1.0 - front * _cf()


def student_t_cdf(x: float, df: float) -> float:
    """CDF of Student-t with df degrees of freedom."""
    if df <= 0:
        return 0.5
    if student_t_dist is not None:
        return float(student_t_dist.cdf(x, df=df))
    t = df / (df + x * x)
    ib = _betainc_cf(df / 2.0, 0.5, t)
    return 1.0 - 0.5 * ib if x >= 0 else 0.5 * ib


def bvc_buy_volume(
    volume: float,
    delta_p: float,
    sigma: float,
    df: float = 5.0,
) -> float:
    """Bulk Volume Classification: V_τ^B = V_τ * Z(ΔP/σ) with Student-t CDF."""
    if volume <= 0:
        return 0.0
    if sigma <= 1e-12:
        return volume * 0.5
    z = delta_p / sigma
    prob_buy = student_t_cdf(z, df)
    return volume * prob_buy


def compute_vpin(
    buy_volumes: List[float],
    sell_volumes: List[float],
) -> float:
    """VPIN = Σ|V^B - V^S| / (n * V_bar) where V_bar is mean bucket volume."""
    if not buy_volumes:
        return 0.0
    imbalances = [abs(b - s) for b, s in zip(buy_volumes, sell_volumes)]
    total_vol = sum(b + s for b, s in zip(buy_volumes, sell_volumes))
    n = len(buy_volumes)
    if total_vol <= 0 or n == 0:
        return 0.0
    v_bar = total_vol / n
    return sum(imbalances) / (n * v_bar)


class VPINToxicityModel(BaseStructuralModel[VPINToxicityOutput]):
    model_id = "PDF_MODEL_3"

    def __init__(self, params: Optional[dict] = None):
        cfg = (params or {}).get("vpin", {})
        self.buckets_per_day = int(cfg.get("buckets_per_day", 200))
        self.lookback_bars = int(cfg.get("lookback_bars", 30))
        self.alert_percentile = float(cfg.get("alert_percentile", 0.99))
        self.student_t_df = float(cfg.get("student_t_df", 5))
        self._buy_buckets: Deque[float] = deque(maxlen=self.lookback_bars)
        self._sell_buckets: Deque[float] = deque(maxlen=self.lookback_bars)
        self._vpin_history: Deque[float] = deque(maxlen=500)
        self._bucket_buy = 0.0
        self._bucket_sell = 0.0
        self._bucket_volume_target = 0.0
        self._prev_mid: Optional[float] = None
        self._returns: Deque[float] = deque(maxlen=100)

    def _rolling_sigma(self) -> float:
        if len(self._returns) < 2:
            return 1.0
        return max(float(np.std(self._returns, ddof=1)), 1e-9)

    def ingest_bar(
        self,
        mid: float,
        volume: float,
        timestamp_ns: int = 0,
        bucket_volume: Optional[float] = None,
    ) -> Optional[ModelOutput[VPINToxicityOutput]]:
        """Add trade/bar volume; flush bucket when target reached."""
        if self._prev_mid is not None:
            self._returns.append(mid - self._prev_mid)
        self._prev_mid = mid
        sigma = self._rolling_sigma()
        delta_p = self._returns[-1] if self._returns else 0.0
        buy = bvc_buy_volume(volume, delta_p, sigma, self.student_t_df)
        sell = volume - buy
        self._bucket_buy += buy
        self._bucket_sell += sell

        target = bucket_volume
        if target is None:
            target = max(volume, 1.0)
        if self._bucket_volume_target <= 0:
            self._bucket_volume_target = target

        bucket_total = self._bucket_buy + self._bucket_sell
        if bucket_total < self._bucket_volume_target:
            return None

        self._buy_buckets.append(self._bucket_buy)
        self._sell_buckets.append(self._bucket_sell)
        self._bucket_buy = 0.0
        self._bucket_sell = 0.0

        return self._emit(timestamp_ns)

    def _emit(self, timestamp_ns: int) -> ModelOutput[VPINToxicityOutput]:
        vpin = compute_vpin(list(self._buy_buckets), list(self._sell_buckets))
        self._vpin_history.append(vpin)
        percentile = 0.0
        if self._vpin_history:
            arr = np.array(self._vpin_history, dtype=float)
            percentile = float(np.mean(arr <= vpin))
        alert = percentile >= self.alert_percentile
        regime = "toxic" if alert else ("elevated" if percentile >= 0.9 else "normal")
        vol_warn = percentile >= 0.95
        payload = VPINToxicityOutput(
            VPIN_value=vpin,
            VPIN_percentile=percentile,
            toxicity_regime=regime,
            toxic_flow_alert=alert,
            volatility_warning=vol_warn,
        )
        return ModelOutput(model_id=self.model_id, timestamp_ns=timestamp_ns, payload=payload)

    def evaluate(self, **kwargs: Any) -> ModelOutput[VPINToxicityOutput]:
        bars: List[dict] = kwargs.get("bars", [])
        if bars:
            last: Optional[ModelOutput[VPINToxicityOutput]] = None
            bucket_vol = kwargs.get("bucket_volume")
            for bar in bars:
                out = self.ingest_bar(
                    bar["mid"],
                    bar["volume"],
                    bar.get("timestamp_ns", 0),
                    bucket_vol,
                )
                if out is not None:
                    last = out
            if last is not None:
                return last
        mid = kwargs.get("mid", 0.0)
        volume = kwargs.get("volume", 0.0)
        out = self.ingest_bar(mid, volume, kwargs.get("timestamp_ns", 0), kwargs.get("bucket_volume"))
        if out is None:
            return ModelOutput(
                model_id=self.model_id,
                timestamp_ns=kwargs.get("timestamp_ns", 0),
                payload=VPINToxicityOutput(),
            )
        return out
