"""Stage 0 input validation for HftBacktest campaigns."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from backtest_pipeline.src.hft_campaign.prepared_data import validate_prepared_data_dir
from backtest_pipeline.src.hft_campaign.scenario import HftReplayScenario
from backtest_pipeline.src.hft_campaign.transitional_handoff import (
    load_screening_artifact,
)
from backtest_pipeline.src.hftbacktest_realism import (
    validate_candidate_replay_eligibility,
    validate_hftbacktest_data_path,
    validate_hftbacktest_fill_queue_model,
    validate_hftbacktest_latency_model,
)
from backtest_pipeline.src.hft_campaign.ontology import (
    load_vault_gate_receipt,
    validate_feature_plane_status,
    validate_screening_feature_plane,
)
from backtest_pipeline.src.hft_campaign.source_lock import build_campaign_source_lock
from backtest_pipeline.src.recipe_hash_gate import validate_feature_recipe_hash_handoff
from backtest_pipeline.src.vectorbt_adapter import validate_screening_artifact


@dataclass
class Stage0ValidationResult:
    ok: bool
    reasons: list[str]
    screening_artifact: dict[str, Any]
    transitional_handoff: bool = False


def validate_stage0_scenario(scenario: HftReplayScenario, *, repo_root: Path) -> Stage0ValidationResult:
    reasons: list[str] = []
    screening_path = Path(scenario.upstream_screening_artifact)
    screening, load_reasons, transitional = load_screening_artifact(screening_path)
    reasons.extend(load_reasons)

    if not transitional:
        reasons.extend(validate_screening_artifact(screening))
        observed_hash = str(screening.get("screening_artifact_hash", ""))
        if observed_hash and observed_hash != scenario.upstream_screening_artifact_hash:
            reasons.append("upstream_screening_artifact_hash_mismatch")
    reasons.extend(validate_screening_feature_plane(screening))

    candidate_row = _find_candidate_row(screening, scenario.candidate_id)
    if candidate_row is None:
        reasons.append("candidate_metadata_missing_from_screening_artifact")
    else:
        if not transitional:
            reasons.extend(validate_candidate_replay_eligibility(candidate_row))
            if str(candidate_row.get("replay_eligibility_status", "")).lower() != "eligible":
                reasons.append("candidate_not_replay_eligible")
        elif scenario.replay_tier not in ("stage1_minimal",):
            reasons.append("transitional_handoff_cannot_certify")
        recipe_reasons = validate_feature_recipe_hash_handoff(
            scenario_feature_recipe_hash=scenario.feature_recipe_hash,
            promoted_row=candidate_row,
        )
        reasons.extend(recipe_reasons)

    if scenario.transitional_handoff and not transitional:
        reasons.append("scenario_transitional_flag_mismatch")

    reasons.extend(validate_feature_plane_status(scenario.feature_plane_status))
    screening_fps = str(screening.get("feature_plane_status", scenario.feature_plane_status))
    if screening_fps != scenario.feature_plane_status:
        reasons.append("scenario_feature_plane_status_mismatch_with_screening")

    if scenario.feature_plane_status == "feature_complete_pit_declared" and scenario.replay_tier in (
        "stage2_individual",
        "stage3_stress",
    ):
        reasons.append("feature_complete_pit_replay_engine_not_wired")

    _, vault_reasons = load_vault_gate_receipt(repo_root)
    reasons.extend(vault_reasons)

    prepared_reasons = validate_prepared_data_dir(
        scenario.prepared_data_path.parent,
        expected_hash=scenario.prepared_data_hash,
    )
    reasons.extend(prepared_reasons)

    data_validation = validate_hftbacktest_data_path(scenario.prepared_data_path)
    if str(data_validation.get("status", "")).lower() != "pass":
        reasons.append("prepared_data_validation_failed")

    latency_model = _load_json(scenario.latency_model_path)
    reasons.extend(validate_hftbacktest_latency_model(latency_model, repo_root=repo_root))
    if scenario.latency_model_hash:
        from backtest_pipeline.src.hft_campaign._hashing import sha256_hex

        if sha256_hex(latency_model) != scenario.latency_model_hash:
            reasons.append("latency_model_hash_mismatch")

    fill_queue_model = _load_json(scenario.fill_queue_model_path)
    reasons.extend(validate_hftbacktest_fill_queue_model(fill_queue_model))
    if scenario.fill_queue_model_hash:
        from backtest_pipeline.src.hft_campaign._hashing import sha256_hex

        if sha256_hex(fill_queue_model) != scenario.fill_queue_model_hash:
            reasons.append("fill_queue_model_hash_mismatch")

    source_lock, source_lock_reasons = build_campaign_source_lock(repo_root)
    if scenario.replay_tier in ("stage2_individual", "stage3_stress") and not transitional:
        reasons.extend(source_lock_reasons)

    if scenario.scenario_hash() != HftReplayScenario.from_dict(scenario.to_dict()).scenario_hash():
        reasons.append("scenario_hash_inconsistent")

    deduped = list(dict.fromkeys(reasons))
    return Stage0ValidationResult(
        ok=not deduped,
        reasons=deduped,
        screening_artifact=screening,
        transitional_handoff=transitional,
    )


def _find_candidate_row(screening: Mapping[str, Any], candidate_id: str) -> dict[str, Any] | None:
    for row in screening.get("promoted") or []:
        if isinstance(row, Mapping) and str(row.get("candidate_id")) == candidate_id:
            return dict(row)
    return None


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
