"""T2: robustness pack smoke."""
from __future__ import annotations

from workbench.src.robustness.pack import run_robustness_pack


def test_robustness_pack_runs_on_constant_pnl() -> None:
    def _base():
        return {"net_pnl": 100.0, "num_trades": 10, "expectancy": 10.0}

    result = run_robustness_pack(_base, [10.0] * 10, sweep_count=2)
    assert hasattr(result, "passed")
    assert hasattr(result, "overfit_risk")
