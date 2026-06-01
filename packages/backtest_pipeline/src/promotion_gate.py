"""Promotion gate and candidate artifact — sits between VectorBT filter and HftBacktest.

Traces to integration spec: every promoted candidate must carry full traceable metadata.
A candidate must not reach HftBacktest without this artifact.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import json

_REPO = Path(__file__).resolve().parents[3]


@dataclass
class PromotedCandidate:
    candidate_id: str
    hypothesis_id: str
    strategy_family: str
    asset_class: str
    symbol: str
    timeframe: str
    param_values: Dict[str, Any]
    vectorbt_run_id: str
    vectorbt_results: Dict[str, Any]
    pass_reason: str
    in_sample_results: Dict[str, Any] = field(default_factory=dict)
    out_of_sample_results: Dict[str, Any] = field(default_factory=dict)
    walk_forward_results: List[Dict[str, Any]] = field(default_factory=list)
    robustness_metrics: Dict[str, float] = field(default_factory=dict)
    turnover_metrics: Dict[str, float] = field(default_factory=dict)
    drawdown_metrics: Dict[str, float] = field(default_factory=dict)
    execution_classification: str = ""
    data_version: str = ""
    git_commit: str = ""
    config_path: str = ""
    seed: int = 42
    timestamp_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "hypothesis_id": self.hypothesis_id,
            "strategy_family": self.strategy_family,
            "asset_class": self.asset_class,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "param_values": self.param_values,
            "vectorbt_run_id": self.vectorbt_run_id,
            "vectorbt_results": self.vectorbt_results,
            "pass_reason": self.pass_reason,
            "in_sample_results": self.in_sample_results,
            "out_of_sample_results": self.out_of_sample_results,
            "walk_forward_results": self.walk_forward_results,
            "robustness_metrics": self.robustness_metrics,
            "turnover_metrics": self.turnover_metrics,
            "drawdown_metrics": self.drawdown_metrics,
            "data_version": self.data_version,
            "git_commit": self.git_commit,
            "config_path": self.config_path,
            "execution_classification": self.execution_classification,
            "seed": self.seed,
            "timestamp_utc": self.timestamp_utc,
        }


@dataclass
class RejectedCandidate:
    candidate_id: str
    hypothesis_id: str
    reject_reason: str
    metric_values: Dict[str, Any] = field(default_factory=dict)
    vectorbt_results: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "hypothesis_id": self.hypothesis_id,
            "reject_reason": self.reject_reason,
            "metric_values": self.metric_values,
            "vectorbt_results": self.vectorbt_results,
        }


@dataclass
class PromotionGate:
    """Configurable thresholds. A candidate must pass ALL to reach HftBacktest."""
    min_oos_expectancy: float = 0.0
    min_walk_forward_consistency: float = 0.5
    max_turnover_pct: float = 200.0
    max_drawdown_pct: float = -30.0
    min_trades: int = 10
    param_stability_rtol: float = 0.3
    max_slippage_sensitivity: float = 0.5

    def evaluate(self, candidate: PromotedCandidate) -> bool:
        if candidate.vectorbt_results.get("oos_expectancy", 0.0) < self.min_oos_expectancy:
            return False
        if candidate.vectorbt_results.get("wf_consistency", 0.0) < self.min_walk_forward_consistency:
            return False
        if abs(candidate.vectorbt_results.get("max_drawdown_pct", 0.0)) > abs(self.max_drawdown_pct):
            return False
        if candidate.vectorbt_results.get("turnover_mean_pct", 0.0) > self.max_turnover_pct:
            return False
        if candidate.vectorbt_results.get("num_trades", 0) < self.min_trades:
            return False
        if candidate.vectorbt_results.get("param_stability_score", 0.0) < (1.0 - self.param_stability_rtol):
            return False
        if candidate.vectorbt_results.get("slippage_sensitivity", 0.0) > self.max_slippage_sensitivity:
            return False
        return True


def serialize_promoted(
    candidate: PromotedCandidate,
    out_dir: Optional[Path] = None,
) -> Path:
    out_dir = out_dir or (_REPO / "research_cards" / "promotion")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{candidate.candidate_id}.json"
    path.write_text(json.dumps(candidate.to_dict(), indent=2), encoding="utf-8")
    return path


def set_execution_classification(candidate_id: str, classification: str) -> bool:
    """Update the execution_classification field of a serialized PromotedCandidate."""
    path = (_REPO / "research_cards" / "promotion" / f"{candidate_id}.json")
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    data["execution_classification"] = classification
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)
    return True


def load_promoted(path: Path) -> PromotedCandidate:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return PromotedCandidate(
        candidate_id=raw["candidate_id"],
        hypothesis_id=raw["hypothesis_id"],
        strategy_family=raw["strategy_family"],
        asset_class=raw["asset_class"],
        symbol=raw["symbol"],
        timeframe=raw["timeframe"],
        param_values=raw["param_values"],
        vectorbt_run_id=raw["vectorbt_run_id"],
        vectorbt_results=raw["vectorbt_results"],
        pass_reason=raw["pass_reason"],
        in_sample_results=raw.get("in_sample_results", {}),
        out_of_sample_results=raw.get("out_of_sample_results", {}),
        walk_forward_results=raw.get("walk_forward_results", []),
        robustness_metrics=raw.get("robustness_metrics", {}),
        turnover_metrics=raw.get("turnover_metrics", {}),
        drawdown_metrics=raw.get("drawdown_metrics", {}),
        execution_classification=raw.get("execution_classification", ""),
        data_version=raw.get("data_version", ""),
        git_commit=raw.get("git_commit", ""),
        config_path=raw.get("config_path", ""),
        seed=raw.get("seed", 42),
        timestamp_utc=raw.get("timestamp_utc", ""),
    )
