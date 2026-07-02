"""Scenario manifest generation from validated VectorBT screening artifacts."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from backtest_pipeline.src.hft_campaign.prepared_data import prepare_replay_data
from backtest_pipeline.src.hft_campaign.scenario import HftReplayScenario, compute_scenario_id
from backtest_pipeline.src.hft_campaign.transitional_handoff import load_screening_artifact
from backtest_pipeline.src.recipe_hash_gate import extract_feature_recipe_hash_from_promoted_row
from backtest_pipeline.src.hftbacktest_realism import validate_candidate_replay_eligibility
from backtest_pipeline.src.vectorbt_adapter import (
    ScreeningArtifactError,
    validate_screening_artifact,
)


@dataclass
class ManifestGenerationConfig:
    screening_artifact_path: Path
    repo_root: Path
    event_id: str
    source_npz_path: Path
    latency_model_path: Path
    fill_queue_model_path: Path
    candidate_ids: tuple[str, ...] = ()
    select_all_replay_eligible: bool = False
    stress_dimensions: tuple[str, ...] = ()
    replay_tier: str = "stage2_individual"
    lake_manifest_hash: str = ""
    transitional_allowed: bool = False


def _git_commit(repo_root: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except Exception:
        return "unknown"


def _package_version() -> str:
    try:
        import importlib.metadata

        return importlib.metadata.version("hftbacktest")
    except Exception:
        return "unknown"


def _hash_file(path: Path) -> str:
    from backtest_pipeline.src.hft_campaign._hashing import sha256_file, sha256_hex

    if path.suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return sha256_hex(payload)
    return sha256_file(path)


def select_replay_eligible_candidates(
    screening: Mapping[str, Any],
    *,
    candidate_ids: Iterable[str] = (),
    select_all_replay_eligible: bool = False,
    transitional: bool = False,
) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    promoted_rows = [dict(r) for r in screening.get("promoted") or [] if isinstance(r, Mapping)]
    eligible_ids: list[str] = []
    for row in promoted_rows:
        cid = str(row.get("candidate_id", ""))
        if not cid:
            continue
        if transitional:
            eligible_ids.append(cid)
            continue
        row_reasons = validate_candidate_replay_eligibility(
            row,
            screening_artifact=screening,
        )
        if row_reasons:
            reasons.extend([f"{cid}:{r}" for r in row_reasons])
            continue
        if str(row.get("replay_eligibility_status", "")).lower() != "eligible":
            reasons.append(f"{cid}:not_replay_eligible")
            continue
        eligible_ids.append(cid)

    if candidate_ids:
        selected = [cid for cid in candidate_ids if cid in eligible_ids]
        missing = [cid for cid in candidate_ids if cid not in eligible_ids]
        for cid in missing:
            reasons.append(f"candidate_not_replay_eligible:{cid}")
    elif select_all_replay_eligible:
        selected = eligible_ids
    else:
        reasons.append("candidate_selection_not_explicit")
        selected = []

    return selected, reasons


def _stress_variants(stress_dimensions: tuple[str, ...]) -> list[tuple[str, int]]:
    variants: list[tuple[str, int]] = [("baseline", 0)]
    if "latency" in stress_dimensions:
        variants.append(("latency_stress", 1))
    if "queue" in stress_dimensions:
        variants.append(("queue_stress", 2))
    if "fee" in stress_dimensions:
        variants.append(("fee_stress", 3))
    return variants


def generate_scenario_manifest(cfg: ManifestGenerationConfig) -> tuple[list[HftReplayScenario], list[str]]:
    screening, load_reasons, transitional = load_screening_artifact(cfg.screening_artifact_path)
    reasons = list(load_reasons)
    if transitional and not cfg.transitional_allowed:
        reasons.append("transitional_handoff_not_allowed_in_production")
        return [], reasons
    if not transitional:
        try:
            validate_screening_artifact(screening)
        except ScreeningArtifactError as exc:
            reasons.append(str(exc))
            return [], reasons

    selected_ids, selection_reasons = select_replay_eligible_candidates(
        screening,
        candidate_ids=cfg.candidate_ids,
        select_all_replay_eligible=cfg.select_all_replay_eligible,
        transitional=transitional,
    )
    reasons.extend(selection_reasons)
    if not selected_ids:
        return [], reasons

    prepared = prepare_replay_data(
        source_npz_path=cfg.source_npz_path,
        repo_root=cfg.repo_root,
        symbol=_infer_symbol(screening, selected_ids[0]),
        event_id=cfg.event_id,
        lake_manifest_hash=cfg.lake_manifest_hash,
    )
    latency_hash = _hash_file(cfg.latency_model_path)
    queue_hash = _hash_file(cfg.fill_queue_model_path)
    hft3_commit = _git_commit(cfg.repo_root)
    package_version = _package_version()
    screening_hash = str(screening.get("screening_artifact_hash", ""))

    scenarios: list[HftReplayScenario] = []
    promoted_by_id = {
        str(row.get("candidate_id")): dict(row)
        for row in screening.get("promoted") or []
        if isinstance(row, Mapping)
    }

    tier = cfg.replay_tier
    if cfg.stress_dimensions:
        tier = "stage3_stress"

    for candidate_id in selected_ids:
        row = promoted_by_id.get(candidate_id, {})
        recipe_hash = extract_feature_recipe_hash_from_promoted_row(row) or ""
        symbol = str(row.get("symbol") or "")
        if not symbol:
            raise ValueError(f"campaign_manifest_symbol_missing:{candidate_id}")
        variants = _stress_variants(cfg.stress_dimensions)
        for stress_label, stress_seed in variants:
            payload = {
                "candidate_id": candidate_id,
                "model_id": str(row.get("model_id", "UNKNOWN")),
                "symbol": symbol,
                "event_id": cfg.event_id,
                "event_type": str(row.get("opportunity_type_or_event_type", "screen")),
                "prepared_data_hash": prepared.prepared_data_hash,
                "source_data_hash": prepared.source_data_hash,
                "feature_set_id": str(screening.get("feature_set_id", "")),
                "feature_set_hash": str(screening.get("feature_set_hash", "")),
                "feature_recipe_hash": recipe_hash,
                "research_clock": str(row.get("research_clock", screening.get("research_clock", ""))),
                "latency_model_hash": latency_hash,
                "fill_queue_model_hash": queue_hash,
                "fee_model_id": str(screening.get("fees_model_id", "default")),
                "split_scheme_id": str(screening.get("split_scheme_id", "default")),
                "replay_mode": stress_label,
                "seed": stress_seed,
                "parameter_values_hash": str(row.get("parameter_values_hash", "")),
                "execution_policy_id": "default",
                "feature_timeline_hash": "",
                "hftbacktest_package_version": package_version,
                "hft3_commit": hft3_commit,
                "stepping_mode": "event",
                "replay_tier": tier,
                "accelerated_mode": False,
                "transitional_handoff": transitional,
                "feature_plane_status": str(screening.get("feature_plane_status", "scheduled_event_only")),
                "upstream_screening_artifact_hash": screening_hash,
            }
            scenario_id = compute_scenario_id(payload)
            scenarios.append(
                HftReplayScenario(
                    scenario_id=scenario_id,
                    upstream_screening_artifact=cfg.screening_artifact_path,
                    upstream_screening_artifact_hash=screening_hash,
                    candidate_id=candidate_id,
                    model_id=str(payload["model_id"]),
                    symbol=str(payload["symbol"]),
                    event_id=cfg.event_id,
                    event_type=str(payload["event_type"]),
                    prepared_data_path=prepared.path,
                    prepared_data_hash=prepared.prepared_data_hash,
                    source_data_hash=prepared.source_data_hash,
                    feature_set_id=str(payload["feature_set_id"]),
                    feature_set_hash=str(payload["feature_set_hash"]),
                    research_clock=str(payload["research_clock"]),
                    latency_model_path=cfg.latency_model_path,
                    latency_model_hash=latency_hash,
                    fill_queue_model_path=cfg.fill_queue_model_path,
                    fill_queue_model_hash=queue_hash,
                    fee_model_id=str(payload["fee_model_id"]),
                    split_scheme_id=str(payload["split_scheme_id"]),
                    replay_mode=stress_label,
                    seed=stress_seed,
                    parameter_values_hash=str(payload["parameter_values_hash"]),
                    execution_policy_id="default",
                    feature_timeline_hash="",
                    hftbacktest_package_version=package_version,
                    hft3_commit=hft3_commit,
                    stepping_mode="event",
                    replay_tier=tier,
                    accelerated_mode=False,
                    transitional_handoff=transitional,
                    feature_plane_status=str(screening.get("feature_plane_status", "scheduled_event_only")),
                    feature_recipe_hash=recipe_hash,
                )
            )
    return scenarios, reasons


def _infer_symbol(screening: Mapping[str, Any], candidate_id: str) -> str:
    for row in screening.get("promoted") or []:
        if isinstance(row, Mapping) and str(row.get("candidate_id")) == candidate_id:
            symbol = str(row.get("symbol") or "").split(".")[0]
            if symbol:
                return symbol
            break
    raise ValueError(f"campaign_manifest_symbol_missing:{candidate_id}")


def write_scenario_manifest(path: Path, scenarios: list[HftReplayScenario], *, reasons: list[str] | None = None) -> None:
    payload = {
        "schema": "hft_campaign_scenario_manifest_v1",
        "scenario_count": len(scenarios),
        "generation_reasons": reasons or [],
        "scenarios": [s.to_dict() for s in scenarios],
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
