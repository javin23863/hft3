"""Typed structures for the paid-screen execution path.

Phase 2 deliverable: structured execution path.
PaidScreenUnit replaces thesis-based NL parsing with structured manifest fields.
BatchingKey determines which units can safely share a batch.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
import hashlib
import json

from backtest_pipeline.src.research_clock import (
    RESEARCH_CLOCK_SCHEDULED_EVENT,
    validate_research_clock,
)


TARGET_ONLY_CONTEXT_SET_ID = "target_only"
CONTEXT_SET_VALUES: frozenset[str] = frozenset(
    {
        TARGET_ONLY_CONTEXT_SET_ID,
        "target_plus_macro",
        "target_plus_vix_vvix",
        "target_plus_vix_options",
        "target_plus_cme_options",
        "target_plus_cross_asset",
        "target_plus_continuous_session",
        "target_plus_latency",
        "full_available_context",
        "full_required_context",
        "negative_controls",
    }
)


class ContextSetError(ValueError):
    """Raised when a context-set label is outside the closed plan ontology."""


def validate_context_set_id(value: object, *, context: str = "context_set_id") -> str:
    """Return a canonical context-set ID or raise ``ContextSetError``."""
    normalized = str(value or "").strip().lower().replace("-", "_")
    if not normalized:
        raise ContextSetError(f"{context}:context_set_id_empty")
    if normalized not in CONTEXT_SET_VALUES:
        allowed = sorted(CONTEXT_SET_VALUES)
        raise ContextSetError(
            f"{context}:context_set_id_invalid:{value}; allowed={allowed}"
        )
    return normalized


@dataclass(frozen=True)
class PaidScreenUnit:
    """One logical work unit: (model_id, symbol, event_id).

    Structured fields determine execution — not thesis text.
    The thesis remains as descriptive metadata only.
    """
    unit_id: str
    model_id: str
    hyp_id: int | None
    symbol: str
    event_id: str
    event_type: str
    feature_set_id: str | None = None
    research_split: str | None = None
    thesis: str = ""
    research_clock: str = "scheduled_event"
    context_set_id: str = "target_only"
    declared_context_sets: tuple[str, ...] | None = None
    ablation_group_id: str | None = None
    negative_control_policy: Any | None = None

    def __post_init__(self) -> None:
        research_clock = validate_research_clock(self.research_clock)
        context_set_id = validate_context_set_id(self.context_set_id)
        declared_values = self.declared_context_sets or ()
        declared = tuple(
            validate_context_set_id(value, context="declared_context_sets")
            for value in declared_values
            if str(value).strip()
        )
        if not declared:
            declared = self._default_declared_context_sets(context_set_id)
        if context_set_id not in declared:
            raise ContextSetError(
                "declared_context_sets_missing_context_set_id:"
                f"{context_set_id}"
            )
        object.__setattr__(self, "research_clock", research_clock)
        object.__setattr__(self, "context_set_id", context_set_id)
        object.__setattr__(self, "declared_context_sets", declared)

    def identity_hash(self) -> str:
        """Stable hash of the execution-critical fields."""
        identity_fields = {
            "model_id": self.model_id,
            "symbol": self.symbol,
            "event_id": self.event_id,
            "hyp_id": self.hyp_id,
            "feature_set_id": self.feature_set_id,
        }
        if self.research_clock != RESEARCH_CLOCK_SCHEDULED_EVENT:
            identity_fields["research_clock"] = self.research_clock
        if self.context_set_id != TARGET_ONLY_CONTEXT_SET_ID:
            identity_fields["context_set_id"] = self.context_set_id
        payload = json.dumps(identity_fields, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    @staticmethod
    def _default_declared_context_sets(context_set_id: str) -> tuple[str, ...]:
        if context_set_id == TARGET_ONLY_CONTEXT_SET_ID:
            return (TARGET_ONLY_CONTEXT_SET_ID,)
        return (TARGET_ONLY_CONTEXT_SET_ID, context_set_id)

    @classmethod
    def _parse_declared_context_sets(cls, value: Any, context_set_id: str) -> tuple[str, ...]:
        if value is None:
            return cls._default_declared_context_sets(context_set_id)
        if isinstance(value, str):
            parsed = tuple(part.strip() for part in value.split(",") if part.strip())
        else:
            parsed = tuple(str(part).strip() for part in value if str(part).strip())
        return parsed or cls._default_declared_context_sets(context_set_id)

    @classmethod
    def from_jsonl_row(cls, row: dict) -> "PaidScreenUnit":
        """Parse a JSONL row into a typed unit. Uses structured fields directly."""
        context_set_id = (
            row.get("context_set_id")
            or row.get("allowed_context_set_id")
            or row.get("allowed_context_set_id_or_null")
            or TARGET_ONLY_CONTEXT_SET_ID
        )
        return cls(
            unit_id=row["unit_id"],
            model_id=row["model_id"],
            hyp_id=row.get("hyp_id"),
            symbol=row["symbol"],
            event_id=row["event_id"],
            event_type=row.get("event_type", ""),
            feature_set_id=row.get("feature_set_id"),
            research_split=row.get("research_split"),
            thesis=row.get("thesis", ""),
            research_clock=row.get("research_clock") or "scheduled_event",
            context_set_id=context_set_id,
            declared_context_sets=cls._parse_declared_context_sets(row.get("declared_context_sets"), context_set_id),
            ablation_group_id=row.get("ablation_group_id"),
            negative_control_policy=row.get("negative_control_policy"),
        )


@dataclass(frozen=True)
class WorkerContext:
    """Immutable context shared across all units processed by one worker."""
    repo_root: str
    git_commit: str
    screening_scope: str
    vectorbt_engine: str
    vectorbt_version: str
    rust_runtime_proof: bool
    events_csv_hash: str
    lake_manifest_hash: str
    run_budget: dict[str, Any] = field(default_factory=dict)


@dataclass
class UnitScreeningResult:
    """Result of screening one unit, including artifact path and status."""
    unit_id: str
    status: str  # OK | OK_CACHED | ERROR | SKIPPED
    screening_artifact_path: str | None = None
    screening_artifact_hash: str | None = None
    error: str | None = None
    elapsed_seconds: float = 0.0
    promoted_ids: list[str] = field(default_factory=list)
    rejected_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BatchingKey:
    """Determines which units can safely share a batch.

    Two units can batch together iff their BatchingKeys are equal.
    Every field that can change semantics is included.
    """
    symbol: str
    event_id: str
    event_type: str
    data_manifest_hash: str
    lake_manifest_hash: str
    events_csv_hash: str
    bar_construction_id: str
    feature_set_id: str | None
    feature_set_hash: str
    research_clock: str
    context_set_id: str
    split_scheme_id: str
    fees_model_id: str
    slippage_model_id: str
    signal_implementation_hash: str
    model_registry_hash: str

    def cache_key(self) -> str:
        """Content-addressed cache key for the compatible data batch."""
        payload = json.dumps({
            "symbol": self.symbol,
            "event_id": self.event_id,
            "data_manifest_hash": self.data_manifest_hash,
            "lake_manifest_hash": self.lake_manifest_hash,
            "events_csv_hash": self.events_csv_hash,
            "bar_construction_id": self.bar_construction_id,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    def feature_cache_key(self) -> str:
        """Cache key for the feature plane."""
        payload = json.dumps({
            "symbol": self.symbol,
            "event_id": self.event_id,
            "data_manifest_hash": self.data_manifest_hash,
            "feature_set_id": self.feature_set_id,
            "feature_set_hash": self.feature_set_hash,
            "bar_construction_id": self.bar_construction_id,
            "context_set_id": self.context_set_id,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    def signal_cache_key(self, model_id: str) -> str:
        """Cache key for raw signals from a specific model."""
        payload = json.dumps({
            "symbol": self.symbol,
            "event_id": self.event_id,
            "model_id": model_id,
            "data_manifest_hash": self.data_manifest_hash,
            "feature_set_hash": self.feature_set_hash,
            "signal_implementation_hash": self.signal_implementation_hash,
            "research_clock": self.research_clock,
            "context_set_id": self.context_set_id,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    def vbt_result_cache_key(self, model_id: str, param_chunk_hash: str,
                              vectorbt_version: str, vectorbt_engine: str) -> str:
        """Cache key for VectorBT result matrix."""
        payload = json.dumps({
            "symbol": self.symbol,
            "event_id": self.event_id,
            "model_id": model_id,
            "data_manifest_hash": self.data_manifest_hash,
            "feature_set_hash": self.feature_set_hash,
            "signal_implementation_hash": self.signal_implementation_hash,
            "research_clock": self.research_clock,
            "context_set_id": self.context_set_id,
            "split_scheme_id": self.split_scheme_id,
            "fees_model_id": self.fees_model_id,
            "slippage_model_id": self.slippage_model_id,
            "param_chunk_hash": param_chunk_hash,
            "vectorbt_version": vectorbt_version,
            "vectorbt_engine": vectorbt_engine,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    def group_id(self) -> str:
        """Stable group identifier for batch compatibility."""
        payload = json.dumps({
            "symbol": self.symbol,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "data_manifest_hash": self.data_manifest_hash,
            "lake_manifest_hash": self.lake_manifest_hash,
            "events_csv_hash": self.events_csv_hash,
            "bar_construction_id": self.bar_construction_id,
            "feature_set_id": self.feature_set_id,
            "feature_set_hash": self.feature_set_hash,
            "research_clock": self.research_clock,
            "context_set_id": self.context_set_id,
            "split_scheme_id": self.split_scheme_id,
            "fees_model_id": self.fees_model_id,
            "slippage_model_id": self.slippage_model_id,
            "signal_implementation_hash": self.signal_implementation_hash,
            "model_registry_hash": self.model_registry_hash,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:32]
