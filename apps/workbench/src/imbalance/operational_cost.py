"""Estimate operational cost for imbalance feature families."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from features_engine.src.imbalance.registry import load_imbalance_registry
from hft3_bootstrap import repo_root


def estimate_operational_cost(
    snapshots: List[dict],
    *,
    modes_run: int = 1,
    replay_steps: int = 0,
) -> Dict[str, Any]:
    reg = load_imbalance_registry(repo_root())
    per_family_ns = {}
    for f in reg["features"]:
        per_family_ns[f.feature_family] = per_family_ns.get(f.feature_family, 0) + f.latency_estimate_ns

    sample_bytes = len(json.dumps(snapshots[:100]).encode("utf-8")) if snapshots else 0
    storage_bytes = sample_bytes * max(1, replay_steps // max(len(snapshots), 1))

    budget_path = repo_root() / "runtime" / "validation" / "feature_latency_budget.json"
    budget = {}
    if budget_path.is_file():
        budget = json.loads(budget_path.read_text(encoding="utf-8"))

    total_ns = sum(per_family_ns.values()) * max(replay_steps, 1)
    max_ns = int(budget.get("max_feature_compute_ns", 5000))
    live_eligible = total_ns / max(replay_steps, 1) <= max_ns

    return {
        "per_family_ns": per_family_ns,
        "storage_bytes_estimate": storage_bytes,
        "replay_multiplier": modes_run,
        "aggregate_compute_ns": total_ns,
        "live_eligible": live_eligible,
        "profile": budget.get("profile", "hft3_default"),
    }
