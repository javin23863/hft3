"""Consolidation pattern labels (bull flag / flat top)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from equities_lane.src.models import SessionTick
from equities_lane.src.types import PatternConfig


@dataclass
class ConsolidationLabel:
    pattern: str
    label: str
    volume_declining: bool
    ascending_lows: bool
    flat_top: bool
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "label": self.label,
            "volume_declining": self.volume_declining,
            "ascending_lows": self.ascending_lows,
            "flat_top": self.flat_top,
            "confidence": self.confidence,
        }


def label_consolidation(
    ticks: list[SessionTick],
    patterns: PatternConfig,
) -> ConsolidationLabel:
    """Labels from prefix ticks only — caller must pass ticks[:i+1] at decision time t."""
    if len(ticks) < 20:
        return ConsolidationLabel("consolidation", "insufficient_data", False, False, False, 0.0)

    mids = np.array([_mid(t) for t in ticks])
    vols = np.array([float(t.trade_sz or t.bid_sz + t.ask_sz) for t in ticks])
    third = len(mids) // 3
    impulse = mids[:third]
    pull = mids[third : 2 * third]
    cont = mids[2 * third :]

    ascending_lows = len(pull) > 2 and float(np.min(pull[-3:])) > float(np.min(pull[:3]))
    flat_top = len(cont) > 2 and float(np.std(cont[-5:])) < patterns.consolidation_pullback_pct / 100.0
    vol_impulse = float(np.mean(vols[:third])) if third else 0.0
    vol_pull = float(np.mean(vols[third : 2 * third])) if third else 0.0
    volume_declining = vol_pull < vol_impulse * 0.8 if vol_impulse > 0 else False

    label = "bull_flag" if ascending_lows and volume_declining else "unknown"
    if flat_top and ascending_lows:
        label = "flat_top_breakout_setup"
    confidence = 0.6 if label != "unknown" else 0.2

    return ConsolidationLabel(
        pattern="consolidation",
        label=label,
        volume_declining=volume_declining,
        ascending_lows=ascending_lows,
        flat_top=flat_top,
        confidence=confidence,
    )


def _mid(t: SessionTick) -> float:
    if t.bid_px > 0 and t.ask_px > 0:
        return (t.bid_px + t.ask_px) / 2.0
    return t.trade_px or 0.0
