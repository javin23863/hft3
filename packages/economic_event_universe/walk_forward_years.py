"""Backtest year range from apps/workbench/config/walk_forward.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml


def _walk_forward_path(repo_root: Path) -> Path:
    wb = repo_root / "apps" / "workbench"
    if not wb.is_dir():
        wb = repo_root / "workbench"
    return wb / "config" / "walk_forward.yaml"


def backtest_year_range(repo_root: Path) -> tuple[int, int]:
    """Min/max calendar years across all walk-forward periods."""
    path = _walk_forward_path(repo_root)
    if not path.is_file():
        return 2018, 2025
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    periods = raw.get("periods") or []
    if not periods:
        return 2018, 2025
    starts = [int(p["start_year"]) for p in periods if "start_year" in p]
    ends = [int(p["end_year"]) for p in periods if "end_year" in p]
    return min(starts), max(ends)
