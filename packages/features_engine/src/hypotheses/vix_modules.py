import math
import numpy as np
from .modules import BaseHypothesis, MarketState

_MACRO_TIGHT_CONTEXTS = frozenset({
    "CPI_TIGHT", "FOMC_TIGHT", "NFP_TIGHT", "NEWS_RESTRICTION",
})


class VixSpikeEventFade(BaseHypothesis):
    """
    Hypothesis 46: VIX spike event fade.

    When bipower variance spikes relative to TSRV (jump/diffusion separation),
    a vol-jump is detected. If aggressor imbalance is large, fade the direction:
    jumpers overshoot on execution demand and snap back intrabar.

    Grounding: Barndorff-Nielsen & Shephard 2004 jump/diffusion separation
    [[08 High-Frequency Econometrics]].
    """
    def __init__(self):
        super().__init__(46, "VIX_SPIKE_EVENT_FADE")

    def evaluate(self, state: MarketState) -> float:
        vix = state.cross_asset_features.get("VIX")
        if vix is None:
            return 0.0  # abstain: VIX coverage 2023+ only; never fabricate (defect-ke discipline)

        bipower = vix.get("vix_opt_bipower_var")
        tsrv = vix.get("vix_opt_tsrv")
        if bipower is None or tsrv is None:
            return 0.0
        if math.isnan(bipower) or math.isnan(tsrv):
            return 0.0

        # Gate: only fire in macro tight / event windows
        ctx = state.event_context
        if not (ctx.endswith("_TIGHT") or ctx in _MACRO_TIGHT_CONTEXTS):
            return 0.0

        eps = 1e-8
        jump_ratio = bipower / (tsrv + eps)
        if jump_ratio <= 1.5:
            return 0.0

        agg_imb = state.f("aggressor_volume_imbalance", 0.0)
        if abs(agg_imb) <= 0.5:
            return 0.0

        # Fade: oppose aggressor direction, scale by jump excess and imbalance magnitude
        fade = -math.copysign(1.0, agg_imb) * np.clip(jump_ratio - 1.0, 0.0, 1.0) * abs(math.tanh(2.0 * agg_imb))
        return float(np.clip(fade, -1.0, 1.0))


class VixQuotePullLiquidityVacuum(BaseHypothesis):
    """
    Hypothesis 47: VIX quote-pull liquidity vacuum.

    Decelerating VIX option quote arrivals combined with spread stress and an
    underlying liquidity vacuum → options MMs are pulling hedges, leaving the
    book thin. Continuation in the book-slope direction.

    Grounding: option-MM hedging→liquidity [[11 Options Microstructure]];
    quote-pull Hasbrouck-Saar 2013 [[10]].
    """
    def __init__(self):
        super().__init__(47, "VIX_QUOTE_PULL_LIQUIDITY_VACUUM")

    def evaluate(self, state: MarketState) -> float:
        vix = state.cross_asset_features.get("VIX")
        if vix is None:
            return 0.0  # abstain: VIX coverage 2023+ only; never fabricate (defect-ke discipline)

        arrival_accel = vix.get("vix_quote_arrival_accel")
        spread_stress = vix.get("vix_opt_spread_stress")
        if arrival_accel is None or spread_stress is None:
            return 0.0
        if math.isnan(arrival_accel) or math.isnan(spread_stress):
            return 0.0

        if arrival_accel >= 0.0:
            return 0.0
        if spread_stress <= 1.5:
            return 0.0

        vacuum = state.f("liquidity_vacuum_score", 0.0)
        if vacuum <= 0.3:
            return 0.0

        book_slope = state.f("book_slope", 0.0)
        signal = math.copysign(1.0, book_slope) * min(1.0, spread_stress / 3.0) * math.tanh(3.0 * vacuum)
        return float(np.clip(signal, -1.0, 1.0))


