"""Run mode enforcement — controls promotion eligibility and data policies."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class RunMode(str, Enum):
    REAL_RESEARCH = "REAL_RESEARCH"
    PAPER_REPLAY = "PAPER_REPLAY"
    FIXTURE_CI = "FIXTURE_CI"
    PERFORMANCE_BENCHMARK = "PERFORMANCE_BENCHMARK"
    DEBUG = "DEBUG"

    @property
    def promotion_eligible(self) -> bool:
        return self in (RunMode.REAL_RESEARCH, RunMode.PAPER_REPLAY)

    @property
    def allows_synthetic_data(self) -> bool:
        return self in (RunMode.FIXTURE_CI, RunMode.PERFORMANCE_BENCHMARK, RunMode.DEBUG)

    @property
    def allows_fixture_data(self) -> bool:
        return self in (RunMode.FIXTURE_CI, RunMode.DEBUG)


@dataclass
class RunContext:
    run_mode: RunMode
    run_id: str = ""
    lane_id: str = ""
    model_id: str = ""
    symbol: str = ""
    event_id: str = ""
    session_id: str = ""
    group_id: str = ""
    synthetic_data_used: bool = False
    fixture_data_used: bool = False
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_mode": self.run_mode.value,
            "run_id": self.run_id,
            "lane_id": self.lane_id,
            "model_id": self.model_id,
            "symbol": self.symbol,
            "event_id": self.event_id,
            "session_id": self.session_id,
            "group_id": self.group_id,
            "promotion_eligible": self.run_mode.promotion_eligible,
            "synthetic_data_used": self.synthetic_data_used,
            "fixture_data_used": self.fixture_data_used,
            "reason": self.reason,
        }

    def check_promotion_eligibility(self) -> tuple[bool, list[str]]:
        blockers = []
        if not self.run_mode.promotion_eligible:
            blockers.append(f"run_mode={self.run_mode.value} is not promotion eligible")
        if self.synthetic_data_used:
            blockers.append("synthetic_data_used=true blocks promotion")
        if self.fixture_data_used and self.run_mode != RunMode.PAPER_REPLAY:
            blockers.append("fixture_data_used=true blocks promotion in non-PAPER mode")
        return (len(blockers) == 0, blockers)


def parse_run_mode(value: str) -> RunMode:
    value_upper = value.upper().replace("-", "_").replace(" ", "_")
    for mode in RunMode:
        if mode.value == value_upper:
            return mode
    raise ValueError(f"Unknown run mode: {value!r}. Valid: {[m.value for m in RunMode]}")
