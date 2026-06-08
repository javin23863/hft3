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

IV_STATUS_SUCCESS = "SUCCESS"
IV_STATUS_NO_VALID_MARKET = "NO_VALID_MARKET"
IV_STATUS_NO_ATM_COVERAGE = "NO_ATM_COVERAGE"
IV_STATUS_NO_ARBITRAGE_FAIL = "NO_ARBITRAGE_FAIL"
IV_STATUS_SYNTHETIC_LOW_CONFIDENCE = "SYNTHETIC_LOW_CONFIDENCE"

CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_BLOCKED = "BLOCKED"

SOURCE_REAL = "REAL"
SOURCE_INTERPOLATED = "INTERPOLATED"
SOURCE_EXTRAPOLATED = "EXTRAPOLATED"
SOURCE_PROXY_SYNTHETIC = "PROXY_SYNTHETIC"


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
    source: str = SOURCE_REAL
    bid_size: int = 0
    ask_size: int = 0
    listed_at_ts_ns: int = 0
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
    real_quote_count: int = 0
    synthetic_quote_count: int = 0
    quote_age_ns: int = 0
    coverage: float = 0.0  # fraction of strikes with valid quotes
    surface_source: str = SOURCE_REAL
    iv_atm_status: str = IV_STATUS_NO_VALID_MARKET
    iv_confidence: str = CONFIDENCE_BLOCKED
    nearest_real_strike_distance_pct: float = 0.0
    real_option_chain_available: bool = False
    real_nbbo_size_available: bool = False
    contract_listing_metadata_available: bool = False
    valid_contract_count: int = 0
    atm_real_available: bool = False
    iv_success_rate: float = 0.0
    no_arbitrage_violation_count: int = 0

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
            "real_quote_count": self.real_quote_count,
            "synthetic_quote_count": self.synthetic_quote_count,
            "quote_age_ns": self.quote_age_ns,
            "coverage": self.coverage,
            "surface_source": self.surface_source,
            "iv_atm_status": self.iv_atm_status,
            "iv_confidence": self.iv_confidence,
            "nearest_real_strike_distance_pct": self.nearest_real_strike_distance_pct,
            "real_option_chain_available": self.real_option_chain_available,
            "real_nbbo_size_available": self.real_nbbo_size_available,
            "contract_listing_metadata_available": self.contract_listing_metadata_available,
            "valid_contract_count": self.valid_contract_count,
            "atm_real_available": self.atm_real_available,
            "iv_success_rate": self.iv_success_rate,
            "no_arbitrage_violation_count": self.no_arbitrage_violation_count,
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
                source = _normalize_source(r.get("source") or r.get("method") or SOURCE_REAL)
                bid_size = int(r.get("bid_size") or r.get("bid_sz") or 0)
                ask_size = int(r.get("ask_size") or r.get("ask_sz") or 0)
                listed_at_ts_ns = int(r.get("listed_at_ts_ns") or r.get("contract_listed_at_ts_ns") or 0)
                q = OptionQuote(
                    ts_ns=ts_ns,
                    strike=float(strike),
                    right=str(right),
                    expiry=str(expiry),
                    bid=float(bid),
                    ask=float(ask),
                    source=source,
                    bid_size=bid_size,
                    ask_size=ask_size,
                    listed_at_ts_ns=listed_at_ts_ns,
                )
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

    def to_snapshot(self, ts_ns: int, spot: float, *, max_lag_ns: int = 120 * 1_000_000_000, decision_date: "date | None" = None) -> OptionsChainSnapshot:
        quotes = self.lookup(ts_ns, max_lag_ns=max_lag_ns)
        snap = OptionsChainSnapshot(ts_ns=ts_ns, underlying=self.underlying, spot=spot)
        if not quotes:
            return snap
        real_quotes = _real_quotes(quotes)
        snap.num_quotes = len(quotes)
        snap.real_quote_count = len(real_quotes)
        snap.synthetic_quote_count = snap.num_quotes - snap.real_quote_count
        snap.quote_age_ns = max(0, ts_ns - max(q.ts_ns for q in quotes))
        snap.real_option_chain_available = snap.real_quote_count > 0
        snap.real_nbbo_size_available = any(q.bid_size > 0 and q.ask_size > 0 for q in real_quotes)
        snap.contract_listing_metadata_available = any(q.listed_at_ts_ns > 0 for q in real_quotes)
        snap.valid_contract_count = sum(1 for q in real_quotes if 0 < q.listed_at_ts_ns <= ts_ns)
        snap.surface_source = SOURCE_REAL if snap.real_quote_count else SOURCE_PROXY_SYNTHETIC
        snap.coverage = _coverage_ratio(real_quotes)
        diagnostics = _iv_diagnostics(quotes, spot, decision_date)
        snap.iv_atm = float(diagnostics["iv_atm"])
        snap.iv_atm_status = str(diagnostics["iv_atm_status"])
        snap.iv_confidence = str(diagnostics["iv_confidence"])
        snap.nearest_real_strike_distance_pct = float(diagnostics["nearest_real_strike_distance_pct"])
        snap.atm_real_available = bool(diagnostics["atm_real_available"])
        snap.iv_success_rate = float(diagnostics["iv_success_rate"])
        snap.no_arbitrage_violation_count = int(diagnostics["no_arbitrage_violation_count"])
        snap.iv_skew_25d = _skew_25d(real_quotes, spot, snap.iv_atm, decision_date)
        snap.iv_term_atm = _term_atm_iv(real_quotes, spot, decision_date)
        snap.gex_net, snap.dex_net, snap.call_wall_strike, snap.put_wall_strike = _exposures(real_quotes, spot, decision_date)
        snap.pc_ratio_volume = _pc_ratio(real_quotes)
        snap.quotes = real_quotes
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


