#!/usr/bin/env python3
"""Preflight workbench imports for scripts/launch_workbench.ps1."""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def _repo_root() -> Path:
    """Resolve repo root from this script's location (launcher sets PYTHONPATH)."""
    script_repo = Path(__file__).resolve().parents[1]
    if (script_repo / "workbench").is_dir():
        return script_repo
    for part in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        candidate = Path(part).resolve()
        if (candidate / "workbench").is_dir():
            return candidate
    return script_repo


def _bootstrap_sys_path(repo: Path) -> None:
    repo_str = str(repo)
    if repo_str not in sys.path:
        sys.path.insert(0, repo_str)
    os.environ.setdefault("PYTHONPATH", repo_str)


def main() -> int:
    repo = _repo_root()
    _bootstrap_sys_path(repo)

    try:
        from workbench.src.core.composition import CatalogEntry, DefensiveStub, ModelComposition
        from workbench.src.registry.model_catalog import load_catalog
        from workbench.ui.campaign_panel import get_session_composition

        catalog = load_catalog(repo)
        if not catalog:
            raise RuntimeError(f"load_catalog() returned empty catalog (repo={repo})")

        _ = CatalogEntry, DefensiveStub, ModelComposition, get_session_composition
    except Exception:
        print(f"workbench preflight failed (repo={repo})", file=sys.stderr)
        traceback.print_exc()
        return 1

    print("workbench import OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
