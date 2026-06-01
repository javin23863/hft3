"""Strategy code must not branch on execution mode."""
from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
STRATEGY_FILES = [
    _REPO / "packages" / "backtest_pipeline" / "src" / "hypothesis_replay_strategy.py",
    _REPO / "packages" / "backtest_pipeline" / "src" / "hft_strategy.py",
]

FORBIDDEN = (
    "execution_mode",
    'if mode == "replay"',
    'if mode == "paper"',
    'if mode == "live"',
    "if backtest",
)


def test_strategy_files_mode_blind() -> None:
    violations = []
    for path in STRATEGY_FILES:
        text = path.read_text(encoding="utf-8").lower()
        for token in FORBIDDEN:
            if token in text:
                violations.append(f"{path.name}: {token}")
    assert not violations, violations
