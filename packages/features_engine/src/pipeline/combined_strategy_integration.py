"""Combined hypothesis strategy — evaluates all 44 HYP + 11 PDF models on each step,
aggregates signals via weighted voting, and produces OrderIntents.

Per AGENTS.md constraint: structural model outputs stay in slots 50-63, never merged
into FeatureIndex slots 0-49 without C++ parity review.
Per BLUEPRINT §7: CombinedHypothesisStrategy aggregates across all model families.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from features_engine.src.hypotheses.completions import get_all_hypotheses
from features_engine.src.hypotheses.modules import MarketState
from features_engine.src.pipeline.structural_integration import (
    StructuralModelIntegrator,
    StructuralSnapshot,
)
from features_engine.src.features.mbo_features import MBOEvent
from replay.replay_session import ReplayStepContext
from execution.interfaces import OrderIntent, CancelIntent, new_intent_id


@dataclass
class AggregatedSignal:
    signal: float = 0.0
    confidence: float = 0.0
    num_positive: int = 0
    num_negative: int = 0
    num_active: int = 0
    total_models: int = 0
    contributions: Dict[str, float] = field(default_factory=dict)


class CombinedHypothesisStrategy:
    """Unified strategy evaluating all 44 HYP models on each ReplayStepContext,
    aggregating signals, and producing OrderIntents.

    The 11 PDF structural models are evaluated separately via StructuralModelIntegrator
    and their outputs influence the aggregation weights (VPIN toxicity regime, etc.).
    """

    def __init__(
        self,
        latency_ms: float = 1.0,
        tick_size: float = 0.25,
        signal_threshold: float = 0.15,
        base_quantity: float = 1.0,
        max_position: float = 10.0,
        model_weights: Optional[Dict[str, float]] = None,
    ):
        self.latency_ms = latency_ms
        self.tick_size = tick_size
        self.signal_threshold = signal_threshold
        self.base_quantity = base_quantity
        self.max_position = max_position
        self.hypotheses = get_all_hypotheses()
        self.structural_integrator = StructuralModelIntegrator(tick_size=tick_size)
        self.model_weights: Dict[str, float] = model_weights or {}
        self._processed_events = 0
        self._last_mbo_events: List[MBOEvent] = []

    def ingest_mbo_event(self, event: MBOEvent, feature_vector: np.ndarray) -> None:
        """Feed an MBO event to keep structural models updated.
        Must be called before on_step if structural model outputs are desired.
        """
        self._last_mbo_events.append(event)
        if len(self._last_mbo_events) > 1000:
            self._last_mbo_events = self._last_mbo_events[-100:]
        self.structural_integrator.integrate(event, feature_vector)

    def evaluate_hypotheses(self, state: MarketState) -> AggregatedSignal:
        signal_total = 0.0
        weight_total = 0.0
        num_pos = 0
        num_neg = 0
        active = 0
        contributions: Dict[str, float] = {}

        for hyp in self.hypotheses:
            slug = f"HYP_{hyp.hyp_id}"
            try:
                sig = hyp.evaluate(state)
            except Exception:
                continue
            if abs(sig) < 1e-9:
                continue
            weight = self.model_weights.get(slug, 1.0)
            signal_total += sig * weight
            weight_total += weight
            active += 1
            if sig > 0:
                num_pos += 1
            else:
                num_neg += 1
            contributions[slug] = float(sig)

        if weight_total > 0:
            signal_total /= weight_total

        confidence = min(1.0, active / max(len(self.hypotheses), 1) * 2.0)

        return AggregatedSignal(
            signal=float(signal_total),
            confidence=confidence,
            num_positive=num_pos,
            num_negative=num_neg,
            num_active=active,
            total_models=len(self.hypotheses),
            contributions=contributions,
        )

    def should_scale_down(self, snapshot: StructuralSnapshot) -> float:
        scale = 1.0
        if snapshot.hybrid and hasattr(snapshot.hybrid, "cancel_quote_flag"):
            if snapshot.hybrid.cancel_quote_flag:
                scale *= 0.5
        if snapshot.hawkes and snapshot.hawkes.toxic_flow_detected:
            scale *= 0.3
        if snapshot.vpin and snapshot.vpin.toxicity_regime == "toxic":
            scale *= 0.25
        if snapshot.quantum_spread and snapshot.quantum_spread.collapse_risk > 0.65:
            scale *= 0.5
        return scale

    def on_step(self, ctx: ReplayStepContext) -> List[OrderIntent | CancelIntent]:
        self._processed_events += 1

        market_state = ctx.market_state
        if hasattr(market_state, "feature_vector") and market_state.feature_vector is not None:
            vec = market_state.feature_vector
        else:
            return []

        snapshot = self.structural_integrator.last_snapshot

        agg = self.evaluate_hypotheses(market_state)

        scale = self.should_scale_down(snapshot)
        net_signal = agg.signal * scale

        if abs(net_signal) < self.signal_threshold:
            return []

        current_position = float(ctx.position) if hasattr(ctx, "position") else 0.0
        side = "BUY" if net_signal > 0 else "SELL"
        direction = 1.0 if net_signal > 0 else -1.0
        qty = max(1, int(self.base_quantity * abs(net_signal) * (1.0 - abs(current_position) / self.max_position)))

        if qty <= 0:
            return []

        price = ctx.best_bid if net_signal > 0 else ctx.best_ask

        intent = OrderIntent(
            intent_id=new_intent_id(),
            run_id=ctx.run_id,
            timestamp_ns=ctx.clock.now_ns,
            strategy_id="combined_hypothesis",
            model_id="COMBINED",
            symbol=ctx.symbol,
            side=side,
            order_type="LIMIT",
            price=price,
            quantity=float(qty),
            time_in_force="GTC",
            latency_budget_ms=self.latency_ms,
            regime_state=getattr(market_state, "regime_state", "NORMAL"),
            event_context=getattr(market_state, "event_context", "NORMAL"),
            risk_metadata={
                "confidence": agg.confidence,
                "scale": scale,
                "active_models": agg.num_active,
                "net_signal": net_signal,
            },
        )
        return [intent]

    @property
    def total_processed_events(self) -> int:
        return self._processed_events
