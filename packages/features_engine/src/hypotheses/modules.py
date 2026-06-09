import numpy as np
from typing import Dict, Any, Optional
from dataclasses import dataclass, field

from features_engine.src.features.feature_index import FEATURE_NAME_TO_INDEX


@dataclass
class MarketState:
    """
    Represents the full mathematical state vector X_t = (B_t, A_t, Q_t, I_t, Z_t, L_t, E_t, V_t)
    as specified in Section 2 of the A+ Developer Prompt.
    """
    primary_features: Dict[str, float]
    cross_asset_features: Dict[str, Dict[str, float]]
    regime_state: str  # argmax Z_t
    event_context: str  # E_t
    volatility_state: str  # V_t
    liquidity_state: str  # V_t
    latency_ms: float  # L_t
    current_inventory: int  # I_t
    feature_vector: Optional[np.ndarray] = None
    regime_posterior: Dict[str, float] = field(default_factory=dict)

    def f(self, name: str, default: float = 0.0) -> float:
        """Indexed feature access (no string hashing on hot path)."""
        if self.feature_vector is not None:
            idx = FEATURE_NAME_TO_INDEX.get(name)
            if idx is not None and idx < len(self.feature_vector):
                return float(self.feature_vector[idx])
        return float(self.primary_features.get(name, default))


class BaseHypothesis:
    """
    Base class for all hypothesis testing modules.
    Evaluates the full MarketState vector to produce continuous mathematical signals 
    for the marked point process or Q-learning approximation.
    """
    def __init__(self, hyp_id: int, name: str):
        self.hyp_id = hyp_id
        self.name = name
        
    def evaluate(self, state: MarketState) -> float:
        raise NotImplementedError

class StopRunExhaustionFade(BaseHypothesis):
    """
    Hypothesis 2: Stop-run exhaustion fade
    Behavior: Sweep through level, stop-flow spike, aggressor deceleration, opposite-side refill.
    """
    def __init__(self):
        super().__init__(2, "Stop-run exhaustion fade")
        
    def evaluate(self, state: MarketState) -> float:
        agg_imb = state.f('aggressor_volume_imbalance', 0.0)
        cancel_pressure = state.f('near_touch_cancel_pressure', 0.0)
        book_slope = state.f('book_slope', 0.0)
        
        # Activation based on cancel pressure and extreme imbalance
        activation = np.clip(cancel_pressure, 0.0, 1.0) * np.abs(np.tanh(agg_imb * 2.0))
        # Fade when book slope opposes the aggressive imbalance
        fade_direction = -np.sign(agg_imb)
        slope_alignment = np.clip(fade_direction * book_slope, 0.0, 1.0)
        
        signal = activation * slope_alignment * fade_direction
        return float(np.clip(signal, -1.0, 1.0))

class DepthRefillImbalance(BaseHypothesis):
    """
    Hypothesis 4: Depth-refill imbalance
    """
    def __init__(self):
        super().__init__(4, "Depth-refill imbalance")
        
    def evaluate(self, state: MarketState) -> float:
        slope_change = state.f('book_slope_change', 0.0)
        cancel_add_ratio = state.f('cancel_to_add_ratio', 1.0)
        
        # Continuous response: high when adds dominate (low ratio)
        refill_strength = np.exp(-cancel_add_ratio)
        signal = np.tanh(slope_change * 5.0) * refill_strength
        return float(signal)

class SpreadBlowoutRecompression(BaseHypothesis):
    """
    Hypothesis 5: Spread blowout/recompression
    """
    def __init__(self):
        super().__init__(5, "Spread blowout/recompression")
        
    def evaluate(self, state: MarketState) -> float:
        spread_stress = state.f('spread_stress', 1.0)
        book_slope = state.f('book_slope', 0.0)
        
        # Exponential activation when spread stress exceeds baseline (1.0)
        activation = 1.0 - np.exp(-np.maximum(0.0, spread_stress - 1.0))
        signal = activation * np.tanh(book_slope * 2.0)
        return float(signal)

class SecondWaveContinuation(BaseHypothesis):
    """
    Hypothesis 1: Second-wave continuation
    """
    def __init__(self):
        super().__init__(1, "Second-wave continuation")
        
    def evaluate(self, state: MarketState) -> float:
        agg_imb = state.f('aggressor_volume_imbalance', 0.0)
        # Sigmoid-like response to strong persistent imbalances
        signal = np.tanh(agg_imb * 3.0)
        return float(signal)

