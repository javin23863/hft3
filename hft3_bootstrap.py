"""Bootstrap sys.path for src/, apps/ and packages/ layout.

Adds the following to sys.path (in order):
  1. packages/    — original lane packages (backward compat)
  2. apps/        — workbench UI and adapters
  3. src/         — new consolidated hft3 package tree
  4. repo root    — top-level modules (hft3_bootstrap, run_workbench, etc.)
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent


def repo_root() -> Path:
    return _ROOT


def setup_repo_paths() -> Path:
    _insert_reversed(pythonpath_entries())
    return _ROOT


def workbench_root(root: Path | None = None) -> Path:
    base = root or _ROOT
    for candidate in (base / "apps" / "workbench", base / "workbench"):
        if candidate.is_dir():
            return candidate
    return base / "apps" / "workbench"


def package_root(name: str, root: Path | None = None) -> Path:
    base = root or _ROOT
    for candidate in (base / "packages" / name, base / name, base / "src" / "hft3" / name):
        if candidate.is_dir():
            return candidate
    return base / "packages" / name


def data_system_root(root: Path | None = None) -> Path:
    return package_root("data_system", root)


def features_engine_root(root: Path | None = None) -> Path:
    return package_root("features_engine", root)


def pythonpath_entries(root: Path | None = None) -> list[str]:
    base = root or _ROOT
    # packages/ first so legacy hft3.validation wins over src/hft3
    entries = [str(base / "packages"), str(base / "apps"), str(base / "src"), str(base)]
    seen: set[str] = set()
    result: list[str] = []
    for e in entries:
        if e not in seen:
            seen.add(e)
            result.append(e)
    return result


def _insert_reversed(entries: list[str]) -> None:
    """Insert entries into sys.path so first entry has highest priority."""
    for s in reversed(entries):
        if s not in sys.path:
            sys.path.insert(0, s)
