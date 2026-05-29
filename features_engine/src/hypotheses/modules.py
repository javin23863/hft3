import numpy as np
from typing import Dict, Any
from dataclasses import dataclass

@dataclass
class MarketState:
    """
    Represents the full mathematical state vector X_t = (B_t, A_t, Q_t, I_t, Z_t, L_t, E_t, V_t)
    as specified in Section 2 of the A+ Developer Prompt.
    """
    # Primary instrument features
    primary_features: Dict[str, float]
    
    # Cross-asset features for lead-lag / macro impulse evaluation
    cross_asset_features: Dict[str, Dict[str, float]] 
    
    # Latent and event states
    regime_state: str            # Z_t
    event_context: str           # E_t (e.g., 'CPI', 'FOMC', 'NORMAL')
    volatility_state: str        # V_t
    liquidity_state: str         # V_t
    latency_ms: float            # L_t
    current_inventory: int       # I_t

class BaseHypothesis:
    """
    Base class for all hypothesis testing modules.
    Evaluates the full MarketState vector.
    """
    def __init__(self, hyp_id: int, name: str):
        self.hyp_id = hyp_id
        self.name = name
        
    def evaluate(self, state: MarketState) -> float:
        """
        Returns a raw signal score [-1.0, 1.0] representing the hypothesis prediction.
        Must be overridden by subclasses.
        """
        raise NotImplementedError

class StopRunExhaustionFade(BaseHypothesis):
    """
    Hypothesis 2: Stop-run exhaustion fade
    Behavior: Sweep through level, stop-flow spike, aggressor deceleration, opposite-side refill.
    """
    def __init__(self):
        super().__init__(2, "Stop-run exhaustion fade")
        
    def evaluate(self, state: MarketState) -> float:
        features = state.primary_features
        agg_imb = features.get('aggressor_volume_imbalance', 0.0)
        cancel_pressure = features.get('near_touch_cancel_pressure', 0.0)
        book_slope = features.get('book_slope', 0.0)
        
        signal = 0.0
        if agg_imb > 0.6 and cancel_pressure > 0.5 and book_slope < -0.2:
            signal = -0.8
        elif agg_imb < -0.6 and cancel_pressure > 0.5 and book_slope > 0.2:
            signal = 0.8
            
        return signal

class DepthRefillImbalance(BaseHypothesis):
    """
    Hypothesis 4: Depth-refill imbalance
    """
    def __init__(self):
        super().__init__(4, "Depth-refill imbalance")
        
    def evaluate(self, state: MarketState) -> float:
        features = state.primary_features
        slope_change = features.get('book_slope_change', 0.0)
        cancel_add_ratio = features.get('cancel_to_add_ratio', 1.0)
        
        if slope_change > 0.1 and cancel_add_ratio < 0.5:
            return 0.6
        elif slope_change < -0.1 and cancel_add_ratio < 0.5:
            return -0.6
        return 0.0

class LiquidityVacuumContinuation(BaseHypothesis):
    """
    Hypothesis 3: Liquidity vacuum continuation
    Behavior: Top 3-10 levels vanish and fail to refill, accelerating price movement.
    """
    def __init__(self):
        super().__init__(3, "Liquidity vacuum continuation")
        
    def evaluate(self, state: MarketState) -> float:
        f = state.primary_features
        vacuum_score = f.get('liquidity_vacuum_score', 0.0)
        agg_imb = f.get('aggressor_volume_imbalance', 0.0)
        
        # High vacuum score combined with directional aggressor flow implies continuation
        if vacuum_score > 0.8:
            if agg_imb > 0.4:
                return 0.7  # Long continuation
            elif agg_imb < -0.4:
                return -0.7 # Short continuation
        return 0.0

class BookSlopeCollapse(BaseHypothesis):
    """
    Hypothesis 11: Book slope collapse
    Behavior: Depth disappears asymmetrically across top levels before price breaks.
    """
    def __init__(self):
        super().__init__(11, "Book slope collapse")
        
    def evaluate(self, state: MarketState) -> float:
        f = state.primary_features
        slope = f.get('book_slope', 0.0)
        slope_change = f.get('book_slope_change', 0.0)
        
        # Severe deterioration of bid depth -> impending downward break
        if slope < -0.5 and slope_change < -0.2:
            return -0.6
        # Severe deterioration of ask depth -> impending upward break
        elif slope > 0.5 and slope_change > 0.2:
            return 0.6
        return 0.0

