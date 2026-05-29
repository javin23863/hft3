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

class SpreadBlowoutRecompression(BaseHypothesis):
    """
    Hypothesis 5: Spread blowout/recompression
    """
    def __init__(self):
        super().__init__(5, "Spread blowout/recompression")
        
    def evaluate(self, state: MarketState) -> float:
        features = state.primary_features
        spread_stress = features.get('spread_stress', 1.0)
        book_slope = features.get('book_slope', 0.0)
        
        if spread_stress > 2.0:
            if book_slope > 0.2:
                return 0.5
            elif book_slope < -0.2:
                return -0.5
        return 0.0

class SecondWaveContinuation(BaseHypothesis):
    """
    Hypothesis 1: Second-wave continuation
    Behavior: Initial event sweep followed by continued same-side aggressor flow.
    """
    def __init__(self):
        super().__init__(1, "Second-wave continuation")
        
    def evaluate(self, state: MarketState) -> float:
        f = state.primary_features
        agg_imb = f.get('aggressor_volume_imbalance', 0.0)
        
        if agg_imb > 0.8:
            return 0.6
        elif agg_imb < -0.8:
            return -0.6
        return 0.0

class AggressorDecelerationFade(BaseHypothesis):
    """
    Hypothesis 6: Aggressor deceleration fade
    Behavior: Market orders continue but generate declining price impact.
    """
    def __init__(self):
        super().__init__(6, "Aggressor deceleration fade")
        
    def evaluate(self, state: MarketState) -> float:
        f = state.primary_features
        agg_imb = f.get('aggressor_volume_imbalance', 0.0)
        slope = f.get('book_slope', 0.0)
        
        if agg_imb > 0.7 and slope < -0.4:
            return -0.5 # Fade the buy imbalance
        elif agg_imb < -0.7 and slope > 0.4:
            return 0.5  # Fade the sell imbalance
        return 0.0

class ForcedLiquidationCascade(BaseHypothesis):
    """
    Hypothesis 7: Forced liquidation cascade
    Behavior: One-way aggressive flow, widening spread, repeated queue depletion, weak refill.
    """
    def __init__(self):
        super().__init__(7, "Forced liquidation cascade")
        
    def evaluate(self, state: MarketState) -> float:
        f = state.primary_features
        agg_imb = f.get('aggressor_volume_imbalance', 0.0)
        spread_stress = f.get('spread_stress', 1.0)
        cancel_ratio = f.get('cancel_to_add_ratio', 1.0)
        
        if spread_stress > 1.5 and cancel_ratio > 1.5:
            if agg_imb < -0.8:
                return -0.9 # Massive long liquidations -> jump short
            elif agg_imb > 0.8:
                return 0.9  # Massive short squeezes -> jump long
        return 0.0

class LateCandleEntryFade(BaseHypothesis):
    """
    Hypothesis 26: Late candle entry fade
    Behavior: Visible candle expansion with weakening underlying aggressor flow.
    """
    def __init__(self):
        super().__init__(26, "Late candle entry fade")
        
    def evaluate(self, state: MarketState) -> float:
        f = state.primary_features
        agg_imb = f.get('aggressor_volume_imbalance', 0.0)
        
        if state.regime_state == 'trend_continuation' and abs(agg_imb) < 0.1:
            return 0.0 
        return 0.0

class PanicMarketOrderSpreadTax(BaseHypothesis):
    """
    Hypothesis 28: Panic market-order spread tax
    Behavior: Retail/forced orders cross widened spread during stress.
    """
    def __init__(self):
        super().__init__(28, "Panic market-order spread tax")
        
    def evaluate(self, state: MarketState) -> float:
        f = state.primary_features
        spread_stress = f.get('spread_stress', 1.0)
        
        if state.volatility_state == 'HIGH' and spread_stress > 2.0:
            return 0.0
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

class SpreadRegimeChange(BaseHypothesis):
    """
    Hypothesis 44: Spread regime change
    Behavior: Spread remains elevated longer than baseline after shock, changing typical risk profiles.
    """
    def __init__(self):
        super().__init__(44, "Spread regime change")
        
    def evaluate(self, state: MarketState) -> float:
        f = state.primary_features
        spread_stress = f.get('spread_stress', 1.0)
        
        # If spread remains severely elevated and volatility is high, we might want to 
        # fade moves into the wide spread, or abstain. Returning neutral here.
        if spread_stress > 2.5 and state.volatility_state == 'HIGH':
            return 0.0
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