class AggressorDecelerationFade(BaseHypothesis):
    """
    Hypothesis 6: Aggressor deceleration fade
    """
    def __init__(self):
        super().__init__(6, "Aggressor deceleration fade")
        
    def evaluate(self, state: MarketState) -> float:
        agg_imb = state.f('aggressor_volume_imbalance', 0.0)
        slope = state.f('book_slope', 0.0)
        
        # Fade strength increases when imbalance and slope diverge
        divergence = -agg_imb * slope
        activation = np.maximum(0.0, divergence)
        signal = -np.sign(agg_imb) * np.tanh(activation * 2.0)
        return float(signal)

class ForcedLiquidationCascade(BaseHypothesis):
    """
    Hypothesis 7: Forced liquidation cascade
    """
    def __init__(self):
        super().__init__(7, "Forced liquidation cascade")
        
    def evaluate(self, state: MarketState) -> float:
        agg_imb = state.f('aggressor_volume_imbalance', 0.0)
        spread_stress = state.f('spread_stress', 1.0)
        cancel_ratio = state.f('cancel_to_add_ratio', 1.0)
        
        stress_activation = 1.0 - np.exp(-np.maximum(0.0, spread_stress - 1.0))
        panic_activation = 1.0 - np.exp(-np.maximum(0.0, cancel_ratio - 1.0))
        signal = -np.tanh(agg_imb * 3.0) * stress_activation * panic_activation
        return float(signal)

class LateCandleEntryFade(BaseHypothesis):
    """
    Hypothesis 26: Late candle entry fade
    """
    def __init__(self):
        super().__init__(26, "Late candle entry fade")
        
    def evaluate(self, state: MarketState) -> float:
        agg_imb = state.f('aggressor_volume_imbalance', 0.0)
        
        # Only active in trend continuation regime
        regime_multiplier = 1.0 if state.regime_state == 'trend_continuation' else 0.0
        # Fade the weakness in the direction of the trend
        fade_signal = np.exp(-np.abs(agg_imb) * 5.0) - 0.5 # Positive when imb is near 0
        return float(regime_multiplier * np.clip(fade_signal, -1.0, 1.0))

class PanicMarketOrderSpreadTax(BaseHypothesis):
    """
    Hypothesis 28: Panic market-order spread tax
    """
    def __init__(self):
        super().__init__(28, "Panic market-order spread tax")
        
    def evaluate(self, state: MarketState) -> float:
        spread_stress = state.f('spread_stress', 1.0)
        
        vol_multiplier = 1.0 if state.volatility_state == 'HIGH' else 0.0
        activation = 1.0 - np.exp(-np.maximum(0.0, spread_stress - 1.5))
        # Neutral direction but high activation (represents passive join edge)
        return float(vol_multiplier * activation * 0.0)

class LiquidityVacuumContinuation(BaseHypothesis):
    """
    Hypothesis 3: Liquidity vacuum continuation
    """
    def __init__(self):
        super().__init__(3, "Liquidity vacuum continuation")
        
    def evaluate(self, state: MarketState) -> float:
        vacuum_score = state.f('liquidity_vacuum_score', 0.0)
        agg_imb = state.f('aggressor_volume_imbalance', 0.0)
        
        signal = np.tanh(vacuum_score * 2.0) * np.tanh(agg_imb * 2.0)
        return float(signal)

class BookSlopeCollapse(BaseHypothesis):
    """
    Hypothesis 11: Book slope collapse
    """
    def __init__(self):
        super().__init__(11, "Book slope collapse")
        
    def evaluate(self, state: MarketState) -> float:
        slope = state.f('book_slope', 0.0)
        slope_change = state.f('book_slope_change', 0.0)
        
        # Product of slope and slope_change identifies accelerating collapse
        collapse_intensity = np.sign(slope) * np.maximum(0.0, slope * slope_change)
        signal = np.tanh(collapse_intensity * 5.0)
        return float(signal)

