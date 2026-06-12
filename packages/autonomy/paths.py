"""Path registry for the autonomy subsystem. Tests override via env."""
from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    # packages/autonomy/paths.py -> repo root is parents[2]
    return Path(__file__).resolve().parents[2]


def autonomy_dir() -> Path:
    override = os.environ.get("HFT3_AUTONOMY_DIR", "").strip()
    return Path(override) if override else (repo_root() / "runtime" / "autonomy")


def config_path() -> Path:
    override = os.environ.get("HFT3_AUTONOMY_CONFIG", "").strip()
    return Path(override) if override else (repo_root() / "configs" / "autonomy.yaml")


def state_path() -> Path:
    return autonomy_dir() / "autonomy_state.json"


def audit_path() -> Path:
    # The audit log lives with the other validation ledgers.
    base = os.environ.get("HFT3_AUTONOMY_DIR", "").strip()
    if base:
        return Path(base) / "autonomy_audit.jsonl"
    return repo_root() / "runtime" / "validation" / "autonomy_audit.jsonl"
