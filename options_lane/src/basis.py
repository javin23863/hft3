"""Pluggable basis adjustments for cross-underlying parity."""
from __future__ import annotations

from options_lane.src.models import LegQuote, ParityGroup


def effective_underlying(group: ParityGroup, quotes: dict[str, LegQuote]) -> float:
    """Compute S_eff for parity RHS: S_eff - DF*K."""
    if group.type == "futures_options":
        future = quotes.get("future")
        if future is None:
            raise ValueError(f"group {group.id}: missing future leg")
        return future.mid

    if group.type == "index_future_basis":
        spot = quotes.get("spot")
        future = quotes.get("future")
        if spot is None:
            raise ValueError(f"group {group.id}: missing spot leg")
        model = group.basis_model or "identity"
        if model == "identity":
            return spot.mid
        if model == "index_minus_future":
            if future is None:
                return spot.mid
            scale = group.basis_params.get("basis_scale", 1.0)
            return spot.mid + scale * (future.mid - spot.mid)
        raise ValueError(f"group {group.id}: unknown basis_model {model!r}")

    raise ValueError(f"group {group.id}: unknown type {group.type!r}")
