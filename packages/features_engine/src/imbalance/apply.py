"""Merge imbalance into MarketState; ablation hypothesis wrapper."""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

from features_engine.src.hypotheses.modules import BaseHypothesis, MarketState
from features_engine.src.imbalance.ablation import (
    ImbalanceAblationMode,
    ImbalanceFamily,
    family_enabled,
)


BOOK_FEATURE_KEYS = (
    "bid_size_l1",
    "ask_size_l1",
    "bid_size_l1_l3",
    "ask_size_l1_l3",
    "bid_size_l1_l5",
    "ask_size_l1_l5",
    "bid_size_l1_l10",
    "ask_size_l1_l10",
    "book_imbalance_l1",
    "book_imbalance_l3",
    "book_imbalance_l5",
    "book_imbalance_l10",
    "spread",
    "mid_price",
    "microprice",
)


def mask_imbalance_catalog_slots(
    vec,
    mode: Optional[ImbalanceAblationMode],
) -> None:
    """Zero catalog imbalance slots when ablation mode disables a family."""
    if mode is None:
        return
    from features_engine.src.features.feature_index import FeatureIndex

    if not family_enabled(mode, ImbalanceFamily.ORDER_FLOW):
        vec[FeatureIndex.MAX_CONTRACT_TRADE_IMBALANCE] = 0.0
    if not family_enabled(mode, ImbalanceFamily.BOOK):
        vec[FeatureIndex.BOOK_IMBALANCE_L1] = 0.0
        vec[FeatureIndex.BOOK_IMBALANCE_L10] = 0.0
        vec[FeatureIndex.MICROPRICE] = 0.0


def apply_imbalance_to_vector(
    vec,
    imbalance_snap: Optional[Dict[str, Any]],
    mode: Optional[ImbalanceAblationMode] = None,
) -> None:
    """Write catalog book slots on the 64-dim vector (Python hot path)."""
    import numpy as np

    from features_engine.src.features.feature_index import FeatureIndex

    if not imbalance_snap:
        return
    if mode is not None and not family_enabled(mode, ImbalanceFamily.BOOK):
        return
    book = imbalance_snap.get("book") or {}
    for key, idx in (
        ("book_imbalance_l1", FeatureIndex.BOOK_IMBALANCE_L1),
        ("book_imbalance_l10", FeatureIndex.BOOK_IMBALANCE_L10),
        ("microprice", FeatureIndex.MICROPRICE),
    ):
        val = book.get(key)
        if val is not None and not (isinstance(val, float) and np.isnan(val)):
            vec[idx] = float(val)


def merge_imbalance_features(
    feat_dict: Dict[str, float],
    imbalance_snap: Optional[Dict[str, Any]],
    mode: Optional[ImbalanceAblationMode] = None,
) -> None:
    if not imbalance_snap:
        return
    book = imbalance_snap.get("book") or {}
    if mode is None or family_enabled(mode, ImbalanceFamily.BOOK):
        for key in BOOK_FEATURE_KEYS:
            val = book.get(key)
            if val is None:
                continue
            if isinstance(val, float) and math.isnan(val):
                continue
            feat_dict[key] = float(val)
    if mode is not None and not family_enabled(mode, ImbalanceFamily.ORDER_FLOW):
        return
    of = imbalance_snap.get("order_flow") or {}
    if of.get("ofi_l1") is not None and not (
        isinstance(of["ofi_l1"], float) and math.isnan(of["ofi_l1"])
    ):
        feat_dict["ofi_l1"] = float(of["ofi_l1"])
    if of.get("signed_trade_pressure") is not None:
        feat_dict["signed_trade_pressure"] = float(of["signed_trade_pressure"])


def imbalance_signal_from_vector(
    state: MarketState,
    mode: ImbalanceAblationMode,
) -> float:
    """Catalog slots 34–37 (masked per ablation mode on the replay path)."""
    if state.feature_vector is None:
        return 0.0
    from features_engine.src.features.feature_index import FeatureIndex

    vec = state.feature_vector
    boost = 0.0
    if family_enabled(mode, ImbalanceFamily.BOOK):
        boost += 0.12 * float(vec[FeatureIndex.BOOK_IMBALANCE_L1])
        boost += 0.06 * float(vec[FeatureIndex.BOOK_IMBALANCE_L10])
        mid = float(vec[FeatureIndex.MID_PRICE]) if FeatureIndex.MID_PRICE < len(vec) else 0.0
        micro = float(vec[FeatureIndex.MICROPRICE])
        if mid > 0 and not math.isnan(micro):
            boost += 0.04 * ((micro - mid) / mid)
    if family_enabled(mode, ImbalanceFamily.ORDER_FLOW):
        boost += 0.08 * float(vec[FeatureIndex.MAX_CONTRACT_TRADE_IMBALANCE])
    return boost


def imbalance_signal_boost(
    state: MarketState,
    mode: ImbalanceAblationMode,
) -> float:
    """Vector catalog slots + auction snapshot (auction has no vec slot)."""
    boost = imbalance_signal_from_vector(state, mode)
    if family_enabled(mode, ImbalanceFamily.AUCTION):
        auc = (state.imbalance_snapshot or {}).get("auction") or {}
        score = auc.get("auction_pressure_score")
        if score is not None and not (isinstance(score, float) and math.isnan(score)):
            boost += 0.10 * float(score)
    return boost


def wrap_hypothesis_for_ablation(
    hypothesis: BaseHypothesis,
    mode: ImbalanceAblationMode,
) -> BaseHypothesis:
    if not mode.active_families:
        return hypothesis
    inner = hypothesis

    class _ImbalanceAblationHypothesis:
        hyp_id = inner.hyp_id

        def evaluate(self, state: MarketState) -> float:
            base = float(inner.evaluate(state))
            return base + imbalance_signal_boost(state, mode)

    return _ImbalanceAblationHypothesis()  # type: ignore[return-value]
