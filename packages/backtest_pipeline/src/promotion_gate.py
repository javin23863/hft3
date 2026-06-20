"""Promotion gate and candidate artifact — sits between VectorBT filter and HftBacktest.

Traces to integration spec: every promoted candidate must carry full traceable metadata.
A candidate must not reach HftBacktest without this artifact.

Authority: docs/vault/UNIFIED_RESEARCH_PIPELINE.md (Stage 2)
Vault: library/13 Robust Backtesting and Multiple Testing.md
Literature: docs/references/Ultimate_Quantitative_Finance_Researcher.pdf (DSR/PBO/CSCV);
  docs/project/ROBUSTNESS_TESTING_SPEC.md
Lifecycle: promoted rows carry hypothesis_id for Stage 5 enrollment via research_pipeline_stages.
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

    def evaluate_failures(self, candidate: PromotedCandidate) -> List[str]:
        """Return explicit gate failure codes; empty list means pass."""
        metrics = candidate.vectorbt_results or {}
        failures: List[str] = []

        oos_expectancy = metrics.get("oos_expectancy")
        if oos_expectancy is None:
            failures.append("missing_oos_expectancy")
        elif float(oos_expectancy) < self.min_oos_expectancy:
            failures.append("oos_expectancy_below_threshold")

        wf_consistency = metrics.get("wf_consistency")
        if wf_consistency is None:
            failures.append("missing_wf_consistency")
        elif float(wf_consistency) < self.min_walk_forward_consistency:
            failures.append("wf_consistency_below_threshold")

        max_drawdown_pct = metrics.get("max_drawdown_pct")
        if max_drawdown_pct is None:
            failures.append("missing_max_drawdown_pct")
        elif abs(float(max_drawdown_pct)) > abs(self.max_drawdown_pct):
            failures.append("max_drawdown_above_threshold")

        turnover_mean_pct = metrics.get("turnover_mean_pct")
        if turnover_mean_pct is None:
            failures.append("missing_turnover_mean_pct")
        elif float(turnover_mean_pct) > self.max_turnover_pct:
            failures.append("turnover_above_threshold")

        num_trades = metrics.get("num_trades")
        if num_trades is None:
            failures.append("missing_num_trades")
        elif int(num_trades) < self.min_trades:
            failures.append("num_trades_below_threshold")

        param_stability_score = metrics.get("param_stability_score")
        min_param_stability = 1.0 - self.param_stability_rtol
        if param_stability_score is None:
            failures.append("missing_param_stability_score")
        elif float(param_stability_score) < min_param_stability:
            failures.append("param_stability_below_threshold")

        slippage_sensitivity = metrics.get("slippage_sensitivity")
        if slippage_sensitivity is None:
            failures.append("missing_slippage_sensitivity")
        elif float(slippage_sensitivity) > self.max_slippage_sensitivity:
            failures.append("slippage_sensitivity_above_threshold")

        return failures

    def evaluate(self, candidate: PromotedCandidate) -> bool:
        return not self.evaluate_failures(candidate)


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
