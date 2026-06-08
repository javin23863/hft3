"""Load OPRA options chain NDJSON and build per-tick options state.

Schema (per line):
  {session_id, underlying, quote_ts_ns, symbol, strike, right, expiry, bid, ask}

The data is 1-minute bars (cbbo-1m). For each equity tick, we look up
the most recent options bar (1-min lookback window) and compute:
  - mid_iv, bid_iv, ask_iv  (per-strike Black-Scholes IV)
  - delta, gamma           (per-strike BS greeks)
  - GEX, DEX               (aggregate gamma/delta exposure in $)
  - skew_25delta           (25-delta put IV minus 25-delta call IV)
  - call_wall, put_wall    (strikes with max OI/GEX on each side)
  - iv_term_atm            (ATM front-month IV)

Quarantine: OPRA data lives under ``data/options/equity_chains/normalized/``.
No writes to ``data/npz/`` or ``data/equities/``.
"""
from __future__ import annotations

import bisect
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

from equities_lane.src.l3_policy import L3OnlyViolation

_REPO = Path(__file__).resolve().parents[3]


def _options_ndjson_path(session_id: str) -> Path:
    return _REPO / "data" / "options" / "equity_chains" / "normalized" / f"{session_id}.ndjson"


@dataclass
class OptionQuote:
    ts_ns: int
    strike: float
    right: str  # 'C' or 'P'
    expiry: str  # YYYY-MM-DD
    bid: float
    ask: float
    mid: float = 0.0
    spread: float = 0.0

    def __post_init__(self) -> None:
        self.mid = (self.bid + self.ask) / 2.0 if (self.bid > 0 and self.ask > 0) else max(self.bid, self.ask)
        self.spread = self.ask - self.bid if (self.bid > 0 and self.ask > 0) else 0.0


@dataclass
class OptionsChainSnapshot:
    ts_ns: int
    underlying: str
    spot: float
    quotes: list[OptionQuote] = field(default_factory=list)
    iv_atm: float = 0.0
    iv_skew_25d: float = 0.0
    iv_term_atm: float = 0.0
    gex_net: float = 0.0  # Net gamma exposure ($ per 1% move)
    dex_net: float = 0.0  # Net delta exposure ($ per 1% move)
    call_wall_strike: float = 0.0  # Strike with max positive GEX on call side
    put_wall_strike: float = 0.0   # Strike with max negative GEX on put side
    pc_ratio_volume: float = 0.0
    num_quotes: int = 0
    coverage: float = 0.0  # fraction of strikes with valid quotes

    def to_dict(self) -> dict:
        return {
            "ts_ns": self.ts_ns,
            "spot": self.spot,
            "iv_atm": self.iv_atm,
            "iv_skew_25d": self.iv_skew_25d,
            "iv_term_atm": self.iv_term_atm,
            "gex_net": self.gex_net,
            "dex_net": self.dex_net,
            "call_wall_strike": self.call_wall_strike,
            "put_wall_strike": self.put_wall_strike,
            "pc_ratio_volume": self.pc_ratio_volume,
            "num_quotes": self.num_quotes,
            "coverage": self.coverage,
        }