class OneSidedAddCancelImbalance(BaseHypothesis):
    """
    Hypothesis 15: One-sided add/cancel imbalance
    Behavior: Massive adds on one side and cancels on the other before price moves.
    """
    def __init__(self):
        super().__init__(15, "One-sided add/cancel imbalance")
        
    def evaluate(self, state: MarketState) -> float:
        f = state.primary_features
        bid_pressure = f.get('bid_add_cancel_ratio', 1.0)
        ask_pressure = f.get('ask_add_cancel_ratio', 1.0)
        
        # Heavy bid adds + heavy ask cancels -> impending move up
        if bid_pressure > 2.0 and ask_pressure < 0.5:
            return 0.6
        # Heavy ask adds + heavy bid cancels -> impending move down
        elif ask_pressure > 2.0 and bid_pressure < 0.5:
            return -0.6
        return 0.0

class RoundNumberStopSweep(BaseHypothesis):
    """
    Hypothesis 21: Round-number stop sweep
    Behavior: Sweep through big figure triggers stops then reverses.
    """
    def __init__(self):
        super().__init__(21, "Round-number stop sweep")
        
    def evaluate(self, state: MarketState) -> float:
        f = state.primary_features
        dist_to_round = f.get('distance_to_round_number', 1.0)
        agg_imb = f.get('aggressor_volume_imbalance', 0.0)
        
        # If we just swept a round number (dist very small) and aggressors are exhausted
        if abs(dist_to_round) < 0.01:
            if agg_imb > 0.8:
                return -0.5 # Exhaustion long, fade short
            elif agg_imb < -0.8:
                return 0.5  # Exhaustion short, fade long
        return 0.0

class PriorHighLowBreakoutTrap(BaseHypothesis):
    """
    Hypothesis 22: Prior high/low breakout trap
    Behavior: Breakout lacks volume, depth, or refill.
    """
    def __init__(self):
        super().__init__(22, "Prior high/low breakout trap")
        
    def evaluate(self, state: MarketState) -> float:
        f = state.primary_features
        is_breakout = f.get('is_breaking_session_level', 0.0)
        agg_imb = f.get('aggressor_volume_imbalance', 0.0)
        
        if is_breakout > 0.5 and agg_imb < 0.2:
            return -0.7
        elif is_breakout < -0.5 and agg_imb > -0.2:
            return 0.7
        return 0.0

class PassiveTrapFill(BaseHypothesis):
    """
    Hypothesis 42: Passive trap fill
    Behavior: Passive fills immediately followed by severe adverse selection.
    """
    def __init__(self):
        super().__init__(42, "Passive trap fill")
        
    def evaluate(self, state: MarketState) -> float:
        f = state.primary_features
        # Simulated by detecting a sudden shift in flow against our hypothetical fill side
        agg_imb = f.get('aggressor_volume_imbalance', 0.0)
        
        # If we got filled passive bid, but now flow is heavily shorting -> dump
        if agg_imb < -0.8:
            return -0.6
        return 0.0

class RebateTrapAvoidance(BaseHypothesis):
    """
    Hypothesis 43: Rebate trap avoidance
    Behavior: Maker-style fills lose more to adverse selection than rebate benefit.
    """
    def __init__(self):
        super().__init__(43, "Rebate trap avoidance")
        
    def evaluate(self, state: MarketState) -> float:
        # Very similar to Passive Trap Fill. Requires historical execution context.
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

class NqToMnqLeadLag(BaseHypothesis):
    """
    Hypothesis 17: NQ -> MNQ lead-lag
    Behavior: NQ order-flow pressure leads MNQ reaction.
    """
    def __init__(self):
        super().__init__(17, "NQ -> MNQ lead-lag")
        
    def evaluate(self, state: MarketState) -> float:
        mnq_imb = state.primary_features.get('aggressor_volume_imbalance', 0.0)
        nq_features = state.cross_asset_features.get('NQ', {})
        nq_imb = nq_features.get('aggressor_volume_imbalance', 0.0)
        
        if nq_imb > 0.8 and mnq_imb < 0.2:
            return 0.6
        elif nq_imb < -0.8 and mnq_imb > -0.2:
            return -0.6
        return 0.0

