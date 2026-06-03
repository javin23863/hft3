from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from ..types import HazardEstimate, ModelConfig, PayoffEstimate, RiskEstimate, TimingPolicy
from .features import L3Features
from .heads import L3PredictionHeads, L3PredictionModel
from .instability import InstabilityScore, MicrostructureInstabilityScorer
from .intensity import HawkesProcessIntensity
from .snapshots import L3Snapshot, L3SnapshotBuilder, L3SnapshotType


@dataclass
class L3EnhancedPrediction:
    base_hazard: HazardEstimate
    l3_heads: L3PredictionHeads
    instability: InstabilityScore
    enhanced_hazard: HazardEstimate
    l3_timing_recommendation: str
    l3_reject_reasons: list[str]
    incremental_alpha: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_hazard": self.base_hazard.to_dict(),
            "l3_heads": self.l3_heads.to_dict(),
            "instability": self.instability.to_dict(),
            "enhanced_hazard": self.enhanced_hazard.to_dict(),
            "l3_timing_recommendation": self.l3_timing_recommendation,
            "l3_reject_reasons": self.l3_reject_reasons,
            "incremental_alpha": self.incremental_alpha,
        }


class L3IntegrationLayer:
    def __init__(self, config: ModelConfig):
        self._config = config
        self._instability_scorer = MicrostructureInstabilityScorer()
        self._prediction_model = L3PredictionModel()
        self._hawkes = HawkesProcessIntensity()

    def integrate_l3_with_hazard(
        self,
        base_hazard: HazardEstimate,
        l3_features: L3Features,
        snapshot: L3Snapshot | None = None,
    ) -> L3EnhancedPrediction:
        instability = self._instability_scorer.compute(l3_features)
        l3_heads = self._prediction_model.predict(l3_features, instability)

        enhanced_hazard = self._enhance_hazard(base_hazard, instability, l3_heads)

        timing_rec = self._recommend_timing(enhanced_hazard, l3_heads, instability)
        reject_reasons = self._check_reject_conditions(l3_heads, instability, l3_features)

        incremental_alpha = self._compute_incremental_alpha(
            base_hazard, enhanced_hazard, l3_heads
        )

        return L3EnhancedPrediction(
            base_hazard=base_hazard,
            l3_heads=l3_heads,
            instability=instability,
            enhanced_hazard=enhanced_hazard,
            l3_timing_recommendation=timing_rec,
            l3_reject_reasons=reject_reasons,
            incremental_alpha=incremental_alpha,
        )

    def _enhance_hazard(
        self,
        base: HazardEstimate,
        instability: InstabilityScore,
        l3_heads: L3PredictionHeads,
    ) -> HazardEstimate:
        l3_boost = instability.score * 0.3

        enhanced_p1d = min(base.p_run_1d + l3_boost * l3_heads.p_micro_ignite_next_30s, 1.0)
        enhanced_p2d = min(base.p_run_2d + l3_boost * 0.8, 1.0)
        enhanced_p5d = min(base.p_run_5d + l3_boost * 0.6, 1.0)

        enhanced_ah = min(
            base.p_afterhours_ignite + l3_heads.p_micro_ignite_next_5m * 0.5,
            1.0,
        )
        enhanced_pm = min(
            base.p_premarket_ignite + l3_heads.p_micro_ignite_next_5m * 0.7,
            1.0,
        )
        enhanced_day = min(
            base.p_intraday_continuation + l3_heads.p_sweep_continuation * 0.4,
            1.0,
        )

        return HazardEstimate(
            p_run_5d=enhanced_p5d,
            p_run_2d=enhanced_p2d,
            p_run_1d=enhanced_p1d,
            p_afterhours_ignite=enhanced_ah,
            p_premarket_ignite=enhanced_pm,
            p_intraday_continuation=enhanced_day,
        )

    def _recommend_timing(
        self,
        hazard: HazardEstimate,
        l3_heads: L3PredictionHeads,
        instability: InstabilityScore,
    ) -> str:
        if l3_heads.p_sweep_failure > 0.6:
            return "REJECT_SWEEP_FAILURE"

        if l3_heads.expected_adverse_selection > 0.05:
            return "REJECT_ADVERSE_SELECTION"

        if l3_heads.expected_queue_fill_probability < 0.2:
            return "REJECT_TOO_ILLIQUID"

        if l3_heads.p_micro_ignite_next_1s > 0.3:
            return "ENTER_IMMEDIATE"

        if l3_heads.p_micro_ignite_next_30s > 0.4:
            return "ENTER_OPEN_CONFIRMATION"

        if l3_heads.p_micro_ignite_next_5m > 0.5:
            return "ENTER_PREMARKET"

        if instability.score > 0.7:
            return "ENTER_INTRADAY_CONTINUATION"

        return "WATCH"

    def _check_reject_conditions(
        self,
        l3_heads: L3PredictionHeads,
        instability: InstabilityScore,
        features: L3Features,
    ) -> list[str]:
        reasons = []

        if l3_heads.p_sweep_failure > 0.7:
            reasons.append("HIGH_SWEEP_FAILURE_RISK")

        if l3_heads.expected_adverse_selection > 0.08:
            reasons.append("HIGH_ADVERSE_SELECTION")

        if l3_heads.expected_queue_fill_probability < 0.15:
            reasons.append("QUEUE_FILL_UNLIKELY")

        if features.locked_or_crossed_flag > 0.5:
            reasons.append("LOCKED_OR_CROSSED_MARKET")

        if features.total_ask_depth_1 < 100:
            reasons.append("INSUFFICIENT_LIQUIDITY")

        if l3_heads.p_bid_support_collapse > 0.6:
            reasons.append("BID_SUPPORT_COLLAPSE_RISK")

        return reasons

    def _compute_incremental_alpha(
        self,
        base: HazardEstimate,
        enhanced: HazardEstimate,
        l3_heads: L3PredictionHeads,
    ) -> float:
        base_eu = base.p_run_5d * 0.3 - (1 - base.p_run_5d) * 0.08
        enhanced_eu = enhanced.p_run_5d * 0.3 - (1 - enhanced.p_run_5d) * 0.08

        l3_adj = l3_heads.expected_micro_mfe - l3_heads.expected_micro_slippage

        return (enhanced_eu - base_eu) + l3_adj

    def process_event_stream(
        self,
        symbol: str,
        events: list[Any],
        base_hazard: HazardEstimate,
    ) -> L3EnhancedPrediction | None:
        from .event_types import MBOEvent

        builder = L3SnapshotBuilder(symbol)
        for event in events:
            if isinstance(event, MBOEvent):
                builder.add_event(event)
                self._hawkes.add_event(event)

        snapshot = builder.build_wall_clock_snapshot(
            window_ns=1_000_000_000,
            snapshot_type=L3SnapshotType.EVENT_WINDOW,
        )

        if not snapshot:
            return None

        return self.integrate_l3_with_hazard(base_hazard, snapshot.features, snapshot)
