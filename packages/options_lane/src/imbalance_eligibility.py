"""Quote quality gate before option contract-level imbalance."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OptionQuote:
    bid_px: float
    ask_px: float
    bid_sz: float
    ask_sz: float
    ts_ns: int = 0


@dataclass
class EligibilityConfig:
    max_spread_bps: float = 500.0
    min_bid_sz: float = 1.0
    min_ask_sz: float = 1.0
    max_stale_ms: int = 5000


def option_imbalance_eligible(quote: OptionQuote, cfg: EligibilityConfig | None = None) -> tuple[bool, str]:
    cfg = cfg or EligibilityConfig()
    if quote.bid_px <= 0 or quote.ask_px <= 0:
        return False, "missing_quote"
    mid = (quote.bid_px + quote.ask_px) / 2.0
    spread_bps = (quote.ask_px - quote.bid_px) / mid * 10_000.0
    if spread_bps > cfg.max_spread_bps:
        return False, f"spread_bps={spread_bps:.1f}"
    if quote.bid_sz < cfg.min_bid_sz or quote.ask_sz < cfg.min_ask_sz:
        return False, "insufficient_size"
    return True, "ok"
