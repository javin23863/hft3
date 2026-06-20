"""Cross-generation autoresearch manifest and state."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

MANIFEST_VERSION = 1
GENERATION_STATUS_PENDING = "pending"
GENERATION_STATUS_IN_PROGRESS = "in_progress"
GENERATION_STATUS_BLOCKED = "blocked"
GENERATION_STATUS_COMPLETE = "complete"
GENERATION_STATUS_FAILED = "failed"
GENERATION_STATUS_ABORTED = "aborted"

TERMINAL_GENERATION_STATUSES: frozenset[str] = frozenset(
    {
        GENERATION_STATUS_COMPLETE,
        GENERATION_STATUS_FAILED,
        GENERATION_STATUS_BLOCKED,
        GENERATION_STATUS_ABORTED,
    }
)

_GATE_VERSIONS: dict[str, str] = {
    "ontology_gate": "1.0.0",
    "manifest_gate": "1.0.0",
    "vectorbt_gate": "1.0.0",
    "surface_stability_gate": "1.0.0",
    "regular_walk_forward_gate": "1.0.0",
    "walk_forward_correlation_gate": "1.0.0",
    "statistical_robustness_gate": "1.0.0",
    "hftbacktest_gate": "1.0.0",
}


def autoresearch_campaign_dir(repo_root: Path, campaign_id: str) -> Path:
    return Path(repo_root) / "research_cards" / "autoresearch" / campaign_id


def manifest_path(repo_root: Path, campaign_id: str) -> Path:
    return autoresearch_campaign_dir(repo_root, campaign_id) / "autoresearch_manifest.json"


def generation_dir(repo_root: Path, campaign_id: str, generation_index: int) -> Path:
    return autoresearch_campaign_dir(repo_root, campaign_id) / f"generation_{generation_index:03d}"


def _file_content_hash(path: Path | None) -> str | None:
    if path is None or not Path(path).is_file():
        return None
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def _yaml_config_snapshot(repo_root: Path, *relative_paths: str) -> dict[str, Any] | None:
    for rel in relative_paths:
        candidate = Path(repo_root) / rel
        if candidate.is_file():
            try:
                payload = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            except Exception:
                return None
            return dict(payload) if isinstance(payload, dict) else {"value": payload}
    return None


def collect_semantic_config_inputs(
    *,
    repo_root: Path,
    event_id: str,
    campaign_cfg: Mapping[str, Any],
) -> dict[str, Any]:
    """Semantic inputs for deterministic resume per assignment §18."""
    wf = _yaml_config_snapshot(
        repo_root,
        "apps/workbench/config/walk_forward.yaml",
        "workbench/config/walk_forward.yaml",
    )
    wfc = _yaml_config_snapshot(
        repo_root,
        "apps/workbench/config/wfc_gate.yaml",
        "workbench/config/wfc_gate.yaml",
    )
    payload: dict[str, Any] = {
        "event_id": event_id,
        "gate_versions": dict(_GATE_VERSIONS),
        "walk_forward": wf,
        "wfc_gate": wfc,
        "max_candidates_per_generation": campaign_cfg.get("max_candidates_per_generation"),
        "robustness_max_candidates": campaign_cfg.get("robustness_max_candidates"),
        "exploration_fraction": campaign_cfg.get("exploration_fraction"),
        "family_search_enabled": campaign_cfg.get("family_search_enabled"),
        "family_search_fraction": campaign_cfg.get("family_search_fraction"),
        "screening_scope": campaign_cfg.get("screening_scope"),
        "vectorbt_min_trades": campaign_cfg.get("vectorbt_min_trades"),
        "symbol": campaign_cfg.get("symbol"),
        "run_robustness": campaign_cfg.get("run_robustness"),
        "run_hft_campaign": campaign_cfg.get("run_hft_campaign"),
        "hft_stages": list(campaign_cfg.get("hft_stages") or []),
        "hft_workers": campaign_cfg.get("hft_workers"),
        "hft_source_npz_hash": _file_content_hash(campaign_cfg.get("hft_source_npz")),
        "hft_latency_model_hash": _file_content_hash(campaign_cfg.get("hft_latency_model")),
        "hft_fill_queue_model_hash": _file_content_hash(campaign_cfg.get("hft_fill_queue_model")),
    }
    return payload


def compute_config_hash(config: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(config), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def assert_config_hash_matches(manifest: Mapping[str, Any], config_hash: str) -> None:
    stored = str(manifest.get("config_hash") or "")
    if stored and stored != config_hash:
        raise ValueError("autoresearch config_hash mismatch; refuse to continue with changed config")


def new_campaign_id(*, thesis: str, event_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = hashlib.sha256(f"{thesis}:{event_id}".encode()).hexdigest()[:8]
    return f"autoresearch_{event_id}_{ts}_{slug}"


def default_manifest(
    *,
    campaign_id: str,
    event_id: str,
    symbol: str,
    thesis: str,
    config_hash: str,
    parent_campaign_id: str | None = None,
    parent_generation: int | None = None,
) -> dict[str, Any]:
    return {
        "manifest_version": MANIFEST_VERSION,
        "campaign_id": campaign_id,
        "event_id": event_id,
        "symbol": symbol,
        "thesis": thesis,
        "config_hash": config_hash,
        "generation_index": 0,
        "parent_campaign_id": parent_campaign_id,
        "parent_generation": parent_generation,
        "tested_parameter_hashes": [],
        "candidate_ids": [],
        "generation_status": GENERATION_STATUS_PENDING,
        "stop_reason": None,
        "pipeline_run_ids": [],
        "screening_artifact_paths": [],
        "robustness_campaign_ids": [],
        "hft_campaign_ids": [],
        "generation_summary_paths": [],
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def load_manifest(repo_root: Path, campaign_id: str) -> dict[str, Any]:
    path = manifest_path(repo_root, campaign_id)
    if not path.is_file():
        raise FileNotFoundError(f"autoresearch manifest missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("autoresearch manifest must be a JSON object")
    return payload


def save_manifest(repo_root: Path, manifest: dict[str, Any]) -> Path:
    campaign_id = str(manifest["campaign_id"])
    path = manifest_path(repo_root, campaign_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def register_tested_hashes(manifest: dict[str, Any], hashes: list[str]) -> None:
    seen = set(manifest.get("tested_parameter_hashes") or [])
    for value in hashes:
        if value and value not in seen:
            seen.add(value)
            manifest.setdefault("tested_parameter_hashes", []).append(value)


def append_pointer(manifest: dict[str, Any], key: str, value: str) -> None:
    if not value:
        return
    bucket = manifest.setdefault(key, [])
    if value not in bucket:
        bucket.append(value)


def load_frozen_manifests(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows
