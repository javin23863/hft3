"""Config-driven options fee model for parity arb."""
from __future__ import annotations

from options_lane.src.models import ParityGroup


class OptionsFeeModel:
    """Per-leg fees keyed by group tick_size / leg count — no hardcoded products."""

    def __init__(self, fee_per_contract: float | None = None, fee_table: dict[str, float] | None = None):
        self._default = fee_per_contract if fee_per_contract is not None else 0.65
        self._table = fee_table or {}

    def fee_per_contract(self, group: ParityGroup) -> float:
        return self._table.get(group.id, self._default)

    def total_leg_cost(self, group: ParityGroup, num_legs: int) -> float:
        return self.fee_per_contract(group) * num_legs