class DOMIllusionTrap(BaseHypothesis):
    """
    Hypothesis 25: DOM illusion trap
    """
    def __init__(self):
        super().__init__(25, "DOM illusion trap")
        
    def evaluate(self, state: MarketState) -> float:
        near_cancel = state.f('near_touch_cancel_pressure', 0.0)
        agg_imb = state.f('aggressor_volume_imbalance', 0.0)
        
        # Cancel pressure combined with opposing flow -> snapback
        activation = np.tanh(near_cancel * 3.0)
        divergence = -agg_imb * near_cancel
        signal = activation * np.tanh(divergence * 2.0)
        return float(signal)

class ThinBookContinuation(BaseHypothesis):
    """
    Hypothesis 41: Thin-book continuation
    """
    def __init__(self):
        super().__init__(41, "Thin-book continuation")
        
    def evaluate(self, state: MarketState) -> float:
        spread_stress = state.f('spread_stress', 1.0)
        refill_ratio = state.f('cancel_to_add_ratio', 1.0) 
        agg_imb = state.f('aggressor_volume_imbalance', 0.0)
        
        stress_act = 1.0 - np.exp(-np.maximum(0.0, spread_stress - 1.0))
        refill_act = 1.0 - np.exp(-np.maximum(0.0, refill_ratio - 1.0))
        signal = stress_act * refill_act * np.tanh(agg_imb * 2.0)
        return float(signal)

class SpreadRegimeChange(BaseHypothesis):
    """
    Hypothesis 44: Spread regime change
    """
    def __init__(self):
        super().__init__(44, "Spread regime change")
        
    def evaluate(self, state: MarketState) -> float:
        spread_stress = state.f('spread_stress', 1.0)
        
        vol_act = 1.0 if state.volatility_state == 'HIGH' else 0.0
        activation = 1.0 - np.exp(-np.maximum(0.0, spread_stress - 2.0))
        return float(vol_act * activation * 0.0) # Modulates other risk components

class FalseBreakoutTrap(BaseHypothesis):
    """
    Hypothesis 8: False breakout trap

    Activates when spread is in a stress regime (SPREAD_STRESS_ELEVATED = 1)
    but flow and book slope do not confirm a directional move, suggesting the
    wide spread is driven by noise rather than a genuine level break.
    """
    def __init__(self):
        super().__init__(8, "False breakout trap")

    def evaluate(self, state: MarketState) -> float:
        # SPREAD_STRESS_ELEVATED (index 27): 1.0 when spread_stress > 2.0
        stress_elevated = state.f('spread_stress_elevated', 0.0)
        agg_imb = state.f('aggressor_volume_imbalance', 0.0)
        book_slope = state.f('book_slope', 0.0)

        # Only fire when spread is stressed; fade the direction of the imbalance
        # since stress without flow confirmation often reverses.
        flow_confirmation = np.tanh(agg_imb * 2.0)
        slope_confirmation = np.tanh(book_slope * 2.0)
        confirmation = (np.abs(flow_confirmation) + np.abs(slope_confirmation)) / 2.0

        # Fade signal: negative (oppose the imbalance direction) when stress is
        # high and confirmation is low.
        signal = -stress_elevated * (1.0 - confirmation) * np.tanh(agg_imb * 2.0)
        return float(np.clip(signal, -1.0, 1.0))

class AbsorptionFade(BaseHypothesis):
    """
    Hypothesis 12: Absorption fade
    """
    def __init__(self):
        super().__init__(12, "Absorption fade")
        
    def evaluate(self, state: MarketState) -> float:
        absorption = state.f('absorption_score', 0.0)
        agg_imb = state.f('aggressor_volume_imbalance', 0.0)
        
        # High absorption opposing the imbalance
        signal = -np.tanh(agg_imb * 2.0) * np.tanh(absorption * 3.0)
        return float(signal)

class IcebergReloadDetection(BaseHypothesis):
    """
    Hypothesis 13: Iceberg/reload detection
    """
    def __init__(self):
        super().__init__(13, "Iceberg/reload detection")
        
    def evaluate(self, state: MarketState) -> float:
        reload_score = state.f('iceberg_reload_score', 0.0)
        # Join the side that is reloading
        return float(np.tanh(reload_score * 2.0))

class LiquidityDefenseBreak(BaseHypothesis):
    """
    Hypothesis 14: Liquidity defense break
    """
    def __init__(self):
        super().__init__(14, "Liquidity defense break")
        
    def evaluate(self, state: MarketState) -> float:
        reload_drop = state.f('reload_drop_score', 0.0) 
        agg_imb = state.f('aggressor_volume_imbalance', 0.0)
        
        # Acceleration in direction of flow when defense breaks
        signal = np.tanh(reload_drop * 3.0) * np.tanh(agg_imb * 2.0)
        return float(signal)

