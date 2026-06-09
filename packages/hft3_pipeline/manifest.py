"""Pipeline manifest schemas — VectorBT filter, HFT truth, pipeline run."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class StageStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"


@dataclass
class VectorbtFilterManifest:
    run_id: str = ""
    created_at: str = ""
    repo_commit: str = ""
    lane_id: str = ""
    model_id: str = ""
    symbol: str = ""
    session_id: str = ""
    group_id: str = ""
    run_mode: str = "REAL_RESEARCH"
    promotion_eligible: bool = True
    data_artifacts: List[str] = field(default_factory=list)
    feature_artifacts: List[str] = field(default_factory=list)
    feature_hashes: Dict[str, str] = field(default_factory=dict)
    search_space_id: str = ""
    search_space_version: str = ""
    parameter_count: int = 0
    parameters_tested: int = 0
    entry_logic: str = ""
    exit_logic: str = ""
    fast_metric_names: List[str] = field(default_factory=list)
    fast_results: List[Dict[str, Any]] = field(default_factory=list)
    top_candidates: List[Dict[str, Any]] = field(default_factory=list)
    rejected_candidates_summary: List[Dict[str, Any]] = field(default_factory=list)
    selection_policy: str = "top_n_by_deflated_sharpe"
    top_n_forwarded: int = 0
    hftbacktest_required: bool = True
    pit_status: str = "PASS"
    leakage_status: str = "PASS"
    missing_reasons: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    next_action: str = "run_hftbacktest_truth"
    vectorbt_available: bool = False
    backend: str = ""
    time_taken_sec: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "repo_commit": self.repo_commit,
            "lane_id": self.lane_id,
            "model_id": self.model_id,
            "symbol": self.symbol,
            "session_id": self.session_id,
            "group_id": self.group_id,
            "run_mode": self.run_mode,
            "promotion_eligible": self.promotion_eligible,
            "data_artifacts": self.data_artifacts,
            "feature_artifacts": self.feature_artifacts,
            "feature_hashes": self.feature_hashes,
            "search_space_id": self.search_space_id,
            "search_space_version": self.search_space_version,
            "parameter_count": self.parameter_count,
            "parameters_tested": self.parameters_tested,
            "entry_logic": self.entry_logic,
            "exit_logic": self.exit_logic,
            "fast_metric_names": self.fast_metric_names,
            "fast_results": self.fast_results,
            "top_candidates": self.top_candidates,
            "rejected_candidates_summary": self.rejected_candidates_summary,
            "selection_policy": self.selection_policy,
            "top_n_forwarded": self.top_n_forwarded,
            "hftbacktest_required": self.hftbacktest_required,
            "pit_status": self.pit_status,
            "leakage_status": self.leakage_status,
            "missing_reasons": self.missing_reasons,
            "warnings": self.warnings,
            "next_action": self.next_action,
            "vectorbt_available": self.vectorbt_available,
            "backend": self.backend,
            "time_taken_sec": self.time_taken_sec,
        }


@dataclass
class HftTruthManifest:
    run_id: str = ""
    parent_vectorbt_run_id: str = ""
    candidate_id: str = ""
    parameter_set_id: str = ""
    lane_id: str = ""
    model_id: str = ""
    symbol: str = ""
    event_id: str = ""
    session_id: str = ""
    group_id: str = ""
    run_mode: str = "REAL_RESEARCH"
    promotion_eligible: bool = True
    feature_artifacts: List[str] = field(default_factory=list)
    hftbacktest_config: Dict[str, Any] = field(default_factory=dict)
    latency_config: Dict[str, Any] = field(default_factory=dict)
    queue_model: str = ""
    fill_model: str = ""
    fee_model: str = ""
    slippage_model: str = ""
    orders: int = 0
    fills: int = 0
    pnl: float = 0.0
    trades: int = 0
    positions: List[Dict[str, Any]] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    execution_realism: Dict[str, Any] = field(default_factory=dict)
    vectorbt_vs_hft_delta: Optional[Dict[str, Any]] = None
    divergence_reason: str = ""
    rejection_reason: str = ""
    promotion_eligible_reason: str = ""
    next_action: str = ""
    synthetic_data_used: bool = False
    fixture_data_used: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "parent_vectorbt_run_id": self.parent_vectorbt_run_id,
            "candidate_id": self.candidate_id,
            "parameter_set_id": self.parameter_set_id,
            "lane_id": self.lane_id,
            "model_id": self.model_id,
            "symbol": self.symbol,
            "event_id": self.event_id,
            "session_id": self.session_id,
            "group_id": self.group_id,
            "run_mode": self.run_mode,
            "promotion_eligible": self.promotion_eligible,
            "feature_artifacts": self.feature_artifacts,
            "hftbacktest_config": self.hftbacktest_config,
            "latency_config": self.latency_config,
            "queue_model": self.queue_model,
            "fill_model": self.fill_model,
            "fee_model": self.fee_model,
            "slippage_model": self.slippage_model,
            "orders": self.orders,
            "fills": self.fills,
            "pnl": self.pnl,
            "trades": self.trades,
            "positions": self.positions,
            "metrics": self.metrics,
            "execution_realism": self.execution_realism,
            "vectorbt_vs_hft_delta": self.vectorbt_vs_hft_delta,
            "divergence_reason": self.divergence_reason,
            "rejection_reason": self.rejection_reason,
            "promotion_eligible_reason": self.promotion_eligible_reason,
            "next_action": self.next_action,
            "synthetic_data_used": self.synthetic_data_used,
            "fixture_data_used": self.fixture_data_used,
        }


@dataclass
class PipelineManifest:
    run_id: str = ""
    created_at: str = ""
    repo_commit: str = ""
    lane_id: str = ""
    model_id: str = ""
    symbol: str = ""
    event_id: str = ""
    session_id: str = ""
    group_id: str = ""
    run_mode: str = "REAL_RESEARCH"
    promotion_eligible: bool = True
    synthetic_data_used: bool = False
    fixture_data_used: bool = False
    reason: str = ""
    stages: Dict[str, StageStatus] = field(default_factory=dict)
    vectorbt_manifest: Optional[VectorbtFilterManifest] = None
    hft_truth_manifest: Optional[HftTruthManifest] = None
    scorecard: Dict[str, Any] = field(default_factory=dict)
    promotion_status: str = "PENDING"
    trade_manager_status: str = "PENDING"
    session_id_live: str = ""
    blockers: List[str] = field(default_factory=list)
    next_action: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "repo_commit": self.repo_commit,
            "lane_id": self.lane_id,
            "model_id": self.model_id,
            "symbol": self.symbol,
            "event_id": self.event_id,
            "session_id": self.session_id,
            "group_id": self.group_id,
            "run_mode": self.run_mode,
            "promotion_eligible": self.promotion_eligible,
            "synthetic_data_used": self.synthetic_data_used,
            "fixture_data_used": self.fixture_data_used,
            "reason": self.reason,
            "stages": {k: v.value for k, v in self.stages.items()},
            "vectorbt_manifest": self.vectorbt_manifest.to_dict() if self.vectorbt_manifest else None,
            "hft_truth_manifest": self.hft_truth_manifest.to_dict() if self.hft_truth_manifest else None,
            "scorecard": self.scorecard,
            "promotion_status": self.promotion_status,
            "trade_manager_status": self.trade_manager_status,
            "session_id_live": self.session_id_live,
            "blockers": self.blockers,
            "next_action": self.next_action,
        }
