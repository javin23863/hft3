from __future__ import annotations

import numpy as np

from features_engine.src.hypotheses.modules import BaseHypothesis, MarketState


class PanicMarketOrderSpreadTaxComplete(BaseHypothesis):
    """Hypothesis 28: When spread is stressed and vol is LOW, passive join has real edge.
    Signal: proportional to spread deviation, scaled by inverse vol.
    """
    def __init__(self):
        super().__init__(28, "Panic market-order spread tax")

    def evaluate(self, state: MarketState) -> float:
        spread_stress = state.f('spread_stress', 1.0)
        vol_state = state.f('realized_vol_state', 0.0)
        agg_imb = state.f('aggressor_volume_imbalance', 0.0)

        if spread_stress < 1.2 or vol_state > 0.1:
            return 0.0
        spread_edge = (spread_stress - 1.0) / max(vol_state, 1e-6)
        direction = -np.sign(agg_imb) if abs(agg_imb) > 0.01 else 0.0
        return float(direction * np.tanh(spread_edge * 0.5))


class DailyLossLimitDefenseComplete(BaseHypothesis):
    """Hypothesis 32: When prop firms approach daily loss limits, forced flattening
    creates predictable flow. Fade the continuation during flatten risk.
    """
    def __init__(self):
        super().__init__(32, "Daily loss-limit defense")

    def evaluate(self, state: MarketState) -> float:
        regime = state.regime_state
        event_ctx = state.event_context
        agg_imb = state.f('aggressor_volume_imbalance', 0.0)

        is_flatten_risk = (
            regime in ('prop_flatten', 'stop_cascade') or
            event_ctx in ('PROP_FLATTEN_TOPSTEP', 'TPT_FLATTEN', 'APEX_FLATTEN')
        )
        if not is_flatten_risk:
            return 0.0

        return float(-np.tanh(agg_imb * 1.5))


class QuotePullBeforeVolatilityComplete(BaseHypothesis):
    """Hypothesis 39: Before macro events (CPI, FOMC, NFP), dealers pull quotes.
    Detect the pull pattern: rising cancel pressure + book slope change.
    """
    def __init__(self):
        super().__init__(39, "Quote pull before volatility")

    def evaluate(self, state: MarketState) -> float:
        event_ctx = state.event_context
        cancel_pressure = state.f('near_touch_cancel_pressure', 0.0)
        slope_change = state.f('book_slope_change', 0.0)
        cancel_ratio = state.f('cancel_to_add_ratio', 1.0)

        event_types = ('CPI_TIGHT', 'FOMC', 'NFP', 'NEWS_RESTRICTION')
        if event_ctx not in event_types:
            return 0.0

        pull_intensity = cancel_pressure * abs(slope_change) * (cancel_ratio - 1.0)
        if pull_intensity > 0.1:
            return float(-np.tanh(pull_intensity * 2.0))
        return 0.0


class RebateTrapAvoidanceComplete(BaseHypothesis):
    """Hypothesis 43: One-sided add/cancel imbalance reveals rebate-seeking flow.
    When adds dominate on one side but aggressor flow pushes the other way,
    fade the aggressor direction to avoid filling into a rebate trap.
    """
    def __init__(self):
        super().__init__(43, "Rebate trap avoidance")

    def evaluate(self, state: MarketState) -> float:
        bid_ac = state.f('bid_add_cancel_ratio', 1.0)
        ask_ac = state.f('ask_add_cancel_ratio', 1.0)
        agg_imb = state.f('aggressor_volume_imbalance', 0.0)

        rebate_on_bid = bid_ac > 2.0 and ask_ac < 1.5
        rebate_on_ask = ask_ac > 2.0 and bid_ac < 1.5

        if not rebate_on_bid and not rebate_on_ask:
            return 0.0

        if rebate_on_bid and agg_imb < 0:
            return float(np.tanh(-agg_imb * 1.0))
        elif rebate_on_ask and agg_imb > 0:
            return float(-np.tanh(agg_imb * 1.0))
        return 0.0


class SpreadRegimeChangeComplete(BaseHypothesis):
    """Hypothesis 44: Detect spread regime transitions (normal ↔ stressed).
    When spread stress crosses threshold, signal a position-size cooldown.
    Returns a RISK_MODULATOR signal (always magnitude, not direction) that
    other strategies use to scale down during regime change.
    """
    def __init__(self):
        super().__init__(44, "Spread regime change")
        self._prev_stress = 1.0
        self._cooldown_counter = 0
        self._cooldown_duration = 20

    def evaluate(self, state: MarketState) -> float:
        spread_stress = state.f('spread_stress', 1.0)
        was_stressed = self._prev_stress >= 1.2
        is_stressed = spread_stress >= 1.2
        transition = was_stressed != is_stressed

        self._prev_stress = spread_stress

        if transition:
            self._cooldown_counter = self._cooldown_duration

        if self._cooldown_counter > 0:
            self._cooldown_counter -= 1
            return -0.5

        return 0.0


def get_all_hypotheses() -> list:
    """Returns all 44 active hypotheses: completions replace stubs, originals otherwise.

    Instances are cached by hyp_id so stateful completions (like SpreadRegimeChangeComplete
    which tracks cooldown counters) persist across evaluate calls.
    """
    from features_engine.src.hypotheses.registry import get_active_hypotheses

    originals = get_active_hypotheses()

    completions: dict[int, type] = {
        28: PanicMarketOrderSpreadTaxComplete,
        32: DailyLossLimitDefenseComplete,
        39: QuotePullBeforeVolatilityComplete,
        43: RebateTrapAvoidanceComplete,
        44: SpreadRegimeChangeComplete,
    }

    completed_ids = set(completions.keys())
    if not hasattr(get_all_hypotheses, "_cache"):
        get_all_hypotheses._cache = {}
    cache = get_all_hypotheses._cache

    result = []
    for hyp in originals:
        if hyp.hyp_id in completed_ids:
            if hyp.hyp_id not in cache:
                cache[hyp.hyp_id] = completions[hyp.hyp_id]()
            result.append(cache[hyp.hyp_id])
        else:
            result.append(hyp)
    return result


def get_completed_hypotheses() -> list:
    """Returns only the 5 completion instances."""
    return [
        PanicMarketOrderSpreadTaxComplete(),
        DailyLossLimitDefenseComplete(),
        QuotePullBeforeVolatilityComplete(),
        RebateTrapAvoidanceComplete(),
        SpreadRegimeChangeComplete(),
    ]