class OneSidedAddCancelImbalance(BaseHypothesis):
    """
    Hypothesis 15: One-sided add/cancel imbalance
    """
    def __init__(self):
        super().__init__(15, "One-sided add/cancel imbalance")
        
    def evaluate(self, state: MarketState) -> float:
        bid_pressure = state.f('bid_add_cancel_ratio', 1.0)
        ask_pressure = state.f('ask_add_cancel_ratio', 1.0)
        
        # Ratio transformation for continuous score
        bid_score = np.log1p(bid_pressure)
        ask_score = np.log1p(ask_pressure)
        signal = np.tanh(bid_score - ask_score)
        return float(signal)

class RoundNumberStopSweep(BaseHypothesis):
    """
    Hypothesis 21: Round-number stop sweep

    DISTANCE_TO_ROUND_NUMBER is the distance in ticks from mid to the nearest
    configurable round-number increment (default 10 pts = 40 ticks for ES/MES).
    The Gaussian proximity kernel uses sigma=4 ticks so the hypothesis is
    active only when price is within ~1 point of a round level.  Fade the
    aggressor flow direction near round numbers (stop sweep / false breakout).
    """
    def __init__(self):
        super().__init__(21, "Round-number stop sweep")

    def evaluate(self, state: MarketState) -> float:
        dist_to_round = state.f('distance_to_round_number', 0.0)  # ticks
        agg_imb = state.f('aggressor_volume_imbalance', 0.0)

        # Gaussian proximity: sigma=4 ticks; most active within 1 point of level.
        proximity = np.exp(-(dist_to_round ** 2) / (2.0 * 4.0 ** 2))
        # Fade the exhaustion near the round number (counter-trend)
        signal = -proximity * np.tanh(agg_imb * 2.0)
        return float(signal)

class PriorHighLowBreakoutTrap(BaseHypothesis):
    """
    Hypothesis 22: Prior high/low breakout trap

    IS_BREAKING_SESSION_LEVEL is now the real session extreme break:
    +1 when mid takes out the session high, -1 when it takes out the session
    low, 0 otherwise.  Fade when price breaks the session extreme but aggressor
    flow does not confirm (potential false breakout / stop sweep).
    """
    def __init__(self):
        super().__init__(22, "Prior high/low breakout trap")

    def evaluate(self, state: MarketState) -> float:
        # Signed: +1 = high break, -1 = low break, 0 = inside range
        is_breakout = state.f('is_breaking_session_level', 0.0)
        agg_imb = state.f('aggressor_volume_imbalance', 0.0)

        # Fade when breakout direction contradicts or is unsupported by flow.
        trap_intensity = np.sign(is_breakout) * (np.abs(is_breakout) - np.abs(np.tanh(agg_imb * 2.0)))
        return float(-np.tanh(trap_intensity * 2.0))

class PassiveTrapFill(BaseHypothesis):
    """
    Hypothesis 42: Passive trap fill
    """
    def __init__(self):
        super().__init__(42, "Passive trap fill")
        
    def evaluate(self, state: MarketState) -> float:
        agg_imb = state.f('aggressor_volume_imbalance', 0.0)
        
        # Simulated continuous loss function representation
        return float(np.tanh(agg_imb * 3.0))

class RebateTrapAvoidance(BaseHypothesis):
    """
    Hypothesis 43: Rebate trap avoidance
    """
    def __init__(self):
        super().__init__(43, "Rebate trap avoidance")
        
    def evaluate(self, state: MarketState) -> float:
        return 0.0

class EsToMesLeadLag(BaseHypothesis):
    """
    Hypothesis 16: ES -> MES lead-lag
    """
    def __init__(self):
        super().__init__(16, "ES -> MES lead-lag")
        
    def evaluate(self, state: MarketState) -> float:
        mes_imb = state.primary_features.get('aggressor_volume_imbalance', 0.0)
        es_features = state.cross_asset_features.get('ES', {})
        es_imb = es_features.get('aggressor_volume_imbalance', 0.0)
        
        # Lead-lag divergence
        divergence = es_imb - mes_imb
        signal = np.tanh(divergence * 2.0)
        return float(signal)

