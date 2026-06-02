"""Automated research pipeline — NL hypothesis to backtest artifacts."""

from research_pipeline.types import (
    CandidateModel,
    EvaluationResult,
    GateThresholds,
    ParsedHypothesis,
    PipelineReport,
)
from research_pipeline.intake_bundle import (
    BUNDLE_FILES,
    is_quarantined,
    load_intake_bundle,
    write_intake_bundle,
)
from research_pipeline.intake_schema import (
    Assumption,
    DataRequirement,
    Equation,
    ExecutionLogic,
    ExperimentTranslationNotes,
    FailureMode,
    FeatureRequirement,
    ParameterRange,
    SignalLogic,
    Table,
    TestableHypothesis,
    ThesisSummary,
)

__all__ = [
    "CandidateModel",
    "EvaluationResult",
    "GateThresholds",
    "ParsedHypothesis",
    "PipelineReport",
    "write_intake_bundle",
    "is_quarantined",
    "load_intake_bundle",
    "BUNDLE_FILES",
    "ThesisSummary",
    "Assumption",
    "DataRequirement",
    "FeatureRequirement",
    "SignalLogic",
    "ExecutionLogic",
    "ParameterRange",
    "FailureMode",
    "TestableHypothesis",
    "ExperimentTranslationNotes",
    "Equation",
    "Table",
]
