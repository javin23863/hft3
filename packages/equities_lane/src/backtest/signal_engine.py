"""Confluence signal engine — thresholds from config only."""
from __future__ import annotations

from equities_lane.src.features.book_adapter import FeatureSnapshot
from equities_lane.src.patterns.consolidation import ConsolidationLabel
from equities_lane.src.patterns.opening_range_breakout import ORBLabel
from equities_lane.src.types import SignalConfig


def entry_signal(
    feat: FeatureSnapshot,
    orb: ORBLabel,
    consolidation: ConsolidationLabel,
    signals: SignalConfig,
) -> bool:
    if orb.breakout_direction != "up":
        return False
    if feat.mlofi_pc1 < signals.min_mlofi_pc1:
        return False
    if feat.hmm_markup_prob < signals.min_hmm_markup_prob:
        return False
    if feat.hawkes_score < signals.min_hawkes_score:
        return False
    if consolidation.label == "unknown" and orb.confidence < 0.5:
        return False
    return True


def exit_signal(feat: FeatureSnapshot, signals: SignalConfig) -> bool:
    if feat.vpin_percentile >= signals.max_vpin_exit_percentile:
        return True
    if feat.hmm_state in ("distribution", "liquidation"):
        return True
    if feat.mlofi_pc1 < -abs(signals.min_mlofi_pc1):
        return True
    l3 = feat.l3 or {}
    cd = l3.get("cancellation_delta")
    if cd is not None and cd > 5.0:
        return True
    return False
