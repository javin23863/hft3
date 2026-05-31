"""Shared artifact and runtime path helpers."""
from __future__ import annotations

import os
from pathlib import Path

from hft3_bootstrap import repo_root as _bootstrap_repo_root


def repo_root() -> Path:
    return _bootstrap_repo_root()


def artifact_root() -> Path:
    env = os.environ.get("HFT3_ARTIFACTS_ROOT")
    if env:
        return Path(env).resolve()
    root = repo_root()
    nested = root / "artifacts" / "research_cards"
    if nested.is_dir():
        return nested
    artifacts = root / "artifacts"
    if artifacts.is_dir():
        return artifacts
    legacy = root / "research_cards"
    return legacy if legacy.is_dir() else nested


def workbench_runs_dir() -> Path:
    return artifact_root() / "workbench_runs"


def workbench_runs_dir_for(repo: Path) -> Path:
    for root in (
        repo / "artifacts" / "research_cards",
        repo / "artifacts",
        repo / "research_cards",
    ):
        candidate = root / "workbench_runs"
        if candidate.is_dir():
            return candidate
    env = os.environ.get("HFT3_ARTIFACTS_ROOT")
    if env:
        return Path(env).resolve() / "workbench_runs"
    return workbench_runs_dir()


def campaign_dir(campaign_id: str) -> Path:
    return workbench_runs_dir() / campaign_id


def campaign_dir_for(repo: Path, campaign_id: str) -> Path:
    return workbench_runs_dir_for(repo) / campaign_id


def campaign_event_dir(campaign_id: str, period: str, event_id: str) -> Path:
    period_key = period.replace(" ", "_")
    return campaign_dir(campaign_id) / "periods" / period_key / "events" / event_id


def runtime_validation_dir() -> Path:
    return repo_root() / "runtime" / "validation"


def event_replays_dir() -> Path:
    return artifact_root() / "event_replays"