class NqToMnqLeadLag(BaseHypothesis):
    """
    Hypothesis 17: NQ -> MNQ lead-lag
    """
    def __init__(self):
        super().__init__(17, "NQ -> MNQ lead-lag")
        
    def evaluate(self, state: MarketState) -> float:
        mnq_imb = state.primary_features.get('aggressor_volume_imbalance', 0.0)
        nq_features = state.cross_asset_features.get('NQ', {})
        nq_imb = nq_features.get('aggressor_volume_imbalance', 0.0)
        
        divergence = nq_imb - mnq_imb
        return float(np.tanh(divergence * 2.0))

class EsNqDivergenceSnapback(BaseHypothesis):
    """
    Hypothesis 18: ES/NQ divergence snapback
    """
    def __init__(self):
        super().__init__(18, "ES/NQ divergence snapback")
        
    def evaluate(self, state: MarketState) -> float:
        nq_features = state.cross_asset_features.get('NQ', {})
        es_features = state.cross_asset_features.get('ES', {})
        
        nq_imb = nq_features.get('aggressor_volume_imbalance', 0.0)
        es_imb = es_features.get('aggressor_volume_imbalance', 0.0)
        
        # Mean reversion of extreme divergence
        divergence = es_imb - nq_imb
        return float(-np.tanh(divergence * 1.5))

class ZnZbToEsNqMacroImpulse(BaseHypothesis):
    """
    Hypothesis 19: ZN/ZB -> ES/NQ macro impulse
    """
    def __init__(self):
        super().__init__(19, "ZN/ZB -> ES/NQ macro impulse")
        
    def evaluate(self, state: MarketState) -> float:
        zn_features = state.cross_asset_features.get('ZN', {})
        zn_imb = zn_features.get('aggressor_volume_imbalance', 0.0)
        es_imb = state.primary_features.get('aggressor_volume_imbalance', 0.0)
        
        # Rates down -> Treasuries up -> Equities up. (Positive correlation here)
        impulse = zn_imb - es_imb
        return float(np.tanh(impulse * 2.0))

class MicroContractRetailLag(BaseHypothesis):
    """
    Hypothesis 20: Micro contract retail lag
    """
    def __init__(self):
        super().__init__(20, "Micro contract retail lag")
        
    def evaluate(self, state: MarketState) -> float:
        es_features = state.cross_asset_features.get('ES', {})
        es_inst_flow = es_features.get('institutional_flow_score', 0.0)
        return float(np.tanh(es_inst_flow * 2.0))

class MaxContractCrowding(BaseHypothesis):
    """
    Hypothesis 35: Max-contract crowding in micros
    """
    def __init__(self):
        super().__init__(35, "Max-contract crowding in micros")
        
    def evaluate(self, state: MarketState) -> float:
        block_trade = state.f('max_contract_trade_imbalance', 0.0)
        return float(np.tanh(block_trade * 2.0))

class CancelStormBeforeMove(BaseHypothesis):
    """
    Hypothesis 9: Cancel storm before move
    """
    def __init__(self):
        super().__init__(9, "Cancel storm before move")
        
    def evaluate(self, state: MarketState) -> float:
        cancel_ratio = state.f('cancel_to_add_ratio', 1.0)
        cancel_pressure = state.f('near_touch_cancel_pressure', 0.0)
        book_slope = state.f('book_slope', 0.0)
        
        storm_activation = 1.0 - np.exp(-np.maximum(0.0, cancel_ratio - 1.5))
        pressure_activation = np.tanh(cancel_pressure * 2.0)
        signal = storm_activation * pressure_activation * np.tanh(book_slope * 2.0)
        return float(signal)

class QueueDepletionTrigger(BaseHypothesis):
    """
    Hypothesis 10: Queue depletion trigger
    """
    def __init__(self):
        super().__init__(10, "Queue depletion trigger")
        
    def evaluate(self, state: MarketState) -> float:
        depletion_rate_bid = state.f('queue_depletion_rate_bid', 0.0)
        depletion_rate_ask = state.f('queue_depletion_rate_ask', 0.0)
        
        depletion_diff = depletion_rate_ask - depletion_rate_bid # positive if ask depletes faster
        return float(np.tanh(depletion_diff * 3.0))

