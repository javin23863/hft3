"""Pipeline manifest schemas — VectorBT filter, HFT truth, pipeline run."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class EvidenceGrade(str, Enum):
    AUTHORITATIVE_EVIDENCE = "AUTHORITATIVE_EVIDENCE"
    NON_AUTHORITATIVE_PREFILTER = "NON_AUTHORITATIVE_PREFILTER"
    DEBUG_ONLY = "DEBUG_ONLY"
    FIXTURE_ONLY = "FIXTURE_ONLY"
    BLOCKED = "BLOCKED"
    FAILED_ACCOUNTING_RECONCILIATION = "FAILED_ACCOUNTING_RECONCILIATION"
    SMOKE_E2E_SINGLE_EVENT = "SMOKE_E2E_SINGLE_EVENT"
    PARTIAL_HFT_TRUTH_DEBUG_ONLY = "PARTIAL_HFT_TRUTH_DEBUG_ONLY"


class EngineKind(str, Enum):
    VECTORBT = "VECTORBT"
    NUMPY_FALLBACK = "NUMPY_FALLBACK"
    REPLAY_SESSION_HFTBACKTEST = "REPLAY_SESSION_HFTBACKTEST"
    TOY_STRATEGY = "TOY_STRATEGY"
    UNKNOWN = "UNKNOWN"


class SignalSource(str, Enum):
    MODEL_CATALOG_MSP = "MODEL_CATALOG_MSP"
    TOY_ALWAYS_LONG = "TOY_ALWAYS_LONG"
    COMBINED_HYPOTHESIS = "COMBINED_HYPOTHESIS"
    UNRESOLVED = "UNRESOLVED"


class ReconciliationStatus(str, Enum):
    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class StageStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"


_ENUM_FIELDS = {
    "VectorbtFilterManifest": [
        ("engine_requested", EngineKind), ("engine_used", EngineKind),
        ("evidence_status", EvidenceGrade), ("signal_source", SignalSource),
    ],
    "HftTruthManifest": [
        ("engine_requested", EngineKind), ("engine_used", EngineKind),
        ("evidence_status", EvidenceGrade),
        ("reconciliation_status", ReconciliationStatus),
    ],
    "PipelineManifest": [
        ("evidence_grade", EvidenceGrade),
    ],
}


def _coerce_enums(obj: Any) -> None:
    cls_name = type(obj).__name__
    for field_name, enum_cls in _ENUM_FIELDS.get(cls_name, []):
        v = getattr(obj, field_name, "")
        if isinstance(v, Enum):
            object.__setattr__(obj, field_name, v.value)


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
    promotion_eligible: bool = False
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
    walk_forward_period: str = ""
    tuning_skipped_reason: str = ""
    engine_requested: str = EngineKind.VECTORBT.value
    engine_used: str = ""
    evidence_status: str = ""
    signal_source: str = ""
    signal_model_id: str = ""

    def __post_init__(self) -> None:
        _coerce_enums(self)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


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
    promotion_eligible: bool = False
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
    engine_requested: str = EngineKind.REPLAY_SESSION_HFTBACKTEST.value
    engine_used: str = ""
    evidence_status: str = ""
    max_steps_set: Optional[int] = None
    total_steps_available: Optional[int] = None
    ledger_paths: Dict[str, str] = field(default_factory=dict)
    reconciliation_status: str = ReconciliationStatus.PENDING.value
    pnl_reconciliation_pass: Optional[bool] = None
    trade_count_reconciliation_pass: Optional[bool] = None
    position_reconciliation_pass: Optional[bool] = None
    pnl_from_fills: Optional[float] = None
    pnl_from_account: Optional[float] = None

    def __post_init__(self) -> None:
        _coerce_enums(self)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


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
    promotion_eligible: bool = False
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
    single_event_smoke: bool = False
    evidence_grade: str = ""

    def __post_init__(self) -> None:
        _coerce_enums(self)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["stages"] = {k: (v.value if isinstance(v, Enum) else v) for k, v in self.stages.items()}
        d["vectorbt_manifest"] = self.vectorbt_manifest.to_dict() if self.vectorbt_manifest else None
        d["hft_truth_manifest"] = self.hft_truth_manifest.to_dict() if self.hft_truth_manifest else None
        return d
