"""Historical replay through C++ production engine (approach 3 — stub interface)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class CppReplayResult:
    """Orders, fills, decisions, latency logs returned from C++ harness."""

    available: bool = False
    reason: str = "C++ replay harness not wired; use pybind or latency injection"
    orders: List[Dict[str, Any]] = field(default_factory=list)
    fills: List[Dict[str, Any]] = field(default_factory=list)
    latency_logs: List[Dict[str, Any]] = field(default_factory=list)


class CppReplayHarness:
    """
    Best production replay: feed recorded MBO through C++ decision engine.
    Returns structured logs for Python dashboard — no Python hot-path timing.
    """

    def __init__(self, engine_binary: Optional[Path] = None):
        self.engine_binary = engine_binary

    def is_available(self) -> bool:
        return self.engine_binary is not None and self.engine_binary.is_file()

    def replay(self, npz_path: Path, model_id: str) -> CppReplayResult:
        if not self.is_available():
            return CppReplayResult(
                available=False,
                reason="Build decision_engine C++ replay binary or wire rithmic_gateway harness",
            )
        raise NotImplementedError("C++ replay harness integration pending")
