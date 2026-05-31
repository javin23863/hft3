"""Resolve OPRA pull symbols from catalog chain_rules (config-only)."""
from __future__ import annotations

from equities_lane.src.types import DecadalSession, OptionsChainSpec


def resolve_pull_symbols(session: DecadalSession) -> tuple[list[str], str, str]:
    """
    Return (symbols, stype_in, schema) for Databento options pull.

    Uses parent stype so OPRA returns the full underlying option chain in-window.
    Strike/expiry rules live in catalog for downstream quant use; pull is not filtered here.
    """
    opts = session.options
    if opts is None or not opts.enabled:
        return [], "", ""
    underlying = session.resolved_symbol or session.symbol
    if opts.stype_in == "parent" and not underlying.endswith((".OPT", ".FUT", ".SPOT")):
        underlying = f"{underlying}.OPT"
    return [underlying], opts.stype_in, opts.schema


def reference_price_from_meta(
    session: DecadalSession,
    *,
    prior_close: float | None,
    premarket_open: float | None,
) -> float | None:
    """Pick reference price enum from catalog chain_rules."""
    if session.options is None:
        return None
    ref = session.options.chain_rules.reference_price
    if ref == "prior_close":
        return prior_close
    if ref == "premarket_open":
        return premarket_open
    return premarket_open or prior_close