class OpeningCandleChase(BaseHypothesis):
    """
    Hypothesis 23: Opening candle chase
    """
    def __init__(self):
        super().__init__(23, "Opening candle chase")
        
    def evaluate(self, state: MarketState) -> float:
        if state.event_context != 'CASH_EQUITY_OPEN':
            return 0.0
        agg_imb = state.f('aggressor_volume_imbalance', 0.0)
        spread_stress = state.f('spread_stress', 1.0)
        
        stress_act = 1.0 - np.exp(-np.maximum(0.0, spread_stress - 1.0))
        # Fade the opening chase if spread is stressed
        return float(-stress_act * np.tanh(agg_imb * 2.0))

class VWAPDefenseBreak(BaseHypothesis):
    """
    Hypothesis 24: VWAP defense/break

    DISTANCE_TO_VWAP is now a real session VWAP computed from trade events:
    signed distance in ticks, positive when mid > VWAP (above), negative when
    mid < VWAP (below).  Zero until the first TRADE event is seen.

    Proximity kernel uses a Gaussian over tick distance; sigma=8 ticks (~2pts
    for ES/MES) means the hypothesis is most active when price is within a few
    ticks of VWAP.  Defense holds if reload score opposes the aggressor flow.
    """
    def __init__(self):
        super().__init__(24, "VWAP defense/break")

    def evaluate(self, state: MarketState) -> float:
        dist_to_vwap = state.f('distance_to_vwap', 0.0)  # signed ticks
        reload_score = state.f('iceberg_reload_score', 0.0)
        agg_imb = state.f('aggressor_volume_imbalance', 0.0)

        # Gaussian proximity: sigma=8 ticks; peaks at VWAP, decays symmetrically.
        # Returns 0 when VWAP is unavailable (dist=0 → proximity=1, but reload
        # and imbalance will dominate in that case, which is acceptable).
        vwap_proximity = np.exp(-(dist_to_vwap ** 2) / (2.0 * 8.0 ** 2))
        # Defense holds if reload opposes imbalance direction
        defense_strength = -np.sign(agg_imb) * reload_score
        return float(vwap_proximity * np.tanh(defense_strength * 2.0))

class StopLossCascadeContinuation(BaseHypothesis):
    """
    Hypothesis 27: Stop-loss cascade continuation
    """
    def __init__(self):
        super().__init__(27, "Stop-loss cascade continuation")
        
    def evaluate(self, state: MarketState) -> float:
        if state.regime_state != 'stop_cascade':
            return 0.0
        slope = state.f('book_slope', 0.0)
        agg_imb = state.f('aggressor_volume_imbalance', 0.0)
        
        return float(np.tanh(agg_imb * 2.0) * np.tanh(slope * 2.0))

class EndOfDayForcedFlatten(BaseHypothesis):
    """
    Hypothesis 29: End-of-day forced flatten flow
    """
    def __init__(self):
        super().__init__(29, "End-of-day forced flatten flow")
        
    def evaluate(self, state: MarketState) -> float:
        if state.event_context not in ('PROP_FLATTEN_TOPSTEP', 'FRIDAY_CLOSE', 'TPT_FLATTEN'):
            return 0.0
        agg_imb = state.f('aggressor_volume_imbalance', 0.0)
        return float(np.tanh(agg_imb * 2.0))

class CutoffPanicExits(BaseHypothesis):
    """
    Hypothesis 30: Cutoff panic exits
    """
    def __init__(self):
        super().__init__(30, "Cutoff panic exits")
        
    def evaluate(self, state: MarketState) -> float:
        if state.event_context not in ('TPT_FLATTEN', 'APEX_FLATTEN'):
            return 0.0
        cutoff_pressure = state.f('cutoff_pressure_score', 0.0)
        return float(np.tanh(cutoff_pressure * 3.0))

class NoOvernightInventorySqueeze(BaseHypothesis):
    """
    Hypothesis 31: No-overnight inventory squeeze
    """
    def __init__(self):
        super().__init__(31, "No-overnight inventory squeeze")
        
    def evaluate(self, state: MarketState) -> float:
        if state.event_context != 'FRIDAY_CLOSE':
            return 0.0
        agg_imb = state.f('aggressor_volume_imbalance', 0.0)
        return float(np.tanh(agg_imb * 2.0))

class DailyLossLimitDefense(BaseHypothesis):
    """
    Hypothesis 32: Daily loss-limit defense
    """
    def __init__(self):
        super().__init__(32, "Daily loss-limit defense")
        
    def evaluate(self, state: MarketState) -> float:
        if state.regime_state == 'prop_flatten':
            return 0.0
        return 0.0

