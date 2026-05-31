"""Discover and load crypto model candidate YAML specs."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from crypto_lane.src.config_loader import list_backtest_config_paths, list_candidate_paths, load_yaml
from crypto_lane.src.types import BACKTEST_REQUIRED_KEYS, CANDIDATE_REQUIRED_KEYS, repo_root_from_lane


def load_candidate(path: str | Path) -> dict[str, Any]:
    return load_yaml(path)


def discover_candidates(candidates_dir: Path | None = None) -> list[dict[str, Any]]:
    paths = list_candidate_paths(candidates_dir)
    return [load_candidate(p) for p in paths]


def discover_backtest_configs(configs_dir: Path | None = None) -> list[dict[str, Any]]:
    paths = list_backtest_config_paths(configs_dir)
    return [load_yaml(p) for p in paths]


def validate_candidate(doc: dict[str, Any]) -> list[str]:
    missing = sorted(CANDIDATE_REQUIRED_KEYS - set(doc.keys()))
    errs: list[str] = []
    if missing:
        errs.append(f"missing keys: {missing}")
    ab = doc.get("ablation") or {}
    hid = doc.get("hypothesis_id", "")
    if hid in ("CRYPTO_H4", "CRYPTO_H5", "CRYPTO_H6", "CRYPTO_H7"):
        if not ab.get("run_without_btc_node_features") or not ab.get("run_with_btc_node_features"):
            errs.append("H4+ requires btc node ablation flags")
    nc = doc.get("negative_controls") or {}
    for k in ("shuffled_labels", "shifted_features_forward"):
        if not nc.get(k):
            errs.append(f"negative_controls.{k} required")
    return errs


def validate_backtest_config(doc: dict[str, Any]) -> list[str]:
    missing = sorted(BACKTEST_REQUIRED_KEYS - set(doc.keys()))
    return [f"missing keys: {missing}"] if missing else []


def candidate_by_id(candidate_id: str) -> dict[str, Any]:
    root = repo_root_from_lane() / "models" / "candidates"
    path = root / f"{candidate_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(candidate_id)
    return load_candidate(path)