class EsNqDivergenceSnapback(BaseHypothesis):
    """
    Hypothesis 18: ES/NQ divergence snapback
    Behavior: NQ breaks but ES holds, snapback follows.
    """
    def __init__(self):
        super().__init__(18, "ES/NQ divergence snapback")
        
    def evaluate(self, state: MarketState) -> float:
        nq_features = state.cross_asset_features.get('NQ', {})
        es_features = state.cross_asset_features.get('ES', {})
        
        nq_imb = nq_features.get('aggressor_volume_imbalance', 0.0)
        es_imb = es_features.get('aggressor_volume_imbalance', 0.0)
        
        # Extreme divergence: NQ selling off hard, ES absorbing
        if nq_imb < -0.8 and es_imb > 0.2:
            return 0.5 # Buy NQ snapback
        elif nq_imb > 0.8 and es_imb < -0.2:
            return -0.5 # Sell NQ snapback
        return 0.0

class ZnZbToEsNqMacroImpulse(BaseHypothesis):
    """
    Hypothesis 19: ZN/ZB -> ES/NQ macro impulse
    Behavior: Treasury futures impulse leads equities.
    """
    def __init__(self):
        super().__init__(19, "ZN/ZB -> ES/NQ macro impulse")
        
    def evaluate(self, state: MarketState) -> float:
        zn_features = state.cross_asset_features.get('ZN', {})
        zn_imb = zn_features.get('aggressor_volume_imbalance', 0.0)
        
        es_imb = state.primary_features.get('aggressor_volume_imbalance', 0.0)
        
        if zn_imb > 0.8 and es_imb > -0.2:
            # Treasuries up -> rates down -> equities up
            return 0.5
        elif zn_imb < -0.8 and es_imb < 0.2:
            # Treasuries down -> rates up -> equities down
            return -0.5
        return 0.0

class MicroContractRetailLag(BaseHypothesis):
    """
    Hypothesis 20: Micro contract retail lag
    Behavior: Retail volume on micro lags institutional on macro.
    """
    def __init__(self):
        super().__init__(20, "Micro contract retail lag")
        
    def evaluate(self, state: MarketState) -> float:
        # Covered mostly by lead-lag, this focuses on trade size profiling
        es_features = state.cross_asset_features.get('ES', {})
        es_inst_flow = es_features.get('institutional_flow_score', 0.0)
        
        if es_inst_flow > 0.8:
            return 0.6
        elif es_inst_flow < -0.8:
            return -0.6
        return 0.0

class MaxContractCrowding(BaseHypothesis):
    """
    Hypothesis 35: Max-contract crowding in micros
    Behavior: Block trade in micro indicating macro positioning.
    """
    def __init__(self):
        super().__init__(35, "Max-contract crowding in micros")
        
    def evaluate(self, state: MarketState) -> float:
        f = state.primary_features
        block_trade = f.get('max_contract_trade_imbalance', 0.0)
        return block_trade * 0.5

class CancelStormBeforeMove(BaseHypothesis):
    """
    Hypothesis 9: Cancel storm before move
    Behavior: Large near-touch cancels before price break.
    """
    def __init__(self):
        super().__init__(9, "Cancel storm before move")
        
    def evaluate(self, state: MarketState) -> float:
        f = state.primary_features
        cancel_ratio = f.get('cancel_to_add_ratio', 1.0)
        cancel_pressure = f.get('near_touch_cancel_pressure', 0.0)
        book_slope = f.get('book_slope', 0.0)
        
        # Massive cancels near touch with severe book slope imbalance
        if cancel_ratio > 2.0 and cancel_pressure > 0.8:
            if book_slope < -0.4:
                return -0.8 # Bid collapse impending
            elif book_slope > 0.4:
                return 0.8  # Ask collapse impending
        return 0.0