def _normalize_source(raw: object) -> str:
    source = str(raw or SOURCE_REAL).upper()
    if source in {"SYNTHETIC", "PROXY", "PROXY_SYNTHETIC"}:
        return SOURCE_PROXY_SYNTHETIC
    if source in {SOURCE_INTERPOLATED, SOURCE_EXTRAPOLATED, SOURCE_PROXY_SYNTHETIC}:
        return source
    return SOURCE_REAL


def _real_quotes(quotes: list[OptionQuote]) -> list[OptionQuote]:
    return [q for q in quotes if q.source == SOURCE_REAL]


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


def _atm_iv(quotes: list[OptionQuote], spot: float, decision_date: "date | None" = None) -> float:
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
            iv = _bs_implied_vol(q.mid, spot, q.strike, q.expiry, q.right, decision_date)
            if iv > 0:
                mids.append(iv)
    return sum(mids) / len(mids) if mids else 0.0


def _iv_diagnostics(quotes: list[OptionQuote], spot: float, decision_date: "date | None" = None) -> dict[str, object]:
    """Typed IV diagnostics; failed IV stays status/null-like instead of fake zero evidence."""
    out: dict[str, object] = {
        "iv_atm": 0.0,
        "iv_atm_status": IV_STATUS_NO_VALID_MARKET,
        "iv_confidence": CONFIDENCE_BLOCKED,
        "nearest_real_strike_distance_pct": 0.0,
        "atm_real_available": False,
        "iv_success_rate": 0.0,
        "no_arbitrage_violation_count": 0,
    }
    if not quotes or spot <= 0:
        return out

    real_quotes = [q for q in quotes if q.source == SOURCE_REAL]
    if not real_quotes:
        out["iv_atm_status"] = IV_STATUS_SYNTHETIC_LOW_CONFIDENCE
        return out

    front = _nearest_expiry(real_quotes)
    atm = _atm_strike(real_quotes, spot)
    if atm > 0:
        out["nearest_real_strike_distance_pct"] = abs(atm - spot) / spot

    valid_count = 0
    failure_count = 0
    atm_ivs: list[float] = []
    for q in real_quotes:
        if q.mid <= 0:
            continue
        iv = _bs_implied_vol(q.mid, spot, q.strike, q.expiry, q.right, decision_date)
        if iv > 0:
            valid_count += 1
            if q.expiry == front and q.strike == atm:
                atm_ivs.append(iv)
        else:
            failure_count += 1

    total_attempted = valid_count + failure_count
    out["iv_success_rate"] = valid_count / total_attempted if total_attempted else 0.0
    out["no_arbitrage_violation_count"] = failure_count

    if atm <= 0:
        out["iv_atm_status"] = IV_STATUS_NO_ATM_COVERAGE
        return out
    if not atm_ivs and failure_count > 0 and valid_count == 0:
        out["iv_atm_status"] = IV_STATUS_NO_ARBITRAGE_FAIL if failure_count else IV_STATUS_NO_VALID_MARKET
        return out
    if float(out["nearest_real_strike_distance_pct"]) > 0.25:
        out["iv_atm_status"] = IV_STATUS_NO_ATM_COVERAGE
        return out
    if not atm_ivs:
        out["iv_atm_status"] = IV_STATUS_NO_ARBITRAGE_FAIL if failure_count else IV_STATUS_NO_VALID_MARKET
        return out

    out["iv_atm"] = sum(atm_ivs) / len(atm_ivs)
    out["atm_real_available"] = True
    out["iv_atm_status"] = IV_STATUS_SUCCESS
    distance = float(out["nearest_real_strike_distance_pct"])
    success_rate = float(out["iv_success_rate"])
    if distance <= 0.05 and success_rate >= 0.50:
        out["iv_confidence"] = CONFIDENCE_HIGH
    elif distance <= 0.15 and success_rate >= 0.25:
        out["iv_confidence"] = CONFIDENCE_MEDIUM
    else:
        out["iv_confidence"] = CONFIDENCE_LOW
    return out


def _term_atm_iv(quotes: list[OptionQuote], spot: float, decision_date: "date | None" = None) -> float:
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
                iv = _bs_implied_vol(q.mid, spot, q.strike, q.expiry, q.right, decision_date)
                if iv > 0:
                    ivs.append(iv)
    return sum(ivs) / len(ivs) if ivs else 0.0


