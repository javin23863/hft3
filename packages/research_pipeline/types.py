"""Shared datatypes for the autoresearch pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


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
    source: str = "heuristic"  # heuristic | openai_compatible
    llm_status: Optional[str] = None  # hypothesis packet status when GPT-5.5 path used


@dataclass
class CandidateModel:
    candidate_id: str
    model_id: str
    strategy_params: Dict[str, Any]
    thesis: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GateThresholds:
    """Default thresholds are maximally permissive — pass everything unless customized."""
    min_net_pnl: float = -1e9
    min_trades: int = 0
    max_tail_loss: float = 1e9
    min_win_rate: float = 0.0
    min_sharpe: Optional[float] = None
    max_drawdown_bps: Optional[float] = None
    max_avg_latency_us: Optional[float] = None

    def passes(
        self,
        net_pnl: float,
        num_trades: int,
        tail_loss: float,
        win_rate: float,
        *,
        sharpe: Optional[float] = None,
        drawdown_bps: Optional[float] = None,
        avg_latency_us: Optional[float] = None,
    ) -> bool:
        if net_pnl < self.min_net_pnl:
            return False
        if num_trades < self.min_trades:
            return False
        if tail_loss > self.max_tail_loss:
            return False
        if win_rate < self.min_win_rate:
            return False
        if self.min_sharpe is not None:
            if sharpe is None or sharpe < self.min_sharpe:
                return False
        if (
            self.max_drawdown_bps is not None
            and (drawdown_bps is None or drawdown_bps > self.max_drawdown_bps)
        ):
            return False
        if (
            self.max_avg_latency_us is not None
            and (avg_latency_us is None or avg_latency_us > self.max_avg_latency_us)
        ):
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
    sharpe: Optional[float] = None
    drawdown_bps: Optional[float] = None
    avg_latency_us: Optional[float] = None
    workbench_out: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def passes_all_gates(self) -> bool:
        if self.error:
            return False
        return self.gates.passes(
            self.net_pnl,
            self.num_trades,
            self.tail_loss,
            self.win_rate,
            sharpe=self.sharpe,
            drawdown_bps=self.drawdown_bps,
            avg_latency_us=self.avg_latency_us,
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
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    document_summary: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "thesis": self.thesis,
            "event_id": self.event_id,
            "parsed": {
                "primary_model_id": self.parsed.primary_model_id,
                "instrument_universe": self.parsed.instrument_universe,
                "indicators": self.parsed.indicators,
                "source": self.parsed.source,
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
                    "net_pnl": r.net_pnl,
                    "num_trades": r.num_trades,
                    "sharpe": r.sharpe,
                    "drawdown_bps": r.drawdown_bps,
                    "avg_latency_us": r.avg_latency_us,
                    "passes": r.passes_all_gates(),
                    "error": r.error,
                }
                for r in self.results
            ],
        }
