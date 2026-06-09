"""Robustness manifest: complete record of a robustness run.

Every run produces a manifest recording what was tried, what worked,
what failed, and why.  The format is lane-aware.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class RobustnessManifest:
    """Complete record of a single robustness run."""

    # Identity
    run_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    repo_commit: str = ""
    lane_id: str = ""

    # Model binding
    model_id: str = ""

    # Lane-specific session/symbol/group
    symbol: str = ""
    session_id: str = ""
    session_date: str = ""
    catalyst: str = ""
    group_id: str = ""

    # Data sources
    data_sources: dict[str, str] = field(default_factory=dict)
    daily_path: str = ""
    float_metadata_path: str = ""
    raw_mbo_path: str = ""
    normalized_mbo_path: str = ""
    option_feature_path: str = ""
    dataset_hashes: dict[str, str] = field(default_factory=dict)

    # Data status
    daily_coverage: str = "unknown"
    float_as_of: str = "unknown"
    l3_status: str = "unknown"
    degraded_status: str = "unknown"

    # Search space
    search_space_version: str = ""
    features_tested: list[str] = field(default_factory=list)
    parameters_tested: list[dict[str, Any]] = field(default_factory=list)
    windows_tested: list[str] = field(default_factory=list)

    # Walk-forward
    sessions_tested: list[str] = field(default_factory=list)
    cohorts_tested: list[str] = field(default_factory=list)
    years_tested: list[int] = field(default_factory=list)
    catalysts_tested: list[str] = field(default_factory=list)
    negative_controls_tested: list[str] = field(default_factory=list)
    walk_forward_folds: int = 0

    # Pipeline stages
    binding_valid: bool = True
    binding_errors: list[str] = field(default_factory=list)
    data_inventory: dict[str, str] = field(default_factory=dict)
    discovery_results: list[dict[str, Any]] = field(default_factory=list)
    wfc_results: dict[str, Any] = field(default_factory=dict)
    confirmation_results: dict[str, Any] = field(default_factory=dict)
    holdout_results: dict[str, Any] = field(default_factory=dict)

    # Selected candidate
    selected_candidate: str = ""
    selection_reason: str = ""
    frozen_parameters: dict[str, Any] = field(default_factory=dict)

    # Execution
    execution_assumptions: dict[str, Any] = field(default_factory=dict)
    cost_assumptions: dict[str, Any] = field(default_factory=dict)
    latency_assumptions: dict[str, Any] = field(default_factory=dict)

    # Integrity
    pit_status: str = "unknown"
    leakage_status: str = "unknown"
    option_pit_status: str = "unknown"

    # Route
    route_type: str = ""
    route_reason_codes: list[str] = field(default_factory=list)
    route_status: str = "unknown"

    # Final decisions
    robustness_status: str = "unknown"
    champion_status: str = ""
    edge_status: str = ""  # EDGE_FOUND | NO_EDGE_FOUND | BLOCKED | etc.
    edge_explanation: str = ""
    no_edge_reason: str = ""
    failure_modes: list[str] = field(default_factory=list)
    blocking_reasons: list[str] = field(default_factory=list)
    next_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "repo_commit": self.repo_commit,
            "lane_id": self.lane_id,
            "model_id": self.model_id,
            "symbol": self.symbol,
            "session_id": self.session_id,
            "session_date": self.session_date,
            "catalyst": self.catalyst,
            "group_id": self.group_id,
            "data_sources": self.data_sources,
            "daily_path": self.daily_path,
            "float_metadata_path": self.float_metadata_path,
            "raw_mbo_path": self.raw_mbo_path,
            "normalized_mbo_path": self.normalized_mbo_path,
            "option_feature_path": self.option_feature_path,
            "dataset_hashes": self.dataset_hashes,
            "daily_coverage": self.daily_coverage,
            "float_as_of": self.float_as_of,
            "l3_status": self.l3_status,
            "degraded_status": self.degraded_status,
            "search_space_version": self.search_space_version,
            "features_tested": self.features_tested,
            "parameters_tested": self.parameters_tested,
            "windows_tested": self.windows_tested,
            "sessions_tested": self.sessions_tested,
            "cohorts_tested": self.cohorts_tested,
            "years_tested": self.years_tested,
            "catalysts_tested": self.catalysts_tested,
            "negative_controls_tested": self.negative_controls_tested,
            "walk_forward_folds": self.walk_forward_folds,
            "binding_valid": self.binding_valid,
            "binding_errors": self.binding_errors,
            "data_inventory": self.data_inventory,
            "discovery_results": self.discovery_results,
            "wfc_results": self.wfc_results,
            "confirmation_results": self.confirmation_results,
            "holdout_results": self.holdout_results,
            "selected_candidate": self.selected_candidate,
            "selection_reason": self.selection_reason,
            "frozen_parameters": self.frozen_parameters,
            "execution_assumptions": self.execution_assumptions,
            "cost_assumptions": self.cost_assumptions,
            "latency_assumptions": self.latency_assumptions,
            "pit_status": self.pit_status,
            "leakage_status": self.leakage_status,
            "option_pit_status": self.option_pit_status,
            "route_type": self.route_type,
            "route_reason_codes": self.route_reason_codes,
            "route_status": self.route_status,
            "robustness_status": self.robustness_status,
            "champion_status": self.champion_status,
            "edge_status": self.edge_status,
            "edge_explanation": self.edge_explanation,
            "no_edge_reason": self.no_edge_reason,
            "failure_modes": self.failure_modes,
            "blocking_reasons": self.blocking_reasons,
            "next_action": self.next_action,
        }

    def write(self, path: Path) -> None:
        import json
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")


def create_cme_manifest(
    run_id: str,
    repo_commit: str,
    model_id: str,
    symbol: str,
    **kwargs: Any,
) -> RobustnessManifest:
    return RobustnessManifest(
        run_id=run_id,
        repo_commit=repo_commit,
        lane_id="cme_futures",
        model_id=model_id,
        symbol=symbol,
        **kwargs,
    )


def create_equities_manifest(
    run_id: str,
    repo_commit: str,
    model_id: str,
    session_id: str,
    symbol: str,
    date: str,
    catalyst: str,
    **kwargs: Any,
) -> RobustnessManifest:
    return RobustnessManifest(
        run_id=run_id,
        repo_commit=repo_commit,
        lane_id="equities_low_float",
        model_id=model_id,
        session_id=session_id,
        symbol=symbol,
        session_date=date,
        catalyst=catalyst,
        **kwargs,
    )


def create_options_manifest(
    run_id: str,
    repo_commit: str,
    model_id: str,
    group_id: str,
    **kwargs: Any,
) -> RobustnessManifest:
    return RobustnessManifest(
        run_id=run_id,
        repo_commit=repo_commit,
        lane_id="options_parity",
        model_id=model_id,
        group_id=group_id,
        **kwargs,
    )