class TrailingDrawdownPressure(BaseHypothesis):
    """
    Hypothesis 33: Trailing drawdown pressure
    """
    def __init__(self):
        super().__init__(33, "Trailing drawdown pressure")
        
    def evaluate(self, state: MarketState) -> float:
        agg_imb = state.f('aggressor_volume_imbalance', 0.0)
        
        regime_mult = 1.0 if state.regime_state == 'trend_continuation' else 0.0
        return float(regime_mult * np.tanh(agg_imb * 3.0))

class ProfitLockBehavior(BaseHypothesis):
    """
    Hypothesis 34: Profit-lock behavior
    """
    def __init__(self):
        super().__init__(34, "Profit-lock behavior")
        
    def evaluate(self, state: MarketState) -> float:
        if state.event_context in ('PROP_FLATTEN_TOPSTEP', 'TPT_FLATTEN') and state.regime_state == 'trend_continuation':
            agg_imb = state.f('aggressor_volume_imbalance', 0.0)
            return float(np.tanh(agg_imb * 2.0))
        return 0.0

class PropResetReopenWindow(BaseHypothesis):
    """
    Hypothesis 36: Prop reset/reopen window
    """
    def __init__(self):
        super().__init__(36, "Prop reset/reopen window")
        
    def evaluate(self, state: MarketState) -> float:
        if state.event_context == 'PROP_REOPEN':
            reentry_score = state.f('prop_reentry_score', 0.0)
            return float(np.tanh(reentry_score * 2.0))
        return 0.0

class FridayWeekendDerisking(BaseHypothesis):
    """
    Hypothesis 37: Friday/weekend de-risking
    """
    def __init__(self):
        super().__init__(37, "Friday/weekend de-risking")
        
    def evaluate(self, state: MarketState) -> float:
        if state.event_context == 'FRIDAY_CLOSE':
            agg_imb = state.f('aggressor_volume_imbalance', 0.0)
            return float(np.tanh(agg_imb * 2.0))
        return 0.0

class EconomicEventRestrictionFlattening(BaseHypothesis):
    """
    Hypothesis 38: Economic-event restriction flattening
    """
    def __init__(self):
        super().__init__(38, "Economic-event restriction flattening")
        
    def evaluate(self, state: MarketState) -> float:
        if state.event_context == 'NEWS_RESTRICTION':
            flatten_score = state.f('news_restriction_flatten_score', 0.0)
            return float(np.tanh(flatten_score * 2.0))
        return 0.0

class QuotePullBeforeVolatility(BaseHypothesis):
    """
    Hypothesis 39: Quote pull before volatility
    """
    def __init__(self):
        super().__init__(39, "Quote pull before volatility")
        
    def evaluate(self, state: MarketState) -> float:
        if state.event_context == 'CPI_TIGHT':
            slope_change = state.f('book_slope_change', 0.0)
            return 0.0
        return 0.0

class RequoteRaceAfterShock(BaseHypothesis):
    """
    Hypothesis 40: Re-quote race after shock
    """
    def __init__(self):
        super().__init__(40, "Re-quote race after shock")
        
    def evaluate(self, state: MarketState) -> float:
        if state.regime_state == 'event_shock':
            slope_change = state.f('book_slope_change', 0.0)
            return float(np.tanh(slope_change * 3.0))
        return 0.0

class GhostRoute(BaseHypothesis):
    """
    Hypothesis 45: Ghost Route MBO queue-decay

    Research-only macro-to-micro futures lead/lag signal. The full event-time
    implementation lives in models/ghost_route; this lightweight hypothesis
    class lets the unified Workbench registry route the model honestly.
    """
    def __init__(self):
        super().__init__(45, "Ghost Route MBO queue-decay")

    def evaluate(self, state: MarketState) -> float:
        shadow_decay = state.f('macro_shadow_decay', 0.0)
        stale_quote = state.f('micro_stale_quote_zscore', 0.0)
        ofi = state.f('macro_nofi', 0.0)
        edge = state.f('expected_edge_ticks', 0.0)
        if edge <= 0:
            return 0.0
        return float(np.clip(np.tanh(shadow_decay) * np.tanh(stale_quote) * np.sign(ofi), -1.0, 1.0))
