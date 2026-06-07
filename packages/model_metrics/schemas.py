"""Versioned data contracts for institutional model metrics."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any


CALCULATION_VERSION = "model_metrics.v1"
SCHEMA_VERSION = "model_metrics.schema.v1"
DEFAULT_CREATED_AT = "1970-01-01T00:00:00+00:00"
MODEL_STATES = frozenset({"GREEN", "YELLOW", "RED"})


def strict_json_dumps(payload: Any, **kwargs: Any) -> str:
    return json.dumps(payload, allow_nan=False, sort_keys=True, **kwargs)


def stable_id(prefix: str, payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(strict_json_dumps(payload).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def finite_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return value
    return value


@dataclass(frozen=True)
class MetricValue:
    metric_name: str
    metric_value: float | None
    metric_unit: str
    metric_window: str
    sample_size: int
    calculation_version: str
    source_artifact_ids: tuple[str, ...] = ()
    created_at: str = DEFAULT_CREATED_AT
    group: str = ""
    status: str = "ok"
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    reliability: str = "high"

    def to_dict(self) -> dict[str, Any]:
        return json_safe(asdict(self))


@dataclass(frozen=True)
class CategoryScore:
    category: str
    weight: float
    normalized_score: float
    status: str
    explanation: str
    metric_names: tuple[str, ...] = ()
    sample_size_warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return json_safe(asdict(self))


@dataclass(frozen=True)
class ModelScorecard:
    scorecard_id: str
    model_id: str
    model_version: str
    run_id: str
    campaign_id: str
    asset_class: str
    symbol: str
    robustness_run_id: str
    calculation_version: str
    created_at: str
    grade: str
    weighted_score: float
    category_scores: tuple[CategoryScore, ...]
    metrics: tuple[MetricValue, ...]
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = SCHEMA_VERSION
        payload["category_scores"] = [score.to_dict() for score in self.category_scores]
        payload["metrics"] = [metric.to_dict() for metric in self.metrics]
        return json_safe(payload)


@dataclass(frozen=True)
class ModelBehaviorEnvelope:
    envelope_id: str
    model_id: str
    model_version: str
    robustness_run_id: str
    calculation_version: str
    created_at: str
    approved_by_system_rule: bool
    active: bool
    expected_daily_pnl_range: tuple[float | None, float | None]
    expected_weekly_pnl_range: tuple[float | None, float | None]
    expected_intraday_drawdown_range: tuple[float | None, float | None]
    warning_drawdown_threshold: float | None
    kill_drawdown_threshold: float | None
    expected_drawdown_velocity: tuple[float | None, float | None]
    max_allowed_drawdown_velocity: float | None
    expected_trade_count_per_session: tuple[float | None, float | None]
    min_trade_count_per_session: float | None
    max_trade_count_per_session: float | None
    expected_holding_period_min: float | None
    expected_holding_period_median: float | None
    expected_holding_period_max: float | None
    expected_win_rate_range: tuple[float | None, float | None]
    expected_payoff_ratio_range: tuple[float | None, float | None]
    expected_expectancy_range: tuple[float | None, float | None]
    expected_slippage_range: tuple[float | None, float | None]
    max_allowed_slippage: float | None
    expected_spread_range: tuple[float | None, float | None]
    max_allowed_spread: float | None
    expected_fill_rate_range: tuple[float | None, float | None]
    min_allowed_fill_rate: float | None
    expected_signal_score_distribution: dict[str, Any] = field(default_factory=dict)
    abnormal_signal_score_thresholds: dict[str, Any] = field(default_factory=dict)
    expected_model_confidence_distribution: dict[str, Any] = field(default_factory=dict)
    expected_feature_ranges: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)
    feature_training_domain_bounds: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)
    approved_regime_ids: tuple[str, ...] = ()
    blocked_regime_ids: tuple[str, ...] = ()
    expected_correlation_range_to_book: tuple[float | None, float | None] = (None, None)
    max_allowed_correlation_to_book: float | None = None
    max_allowed_position_size: float | None = None
    max_allowed_gross_exposure: float | None = None
    max_allowed_net_exposure: float | None = None
    asset_class_specific_limits: dict[str, Any] = field(default_factory=dict)
    venue_specific_limits: dict[str, Any] = field(default_factory=dict)
    symbol_specific_limits: dict[str, Any] = field(default_factory=dict)
    latency_bounds: dict[str, tuple[float | None, float | None]] = field(default_factory=dict)
    operating_band: str = ""
    async_state_model_required: bool = False
    max_pending_orders: int | None = None
    max_pending_quantity: float | None = None
    max_pending_notional: float | None = None
    stale_pending_timeout_policy: dict[str, Any] = field(default_factory=dict)
    competitor_speed_assumption_used: dict[str, Any] = field(default_factory=dict)
    opportunity_decay_window_used: float | None = None
    low_latency_execution_path_status: dict[str, Any] = field(default_factory=dict)
    alpha_half_life_bounds: tuple[float | None, float | None] = (None, None)
    data_freshness_requirements: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = SCHEMA_VERSION
        return json_safe(payload)


@dataclass(frozen=True)
class ModelRuntimeObservation:
    model_id: str
    model_version: str = ""
    run_id: str = ""
    mode: str = "shadow"
    timestamp: str = DEFAULT_CREATED_AT
    pnl: float | None = None
    drawdown: float | None = None
    drawdown_velocity: float | None = None
    trade_count: int | None = None
    holding_period_min: float | None = None
    win_rate: float | None = None
    payoff_ratio: float | None = None
    expectancy: float | None = None
    slippage_bps: float | None = None
    spread_bps: float | None = None
    fill_rate: float | None = None
    tick_to_send_trigger_us: float | None = None
    decision_to_send_trigger_us: float | None = None
    tick_to_send_us: float | None = None
    decision_to_send_us: float | None = None
    rithmic_send_call_us: float | None = None
    cancel_to_send_us: float | None = None
    replace_to_send_us: float | None = None
    send_to_ack_us: float | None = None
    cancel_to_ack_us: float | None = None
    replace_to_ack_us: float | None = None
    latency_order_to_ack: float | None = None
    alpha_half_life: float | None = None
    signal_score: float | None = None
    confidence: float | None = None
    realized_accuracy: float | None = None
    feature_values: dict[str, float] = field(default_factory=dict)
    regime_id: str = ""
    correlation_to_book: float | None = None
    data_age_ns: int | None = None
    order_reject_rate: float | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ModelRuntimeObservation":
        fields = cls.__dataclass_fields__
        values = {name: raw[name] for name in fields if name in raw}
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return json_safe(asdict(self))


@dataclass(frozen=True)
class ModelStateDecision:
    state: str
    action: str
    triggers: tuple[dict[str, Any], ...]
    alerts: tuple[dict[str, Any], ...]
    observation: ModelRuntimeObservation
    envelope_id: str
    calculation_version: str = CALCULATION_VERSION

    def __post_init__(self) -> None:
        if self.state not in MODEL_STATES:
            raise ValueError("state must be GREEN, YELLOW, or RED")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = SCHEMA_VERSION
        payload["observation"] = self.observation.to_dict()
        return json_safe(payload)