class DOMIllusionTrap(BaseHypothesis):
    """
    Hypothesis 25: DOM illusion trap
    Behavior: Large displayed size cancels just before price reaches it, leading to a snapback.
    """
    def __init__(self):
        super().__init__(25, "DOM illusion trap")
        
    def evaluate(self, state: MarketState) -> float:
        f = state.primary_features
        near_cancel = f.get('near_touch_cancel_pressure', 0.0)
        agg_imb = f.get('aggressor_volume_imbalance', 0.0)
        
        # Massive near-touch cancels on the bid side despite sell pressure 
        # (Spoofing the bid to trap shorts) -> snapback up
        if near_cancel > 0.8 and agg_imb < -0.5:
            return 0.7 
            
        # Massive near-touch cancels on ask side despite buy pressure -> snapback down
        elif near_cancel > 0.8 and agg_imb > 0.5:
            return -0.7
            
        return 0.0

class ThinBookContinuation(BaseHypothesis):
    """
    Hypothesis 41: Thin-book continuation
    Behavior: Market makers remain wide/thin after first move, allowing momentum to carry.
    """
    def __init__(self):
        super().__init__(41, "Thin-book continuation")
        
    def evaluate(self, state: MarketState) -> float:
        f = state.primary_features
        spread_stress = f.get('spread_stress', 1.0)
        refill_ratio = f.get('cancel_to_add_ratio', 1.0) # Using cancel/add as proxy for lack of refill
        agg_imb = f.get('aggressor_volume_imbalance', 0.0)
        
        if spread_stress > 1.5 and refill_ratio > 1.2:
            if agg_imb > 0.3:
                return 0.5
            elif agg_imb < -0.3:
                return -0.5
        return 0.0

class FalseBreakoutTrap(BaseHypothesis):
    """
    Hypothesis 8: False breakout trap
    Behavior: Price breaks prior high/low, but MBO follow-through fails.
    """
    def __init__(self):
        super().__init__(8, "False breakout trap")
        
    def evaluate(self, state: MarketState) -> float:
        f = state.primary_features
        # Assumes external feature flags for breakout condition
        is_breakout = f.get('is_breaking_level', 0.0) 
        agg_imb = f.get('aggressor_volume_imbalance', 0.0)
        book_slope = f.get('book_slope', 0.0)
        
        # Breaking up, but buy volume is weak and asks are stacked
        if is_breakout > 0.5 and agg_imb < 0.2 and book_slope < -0.3:
            return -0.6
        # Breaking down, but sell volume is weak and bids are stacked
        elif is_breakout < -0.5 and agg_imb > -0.2 and book_slope > 0.3:
            return 0.6
        return 0.0

class AbsorptionFade(BaseHypothesis):
    """
    Hypothesis 12: Absorption fade
    Behavior: Aggressors hit same level repeatedly; price fails to move.
    """
    def __init__(self):
        super().__init__(12, "Absorption fade")
        
    def evaluate(self, state: MarketState) -> float:
        f = state.primary_features
        absorption = f.get('absorption_score', 0.0)
        agg_imb = f.get('aggressor_volume_imbalance', 0.0)
        
        # High absorption of buy orders -> fade the buyers
        if absorption > 0.8 and agg_imb > 0.5:
            return -0.7
        # High absorption of sell orders -> fade the sellers
        elif absorption > 0.8 and agg_imb < -0.5:
            return 0.7
        return 0.0

class IcebergReloadDetection(BaseHypothesis):
    """
    Hypothesis 13: Iceberg/reload detection
    Behavior: Displayed level repeatedly trades and replenishes.
    """
    def __init__(self):
        super().__init__(13, "Iceberg/reload detection")
        
    def evaluate(self, state: MarketState) -> float:
        f = state.primary_features
        reload_score = f.get('iceberg_reload_score', 0.0)
        
        # Direction depends on which side is reloading. 
        # Positive score = bid reloading, Negative = ask reloading.
        if reload_score > 0.7:
            return 0.6 # Join the bid iceberg
        elif reload_score < -0.7:
            return -0.6 # Join the ask iceberg
        return 0.0

