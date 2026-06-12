"""Put/call parity residual and violation detection."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from options_lane.src.basis import effective_underlying
from options_lane.src.models import BandViolation, CalendarViolation, LegQuote, ParityGroup, QuoteSnapshot, Violation

if TYPE_CHECKING:
    from options_lane.src.backtest.options_fee_model import OptionsFeeModel


def required_parity_roles(group: ParityGroup) -> set[str]:
    required = {"call", "put"}
    if group.type == "futures_options":
        required.add("future")
    elif group.type == "index_future_basis":
        required.add("spot")
        if (group.basis_model or "identity") == "index_minus_future":
            required.add("future")
    return required


def discount_factor(rate: float, time_years: float) -> float:
    return math.exp(-rate * time_years)


def theoretical_spread(group: ParityGroup, quotes: dict[str, LegQuote]) -> float:
    """No-arbitrage C - P under European/futures-options parity."""
    call = quotes.get("call")
    put = quotes.get("put")
    if call is None or put is None:
        raise ValueError(f"group {group.id}: missing call or put leg")
    strike = call.strike if call.strike is not None else put.strike
    if strike is None:
        raise ValueError(f"group {group.id}: strike required on call/put legs")
    s_eff = effective_underlying(group, quotes)
    df = discount_factor(group.rate.value, group.time_to_expiry_years)
    return s_eff - df * strike


def compute_violation(
    group: ParityGroup,
    snapshot: QuoteSnapshot,
    fee_per_leg: float = 0.0,
    as_of_ns: int | None = None,
) -> Violation | None:
    """
    Compute parity violation at as_of_ns using only quotes with timestamp_ns <= as_of_ns.
    Returns None if required legs are missing or not yet visible (filtration-safe).
    """
    t_ns = as_of_ns if as_of_ns is not None else snapshot.timestamp_ns
    filtered: dict[str, LegQuote] = {}
    for role, q in snapshot.quotes.items():
        if q.timestamp_ns <= t_ns:
            filtered[role] = q

    required_roles = required_parity_roles(group)

    if not required_roles.issubset(filtered.keys()):
        return None

    try:
        theo = theoretical_spread(group, filtered)
    except ValueError:
        return None

    call = filtered["call"]
    put = filtered["put"]
    observed = call.mid - put.mid
    residual = observed - theo
    edge_ticks = abs(residual) / group.tick_size if group.tick_size > 0 else 0.0
    num_legs = len(required_roles)
    edge_after_fees = abs(residual) - fee_per_leg * num_legs

    return Violation(
        group_id=group.id,
        timestamp_ns=t_ns,
        residual=residual,
        edge_ticks=edge_ticks,
        edge_after_fees=edge_after_fees,
        legs_used=sorted(filtered.keys()),
        observed_spread=observed,
        theoretical_spread=theo,
        underlying_eff=effective_underlying(group, filtered),
    )


def is_actionable(violation: Violation, group: ParityGroup) -> bool:
    return violation.edge_ticks >= group.threshold_ticks and violation.edge_after_fees > 0.0


# ---------------------------------------------------------------------------
# Band-based API (WS-2 detector upgrade)
# ---------------------------------------------------------------------------

def _american_band(f: float, k: float, df: float) -> tuple[float, float]:
    """
    No-arb band [lo, hi] for American futures options: C - P must lie in this range.

    lo = df*F - K  (buy call, sell put, buy future at F, borrow K)
    hi = F - df*K  (sell call, buy put, sell future at F, invest K)
    Width = (F + K)*(1 - df).  A spread strictly inside is never a violation.
    """
    return df * f - k, f - df * k


def _european_band(f: float, k: float, df: float) -> tuple[float, float]:
    """Zero-width band: exact parity C - P = df*F - df*K = df*(F-K)."""
    theo = df * (f - k)
    return theo, theo


def _band_for_group(group: ParityGroup, f: float, k: float, df: float) -> tuple[float, float]:
    """Select band type based on group option_style field (defaults to American for futures_options)."""
    style = getattr(group, "option_style", None) or "american"
    if style == "european":
        return _european_band(f, k, df)
    return _american_band(f, k, df)


def _executable_spread(
    call: LegQuote,
    put: LegQuote,
    future: LegQuote | None,
    direction: str,
) -> tuple[float, float, bool]:
    """
    Return (exec_spread, exec_future_price, degraded).

    direction "long_call_short_put": buy call at ask, sell put at bid, hedge sells future at bid.
    direction "short_call_long_put": sell call at bid, buy put at ask, hedge buys future at ask.
    degraded=True when bid==ask==mid (no spread info).
    """
    call_bid_eq_ask = call.bid == call.ask
    put_bid_eq_ask = put.bid == put.ask

    if direction == "long_call_short_put":
        exec_c = call.ask
        exec_p = put.bid
        exec_f = future.bid if future is not None else None
    else:
        exec_c = call.bid
        exec_p = put.ask
        exec_f = future.ask if future is not None else None

    degraded = call_bid_eq_ask and put_bid_eq_ask
    exec_spread = exec_c - exec_p
    exec_f_out = exec_f if exec_f is not None else (future.mid if future is not None else float("nan"))
    return exec_spread, exec_f_out, degraded


def compute_band_violation(
    group: ParityGroup,
    snapshot: QuoteSnapshot,
    fee_model: "OptionsFeeModel | None" = None,
    fee_per_leg: float = 0.0,
    as_of_ns: int | None = None,
) -> BandViolation | None:
    """
    Compute style-aware no-arb band violation.

    For European: band is zero-width (exact parity).
    For American: band is [df*F - K, F - df*K]; spread inside => no violation.

    Executable prices:
      - When bid/ask differ from mid, buy legs priced at ask and sell legs at bid.
      - When bid==ask on both options (degraded), falls back to mid.

    Fee source priority: fee_model > fee_per_leg argument > 0.
    Returns None if required legs are missing or not yet visible.
    """
    t_ns = as_of_ns if as_of_ns is not None else snapshot.timestamp_ns
    filtered: dict[str, LegQuote] = {
        role: q for role, q in snapshot.quotes.items() if q.timestamp_ns <= t_ns
    }
    required_roles = required_parity_roles(group)
    if not required_roles.issubset(filtered.keys()):
        return None

    call = filtered["call"]
    put = filtered["put"]
    future = filtered.get("future")

    strike = call.strike if call.strike is not None else put.strike
    if strike is None:
        return None

    f_mid = future.mid if future is not None else effective_underlying(group, filtered)
    df = discount_factor(group.rate.value, group.time_to_expiry_years)
    band_lo, band_hi = _band_for_group(group, f_mid, strike, df)

    observed_mid = call.mid - put.mid

    # Determine excess beyond band (signed; 0 if inside)
    if observed_mid < band_lo:
        mid_excess = observed_mid - band_lo
    elif observed_mid > band_hi:
        mid_excess = observed_mid - band_hi
    else:
        mid_excess = 0.0

    # Executable spread analysis
    if mid_excess > 0.0:
        # C-P too high: trade = sell call (bid), buy put (ask); synthetic short future, hedge buys future (ask)
        direction = "short_call_long_put"
    elif mid_excess < 0.0:
        direction = "long_call_short_put"
    else:
        direction = "long_call_short_put"

    exec_spread, exec_f, degraded = _executable_spread(call, put, future, direction)

    # Recompute band on executable future price
    band_lo_ex, band_hi_ex = _band_for_group(group, exec_f, strike, df)
    if exec_spread < band_lo_ex:
        exec_excess = exec_spread - band_lo_ex
    elif exec_spread > band_hi_ex:
        exec_excess = exec_spread - band_hi_ex
    else:
        exec_excess = 0.0

    executable = exec_excess != 0.0

    # Fee calculation
    num_legs = len(required_roles)
    if fee_model is not None:
        fee_total = fee_model.total_leg_cost(group, num_legs)
    else:
        fee_total = fee_per_leg * num_legs

    # Actionable only if executable excess clears fees
    actionable = executable and abs(exec_excess) > fee_total

    return BandViolation(
        group_id=group.id,
        timestamp_ns=t_ns,
        observed_spread=observed_mid,
        theoretical_spread=(band_lo + band_hi) / 2.0,
        band_lo=band_lo,
        band_hi=band_hi,
        excess=mid_excess,
        executable=executable,
        degraded=degraded,
        actionable=actionable,
        fee_total=fee_total,
        legs_used=sorted(filtered.keys()),
        underlying_eff=f_mid,
    )


def check_calendar_monotonicity(
    symbol: str,
    strike: float,
    c_t1: float,
    c_t2: float,
    t1_expiry_years: float,
    t2_expiry_years: float,
    rate: float,
    cost_to_check: float = 0.0,
    same_underlying: bool = False,
) -> CalendarViolation | None:
    """
    Check calendar spread monotonicity: C(T2) >= df(T1->T2) * C(T1) within costs.

    CAVEAT: ES quarterly options reference DIFFERENT underlying futures contracts per
    expiry. This check applies cleanly only to same-underlying chains (dailies/weeklies
    on the same quarterly future). Pass same_underlying=True only when the caller has
    confirmed that both expiries reference the same underlying contract. When
    same_underlying=False this function returns None and emits no signal.

    df(T1->T2) = e^{-r*(T2-T1)} — the forward discount factor from T1 to T2.
    Violation: C(T2) < df(T1->T2) * C(T1) - cost_to_check.

    Returns CalendarViolation if a violation is found, else None.
    """
    if not same_underlying:
        return None

    dt = t2_expiry_years - t1_expiry_years
    if dt <= 0.0:
        raise ValueError(f"t2_expiry_years must be > t1_expiry_years; got {t2_expiry_years} <= {t1_expiry_years}")

    df_forward = math.exp(-rate * dt)
    required_c_t2 = df_forward * c_t1
    excess = required_c_t2 - c_t2  # positive => violation
    actionable = excess > cost_to_check

    return CalendarViolation(
        symbol=symbol,
        strike=strike,
        t1_expiry_years=t1_expiry_years,
        t2_expiry_years=t2_expiry_years,
        c_t1=c_t1,
        c_t2=c_t2,
        df_forward=df_forward,
        required_c_t2=required_c_t2,
        excess=excess,
        actionable=actionable,
        same_underlying=same_underlying,
    )