def _skew_25d(quotes: list[OptionQuote], spot: float, atm_iv: float, decision_date: "date | None" = None) -> float:
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
        delta = _bs_delta(spot, q.strike, atm_iv, q.expiry, q.right, decision_date)
        if q.right == "P" and abs(abs(delta) - target_delta) < 0.10 and abs(delta) > target_delta - 0.02:
            iv = _bs_implied_vol(q.mid, spot, q.strike, q.expiry, q.right, decision_date)
            if iv > 0:
                put_iv = max(put_iv, iv)
        elif q.right == "C" and abs(delta - target_delta) < 0.10 and delta > target_delta - 0.02:
            iv = _bs_implied_vol(q.mid, spot, q.strike, q.expiry, q.right, decision_date)
            if iv > 0:
                call_iv = max(call_iv, iv)
    return put_iv - call_iv


def _exposures(quotes: list[OptionQuote], spot: float, decision_date: "date | None" = None) -> tuple[float, float, float, float]:
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
        iv = _bs_implied_vol(q.mid, spot, q.strike, q.expiry, q.right, decision_date)
        if iv <= 0:
            continue
        d = _bs_delta(spot, q.strike, iv, q.expiry, q.right, decision_date)
        g = _bs_gamma(spot, q.strike, iv, q.expiry, decision_date)
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


def _years_to_expiry(expiry: str, decision_date: "date | None" = None) -> float:
    """Days-to-expiry (calendar) / 365.25, floored at 1/365.

    If decision_date is provided, calculate from that date (for historical data).
    Otherwise, use today's date (for current data).
    """
    from datetime import date, datetime
    try:
        y, m, d = expiry.split("-")
        exp = date(int(y), int(m), int(d))
    except Exception:
        return 0.0
    if decision_date is None:
        decision_date = date.today()
    days = max((exp - decision_date).days, 0)
    return max(days / 365.25, 1.0 / 365.25)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _bs_d1(spot: float, strike: float, iv: float, expiry: str, decision_date: "date | None" = None) -> float:
    T = _years_to_expiry(expiry, decision_date)
    if spot <= 0 or strike <= 0 or iv <= 0 or T <= 0:
        return 0.0
    return (math.log(spot / strike) + (_RISK_FREE + 0.5 * iv * iv) * T) / (iv * math.sqrt(T))


def _bs_d2(spot: float, strike: float, iv: float, expiry: str, decision_date: "date | None" = None) -> float:
    d1 = _bs_d1(spot, strike, iv, expiry, decision_date)
    T = _years_to_expiry(expiry, decision_date)
    if T <= 0:
        return 0.0
    return d1 - iv * math.sqrt(T)


def _bs_call(spot: float, strike: float, iv: float, expiry: str, decision_date: "date | None" = None) -> float:
    T = _years_to_expiry(expiry, decision_date)
    if T <= 0 or spot <= 0 or strike <= 0 or iv <= 0:
        return 0.0
    d1 = _bs_d1(spot, strike, iv, expiry, decision_date)
    d2 = _bs_d2(spot, strike, iv, expiry, decision_date)
    return spot * _norm_cdf(d1) - strike * math.exp(-_RISK_FREE * T) * _norm_cdf(d2)


def _bs_put(spot: float, strike: float, iv: float, expiry: str, decision_date: "date | None" = None) -> float:
    T = _years_to_expiry(expiry, decision_date)
    if T <= 0 or spot <= 0 or strike <= 0 or iv <= 0:
        return 0.0
    d1 = _bs_d1(spot, strike, iv, expiry, decision_date)
    d2 = _bs_d2(spot, strike, iv, expiry, decision_date)
    return strike * math.exp(-_RISK_FREE * T) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def _bs_delta(spot: float, strike: float, iv: float, expiry: str, right: str, decision_date: "date | None" = None) -> float:
    d1 = _bs_d1(spot, strike, iv, expiry, decision_date)
    if right == "C":
        return _norm_cdf(d1)
    return _norm_cdf(d1) - 1.0


def _bs_gamma(spot: float, strike: float, iv: float, expiry: str, decision_date: "date | None" = None) -> float:
    T = _years_to_expiry(expiry, decision_date)
    if T <= 0 or spot <= 0 or strike <= 0 or iv <= 0:
        return 0.0
    d1 = _bs_d1(spot, strike, iv, expiry, decision_date)
    return _norm_pdf(d1) / (spot * iv * math.sqrt(T))


def _bs_implied_vol(mid: float, spot: float, strike: float, expiry: str, right: str, decision_date: "date | None" = None) -> float:
    """Bisection on BS price. Returns 0.0 if no bracket found in [0.01, 5.0]."""
    if mid <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    lo, hi = 0.01, 5.0
    intrinsic = max(0.0, spot - strike) if right == "C" else max(0.0, strike - spot)
    if mid < intrinsic:
        return 0.0
    for _ in range(60):
        mid_iv = (lo + hi) / 2.0
        price = _bs_call(spot, strike, mid_iv, expiry, decision_date) if right == "C" else _bs_put(spot, strike, mid_iv, expiry, decision_date)
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
