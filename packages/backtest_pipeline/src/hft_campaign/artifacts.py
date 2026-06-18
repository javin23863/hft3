"""Atomic per-scenario artifact writes and resumability checks."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from backtest_pipeline.src.hft_campaign._hashing import sha256_file, sha256_hex
from backtest_pipeline.src.hft_campaign.scenario import HftReplayScenario


COMPLETE_MARKER = ".complete"
REQUIRED_SUCCESS_FILES = (
    "input_manifest.json",
    "replay_result.json",
    "replay_summary.json",
    "timings.json",
)


def scenario_artifact_dir(campaign_dir: Path, scenario_id: str) -> Path:
    return Path(campaign_dir) / "scenarios" / scenario_id


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_jsonl_atomic(path: Path, rows: list[Mapping[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    os.replace(tmp, path)


def commit_scenario_success(scenario_dir: Path) -> None:
    marker = scenario_dir / COMPLETE_MARKER
    marker.write_text("ok\n", encoding="utf-8")


def validate_cached_scenario(
    scenario_dir: Path,
    scenario: HftReplayScenario,
    *,
    repo_commit: str,
    package_version: str,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    scenario_dir = Path(scenario_dir)
    if not (scenario_dir / COMPLETE_MARKER).is_file():
        reasons.append("scenario_not_complete")
        return False, reasons

    for name in REQUIRED_SUCCESS_FILES:
        if not (scenario_dir / name).is_file():
            reasons.append(f"missing_required_artifact:{name}")

    input_manifest_path = scenario_dir / "input_manifest.json"
    if input_manifest_path.is_file():
        manifest = json.loads(input_manifest_path.read_text(encoding="utf-8"))
        if manifest.get("scenario_hash") != scenario.scenario_hash():
            reasons.append("cached_scenario_hash_mismatch")
        if manifest.get("upstream_screening_artifact_hash") != scenario.upstream_screening_artifact_hash:
            reasons.append("cached_screening_hash_mismatch")
        if manifest.get("prepared_data_hash") != scenario.prepared_data_hash:
            reasons.append("cached_prepared_data_hash_mismatch")
        if manifest.get("latency_model_hash") != scenario.latency_model_hash:
            reasons.append("cached_latency_model_hash_mismatch")
        if manifest.get("fill_queue_model_hash") != scenario.fill_queue_model_hash:
            reasons.append("cached_fill_queue_model_hash_mismatch")
        if manifest.get("scenario_id") != scenario.scenario_id:
            reasons.append("cached_scenario_id_mismatch")
        if repo_commit and manifest.get("hft3_commit") not in ("", repo_commit):
            reasons.append("cached_code_commit_mismatch")
        if package_version and manifest.get("hftbacktest_package_version") not in ("", package_version):
            reasons.append("cached_package_version_mismatch")

    replay_result_path = scenario_dir / "replay_result.json"
    if replay_result_path.is_file():
        replay = json.loads(replay_result_path.read_text(encoding="utf-8"))
        recorded_hash = replay.get("replay_result_hash")
        if recorded_hash:
            stripped = {k: v for k, v in replay.items() if k != "replay_result_hash"}
            if sha256_hex(stripped) != recorded_hash:
                reasons.append("cached_replay_result_hash_mismatch")

    return not reasons, reasons


def write_scenario_artifacts(
    scenario_dir: Path,
    *,
    scenario: HftReplayScenario,
    replay_result: Mapping[str, Any],
    replay_summary: Mapping[str, Any],
    timings: Mapping[str, Any],
    resource_usage: Mapping[str, Any],
    source_lock: Mapping[str, Any] | None = None,
    data_validation: Mapping[str, Any] | None = None,
    order_lifecycle_rows: list[Mapping[str, Any]] | None = None,
    fill_rows: list[Mapping[str, Any]] | None = None,
    repo_commit: str = "",
    package_version: str = "",
) -> None:
    scenario_dir = Path(scenario_dir)
    scenario_dir.mkdir(parents=True, exist_ok=True)

    input_manifest = {
        "scenario_id": scenario.scenario_id,
        "scenario_hash": scenario.scenario_hash(),
        "candidate_id": scenario.candidate_id,
        "upstream_screening_artifact_hash": scenario.upstream_screening_artifact_hash,
        "prepared_data_hash": scenario.prepared_data_hash,
        "source_data_hash": scenario.source_data_hash,
        "latency_model_hash": scenario.latency_model_hash,
        "fill_queue_model_hash": scenario.fill_queue_model_hash,
        "feature_set_hash": scenario.feature_set_hash,
        "feature_recipe_hash": scenario.feature_recipe_hash,
        "feature_timeline_hash": scenario.feature_timeline_hash,
        "replay_tier": scenario.replay_tier,
        "stepping_mode": scenario.stepping_mode,
        "accelerated_mode": scenario.accelerated_mode,
        "transitional_handoff": scenario.transitional_handoff,
        "feature_plane_status": scenario.feature_plane_status,
        "hft3_commit": repo_commit,
        "hftbacktest_package_version": package_version,
    }
    write_json_atomic(scenario_dir / "input_manifest.json", input_manifest)

    if source_lock is not None:
        write_json_atomic(scenario_dir / "source_lock.json", source_lock)
    if data_validation is not None:
        write_json_atomic(scenario_dir / "data_validation.json", data_validation)

    replay_payload = dict(replay_result)
    replay_payload["replay_result_hash"] = sha256_hex(
        {k: v for k, v in replay_payload.items() if k != "replay_result_hash"}
    )
    write_json_atomic(scenario_dir / "replay_result.json", replay_payload)
    write_json_atomic(scenario_dir / "replay_summary.json", replay_summary)
    write_json_atomic(scenario_dir / "timings.json", timings)
    write_json_atomic(scenario_dir / "resource_usage.json", resource_usage)

    if order_lifecycle_rows is not None:
        write_jsonl_atomic(scenario_dir / "order_lifecycle.jsonl", order_lifecycle_rows)
    if fill_rows is not None:
        write_jsonl_atomic(scenario_dir / "fills.jsonl", fill_rows)

    commit_scenario_success(scenario_dir)


def write_failure_artifact(scenario_dir: Path, failure: Mapping[str, Any]) -> None:
    scenario_dir = Path(scenario_dir)
    scenario_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(scenario_dir / "failure.json", failure)