class LiquidityDefenseBreak(BaseHypothesis):
    """
    Hypothesis 14: Liquidity defense break
    Behavior: Large defended level stops reloading, price accelerates.
    """
    def __init__(self):
        super().__init__(14, "Liquidity defense break")
        
    def evaluate(self, state: MarketState) -> float:
        f = state.primary_features
        # A previously high reload score that suddenly drops to zero while volume remains high
        reload_drop = f.get('reload_drop_score', 0.0) 
        agg_imb = f.get('aggressor_volume_imbalance', 0.0)
        
        if reload_drop > 0.8:
            if agg_imb > 0.4:
                return 0.8 # Ask defense broke, go long
            elif agg_imb < -0.4:
                return -0.8 # Bid defense broke, go short
        return 0.0

class EsToMesLeadLag(BaseHypothesis):
    """
    Hypothesis 16: ES -> MES lead-lag
    Behavior: ES order-flow pressure leads MES reaction.
    Requires multi-product state observation.
    """
    def __init__(self):
        super().__init__(16, "ES -> MES lead-lag")
        
    def evaluate(self, state: MarketState) -> float:
        # Assuming we are running this model for MES, so primary features are MES
        mes_imb = state.primary_features.get('aggressor_volume_imbalance', 0.0)
        
        # Check cross-asset ES features
        es_features = state.cross_asset_features.get('ES', {})
        es_imb = es_features.get('aggressor_volume_imbalance', 0.0)
        
        # ES is highly aggressive long, but MES hasn't reacted yet
        if es_imb > 0.8 and mes_imb < 0.2:
            return 0.6
        # ES is highly aggressive short, but MES hasn't reacted
        elif es_imb < -0.8 and mes_imb > -0.2:
            return -0.6
        return 0.0

class EndOfDayForcedFlatten(BaseHypothesis):
    """
    Hypothesis 29: End-of-day forced flatten flow
    Behavior: Directional exit pressure into known forced-flat windows.
    """
    def __init__(self):
        super().__init__(29, "End-of-day forced flatten flow")
        
    def evaluate(self, state: MarketState) -> float:
        # Check if we are in an explicit flattening window
        if state.event_context not in ('PROP_FLATTEN_TOPSTEP', 'FRIDAY_CLOSE', 'TPT_FLATTEN'):
            return 0.0
            
        f = state.primary_features
        agg_imb = f.get('aggressor_volume_imbalance', 0.0)
        
        # If we see aggressive liquidations near the cutoff, jump on the momentum
        # as retail traders are forcibly closed out at market by firm risk systems
        if agg_imb > 0.6:
            return 0.5
        elif agg_imb < -0.6:
            return -0.5
            
        return 0.0

class QuotePullBeforeVolatility(BaseHypothesis):
    """
    Hypothesis 39: Quote pull before volatility
    Behavior: Depth vanishes before scheduled high-impact events.
    """
    def __init__(self):
        super().__init__(39, "Quote pull before volatility")
        
    def evaluate(self, state: MarketState) -> float:
        # If we are in the tight window just before CPI
        if state.event_context == 'CPI_TIGHT':
            f = state.primary_features
            slope_change = f.get('book_slope_change', 0.0)
            
            # Massive symmetric liquidity pull means event is seconds away
            # Directional trading here is gambling. Return 0.0 but signal extreme caution.
            # A full implementation would use this to block entries.
            return 0.0
            
        return 0.0

class RequoteRaceAfterShock(BaseHypothesis):
    """
    Hypothesis 40: Re-quote race after shock
    Behavior: Depth returns unevenly after event shock.
    """
    def __init__(self):
        super().__init__(40, "Re-quote race after shock")
        
    def evaluate(self, state: MarketState) -> float:
        if state.regime_state == 'event_shock':
            f = state.primary_features
            slope_change = f.get('book_slope_change', 0.0)
            
            # If bids race back into the book while asks stay pulled
            if slope_change > 0.5:
                return 0.6
            # Asks racing back in while bids stay pulled
            elif slope_change < -0.5:
                return -0.6
        return 0.0
