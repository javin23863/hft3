"""Load crypto lane YAML configs."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from crypto_lane.src.types import repo_root_from_lane


def load_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_absolute():
        p = repo_root_from_lane() / p
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def load_universe(path: str | Path | None = None) -> dict[str, Any]:
    default = repo_root_from_lane() / "packages" / "crypto_lane" / "config" / "universe.yaml"
    return load_yaml(path or default)


def load_hypotheses(path: str | Path | None = None) -> list[dict[str, Any]]:
    default = repo_root_from_lane() / "research" / "hypotheses" / "crypto_alpha_engine_extracted_hypotheses.yaml"
    doc = load_yaml(path or default)
    return list(doc.get("hypotheses", []))


def load_manifest(path: str | Path | None = None) -> dict[str, Any]:
    default = repo_root_from_lane() / "research" / "hypotheses" / "crypto_alpha_engine_manifest.yaml"
    return load_yaml(path or default)


def list_candidate_paths(candidates_dir: Path | None = None) -> list[Path]:
    if candidates_dir is not None:
        return sorted(candidates_dir.glob("crypto_h*.yaml"))
    root = repo_root_from_lane() / "packages" / "crypto_lane" / "config" / "candidates"
    paths = sorted(root.glob("crypto_h*.yaml"))
    if paths:
        return paths
    legacy = repo_root_from_lane() / "models" / "candidates"
    return sorted(legacy.glob("crypto_h*.yaml"))


def list_backtest_config_paths(configs_dir: Path | None = None) -> list[Path]:
    root = configs_dir or (repo_root_from_lane() / "backtests" / "configs" / "crypto_hypotheses")
    return sorted(root.glob("h*.yaml"))
