"""Bootstrap module: ensures `packages/` and `apps/` are on sys.path.

Makes `python -m hft3.research.run_autonomous` work from the repo root
even when hft3 has not been `pip install -e .`'d. This is a no-op when
the package is already importable.

Order of resolution:
  1. If `hft3` is already importable, do nothing.
  2. Otherwise, add `<repo_root>/packages` and `<repo_root>/apps` to
     sys.path, where `<repo_root>` is the parent of the `hft3`
     directory that contains this file.

This file is imported by every CLI entry point under `packages/hft3/`
that must work from a fresh shell. It is intentionally minimal — no
side effects beyond sys.path mutation and a single debug log.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT_HINT = Path(__file__).resolve().parents[3]  # hft3/.../research → hft3 → packages → repo


def _ensure_path_on_sys_path(p: Path) -> None:
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def _bootstrap() -> None:
    try:
        import hft3  # noqa: F401
        return
    except ImportError:
        pass
    repo_root = _REPO_ROOT_HINT
    if (repo_root / "packages").is_dir():
        _ensure_path_on_sys_path(repo_root / "packages")
    if (repo_root / "apps").is_dir():
        _ensure_path_on_sys_path(repo_root / "apps")
    try:
        import hft3  # noqa: F401
    except ImportError:
        # If the user is running from a tree where hft3 doesn't exist,
        # leave it to the original ImportError to surface.
        pass


_bootstrap()
