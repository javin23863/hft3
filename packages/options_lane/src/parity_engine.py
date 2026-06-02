"""Put/call parity residual and violation detection."""
from __future__ import annotations

import math

from options_lane.src.basis import effective_underlying
from options_lane.src.imbalance_eligibility import EligibilityConfig, OptionQuote, option_imbalance_eligible
from options_lane.src.models import LegQuote, ParityGroup, QuoteSnapshot, Violation


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
    elig_cfg = EligibilityConfig()
    for leg in (call, put):
        if leg.role not in ("call", "put"):
            continue
        if leg.bid <= 0 or leg.ask <= 0:
            return None
        ok, _reason = option_imbalance_eligible(
            OptionQuote(leg.bid, leg.ask, 10.0, 10.0, leg.timestamp_ns),
            elig_cfg,
        )
        if not ok:
            return None
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
