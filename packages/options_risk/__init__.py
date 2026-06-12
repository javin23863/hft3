"""Options risk analytics — stress testing and margin approximations.

No I/O is performed at import time; all computation lives in src/.
All margin estimates are approximations; see individual module docstrings
for validity envelopes and omitted SPAN charges.
"""

from options_risk.src.stress_grid import (
    Position,
    ScenarioCell,
    StressGrid,
    DEFAULT_OVERNIGHT_CELLS,
    revalue,
    grid_pnl,
    worst_cell,
)
from options_risk.src.margin_approx import (
    scanning_risk,
    margin_estimate,
)

__all__ = [
    "Position",
    "ScenarioCell",
    "StressGrid",
    "DEFAULT_OVERNIGHT_CELLS",
    "revalue",
    "grid_pnl",
    "worst_cell",
    "scanning_risk",
    "margin_estimate",
]
