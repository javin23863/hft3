"""Typed replay scenario and deterministic scenario identity."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from backtest_pipeline.src.hft_campaign._hashing import (
    VOLATILE_SCENARIO_KEYS,
    hash_mapping,
    sha256_hex,
)

REPLAY_TIERS = frozenset(
    {
        "stage1_minimal",
        "stage2_individual",
        "stage3_stress",
        "stage4_combined",
        "stage5_discrepancy",
    }
)


@dataclass(frozen=True)
class HftReplayScenario:
    scenario_id: str
    upstream_screening_artifact: Path
    upstream_screening_artifact_hash: str
    candidate_id: str
    model_id: str
    symbol: str
    event_id: str
    event_type: str
    prepared_data_path: Path
    prepared_data_hash: str
    source_data_hash: str
    feature_set_id: str
    feature_set_hash: str
    research_clock: str
    latency_model_path: Path
    latency_model_hash: str
    fill_queue_model_path: Path
    fill_queue_model_hash: str
    fee_model_id: str
    split_scheme_id: str
    replay_mode: str
    seed: int
    parameter_values_hash: str = ""
    execution_policy_id: str = "default"
    feature_timeline_hash: str = ""
    hftbacktest_package_version: str = ""
    hft3_commit: str = ""
    stepping_mode: str = "event"
    replay_tier: str = "stage2_individual"
    accelerated_mode: bool = False
    transitional_handoff: bool = False
    feature_plane_status: str = "scheduled_event_only"
    feature_recipe_hash: str = ""

    def execution_hash_payload(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "model_id": self.model_id,
            "symbol": self.symbol,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "prepared_data_hash": self.prepared_data_hash,
            "source_data_hash": self.source_data_hash,
            "feature_set_id": self.feature_set_id,
            "feature_set_hash": self.feature_set_hash,
            "feature_recipe_hash": self.feature_recipe_hash,
            "feature_timeline_hash": self.feature_timeline_hash,
            "research_clock": self.research_clock,
            "latency_model_hash": self.latency_model_hash,
            "fill_queue_model_hash": self.fill_queue_model_hash,
            "fee_model_id": self.fee_model_id,
            "split_scheme_id": self.split_scheme_id,
            "execution_policy_id": self.execution_policy_id,
            "parameter_values_hash": self.parameter_values_hash,
            "replay_mode": self.replay_mode,
            "seed": self.seed,
            "stepping_mode": self.stepping_mode,
            "replay_tier": self.replay_tier,
            "accelerated_mode": self.accelerated_mode,
            "upstream_screening_artifact_hash": self.upstream_screening_artifact_hash,
            "hftbacktest_package_version": self.hftbacktest_package_version,
            "hft3_commit": self.hft3_commit,
            "feature_plane_status": self.feature_plane_status,
        }

    def scenario_hash(self) -> str:
        return hash_mapping(self.execution_hash_payload())

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["upstream_screening_artifact"] = str(self.upstream_screening_artifact)
        payload["prepared_data_path"] = str(self.prepared_data_path)
        payload["latency_model_path"] = str(self.latency_model_path)
        payload["fill_queue_model_path"] = str(self.fill_queue_model_path)
        payload["scenario_hash"] = self.scenario_hash()
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "HftReplayScenario":
        fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in payload.items() if k in fields and k != "scenario_hash"}
        for path_key in (
            "upstream_screening_artifact",
            "prepared_data_path",
            "latency_model_path",
            "fill_queue_model_path",
        ):
            if path_key in kwargs:
                kwargs[path_key] = Path(kwargs[path_key])
        scenario_id = str(kwargs.pop("scenario_id", "") or compute_scenario_id(kwargs))
        return cls(scenario_id=scenario_id, **kwargs)


def compute_scenario_id(payload: Mapping[str, Any]) -> str:
    """Deterministic scenario ID from execution-relevant fields."""
    execution_fields = {
        k: v
        for k, v in payload.items()
        if k not in VOLATILE_SCENARIO_KEYS and not str(k).endswith("_path")
    }
    digest = sha256_hex(execution_fields)[:16]
    candidate_id = str(payload.get("candidate_id", "unknown"))
    event_id = str(payload.get("event_id", "unknown"))
    return f"{candidate_id}__{event_id}__{digest}"
