"""Cross-generation autoresearch manifest and state."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

MANIFEST_VERSION = 1
GENERATION_STATUS_PENDING = "pending"
GENERATION_STATUS_IN_PROGRESS = "in_progress"
GENERATION_STATUS_COMPLETE = "complete"
GENERATION_STATUS_FAILED = "failed"


def autoresearch_campaign_dir(repo_root: Path, campaign_id: str) -> Path:
    return Path(repo_root) / "research_cards" / "autoresearch" / campaign_id


def manifest_path(repo_root: Path, campaign_id: str) -> Path:
    return autoresearch_campaign_dir(repo_root, campaign_id) / "autoresearch_manifest.json"


def generation_dir(repo_root: Path, campaign_id: str, generation_index: int) -> Path:
    return autoresearch_campaign_dir(repo_root, campaign_id) / f"generation_{generation_index:03d}"


def compute_config_hash(config: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(config), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


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