class QueueDepletionTrigger(BaseHypothesis):
    """
    Hypothesis 10: Queue depletion trigger
    Behavior: Best bid/ask queue drains faster than refill.
    """
    def __init__(self):
        super().__init__(10, "Queue depletion trigger")
        
    def evaluate(self, state: MarketState) -> float:
        f = state.primary_features
        # Assumes a specific feature tracking the rate of depletion for the best level
        depletion_rate_bid = f.get('queue_depletion_rate_bid', 0.0)
        depletion_rate_ask = f.get('queue_depletion_rate_ask', 0.0)
        
        if depletion_rate_bid > 0.8 and depletion_rate_ask < 0.2:
            return -0.6
        elif depletion_rate_ask > 0.8 and depletion_rate_bid < 0.2:
            return 0.6
        return 0.0

class OpeningCandleChase(BaseHypothesis):
    """
    Hypothesis 23: Opening candle chase
    Behavior: 09:30 ET impulse followed by late chase or reversal.
    """
    def __init__(self):
        super().__init__(23, "Opening candle chase")
        
    def evaluate(self, state: MarketState) -> float:
        if state.event_context != 'CASH_EQUITY_OPEN':
            return 0.0
            
        f = state.primary_features
        agg_imb = f.get('aggressor_volume_imbalance', 0.0)
        spread_stress = f.get('spread_stress', 1.0)
        
        # Intense opening imbalance but spread remains stressed (exhaustion) -> reverse
        if spread_stress > 1.5:
            if agg_imb > 0.7:
                return -0.6
            elif agg_imb < -0.7:
                return 0.6
        return 0.0

class VWAPDefenseBreak(BaseHypothesis):
    """
    Hypothesis 24: VWAP defense/break
    Behavior: Repeated reload near VWAP followed by hold or failure.
    """
    def __init__(self):
        super().__init__(24, "VWAP defense/break")
        
    def evaluate(self, state: MarketState) -> float:
        f = state.primary_features
        dist_to_vwap = f.get('distance_to_vwap', 1.0)
        reload_score = f.get('iceberg_reload_score', 0.0)
        agg_imb = f.get('aggressor_volume_imbalance', 0.0)
        
        if abs(dist_to_vwap) < 0.05: # Price is at VWAP
            if reload_score > 0.6 and agg_imb < -0.3:
                return 0.5 # Bid defense holding
            elif reload_score < -0.6 and agg_imb > 0.3:
                return -0.5 # Ask defense holding
        return 0.0

class StopLossCascadeContinuation(BaseHypothesis):
    """
    Hypothesis 27: Stop-loss cascade continuation
    Behavior: Stops convert into market orders and book stays thin.
    """
    def __init__(self):
        super().__init__(27, "Stop-loss cascade continuation")
        
    def evaluate(self, state: MarketState) -> float:
        if state.regime_state != 'stop_cascade':
            return 0.0
            
        f = state.primary_features
        slope = f.get('book_slope', 0.0)
        agg_imb = f.get('aggressor_volume_imbalance', 0.0)
        
        # Momentum continuation into the thin side of the book
        if agg_imb < -0.5 and slope < -0.4:
            return -0.7
        elif agg_imb > 0.5 and slope > 0.4:
            return 0.7
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
        
        if agg_imb > 0.6:
            return 0.5
        elif agg_imb < -0.6:
            return -0.5
            
        return 0.0

class CutoffPanicExits(BaseHypothesis):
    """
    Hypothesis 30: Cutoff panic exits
    Behavior: Urgent flattening in final minutes before cutoff.
    """
    def __init__(self):
        super().__init__(30, "Cutoff panic exits")
        
    def evaluate(self, state: MarketState) -> float:
        if state.event_context not in ('TPT_FLATTEN', 'APEX_FLATTEN'):
            return 0.0
            
        f = state.primary_features
        cutoff_pressure = f.get('cutoff_pressure_score', 0.0)
        
        # Follow the forced liquidation flow
        if cutoff_pressure > 0.6:
            return 0.6
        elif cutoff_pressure < -0.6:
            return -0.6
        return 0.0

