"""Registry-to-Trade-Manager handoff for Phase 14.

This module intentionally does not create execution adapters or route orders.
It validates that a model has a latest PROMOTED registry record and that the
run manifest needed for a future trading session is present.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hft3.validation.certification_registry import (
    PromotionRecord,
    list_promotion_models,
    load_latest_promotion,
)
from trade_manager.order_intent import (
    OrderIntentValidationError,
    TradeManagerOrderIntent,
    order_intent_from_signal,
)
from trade_manager.risk_layer import (
    TradeManagerRiskContext,
    TradeManagerRiskDecision,
    TradeManagerRiskError,
    TradeManagerRiskLayer,
)
from trade_manager.signals import ModelSignal, SIGNAL_SIDES, SignalSource


REQUIRED_PROMOTION_FIELDS = (
    "model_id",
    "candidate_id",
    "experiment_id",
    "run_id",
    "allowed_symbols",
    "allowed_instruments",
    "allowed_order_types",
    "risk_limits_reference",
    "capital_allocation_reference",
    "kill_switch_reference",
    "latency_profile",
    "execution_assumptions",
)


class TradeManagerError(RuntimeError):
    """Base error for Phase 14 Trade Manager activation failures."""


class TradeManagerActivationError(TradeManagerError):
    """Raised when a registry record cannot be activated safely."""

    def __init__(
        self,
        model_id: str,
        reason: str,
        *,
        missing_fields: list[str] | None = None,
    ) -> None:
        self.model_id = model_id
        self.reason = reason
        self.missing_fields = missing_fields or []
        detail = f" for {model_id!r}"
        if self.missing_fields:
            detail += f"; missing_fields={self.missing_fields}"
        super().__init__(f"Trade Manager activation failed{detail}: {reason}")


class TradeManagerSignalError(TradeManagerError):
    """Raised when Phase 15 signal ingress is invalid or unsafe."""

    def __init__(
        self,
        model_id: str,
        reason: str,
        *,
        invalid_fields: list[str] | None = None,
    ) -> None:
        self.model_id = model_id
        self.reason = reason
        self.invalid_fields = invalid_fields or []
        detail = f" for {model_id!r}"
        if self.invalid_fields:
            detail += f"; invalid_fields={self.invalid_fields}"
        super().__init__(f"Trade Manager signal ingress failed{detail}: {reason}")


@dataclass(frozen=True)
class ActiveModel:
    """Validated registry model ready for a future Trade Manager session."""

    registry_id: str
    model_id: str
    candidate_id: str
    experiment_id: str
    run_id: str
    manifest_path: str
    artifact_path: str
    allowed_symbols: tuple[str, ...]
    allowed_instruments: tuple[str, ...]
    allowed_order_types: tuple[str, ...]
    risk_limits_reference: str
    capital_allocation_reference: str
    kill_switch_reference: str
    latency_profile: dict[str, Any] = field(default_factory=dict)
    execution_assumptions: dict[str, Any] = field(default_factory=dict)
    data_resolution: str = ""
    model_combination: dict[str, Any] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)
    activation_status: str = "ACTIVE"

    def to_dict(self) -> dict[str, Any]:
        return {
            "registry_id": self.registry_id,
            "model_id": self.model_id,
            "candidate_id": self.candidate_id,
            "experiment_id": self.experiment_id,
            "run_id": self.run_id,
            "manifest_path": self.manifest_path,
            "artifact_path": self.artifact_path,
            "allowed_symbols": list(self.allowed_symbols),
            "allowed_instruments": list(self.allowed_instruments),
            "allowed_order_types": list(self.allowed_order_types),
            "risk_limits_reference": self.risk_limits_reference,
            "capital_allocation_reference": self.capital_allocation_reference,
            "kill_switch_reference": self.kill_switch_reference,
            "latency_profile": dict(self.latency_profile),
            "execution_assumptions": dict(self.execution_assumptions),
            "data_resolution": self.data_resolution,
            "model_combination": dict(self.model_combination),
            "manifest": dict(self.manifest),
            "activation_status": self.activation_status,
        }


class TradeManager:
    """Phase 14 handoff manager for promoted registry records."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else Path.cwd()
        self.active_models: dict[str, ActiveModel] = {}
        self.signal_sources: dict[str, SignalSource] = {}
        self.signals: dict[str, list[ModelSignal]] = {}
        self.order_intents: dict[str, list[TradeManagerOrderIntent]] = {}
        self.risk_decisions: dict[str, list[TradeManagerRiskDecision]] = {}

    def promoted_records(self) -> list[PromotionRecord]:
        """Return latest promotion records that are currently PROMOTED."""

        records: list[PromotionRecord] = []
        for model_id in list_promotion_models(self.root):
            record = load_latest_promotion(model_id, self.root)
            if record is not None and record.promotion_status == "PROMOTED":
                records.append(record)
        return records

    def activate_model(self, model_id: str) -> ActiveModel:
        """Validate and activate one model from the certification registry."""

        record = load_latest_promotion(model_id, self.root)
        if record is None:
            raise TradeManagerActivationError(model_id, "PROMOTION_RECORD_NOT_FOUND")
        active = self._active_model_from_record(record)
        self.active_models[active.model_id] = active
        return active

    def activate_promoted_models(self) -> list[ActiveModel]:
        """Activate all latest PROMOTED records, failing safely on bad evidence."""

        activated = [self._active_model_from_record(record) for record in self.promoted_records()]
        self.active_models = {model.model_id: model for model in activated}
        return activated

    def load_manifest_for_record(self, record: PromotionRecord) -> tuple[Path, dict[str, Any]]:
        """Load and validate the run manifest referenced by a promotion record."""

        manifest_path = self._manifest_path(record)
        if not manifest_path.is_file():
            raise TradeManagerActivationError(record.model_id, "MANIFEST_NOT_FOUND")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise TradeManagerActivationError(record.model_id, "MANIFEST_INVALID_JSON") from exc
        if not isinstance(manifest, dict):
            raise TradeManagerActivationError(record.model_id, "MANIFEST_NOT_OBJECT")
        manifest_run_id = str(manifest.get("run_id", ""))
        if manifest_run_id != record.run_id:
            raise TradeManagerActivationError(record.model_id, "MANIFEST_RUN_ID_MISMATCH")
        return manifest_path, manifest

    def bind_signal_source(self, model_id: str, source: SignalSource) -> None:
        """Bind an active model to a side-effect-free Phase 15 signal source."""

        self._require_active_model(model_id)
        if not getattr(source, "source_name", ""):
            raise TradeManagerSignalError(model_id, "SIGNAL_SOURCE_MISSING_NAME")
        if not callable(getattr(source, "evaluate", None)):
            raise TradeManagerSignalError(model_id, "SIGNAL_SOURCE_MISSING_EVALUATE")
        self.signal_sources[model_id] = source

    def evaluate_signal(
        self,
        model_id: str,
        *,
        symbol: str,
        timestamp_ns: int,
        context: Any = None,
    ) -> ModelSignal:
        """Evaluate a bound signal source and ingest the normalized signal."""

        active = self._require_active_model(model_id)
        source = self.signal_sources.get(model_id)
        if source is None:
            raise TradeManagerSignalError(model_id, "SIGNAL_SOURCE_NOT_BOUND")
        signal = source.evaluate(active, symbol=symbol, timestamp_ns=timestamp_ns, context=context)
        return self.ingest_signal(model_id, signal)

    def ingest_signal(self, model_id: str, signal: ModelSignal) -> ModelSignal:
        """Validate and store a model signal without constructing orders."""

        active = self._require_active_model(model_id)
        invalid = self._invalid_signal_fields(active, signal)
        if invalid:
            raise TradeManagerSignalError(model_id, "SIGNAL_INVALID", invalid_fields=invalid)

        existing_ids = {stored.signal_id for stored in self.signals.get(model_id, [])}
        if signal.signal_id in existing_ids:
            raise TradeManagerSignalError(model_id, "SIGNAL_ALREADY_INGESTED")

        self.signals.setdefault(model_id, []).append(signal)
        return signal

    def create_order_intent(
        self,
        model_id: str,
        signal: ModelSignal,
        *,
        strategy_id: str,
        quantity: float,
        order_type: str,
        risk_budget_id: str,
        limit_price: float | None = None,
        time_in_force: str = "GTC",
        execution_profile: dict[str, Any] | None = None,
        order_intent_id: str | None = None,
    ) -> TradeManagerOrderIntent:
        """Create an inert Phase 16 Trade Manager order-intent envelope."""

        active = self.active_models.get(model_id)
        if active is None or active.activation_status != "ACTIVE":
            raise OrderIntentValidationError(model_id, "MODEL_NOT_ACTIVE")
        stored_signal = next(
            (stored for stored in self.signals.get(model_id, []) if stored.signal_id == signal.signal_id),
            None,
        )
        if stored_signal is None:
            raise OrderIntentValidationError(model_id, "SIGNAL_NOT_INGESTED")
        if signal != stored_signal:
            raise OrderIntentValidationError(model_id, "SIGNAL_ENVELOPE_MISMATCH")
        existing_signal_ids = {intent.signal_id for intent in self.order_intents.get(model_id, [])}
        if signal.signal_id in existing_signal_ids:
            raise OrderIntentValidationError(model_id, "ORDER_INTENT_ALREADY_CREATED")

        intent = order_intent_from_signal(
            active,
            stored_signal,
            strategy_id=strategy_id,
            quantity=quantity,
            order_type=order_type,
            risk_budget_id=risk_budget_id,
            limit_price=limit_price,
            time_in_force=time_in_force,
            execution_profile=execution_profile,
            order_intent_id=order_intent_id,
        )
        self.order_intents.setdefault(model_id, []).append(intent)
        return intent

    def evaluate_order_intent_risk(
        self,
        model_id: str,
        intent: TradeManagerOrderIntent,
        risk_layer: TradeManagerRiskLayer,
        context: TradeManagerRiskContext,
    ) -> TradeManagerRiskDecision:
        """Evaluate Phase 17 risk and store the decision without routing."""

        active = self.active_models.get(model_id)
        if active is None or active.activation_status != "ACTIVE":
            raise TradeManagerRiskError(model_id, "MODEL_NOT_ACTIVE")
        stored_intent = next(
            (stored for stored in self.order_intents.get(model_id, []) if stored.order_intent_id == intent.order_intent_id),
            None,
        )
        if stored_intent is None:
            raise TradeManagerRiskError(model_id, "ORDER_INTENT_NOT_CREATED")
        if intent != stored_intent:
            raise TradeManagerRiskError(model_id, "ORDER_INTENT_ENVELOPE_MISMATCH")

        decision = risk_layer.evaluate(active, stored_intent, context)
        self.risk_decisions.setdefault(model_id, []).append(decision)
        return decision

    def _active_model_from_record(self, record: PromotionRecord) -> ActiveModel:
        if record.promotion_status != "PROMOTED":
            raise TradeManagerActivationError(record.model_id, "PROMOTION_STATUS_NOT_PROMOTED")

        missing = self._missing_required_fields(record)
        if missing:
            raise TradeManagerActivationError(
                record.model_id,
                "PROMOTION_RECORD_INCOMPLETE",
                missing_fields=missing,
            )

        manifest_path, manifest = self.load_manifest_for_record(record)
        return ActiveModel(
            registry_id=record.registry_id,
            model_id=record.model_id,
            candidate_id=record.candidate_id,
            experiment_id=record.experiment_id,
            run_id=record.run_id,
            manifest_path=str(manifest_path),
            artifact_path=record.artifact_path,
            allowed_symbols=tuple(record.allowed_symbols),
            allowed_instruments=tuple(record.allowed_instruments),
            allowed_order_types=tuple(record.allowed_order_types),
            risk_limits_reference=record.risk_limits_reference,
            capital_allocation_reference=record.capital_allocation_reference,
            kill_switch_reference=record.kill_switch_reference,
            latency_profile=dict(record.latency_profile),
            execution_assumptions=dict(record.execution_assumptions),
            data_resolution=record.data_resolution,
            model_combination=dict(record.model_combination),
            manifest=manifest,
        )

    def _missing_required_fields(self, record: PromotionRecord) -> list[str]:
        missing: list[str] = []
        for field_name in REQUIRED_PROMOTION_FIELDS:
            value = getattr(record, field_name)
            if value in (None, "", [], {}):
                missing.append(field_name)
        return missing

    def _require_active_model(self, model_id: str) -> ActiveModel:
        active = self.active_models.get(model_id)
        if active is None or active.activation_status != "ACTIVE":
            raise TradeManagerSignalError(model_id, "MODEL_NOT_ACTIVE")
        return active

    def _invalid_signal_fields(self, active: ActiveModel, signal: ModelSignal) -> list[str]:
        invalid: list[str] = []
        required_string_fields = (
            "signal_id",
            "registry_id",
            "model_id",
            "candidate_id",
            "run_id",
            "symbol",
            "side",
            "reason_code",
            "signal_source",
        )
        for field_name in required_string_fields:
            if not str(getattr(signal, field_name, "")):
                invalid.append(field_name)

        if signal.registry_id != active.registry_id:
            invalid.append("registry_id")
        if signal.model_id != active.model_id:
            invalid.append("model_id")
        if signal.candidate_id != active.candidate_id:
            invalid.append("candidate_id")
        if signal.run_id != active.run_id:
            invalid.append("run_id")
        if signal.symbol not in active.allowed_symbols:
            invalid.append("symbol")
        if signal.side not in SIGNAL_SIDES:
            invalid.append("side")
        if not self._finite(signal.timestamp_ns) or signal.timestamp_ns <= 0:
            invalid.append("timestamp_ns")
        if not self._finite_between(signal.strength, -1.0, 1.0):
            invalid.append("strength")
        if not self._finite_between(signal.confidence, 0.0, 1.0):
            invalid.append("confidence")
        if not self._finite(signal.expected_edge):
            invalid.append("expected_edge")
        if not signal.latency_profile:
            invalid.append("latency_profile")

        return list(dict.fromkeys(invalid))

    @staticmethod
    def _finite(value: float) -> bool:
        return isinstance(value, (int, float)) and value == value and value not in (float("inf"), float("-inf"))

    @classmethod
    def _finite_between(cls, value: float, lower: float, upper: float) -> bool:
        return cls._finite(value) and lower <= float(value) <= upper

    def _manifest_path(self, record: PromotionRecord) -> Path:
        if record.artifact_path:
            path = self._resolve_path(record.artifact_path)
            return path / "manifest.json" if path.is_dir() else path
        return self.root / "artifacts" / "runs" / record.run_id / "manifest.json"

    def _resolve_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return self.root / path
