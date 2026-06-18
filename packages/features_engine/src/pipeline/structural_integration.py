"""Structural model integrator — runs all 11 PDF models and fuses outputs into feature vector slots 50-63.

Traces to: algorithmic_trading_strategy_development.pdf (PDF_MODEL_1..7),
hft_framework_developer_prompt.pdf (PDF_MODEL_8..11), BLUEPRINT §2 (Model Agnosticism).
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

import numpy as np

from features_engine.src.features.feature_index import FEATURE_DIM
from features_engine.src.features.mbo_features import MBOEvent, OrderBook
from features_engine.src.structural_models.base import ModelOutput
from features_engine.src.structural_models.registry import get_structural_models
from features_engine.src.structural_models.types import (
    BookPressureOutput,
    CrossAssetLeadLagOutput,
    DealerHedgingOutput,
    DowYMIndexOutput,
    HawkesToxicOutput,
    HybridExecutionOutput,
    QuantumSpreadOutput,
    StochasticThermoOutput,
    TransferEntropyOutput,
    TreasuryCTDOutput,
    VPINToxicityOutput,
)

logger = logging.getLogger(__name__)

STRUCTURAL_FEATURE_START = 50
STRUCTURAL_FEATURE_COUNT = 14

ToxicityRegime = int


@dataclass
class StructuralSnapshot:
    book_pressure: Optional[BookPressureOutput] = None
    cross_asset: Optional[CrossAssetLeadLagOutput] = None
    vpin: Optional[VPINToxicityOutput] = None
    hybrid: Optional[HybridExecutionOutput] = None
    dealer: Optional[DealerHedgingOutput] = None
    dow_ym: Optional[DowYMIndexOutput] = None
    treasury: Optional[TreasuryCTDOutput] = None
    transfer_entropy: Optional[TransferEntropyOutput] = None
    quantum_spread: Optional[QuantumSpreadOutput] = None
    thermo: Optional[StochasticThermoOutput] = None
    hawkes: Optional[HawkesToxicOutput] = None

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for name in ("book_pressure", "cross_asset", "vpin", "hybrid", "dealer",
                      "dow_ym", "treasury", "transfer_entropy", "quantum_spread",
                      "thermo", "hawkes"):
            val = getattr(self, name, None)
            if val is not None:
                out[name] = val
        return out


class StructuralModelIntegrator:
    """Wraps all 11 PDF structural models, runs them on each MBO event,
    and fuses their outputs into feature vector slots 50-63.

    Does NOT modify the existing 0-49 FeatureIndex slots — structural outputs
    are in a separate band for C++ parity compatibility.
    """

    def __init__(self, tick_size: float = 0.25, *, target_asset: str = "MES", leader_asset: str = "ES"):
        self.models = get_structural_models()
        self.book = OrderBook()
        self.tick_size = tick_size
        self._target_asset = str(target_asset).split(".")[0].upper()
        self._leader_asset = str(leader_asset).split(".")[0].upper()
        self._book_pressure_by_asset: Dict[str, BookPressureOutput] = {}
        self._last_snapshot = StructuralSnapshot()
        self._initialized_for_book_pressure = False
        self._vpin_model = None
        self._book_pressure_model = None
        self._market_order_times: List[float] = []
        self._price_history: Deque[float] = deque(maxlen=50)
        for m in self.models:
            mid = getattr(m, "model_id", "")
            if mid == "VPIN_TOXICITY":
                self._vpin_model = m
            elif mid == "BOOK_PRESSURE":
                self._book_pressure_model = m

    def set_cross_asset_book_pressure(
        self,
        book_pressure_by_asset: Dict[str, BookPressureOutput],
        *,
        target_asset: Optional[str] = None,
        leader_asset: Optional[str] = None,
    ) -> None:
        """Inject per-symbol book pressure from multi-symbol replay (no placeholder OFI)."""
        self._book_pressure_by_asset = dict(book_pressure_by_asset)
        if target_asset is not None:
            self._target_asset = str(target_asset).split(".")[0].upper()
        if leader_asset is not None:
            self._leader_asset = str(leader_asset).split(".")[0].upper()

    def integrate(self, event: MBOEvent, feature_vector: np.ndarray) -> StructuralSnapshot:
        self.book.apply_event(event)
        ts = event.timestamp_ns
        bb = self.book.get_best_bid()
        ba = self.book.get_best_ask()
        mid = (bb + ba) / 2.0 if bb > 0 and ba < float("inf") else 0.0
        spread = ba - bb if bb > 0 and ba < float("inf") else 0.0

        if event.action == "TRADE":
            self._market_order_times.append(ts / 1e9)

        if mid > 0:
            self._price_history.append(mid)

        snapshot = StructuralSnapshot()

        for model in self.models:
            mid_attr = getattr(model, "model_id", "")
            try:
                if mid_attr == "BOOK_PRESSURE":
                    result = model.evaluate(book=self.book, timestamp_ns=ts)
                    snapshot.book_pressure = result.payload
                    self._book_pressure_by_asset[self._target_asset] = result.payload
                elif mid_attr == "CROSS_ASSET_LEAD_LAG":
                    book_by_asset = dict(self._book_pressure_by_asset)
                    target = self._target_asset
                    leader = self._leader_asset
                    if (
                        not book_by_asset
                        or leader not in book_by_asset
                        or target not in book_by_asset
                        or leader == target
                    ):
                        continue
                    own_ofi = book_by_asset[target].OFI_smooth
                    leader_ofi = book_by_asset[leader].OFI_smooth
                    result = model.evaluate(
                        own_ofi=own_ofi,
                        leader_ofi=leader_ofi,
                        book_pressure_by_asset=book_by_asset,
                        leader_asset=leader,
                        target_asset=target,
                        timestamp_ns=ts,
                    )
                    snapshot.cross_asset = result.payload
                elif mid_attr == "VPIN_TOXICITY":
                    if spread > 0 and mid > 0:
                        result = model.evaluate(mid=mid, volume=1.0, timestamp_ns=ts)
                        if result.payload.VPIN_value > 0:
                            snapshot.vpin = result.payload
                elif mid_attr == "HYBRID_EXECUTION":
                    ofi = snapshot.book_pressure.OFI_smooth if snapshot.book_pressure else 0.0
                    vpin_val = snapshot.vpin.VPIN_value if snapshot.vpin else 0.0
                    result = model.evaluate(
                        mid=mid, inventory=0.0, sigma=0.02,
                        ofi_smooth=ofi, vpin_value=vpin_val,
                        timestamp_ns=ts,
                    )
                    snapshot.hybrid = result.payload
                elif mid_attr == "DEALER_HEDGING":
                    if mid > 0:
                        result = model.evaluate(
                            spot=mid, chain=[], timestamp_ns=ts,
                        )
                        snapshot.dealer = result.payload
                elif mid_attr == "DOW_YM_INDEX":
                    result = model.evaluate(timestamp_ns=ts)
                    snapshot.dow_ym = result.payload
                elif mid_attr == "TREASURY_CTD":
                    result = model.evaluate(futures_price=0.0, bonds=[], timestamp_ns=ts)
                    snapshot.treasury = result.payload
                elif mid_attr == "TRANSFER_ENTROPY":
                    returns_list = list(self._price_history)
                    result = model.evaluate(
                        leader_asset="ES", target_asset="MES",
                        leader_returns=returns_list, target_returns=returns_list,
                        timestamp_ns=ts,
                    )
                    snapshot.transfer_entropy = result.payload
                elif mid_attr == "QUANTUM_SPREAD_DEFENSE":
                    spread_ticks = spread / max(self.tick_size, 1e-9)
                    result = model.evaluate(spread_ticks=spread_ticks, timestamp_ns=ts)
                    snapshot.quantum_spread = result.payload
                elif mid_attr == "STOCHASTIC_THERMO":
                    result = model.evaluate(timestamp_ns=ts)
                    snapshot.thermo = result.payload
                elif mid_attr == "HAWKES_TOXIC_FLOW":
                    mo_times = list(self._market_order_times)
                    result = model.evaluate(
                        t=ts / 1e9, market_order_times=mo_times, timestamp_ns=ts,
                    )
                    snapshot.hawkes = result.payload
            except Exception as exc:
                logger.warning("Structural model %s error: %s", mid_attr, exc)

        self._write_structural_features(feature_vector, snapshot)
        self._last_snapshot = snapshot
        return snapshot

    def _write_structural_features(self, vec: np.ndarray, snapshot: StructuralSnapshot) -> None:
        if len(vec) < STRUCTURAL_FEATURE_START + STRUCTURAL_FEATURE_COUNT:
            return
        s = STRUCTURAL_FEATURE_START

        bp = snapshot.book_pressure
        vec[s + 0] = bp.OFI_value if bp else 0.0
        vec[s + 1] = bp.OFI_zscore if bp else 0.0

        vp = snapshot.vpin
        vec[s + 2] = vp.VPIN_value if vp else 0.0

        hy = snapshot.hybrid
        vec[s + 3] = (hy.hybrid_reservation_price - 0.0) if hy else 0.0

        dh = snapshot.dealer
        vec[s + 4] = dh.dealer_hedging_pressure if dh else 0.0

        te = snapshot.transfer_entropy
        vec[s + 5] = float(te.aggressive_liquidity_signal) if te else 0.0

        qs = snapshot.quantum_spread
        vec[s + 6] = qs.collapse_risk if qs else 0.0

        st = snapshot.thermo
        vec[s + 7] = st.free_energy if st else 0.0

        hk = snapshot.hawkes
        vec[s + 8] = hk.toxic_cascade_score if hk else 0.0

        ca = snapshot.cross_asset
        vec[s + 9] = ca.cross_impact_score if ca else 0.0

        dy = snapshot.dow_ym
        vec[s + 10] = dy.synthetic_Dow_pressure if dy else 0.0

        tc = snapshot.treasury
        vec[s + 11] = tc.futures_basis_signal if tc else 0.0

        tr = 0.0
        if vp:
            regime_map = {"normal": 0.0, "elevated": 1.0, "toxic": 2.0}
            tr = regime_map.get(vp.toxicity_regime, 0.0)
        vec[s + 12] = tr

        active_mask = 0
        for i, name in enumerate((
            "book_pressure", "cross_asset", "vpin", "hybrid", "dealer",
            "dow_ym", "treasury", "transfer_entropy", "quantum_spread",
            "thermo", "hawkes",
        )):
            if getattr(snapshot, name, None) is not None:
                active_mask |= (1 << i)
        vec[s + 13] = float(active_mask)

    @property
    def last_snapshot(self) -> StructuralSnapshot:
        return self._last_snapshot
