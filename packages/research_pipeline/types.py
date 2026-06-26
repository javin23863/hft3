"""Shared datatypes for the autoresearch pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def signed_tail_loss_value(tail_loss: float) -> float:
    """Normalize tail loss to signed tail-PnL, accepting legacy positive loss magnitudes."""
    value = float(tail_loss)
    return -value if value > 0.0 else value


@dataclass
class ParsedHypothesis:
    thesis: str
    instrument_universe: List[str]
    entry_rules: List[str]
    exit_rules: List[str]
    indicators: List[str]
    feature_list: List[str]
    param_ranges: Dict[str, List[float]]
    primary_model_id: str
    source: str = "heuristic"  # heuristic | openai_compatible | hypothesis_packet
    llm_status: Optional[str] = None  # hypothesis packet status when GPT-5.5 path used
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CandidateModel:
    candidate_id: str
    model_id: str
    strategy_params: Dict[str, Any]
    thesis: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    feature_recipe: Optional[Dict[str, Any]] = None
    feature_recipe_hash: Optional[str] = None
    target_symbol: str = "MES"
    research_clock: str = "scheduled_event"
    target_event_id: Optional[str] = None


@dataclass
class GateThresholds:
    """Default thresholds are maximally permissive — pass everything unless customized."""
    min_net_pnl: float = -1e9
    min_trades: int = 0
    # Historical name; accepts a signed tail-PnL floor or legacy positive loss cap.
    max_tail_loss: float = -1e9
    min_win_rate: float = 0.0
    min_sharpe: float = -1e9
    min_sortino: float = -1e9
    max_drawdown: float = 1e9
    min_psr: Optional[float] = None
    min_dsr: Optional[float] = None
    max_pbo: Optional[float] = None
    min_tail_ratio: Optional[float] = None
    max_cvar_95: Optional[float] = None
    max_cvar_99: Optional[float] = None
    require_sample_size: bool = False
    min_observations: Optional[int] = None

    def requires_gateable_risk_metrics(self) -> bool:
        return (
            self.min_sharpe > -1e9
            or self.min_sortino > -1e9
            or self.max_drawdown < 1e9
        )

    def signed_tail_loss_floor(self) -> float:
        """Return the signed tail-PnL floor used for gate comparisons."""
        threshold = float(self.max_tail_loss)
        return signed_tail_loss_value(threshold)

    def passes(
        self,
        net_pnl: float,
        num_trades: int,
        tail_loss: float,
        win_rate: float,
        *,
        sharpe: float = 0.0,
        sortino: float = 0.0,
        max_drawdown: float = 0.0,
        include_risk_metrics: bool = True,
        include_edge_metrics: bool = True,
        psr: Optional[float] = None,
        dsr: Optional[float] = None,
        pbo: Optional[float] = None,
        tail_ratio: Optional[float] = None,
        cvar_95: Optional[float] = None,
        cvar_99: Optional[float] = None,
        sample_size_pass: bool = True,
        observations: Optional[int] = None,
    ) -> bool:
        if net_pnl < self.min_net_pnl:
            return False
        if num_trades < self.min_trades:
            return False
        if signed_tail_loss_value(tail_loss) < self.signed_tail_loss_floor():
            return False
        if win_rate < self.min_win_rate:
            return False
        if include_edge_metrics:
            if self.min_psr is not None and (psr is None or psr < self.min_psr):
                return False
            if self.min_dsr is not None and (dsr is None or dsr < self.min_dsr):
                return False
            if self.max_pbo is not None and (pbo is None or pbo > self.max_pbo):
                return False
            if self.min_tail_ratio is not None and (tail_ratio is None or tail_ratio < self.min_tail_ratio):
                return False
            if self.max_cvar_95 is not None and (cvar_95 is None or cvar_95 > self.max_cvar_95):
                return False
            if self.max_cvar_99 is not None and (cvar_99 is None or cvar_99 > self.max_cvar_99):
                return False
            if self.require_sample_size and not sample_size_pass:
                return False
            if self.min_observations is not None and (observations is None or observations < self.min_observations):
                return False
        if not include_risk_metrics:
            return True
        if sharpe < self.min_sharpe:
            return False
        if sortino < self.min_sortino:
            return False
        if max_drawdown > self.max_drawdown:
            return False
        return True


@dataclass
class EvaluationResult:
    candidate: CandidateModel
    event_id: str
    net_pnl: float
    num_trades: int
    win_rate: float
    expectancy: float
    tail_loss: float
    gates: GateThresholds
    sharpe: Optional[float] = 0.0
    sortino: Optional[float] = 0.0
    max_drawdown: float = 0.0
    risk_metrics_source: str = "not_computed"
    risk_metrics_gateable: bool = False
    risk_metric_warning: Optional[str] = None
    event_results: List[Dict[str, Any]] = field(default_factory=list)
    workbench_out: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    gross_pnl: Optional[float] = None
    cost_total: float = 0.0
    cost_breakdown: Dict[str, float] = field(default_factory=dict)
    pnl_series: List[float] = field(default_factory=list)
    gross_pnl_series: List[float] = field(default_factory=list)
    psr: Optional[float] = None
    dsr: Optional[float] = None
    adjusted_p_value: Optional[float] = None
    trial_sr_variance: Optional[float] = None
    num_trials: int = 1
    required_sample_size: Optional[int] = None
    sample_size_pass: bool = True
    skew: Optional[float] = None
    kurtosis: Optional[float] = None
    cvar_95: Optional[float] = None
    cvar_99: Optional[float] = None
    tail_ratio: Optional[float] = None
    turnover: Optional[float] = None
    avg_trade_duration: Optional[float] = None
    pbo: Optional[float] = None
    performance_degradation: Optional[float] = None
    probability_of_loss: Optional[float] = None
    regime_metrics: Dict[str, Dict[str, float]] = field(default_factory=dict)
    instrument_metrics: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def passes_all_gates(self) -> bool:
        if self.error:
            return False
        cont = (self.workbench_out or {}).get("continuous_evaluation")
        if isinstance(cont, dict) and cont.get("lane") == "continuous_microstructure":
            if cont.get("status") != "evaluated":
                return False
            gate = cont.get("gates") or {}
            return bool(gate.get("all_pass"))
        if self.gates.requires_gateable_risk_metrics() and not self.risk_metrics_gateable:
            return False
        return self.gates.passes(
            self.net_pnl,
            self.num_trades,
            self.tail_loss,
            self.win_rate,
            sharpe=self.sharpe or 0.0,
            sortino=self.sortino or 0.0,
            max_drawdown=self.max_drawdown,
            include_risk_metrics=self.risk_metrics_gateable,
            psr=self.psr,
            dsr=self.dsr,
            pbo=self.pbo,
            tail_ratio=self.tail_ratio,
            cvar_95=self.cvar_95,
            cvar_99=self.cvar_99,
            sample_size_pass=self.sample_size_pass,
            observations=len(self.pnl_series) if self.pnl_series else self.num_trades,
        )


@dataclass
class PipelineReport:
    run_id: str
    thesis: str
    event_id: str
    parsed: ParsedHypothesis
    candidates_tested: int
    results: List[EvaluationResult]
    selected: Optional[CandidateModel]
    artifact_dir: Optional[str]
    event_ids: List[str] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    document_summary: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "thesis": self.thesis,
            "event_id": self.event_id,
            "event_ids": list(self.event_ids or [self.event_id]),
            "parsed": {
                "primary_model_id": self.parsed.primary_model_id,
                "instrument_universe": self.parsed.instrument_universe,
                "indicators": self.parsed.indicators,
                "source": self.parsed.source,
                "metadata": self.parsed.metadata,
            },
            "candidates_tested": self.candidates_tested,
            "selected_model_id": self.selected.model_id if self.selected else None,
            "artifact_dir": self.artifact_dir,
            "generated_at": self.generated_at,
            "document_summary": self.document_summary,
            "results": [
                {
                    "candidate_id": r.candidate.candidate_id,
                    "model_id": r.candidate.model_id,
                    "gross_pnl": r.gross_pnl,
                    "net_pnl": r.net_pnl,
                    "cost_total": r.cost_total,
                    "num_trades": r.num_trades,
                    "psr": r.psr,
                    "dsr": r.dsr,
                    "adjusted_p_value": r.adjusted_p_value,
                    "pbo": r.pbo,
                    "performance_degradation": r.performance_degradation,
                    "probability_of_loss": r.probability_of_loss,
                    "sample_size_pass": r.sample_size_pass,
                    "required_sample_size": r.required_sample_size,
                    "skew": r.skew,
                    "kurtosis": r.kurtosis,
                    "cvar_95": r.cvar_95,
                    "cvar_99": r.cvar_99,
                    "tail_ratio": r.tail_ratio,
                    "num_trials": r.num_trials,
                    "trial_sr_variance": r.trial_sr_variance,
                    "passes": r.passes_all_gates(),
                    "sharpe": r.sharpe,
                    "sortino": r.sortino,
                    "max_drawdown": r.max_drawdown,
                    "risk_metrics_source": r.risk_metrics_source,
                    "risk_metrics_gateable": r.risk_metrics_gateable,
                    "risk_metric_warning": r.risk_metric_warning,
                    "error": r.error,
                    "event_results": r.event_results,
                }
                for r in self.results
            ],
        }
