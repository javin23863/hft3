"""Bootstrap sys.path for apps/ and packages/ layout."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent


def repo_root() -> Path:
    return _ROOT


def setup_repo_paths() -> Path:
    for sub in ("", "packages", "apps"):
        p = _ROOT if not sub else _ROOT / sub
        s = str(p)
        if p.is_dir() and s not in sys.path:
            sys.path.insert(0, s)
    return _ROOT


def workbench_root(root: Path | None = None) -> Path:
    base = root or _ROOT
    for candidate in (base / "apps" / "workbench", base / "workbench"):
        if candidate.is_dir():
            return candidate
    return base / "apps" / "workbench"


def package_root(name: str, root: Path | None = None) -> Path:
    base = root or _ROOT
    for candidate in (base / "packages" / name, base / name):
        if candidate.is_dir():
            return candidate
    return base / "packages" / name


def data_system_root(root: Path | None = None) -> Path:
    return package_root("data_system", root)


def features_engine_root(root: Path | None = None) -> Path:
    return package_root("features_engine", root)


def pythonpath_entries(root: Path | None = None) -> list[str]:
    base = root or _ROOT
    return [str(base), str(base / "packages"), str(base / "apps")]
