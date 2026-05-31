"""Opening range breakout pattern labels."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from equities_lane.src.models import SessionTick
from equities_lane.src.types import PatternConfig, SessionMeta


@dataclass
class ORBLabel:
    pattern: str
    breakout_direction: str | None
    above_vwap: bool
    volume_spike: bool
    confidence: float
    orb_high: float
    orb_low: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "breakout_direction": self.breakout_direction,
            "above_vwap": self.above_vwap,
            "volume_spike": self.volume_spike,
            "confidence": self.confidence,
            "orb_high": self.orb_high,
            "orb_low": self.orb_low,
        }


def label_opening_range_breakout(
    ticks: list[SessionTick],
    meta: SessionMeta,
    patterns: PatternConfig,
) -> ORBLabel:
    if not ticks:
        return ORBLabel("orb", None, False, False, 0.0, 0.0, 0.0)

    window_n = max(1, patterns.orb_window_minutes * 10)
    opening = ticks[: min(window_n, len(ticks) // 3 or 1)]
    rest = ticks[len(opening) :]
    if not opening:
        opening = ticks[:1]
        rest = ticks[1:]

    prices = [_mid(t) for t in opening if _mid(t) > 0]
    orb_high = max(prices) if prices else 0.0
    orb_low = min(prices) if prices else 0.0
    vwap = _vwap(ticks)
    gap_up = meta.premarket_open >= meta.prior_close

    breakout_dir = None
    volume_spike = False
    confidence = 0.0
    for t in rest[:50]:
        mid = _mid(t)
        if mid <= 0:
            continue
        if mid > orb_high and gap_up:
            breakout_dir = "up"
            pm_vol = sum(x.trade_sz or 0 for x in opening) / max(len(opening), 1)
            cur_vol = t.trade_sz or 0
            volume_spike = cur_vol > 2 * pm_vol
            confidence = 0.7 if volume_spike else 0.4
            break
        if mid < orb_low and not gap_up:
            breakout_dir = "down"
            confidence = 0.3
            break

    last_mid = _mid(ticks[-1]) if ticks else 0.0
    above_vwap = last_mid > vwap if vwap > 0 else False
    return ORBLabel(
        pattern="orb",
        breakout_direction=breakout_dir,
        above_vwap=above_vwap,
        volume_spike=volume_spike,
        confidence=confidence,
        orb_high=orb_high,
        orb_low=orb_low,
    )


def _mid(t: SessionTick) -> float:
    if t.bid_px > 0 and t.ask_px > 0:
        return (t.bid_px + t.ask_px) / 2.0
    return t.trade_px or 0.0


def _vwap(ticks: list[SessionTick]) -> float:
    num = 0.0
    den = 0.0
    for t in ticks:
        if t.trade_px and t.trade_sz:
            num += t.trade_px * t.trade_sz
            den += t.trade_sz
    return num / den if den > 0 else 0.0