class VixImpliedRealizedGap(BaseHypothesis):
    """
    Hypothesis 48: VIX implied-realized vol gap.

    Compares VIX option bipower realized variance against the underlying's
    realized_vol_state (slot 26). Trades with the regime trend component when
    the gap is elevated: if VIX implied vol > realized, market overprices risk →
    fade momentum; if under, continuation.

    Grounding: [[08]] BNS estimators; VIX-as-predictor = vault gap,
    empirics archived [[System Blueprint]].

    Feature names used:
      - realized_vol_state (FeatureIndex slot 26; fallback via primary_features)
      - regime_posterior["trend_continuation"] (MarketState.regime_posterior dict)
      - book_slope (slot 13)
    """
    def __init__(self):
        super().__init__(48, "VIX_IMPLIED_REALIZED_GAP")

    def evaluate(self, state: MarketState) -> float:
        vix = state.cross_asset_features.get("VIX")
        if vix is None:
            return 0.0  # abstain: VIX coverage 2023+ only; never fabricate (defect-ke discipline)

        bipower = vix.get("vix_opt_bipower_var")
        if bipower is None or math.isnan(bipower):
            return 0.0

        realized = state.f("realized_vol_state", 0.0)
        # Normalize gap: tanh of (bipower - realized) with unit scale
        vol_gap = math.tanh(bipower - realized)

        regime_trend = state.regime_posterior.get("trend_continuation", 0.0)
        book_slope = state.f("book_slope", 0.0)

        signal = vol_gap * regime_trend * math.copysign(1.0, book_slope) if book_slope != 0.0 else 0.0
        return float(np.clip(signal, -1.0, 1.0))


class VixDepthImbalanceDirection(BaseHypothesis):
    """
    Hypothesis 49: VIX depth imbalance direction.

    Bid-heavy VIX option depth implies downside hedging demand → short bias
    on the underlying. Damped when spread is already stressed (execution cost
    exceeds edge). Gated to macro TIGHT windows only.

    Grounding: [[11]] options order imbalance 2023 — labeled proxy
    (no put-call decomposition available).
    """
    def __init__(self):
        super().__init__(49, "VIX_DEPTH_IMBALANCE_DIRECTION")

    def evaluate(self, state: MarketState) -> float:
        vix = state.cross_asset_features.get("VIX")
        if vix is None:
            return 0.0  # abstain: VIX coverage 2023+ only; never fabricate (defect-ke discipline)

        depth_imb = vix.get("vix_opt_depth_imbalance")
        if depth_imb is None or math.isnan(depth_imb):
            return 0.0

        # Gate: macro TIGHT contexts only
        ctx = state.event_context
        if not (ctx.endswith("_TIGHT") or ctx in _MACRO_TIGHT_CONTEXTS):
            return 0.0

        spread_stress = state.f("spread_stress", 0.0)
        damping = 1.0 / (1.0 + max(0.0, spread_stress - 1.0))
        signal = -math.tanh(2.0 * depth_imb) * damping
        return float(np.clip(signal, -1.0, 1.0))


class VixLevelConditionedContinuation(BaseHypothesis):
    """
    Hypothesis 50: VIX level-conditioned continuation.

    Only active when VIX ATM strike >= 20 (elevated vol regime) and the
    event-shock regime posterior is elevated. Continuation in aggressor
    imbalance direction (mirrors HYP_1 SecondWaveContinuation style).

    Grounding: regime-conditional EV [[System Blueprint]].

    Feature names used:
      - vix_atm_strike (VIX cross-asset dict)
      - regime_posterior["event_shock"] (MarketState.regime_posterior dict;
        corresponds to REGIME_EVENT_SHOCK slot 42)
      - aggressor_volume_imbalance (slot 0)
    """
    def __init__(self):
        super().__init__(50, "VIX_LEVEL_CONDITIONED_CONTINUATION")

    def evaluate(self, state: MarketState) -> float:
        vix = state.cross_asset_features.get("VIX")
        if vix is None:
            return 0.0  # abstain: VIX coverage 2023+ only; never fabricate (defect-ke discipline)

        atm_strike = vix.get("vix_atm_strike")
        if atm_strike is None or math.isnan(atm_strike):
            return 0.0

        if atm_strike < 20.0:
            return 0.0

        event_shock = state.regime_posterior.get("event_shock", 0.0)
        if event_shock <= 0.0:
            return 0.0

        agg_imb = state.f("aggressor_volume_imbalance", 0.0)
        signal = math.tanh(3.0 * agg_imb)
        return float(np.clip(signal, -1.0, 1.0))
