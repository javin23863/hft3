"""Pydantic v2 models for the 14-file research intake bundle (Phase 3).

Every JSON file the LLM (or post-processing) emits is typed here. The
validator also enforces the quarantine rules from the 26-phase spec.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class Equation(BaseModel):
    eq_id: str
    latex: str
    context: str
    source_page: Optional[int] = None
    variables: List[str] = Field(default_factory=list)


class Table(BaseModel):
    table_id: str
    caption: str
    headers: List[str]
    rows: List[List[str]]
    source_page: Optional[int] = None


class ThesisSummary(BaseModel):
    main_thesis: str
    instrument_scope: List[str] = Field(default_factory=list)
    time_horizon: str = ""
    confidence: Optional[float] = None


class Assumption(BaseModel):
    assumption_id: str
    statement: str
    category: str
    evidence: Optional[str] = None
    criticality: Optional[Literal["low", "med", "high"]] = None


class DataRequirement(BaseModel):
    data_id: str
    name: str
    source: str
    granularity: Optional[str] = None
    history_years: Optional[float] = None
    vendor: Optional[str] = None


class FeatureRequirement(BaseModel):
    feature_id: str
    name: str
    definition: str
    feature_engine_slug: Optional[str] = None
    group: Optional[str] = None


class SignalLogic(BaseModel):
    signal: str
    entry: List[str] = Field(default_factory=list)
    exit: List[str] = Field(default_factory=list)
    time_stop_bars: Optional[int] = None
    regime_filter: Optional[str] = None


class ExecutionLogic(BaseModel):
    order_types: List[str]
    latency_budget_us: Optional[int] = None
    cost_model: Optional[str] = None
    slippage_bps: Optional[float] = None
    max_position: Optional[int] = None
    venue: Optional[str] = None


class ParameterRange(BaseModel):
    param: str
    min: float
    max: float
    default: float
    units: Optional[str] = None
    distribution: Optional[Literal["uniform", "log", "beta"]] = None


class FailureMode(BaseModel):
    mode_id: str
    condition: str
    severity: Literal["low", "med", "high"]
    mitigation: Optional[str] = None
    detection_signal: Optional[str] = None


class TestableHypothesis(BaseModel):
    # Pytest tries to collect classes whose name starts with "Test".
    # Disable that for the pydantic model.
    __test__ = False

    hypothesis_id: str
    statement: str
    pass_criteria: str
    fail_criteria: str
    metric: Optional[str] = None
    threshold: Optional[float] = None
    evaluation_window: Optional[str] = None


class ExperimentTranslationNotes(BaseModel):
    missing_info: List[str] = Field(default_factory=list)
    hft3_implementation_reqs: List[str] = Field(default_factory=list)
    quarantine: bool = False
    quarantine_reasons: List[str] = Field(default_factory=list)
    confidence: Optional[float] = None

    @model_validator(mode="after")
    def detect_quarantine(self) -> "ExperimentTranslationNotes":
        if self.quarantine:
            return self
        reasons: list[str] = []
        if not self.hft3_implementation_reqs:
            reasons.append("no_hft3_implementation_reqs")
        if not self.missing_info and not self.hft3_implementation_reqs:
            reasons.append("empty_translation_notes")
        if reasons:
            self.quarantine = True
            self.quarantine_reasons = reasons
        return self


def detect_intake_quarantine(
    thesis: ThesisSummary,
    signal: SignalLogic,
    parameters: List[ParameterRange],
    hypotheses: List[TestableHypothesis],
    notes: ExperimentTranslationNotes,
) -> list[str]:
    """Return a list of quarantine reasons. Empty list = no quarantine."""
    reasons: list[str] = []
    if not thesis.main_thesis or len(thesis.main_thesis) < 30:
        reasons.append("thesis_under_30_chars")
    if not thesis.instrument_scope:
        reasons.append("no_instrument_scope")
    if not signal.signal and not signal.entry:
        reasons.append("empty_signal_logic")
    if not parameters:
        reasons.append("no_parameter_ranges")
    else:
        for p in parameters:
            if p.min >= p.max:
                reasons.append(f"param_range_invalid:{p.param}")
    if not hypotheses:
        reasons.append("no_testable_hypotheses")
    else:
        for h in hypotheses:
            if h.threshold is None and not h.metric:
                reasons.append(f"hypothesis_no_numeric_threshold:{h.hypothesis_id}")
    return reasons
