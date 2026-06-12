"""Hedging utilities for the options lane — WS-3.

Exports
-------
zakamouline_band      Zakamouline (2006) optimal delta-hedging band.
whalley_wilmott_band  Whalley-Wilmott asymptotic half-width (comparison).
HedgeBandPolicy       Stateful futures hedge policy using the Zakamouline band.

Governance note
---------------
These are ZERO-ALPHA hedging/operations tools.  They manage transaction-cost
exposure in a known options book; they do not generate directional edge.
"""

from options_lane.src.hedging.zakamouline import (
    HedgeBandPolicy,
    whalley_wilmott_band,
    zakamouline_band,
)

__all__ = [
    "zakamouline_band",
    "whalley_wilmott_band",
    "HedgeBandPolicy",
]