class OptionsChainLoader:
    """Load OPRA NDJSON once into a time-sorted structure for fast lookup."""

    def __init__(self, ndjson_path: Path, underlying: str) -> None:
        self.path = ndjson_path
        self.underlying = underlying
        self._ts: list[int] = []
        self._bars: dict[int, list[OptionQuote]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(f"Options NDJSON not found: {self.path}")
        bars: dict[int, list[OptionQuote]] = {}
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                strike = r.get("strike")
                right = r.get("right")
                expiry = r.get("expiry")
                bid = r.get("bid", 0.0) or 0.0
                ask = r.get("ask", 0.0) or 0.0
                ts_ns = int(r.get("quote_ts_ns", 0))
                if ts_ns <= 0 or strike is None or right is None or expiry is None:
                    continue
                if bid <= 0 and ask <= 0:
                    continue
                q = OptionQuote(ts_ns=ts_ns, strike=float(strike), right=str(right), expiry=str(expiry), bid=float(bid), ask=float(ask))
                bars.setdefault(ts_ns, []).append(q)
        self._bars = bars
        self._ts = sorted(bars.keys())

    @property
    def num_bars(self) -> int:
        return len(self._ts)

    def lookup(self, ts_ns: int, max_lag_ns: int = 120 * 1_000_000_000) -> list[OptionQuote] | None:
        """Return the most recent bar with ts <= ts_ns and within max_lag_ns."""
        if not self._ts:
            return None
        idx = bisect.bisect_right(self._ts, ts_ns) - 1
        if idx < 0:
            return None
        if ts_ns - self._ts[idx] > max_lag_ns:
            return None
        return self._bars[self._ts[idx]]

    def to_snapshot(self, ts_ns: int, spot: float, *, max_lag_ns: int = 120 * 1_000_000_000) -> OptionsChainSnapshot:
        quotes = self.lookup(ts_ns, max_lag_ns=max_lag_ns)
        snap = OptionsChainSnapshot(ts_ns=ts_ns, underlying=self.underlying, spot=spot)
        if not quotes:
            return snap
        snap.num_quotes = len(quotes)
        snap.coverage = _coverage_ratio(quotes)
        snap.iv_atm = _atm_iv(quotes, spot)
        snap.iv_skew_25d = _skew_25d(quotes, spot, snap.iv_atm)
        snap.iv_term_atm = _term_atm_iv(quotes, spot)
        snap.gex_net, snap.dex_net, snap.call_wall_strike, snap.put_wall_strike = _exposures(quotes, spot)
        snap.pc_ratio_volume = _pc_ratio(quotes)
        snap.quotes = quotes
        return snap


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _coverage_ratio(quotes: list[OptionQuote]) -> float:
    """Fraction of strikes with both a C and a P at the same strike/expiry."""
    if not quotes:
        return 0.0
    by_strike_expiry_right: dict[tuple, int] = {}
    for q in quotes:
        k = (q.strike, q.expiry, q.right)
        by_strike_expiry_right[k] = by_strike_expiry_right.get(k, 0) + 1
    return min(1.0, len(by_strike_expiry_right) / max(1, len(quotes) / 2.0))


def _nearest_expiry(quotes: list[OptionQuote]) -> str | None:
    """Front-month (nearest listed expiry)."""
    expiries = sorted({q.expiry for q in quotes if q.expiry})
    return expiries[0] if expiries else None


def _atm_strike(quotes: list[OptionQuote], spot: float) -> float:
    """ATM strike = closest to spot within the front month."""
    front = _nearest_expiry(quotes)
    if not front:
        return 0.0
    strikes = sorted({q.strike for q in quotes if q.expiry == front})
    if not strikes:
        return 0.0
    return min(strikes, key=lambda k: abs(k - spot))


def _atm_iv(quotes: list[OptionQuote], spot: float) -> float:
    """ATM IV: average of front-month ATM call and put mid implied vol."""
    front = _nearest_expiry(quotes)
    if not front:
        return 0.0
    atm = _atm_strike(quotes, spot)
    if atm <= 0:
        return 0.0
    mids: list[float] = []
    for q in quotes:
        if q.expiry == front and q.strike == atm and q.mid > 0:
            iv = _bs_implied_vol(q.mid, spot, q.strike, q.expiry, q.right)
            if iv > 0:
                mids.append(iv)
    return sum(mids) / len(mids) if mids else 0.0


def _term_atm_iv(quotes: list[OptionQuote], spot: float) -> float:
    """Average IV across all listed expiries' ATM strikes (term-structure proxy)."""
    expiries = sorted({q.expiry for q in quotes if q.expiry})
    if not expiries:
        return 0.0
    ivs: list[float] = []
    for exp in expiries:
        strikes = sorted({q.strike for q in quotes if q.expiry == exp})
        if not strikes:
            continue
        atm = min(strikes, key=lambda k: abs(k - spot))
        for q in quotes:
            if q.expiry == exp and q.strike == atm and q.mid > 0:
                iv = _bs_implied_vol(q.mid, spot, q.strike, q.expiry, q.right)
                if iv > 0:
                    ivs.append(iv)
    return sum(ivs) / len(ivs) if ivs else 0.0


def _skew_25d(quotes: list[OptionQuote], spot: float, atm_iv: float) -> float:
    """25-delta put IV minus 25-delta call IV (risk-reversal)."""
    if atm_iv <= 0 or spot <= 0:
        return 0.0
    target_delta = 0.25
    front = _nearest_expiry(quotes)
    if not front:
        return 0.0
    put_iv = 0.0
    call_iv = 0.0
    for q in quotes:
        if q.expiry != front or q.mid <= 0:
            continue
        delta = _bs_delta(spot, q.strike, atm_iv, q.expiry, q.right)
        if q.right == "P" and abs(abs(delta) - target_delta) < 0.10 and abs(delta) > target_delta - 0.02:
            iv = _bs_implied_vol(q.mid, spot, q.strike, q.expiry, q.right)
            if iv > 0:
                put_iv = max(put_iv, iv)
        elif q.right == "C" and abs(delta - target_delta) < 0.10 and delta > target_delta - 0.02:
            iv = _bs_implied_vol(q.mid, spot, q.strike, q.expiry, q.right)
            if iv > 0:
                call_iv = max(call_iv, iv)
    return put_iv - call_iv


def _exposures(quotes: list[OptionQuote], spot: float) -> tuple[float, float, float, float]:
    """Net GEX and DEX, plus call_wall and put_wall strikes (in $ gamma/delta).

    Heuristic: 1 contract = 100 shares, OI assumed = max(bid,ask)*1000
    (volume proxy since raw OI is not in cbbo-1m). GEX/DEX are
    sums of (gamma * OI * spot^2 * 0.01) and (delta * OI * spot * 100).
    """
    if spot <= 0:
        return 0.0, 0.0, 0.0, 0.0
    gex_by_strike: dict[float, float] = {}
    dex_by_strike: dict[float, float] = {}
    front = _nearest_expiry(quotes)
    if not front:
        return 0.0, 0.0, 0.0, 0.0
    for q in quotes:
        if q.expiry != front or q.mid <= 0:
            continue
        iv = _bs_implied_vol(q.mid, spot, q.strike, q.expiry, q.right)
        if iv <= 0:
            continue
        d = _bs_delta(spot, q.strike, iv, q.expiry, q.right)
        g = _bs_gamma(spot, q.strike, iv, q.expiry)
        oi = _oi_proxy(q)
        sign = 1.0 if q.right == "C" else -1.0
        gex = sign * g * oi * spot * spot * 0.01
        dex = sign * d * oi * spot * 100.0
        gex_by_strike[q.strike] = gex_by_strike.get(q.strike, 0.0) + gex
        dex_by_strike[q.strike] = dex_by_strike.get(q.strike, 0.0) + dex
    gex_net = sum(gex_by_strike.values())
    dex_net = sum(dex_by_strike.values())
    call_wall = max(gex_by_strike, key=lambda k: gex_by_strike[k], default=0.0)
    put_wall = min(gex_by_strike, key=lambda k: gex_by_strike[k], default=0.0)
    if gex_by_strike.get(call_wall, 0.0) <= 0:
        call_wall = 0.0
    if gex_by_strike.get(put_wall, 0.0) >= 0:
        put_wall = 0.0
    return gex_net, dex_net, call_wall, put_wall


def _oi_proxy(q: OptionQuote) -> float:
    """OI proxy = max(bid, ask) * 1000 (vol proxy since OI is not in cbbo-1m)."""
    return max(q.bid, q.ask) * 1000.0


def _pc_ratio(quotes: list[OptionQuote]) -> float:
    """Put/Call ratio by quote count (proxy for OI)."""
    calls = sum(1 for q in quotes if q.right == "C")
    puts = sum(1 for q in quotes if q.right == "P")
    return puts / calls if calls > 0 else 0.0


# --------------------------------------------------------------------------- #
# Black-Scholes helpers (no scipy dependency)                                  #
# --------------------------------------------------------------------------- #

_RISK_FREE = 0.045  # Annualized; reasonable for 2020-2026
_TRADING_DAYS_PER_YEAR = 252.0


def _years_to_expiry(expiry: str) -> float:
    """Days-to-expiry (calendar) / 365.25, floored at 1/365."""
    from datetime import date, datetime
    try:
        y, m, d = expiry.split("-")
        exp = date(int(y), int(m), int(d))
    except Exception:
        return 0.0
    today = date.today()
    days = max((exp - today).days, 0)
    return max(days / 365.25, 1.0 / 365.25)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _bs_d1(spot: float, strike: float, iv: float, expiry: str) -> float:
    T = _years_to_expiry(expiry)
    if spot <= 0 or strike <= 0 or iv <= 0 or T <= 0:
        return 0.0
    return (math.log(spot / strike) + (_RISK_FREE + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))


def _bs_d2(spot: float, strike: float, iv: float, expiry: str) -> float:
    d1 = _bs_d1(spot, strike, iv, expiry)
    T = _years_to_expiry(expiry)
    if T <= 0:
        return 0.0
    return d1 - iv * math.sqrt(T)


def _bs_call(spot: float, strike: float, iv: float, expiry: str) -> float:
    T = _years_to_expiry(expiry)
    if T <= 0 or spot <= 0 or strike <= 0 or iv <= 0:
        return 0.0
    d1 = _bs_d1(spot, strike, iv, expiry)
    d2 = _bs_d2(spot, strike, iv, expiry)
    return spot * _norm_cdf(d1) - strike * math.exp(-_RISK_FREE * T) * _norm_cdf(d2)


def _bs_put(spot: float, strike: float, iv: float, expiry: str) -> float:
    T = _years_to_expiry(expiry)
    if T <= 0 or spot <= 0 or strike <= 0 or iv <= 0:
        return 0.0
    d1 = _bs_d1(spot, strike, iv, expiry)
    d2 = _bs_d2(spot, strike, iv, expiry)
    return strike * math.exp(-_RISK_FREE * T) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def _bs_delta(spot: float, strike: float, iv: float, expiry: str, right: str) -> float:
    d1 = _bs_d1(spot, strike, iv, expiry)
    if right == "C":
        return _norm_cdf(d1)
    return _norm_cdf(d1) - 1.0


def _bs_gamma(spot: float, strike: float, iv: float, expiry: str) -> float:
    T = _years_to_expiry(expiry)
    if T <= 0 or spot <= 0 or strike <= 0 or iv <= 0:
        return 0.0
    d1 = _bs_d1(spot, strike, iv, expiry)
    return _norm_pdf(d1) / (spot * iv * math.sqrt(T))


def _bs_implied_vol(mid: float, spot: float, strike: float, expiry: str, right: str) -> float:
    """Bisection on BS price. Returns 0.0 if no bracket found in [0.01, 5.0]."""
    if mid <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    lo, hi = 0.01, 5.0
    intrinsic = max(0.0, spot - strike) if right == "C" else max(0.0, strike - spot)
    if mid < intrinsic:
        return 0.0
    for _ in range(60):
        mid_iv = (lo + hi) / 2.0
        price = _bs_call(spot, strike, mid_iv, expiry) if right == "C" else _bs_put(spot, strike, mid_iv, expiry)
        if abs(price - mid) < 1e-4:
            return mid_iv
        if price > mid:
            hi = mid_iv
        else:
            lo = mid_iv
        if hi - lo < 1e-5:
            break
    iv = (lo + hi) / 2.0
    if iv < 0.005 or iv > 4.0:
        return 0.0
    return iv
