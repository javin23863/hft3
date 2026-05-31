"""Personal sandbox lock — local Windows only; excluded from promotion (B4/B7)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

_LOCK_STATE = Path(".workbench_personal_unlock")  # repo-relative marker file


def load_personal_config(repo_root: Path) -> dict[str, Any]:
    from hft3_bootstrap import workbench_root

    path = workbench_root(repo_root) / "config" / "personal_lock.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def is_locked(repo_root: Path) -> bool:
    cfg = load_personal_config(repo_root)
    if not cfg.get("default_locked", True):
        return False
    return not (repo_root / _LOCK_STATE).is_file()


def set_unlocked(repo_root: Path, unlocked: bool) -> None:
    marker = repo_root / _LOCK_STATE
    if unlocked:
        marker.write_text("unlocked\n", encoding="utf-8")
    elif marker.is_file():
        marker.unlink()


def personal_date_range(repo_root: Path) -> tuple[str, str]:
    cfg = load_personal_config(repo_root)
    return str(cfg.get("start_date", "2026-03-01")), str(cfg.get("end_date", "2026-05-30"))


def is_personal_sandbox_date(release_date: str, repo_root: Path) -> bool:
    start, end = personal_date_range(repo_root)
    return start <= release_date <= end


def artifact_root(repo_root: Path) -> Path:
    cfg = load_personal_config(repo_root)
    rel = cfg.get("artifact_root", "research_cards/workbench_personal/")
    return repo_root / rel
