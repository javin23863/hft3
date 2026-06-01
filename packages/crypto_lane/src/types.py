"""Shared types for crypto lane."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ClockOffset:
    theta_ms: float
    rtt_ms: float
    sample_time_ns: int
    source: str


@dataclass
class PitConfig:
    max_staleness_ms: int = 15000
    strict: bool = False
    network_latency_ms: float = 5.0
    processing_latency_ms: float = 2.0
    exchange_clock_drift_ms: float = 0.0


@dataclass
class FeatureProvenance:
    """Provenance stamp attached to each labeled frame.

    Note on `staleness_delta_ms`: the value is the WORST-CASE (max) staleness
    across the frame, not the mean. The name is historical and not renamed
    to preserve backward compat with serialized artifacts. New consumers
    should treat this as `max_staleness_ms`.
    """
    source: str
    t_avail_ns: int
    t_exch_true_ns: int
    staleness_delta_ms: float
    is_pit_safe: bool
    btc_node_data_available_flag: int = 1


HYPOTHESIS_REQUIRED_KEYS = frozenset({
    "hypothesis_id",
    "name",
    "source_repo",
    "economic_mechanism",
    "null_hypothesis",
    "alternative_hypothesis",
    "expected_direction",
    "asset_scope",
    "venue_scope",
    "prediction_horizons",
    "features_required",
    "labels",
    "feature_timestamp_rule",
    "label_timestamp_rule",
    "point_in_time_controls",
    "leakage_risks",
    "baseline_models",
    "challenger_models",
    "negative_controls",
    "failure_conditions",
    "promotion_criteria",
    "backtest_config",
})

CANDIDATE_REQUIRED_KEYS = frozenset({
    "candidate_id",
    "hypothesis_id",
    "target",
    "horizons",
    "features",
    "model_family",
    "baseline",
    "challengers",
    "validation",
    "backtest",
    "ablation",
    "negative_controls",
    "promotion",
})

BACKTEST_REQUIRED_KEYS = frozenset({
    "config_id",
    "hypothesis_id",
    "candidate_id",
    "universe",
    "venues",
    "date_range",
    "training_window",
    "test_window",
    "embargo",
    "max_feature_staleness_ms",
    "output_report_path",
})


def repo_root_from_lane() -> Path:
    return Path(__file__).resolve().parents[3]
