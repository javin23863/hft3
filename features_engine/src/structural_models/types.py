"""Typed outputs for PDF structural models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class BookPressureOutput:
    OFI_value: float = 0.0
    OFI_zscore: float = 0.0
    OFI_smooth: float = 0.0
    MLOFI_vector: List[float] = field(default_factory=list)
    MLOFI_PC1: float = 0.0
    book_pressure_direction: int = 0
    spoofing_risk_flag: bool = False


@dataclass
class CrossAssetLeadLagOutput:
    leader_asset: str = ""
    target_asset: str = ""
    cross_impact_score: float = 0.0
    predicted_target_return: float = 0.0
    lead_lag_stability: float = 0.0
    signal_decay_curve: List[float] = field(default_factory=list)


@dataclass
class VPINToxicityOutput:
    VPIN_value: float = 0.0
    VPIN_percentile: float = 0.0
    toxicity_regime: str = "normal"
    toxic_flow_alert: bool = False
    volatility_warning: bool = False


@dataclass
class HybridExecutionOutput:
    reservation_price: float = 0.0
    hybrid_reservation_price: float = 0.0
    optimal_bid: float = 0.0
    optimal_ask: float = 0.0
    spread_width: float = 0.0
    inventory_penalty: float = 0.0
    OFI_drift_component: float = 0.0
    VPIN_multiplier: float = 1.0
    cancel_quote_flag: bool = False
    passive_to_aggressive_flag: bool = False


@dataclass
class DealerHedgingOutput:
    total_gex: float = 0.0
    gex_by_strike: Dict[float, float] = field(default_factory=dict)
    vanna_exposure: float = 0.0
    charm_exposure: float = 0.0
    zero_gamma_level: Optional[float] = None
    dealer_hedging_pressure: float = 0.0


@dataclass
class DowYMIndexOutput:
    component_price_weight: Dict[str, float] = field(default_factory=dict)
    top_component_OFI: Dict[str, float] = field(default_factory=dict)
    synthetic_Dow_pressure: float = 0.0
    YM_fair_pressure: float = 0.0
    constituent_to_YM_signal: float = 0.0


@dataclass
class TreasuryCTDOutput:
    current_CTD: str = ""
    delivery_cost_by_bond: Dict[str, float] = field(default_factory=dict)
    implied_repo_by_bond: Dict[str, float] = field(default_factory=dict)
    CTD_switch_threshold: float = 0.0
    quality_option_pressure: float = 0.0
    futures_basis_signal: float = 0.0