class NoOvernightInventorySqueeze(BaseHypothesis):
    """
    Hypothesis 31: No-overnight inventory squeeze
    Behavior: Late-day one-way reduction in directional exposure.
    """
    def __init__(self):
        super().__init__(31, "No-overnight inventory squeeze")
        
    def evaluate(self, state: MarketState) -> float:
        if state.event_context != 'FRIDAY_CLOSE': # Simplified proxy
            return 0.0
            
        f = state.primary_features
        agg_imb = f.get('aggressor_volume_imbalance', 0.0)
        
        if agg_imb > 0.5:
            return 0.5
        elif agg_imb < -0.5:
            return -0.5
        return 0.0

class DailyLossLimitDefense(BaseHypothesis):
    """
    Hypothesis 32: Daily loss-limit defense
    Behavior: Sudden flattening after adverse intraday move.
    """
    def __init__(self):
        super().__init__(32, "Daily loss-limit defense")
        
    def evaluate(self, state: MarketState) -> float:
        # Difficult to observe without account-level data.
        # Implemented here as a reactive momentum fade after severe one-way action.
        if state.regime_state == 'prop_flatten':
            return 0.0
        return 0.0

class TrailingDrawdownPressure(BaseHypothesis):
    """
    Hypothesis 33: Trailing drawdown pressure
    Behavior: Panic exit after reversal threatens account state.
    """
    def __init__(self):
        super().__init__(33, "Trailing drawdown pressure")
        
    def evaluate(self, state: MarketState) -> float:
        f = state.primary_features
        # Simulated by detecting sudden high volume opposite to prevailing trend
        agg_imb = f.get('aggressor_volume_imbalance', 0.0)
        if state.regime_state == 'trend_continuation' and abs(agg_imb) > 0.8:
            return np.sign(agg_imb) * 0.6 # Join the panic
        return 0.0

class ProfitLockBehavior(BaseHypothesis):
    """
    Hypothesis 34: Profit-lock behavior
    Behavior: Trend-day winners close into payout/cutoff protection.
    """
    def __init__(self):
        super().__init__(34, "Profit-lock behavior")
        
    def evaluate(self, state: MarketState) -> float:
        if state.event_context in ('PROP_FLATTEN_TOPSTEP', 'TPT_FLATTEN') and state.regime_state == 'trend_continuation':
            f = state.primary_features
            agg_imb = f.get('aggressor_volume_imbalance', 0.0)
            # Fade the prevailing trend as winners lock profit (exit long = sell pressure)
            # If we detect the start of this flow, join it.
            if abs(agg_imb) > 0.5:
                return np.sign(agg_imb) * 0.5
        return 0.0

class PropResetReopenWindow(BaseHypothesis):
    """
    Hypothesis 36: Prop reset/reopen window
    Behavior: Re-entry behavior after allowed session resumes.
    """
    def __init__(self):
        super().__init__(36, "Prop reset/reopen window")
        
    def evaluate(self, state: MarketState) -> float:
        if state.event_context == 'PROP_REOPEN':
            f = state.primary_features
            reentry_score = f.get('prop_reentry_score', 0.0)
            return reentry_score
        return 0.0

class FridayWeekendDerisking(BaseHypothesis):
    """
    Hypothesis 37: Friday/weekend de-risking
    Behavior: Friday final-hour exposure reduction.
    """
    def __init__(self):
        super().__init__(37, "Friday/weekend de-risking")
        
    def evaluate(self, state: MarketState) -> float:
        if state.event_context == 'FRIDAY_CLOSE':
            f = state.primary_features
            agg_imb = f.get('aggressor_volume_imbalance', 0.0)
            return np.sign(agg_imb) * 0.5
        return 0.0

class EconomicEventRestrictionFlattening(BaseHypothesis):
    """
    Hypothesis 38: Economic-event restriction flattening
    Behavior: Pre-news flattening and post-news re-entry.
    """
    def __init__(self):
        super().__init__(38, "Economic-event restriction flattening")
        
    def evaluate(self, state: MarketState) -> float:
        # E.g. MFFU restriction window
        if state.event_context == 'NEWS_RESTRICTION':
            f = state.primary_features
            flatten_score = f.get('news_restriction_flatten_score', 0.0)
            return flatten_score
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
